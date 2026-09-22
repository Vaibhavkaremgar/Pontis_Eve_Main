"""Read-only, evidence-based dry run for missing Ashby and Lever job URLs.

This module has no mutating SQL. A candidate must be an explicit job-specific
URL on the same ATS and have a unique, sufficiently strong identity match.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sqlalchemy import text
from app.job_ingestion.normalize import _valid_http_url

ATS_TYPES = ("ashby", "lever")
URL_KEYS = {"url", "joburl", "applyurl", "hostedurl", "absoluteurl", "externalurl", "sourceurl", "show", "apply", "postingurl", "careerurl"}
SUMMARY_COLUMNS = ("missing", "historical exact", "database duplicate", "current exact ID", "safe re-resolution", "ambiguous", "unrecoverable")


def _get_session_local():
    from server import SessionLocal  # noqa: PLC0415
    return SessionLocal


def _normal(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _job_url_for_ats(value: Any, ats_type: str) -> str | None:
    """Accept an explicit, same-ATS, job-specific URL only."""
    url = _valid_http_url(value)
    if not url:
        return None
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    parts = [part for part in parsed.path.split("/") if part]
    expected = "jobs.ashbyhq.com" if ats_type == "ashby" else "jobs.lever.co"
    return url if host == expected and len(parts) >= 2 else None


def _decode(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return value


def _walk_stored_urls(value: Any, ats_type: str, key: str = "") -> Iterable[str]:
    """Inspect URL-named fields only; descriptions/prose are intentionally ignored."""
    if isinstance(value, str):
        if key.casefold().replace("_", "") in URL_KEYS:
            url = _job_url_for_ats(value, ats_type)
            if url:
                yield url
    elif isinstance(value, dict):
        for child_key, child_value in value.items():
            yield from _walk_stored_urls(child_value, ats_type, str(child_key))
    elif isinstance(value, list):
        for child_value in value:
            yield from _walk_stored_urls(child_value, ats_type, key)


def _urls_in(value: Any, ats_type: str) -> set[str]:
    return set(_walk_stored_urls(_decode(value), ats_type))


async def _query(SessionLocal, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    async with SessionLocal() as db:
        result = await db.execute(text(sql), params)
        return [dict(row) for row in result.mappings().fetchall()]


async def _load_target_rows(SessionLocal) -> list[dict[str, Any]]:
    return await _query(SessionLocal, """
        SELECT jd.id, jd.job_id, jd.company_name, jd.ats_type, jd.ats_job_id, jd.job_url,
               jd.title, jd.location, jd.department, jd.employment_type, jd.structured_data,
               jd.source_app, jd.created_by_source, jd.updated_by_source, jd.company_website_url,
               cr.identifier, cr.career_url, cr.ats_type AS registry_ats_type,
               js.source_name, js.source_type, js.api_endpoint, js.base_url
        FROM job_descriptions jd
        LEFT JOIN company_registry cr ON cr.id = jd.company_registry_id
        LEFT JOIN LATERAL (
            SELECT source_name, source_type, api_endpoint, base_url FROM job_sources
            WHERE LOWER(source_type) = LOWER(jd.ats_type) AND enabled = TRUE
            ORDER BY updated_at DESC NULLS LAST LIMIT 1
        ) js ON TRUE
        WHERE jd.is_active = TRUE AND LOWER(jd.ats_type) = ANY(:ats_types)
          AND NULLIF(BTRIM(jd.job_url), '') IS NULL
        ORDER BY jd.ats_type, jd.company_name, jd.id
    """, {"ats_types": list(ATS_TYPES)})


async def _load_sync_metadata(SessionLocal) -> list[dict[str, Any]]:
    return await _query(SessionLocal, """
        SELECT LOWER(js.source_type) AS ats_type, jsr.id AS sync_run_id, jsr.started_at,
               jsr.completed_at, jsr.metadata
        FROM job_sync_runs jsr JOIN job_sources js ON js.id = jsr.source_id
        WHERE jsr.metadata IS NOT NULL AND LOWER(js.source_type) = ANY(:ats_types)
        ORDER BY jsr.completed_at DESC NULLS LAST
    """, {"ats_types": list(ATS_TYPES)})


async def _load_exact_duplicates(SessionLocal) -> list[dict[str, Any]]:
    return await _query(SessionLocal, """
        SELECT id, company_name, ats_type, ats_job_id, title, location, department,
               employment_type, job_url, structured_data
        FROM job_descriptions
        WHERE LOWER(ats_type) = ANY(:ats_types) AND ats_job_id IS NOT NULL
          AND NULLIF(BTRIM(job_url), '') IS NOT NULL
    """, {"ats_types": list(ATS_TYPES)})


def _objects_for_id(value: Any, ats_job_id: str) -> Iterable[dict[str, Any]]:
    value = _decode(value)
    if isinstance(value, dict):
        if any(str(item).strip() == ats_job_id for item in value.values() if not isinstance(item, (dict, list))):
            yield value
        for child in value.values():
            yield from _objects_for_id(child, ats_job_id)
    elif isinstance(value, list):
        for child in value:
            yield from _objects_for_id(child, ats_job_id)


def _stable_fields(row: dict[str, Any], job: dict[str, Any]) -> tuple[bool, list[str]]:
    if not _normal(row.get("title")) or _normal(row.get("title")) != _normal(job.get("title")):
        return False, []
    evidence = ["exact normalized title"]
    for field in ("location", "department", "employment_type"):
        stored = _normal(row.get(field))
        if stored:
            if stored != _normal(job.get(field)):
                return False, []
            evidence.append(f"exact {field}")
    return len(evidence) > 1, evidence  # Never title-only.


def _same_stable_identity(row: dict[str, Any], job: dict[str, Any]) -> bool:
    return _stable_fields(row, job)[0]


def _normalised_board_job(raw: dict[str, Any], ats_type: str, company: str) -> dict[str, Any]:
    from app.job_ingestion.normalize import normalize_ashby, normalize_lever
    # Supports normalized fixtures as well as raw connector payloads.
    if "ats_job_id" in raw and "job_url" in raw:
        return raw
    return normalize_ashby(raw, company) if ats_type == "ashby" else normalize_lever(raw, company)


def _candidate(row, url, source, matched_id, evidence, category):
    return {"job_descriptions.id": str(row["id"]), "company": row.get("company_name"), "title": row.get("title"),
            "ats_type": row.get("ats_type"), "old_ats_job_id": row.get("ats_job_id"), "matched_ats_id": matched_id,
            "matched_url": url, "recovery_source": source, "matching_evidence": evidence,
            "confidence/category": category}


def _resolve_board_details(row, jobs):
    ats_type, old_id = _normal(row.get("ats_type")), str(row.get("ats_job_id") or "").strip()
    exact = [job for job in jobs if str(job.get("ats_job_id") or "").strip() == old_id and _job_url_for_ats(job.get("job_url"), ats_type)]
    if len(exact) == 1:
        job = exact[0]
        return "current exact ID", _candidate(row, job["job_url"], "current ATS board", old_id,
            ["exact ATS job ID", "same registered company board"], "safe/current exact ID"), []
    stable = []
    for job in jobs:
        matched, evidence = _stable_fields(row, job)
        if matched and _job_url_for_ats(job.get("job_url"), ats_type):
            stable.append((job, evidence))
    if len(stable) == 1:
        job, evidence = stable[0]
        return "safe re-resolution", _candidate(row, job["job_url"], "current ATS board", str(job["ats_job_id"]),
            evidence + ["same registered company board", "old ATS ID absent from current board"], "safe/re-resolved ATS ID"), []
    competing = [{"ats_job_id": job["ats_job_id"], "url": job["job_url"], "evidence": evidence} for job, evidence in stable]
    return ("ambiguous", None, competing) if stable else ("unrecoverable", None, [])


def _resolve_from_board(row, jobs):
    """Compatibility helper: resolve one board row without report-only detail."""
    kind, candidate, _ = _resolve_board_details(row, jobs)
    return kind, candidate["matched_url"] if candidate else None


def _print_report(report):
    print("ATS | missing | historical exact | database duplicate | current exact ID | safe re-resolution | ambiguous | unrecoverable")
    for ats in ATS_TYPES:
        c = report["summary"][ats]
        print(f"{ats.title()} | " + " | ".join(str(c[name]) for name in SUMMARY_COLUMNS))
        # Retain the original terse lines for existing operational consumers.
        print(f"{ats.title()} missing: {c['missing']}")
        print(f"{ats.title()} exact URL recovered from stored data: {c['historical exact']}")
        print(f"{ats.title()} exact current ATS id recovered: {c['current exact ID']}")
        print(f"{ats.title()} URL recovered by safe re-resolution: {c['safe re-resolution']}")
        print(f"{ats.title()} ambiguous matches: {c['ambiguous']}")
        print(f"{ats.title()} genuinely unrecoverable: {c['unrecoverable']}")
    print("\nRecovered candidates:")
    for item in report["recovered_candidates"]:
        print(json.dumps(item, sort_keys=True))
    print("\nAmbiguous candidates:")
    for item in report["ambiguous_candidates"]:
        print(json.dumps(item, sort_keys=True))


async def build_report():
    from app.job_ingestion.collect_jobs import JobCollector
    SessionLocal = _get_session_local()
    rows, metadata, duplicates = await _load_target_rows(SessionLocal), await _load_sync_metadata(SessionLocal), await _load_exact_duplicates(SessionLocal)
    report = {"scope": "active Ashby and Lever rows with empty job_url only; no updates performed", "summary": {ats: Counter() for ats in ATS_TYPES}, "recovered_candidates": [], "ambiguous_candidates": [], "unrecoverable": [], "board_failures": []}
    duplicate_index = defaultdict(list)
    for duplicate in duplicates:
        url = _job_url_for_ats(duplicate.get("job_url"), _normal(duplicate.get("ats_type")))
        if url:
            duplicate_index[(_normal(duplicate.get("ats_type")), str(duplicate.get("ats_job_id")))].append(url)
    remaining = []
    for row in rows:
        ats, old_id = _normal(row.get("ats_type")), str(row.get("ats_job_id") or "").strip()
        report["summary"][ats]["missing"] += 1
        urls = _urls_in(row.get("structured_data"), ats)
        for run in metadata:
            if run.get("ats_type") == ats:
                for obj in _objects_for_id(run.get("metadata"), old_id):
                    urls.update(_urls_in(obj, ats))
        if len(urls) == 1:
            url = urls.pop()
            report["summary"][ats]["historical exact"] += 1
            report["recovered_candidates"].append(_candidate(row, url, "structured_data/job_sync_runs metadata", old_id, ["explicit retained ATS URL", "exact ATS ID in retained source"], "safe/historical exact"))
        elif len(urls) > 1:
            report["summary"][ats]["ambiguous"] += 1
            report["ambiguous_candidates"].append({"job_descriptions.id": str(row["id"]), "company": row.get("company_name"), "title": row.get("title"), "ats_type": ats, "old_ats_job_id": old_id, "competing_urls": sorted(urls), "rejected_because": "retained explicit URL values disagree"})
        else:
            dup_urls = set(duplicate_index[(ats, old_id)])
            if len(dup_urls) == 1:
                url = dup_urls.pop()
                report["summary"][ats]["database duplicate"] += 1
                report["recovered_candidates"].append(_candidate(row, url, "database duplicate", old_id, ["same ATS type", "exact ATS job ID", "explicit URL on duplicate row"], "safe/database exact duplicate"))
            elif len(dup_urls) > 1:
                report["summary"][ats]["ambiguous"] += 1
                report["ambiguous_candidates"].append({"job_descriptions.id": str(row["id"]), "company": row.get("company_name"), "title": row.get("title"), "ats_type": ats, "old_ats_job_id": old_id, "competing_urls": sorted(dup_urls), "rejected_because": "same ATS ID has conflicting database URLs"})
            else:
                remaining.append(row)
    collector, groups = JobCollector(), defaultdict(list)
    for row in remaining:
        groups[(_normal(row.get("ats_type")), str(row.get("identifier") or "").strip(), str(row.get("company_name") or "").strip())].append(row)
    for (ats, identifier, company), group in groups.items():
        jobs, failure = None, None
        if not identifier:
            failure = "no company_registry identifier"
        else:
            try:
                jobs = [_normalised_board_job(raw, ats, company) for raw in collector.collect_company_jobs(ats, identifier, company)]
            except Exception as exc:
                failure = f"{type(exc).__name__}: {exc}"
        if failure:
            report["board_failures"].append({"ats_type": ats, "company": company, "identifier": identifier, "reason": failure})
        for row in group:
            kind, candidate, competing = _resolve_board_details(row, jobs) if jobs is not None else ("unrecoverable", None, [])
            report["summary"][ats][kind] += 1
            if candidate:
                report["recovered_candidates"].append(candidate)
            elif kind == "ambiguous":
                report["ambiguous_candidates"].append({"job_descriptions.id": str(row["id"]), "company": row.get("company_name"), "title": row.get("title"), "ats_type": ats, "old_ats_job_id": row.get("ats_job_id"), "competing_urls": competing, "rejected_because": "multiple current-board jobs meet stable identity criteria"})
            else:
                report["unrecoverable"].append({"job_descriptions.id": str(row["id"]), "company": row.get("company_name"), "title": row.get("title"), "ats_type": ats, "old_ats_job_id": row.get("ats_job_id"), "reason": "no unique explicit ATS URL established"})
    report["summary"] = {ats: {name: int(report["summary"][ats][name]) for name in SUMMARY_COLUMNS} for ats in ATS_TYPES}
    return report


async def main(output: Path | None = None):
    report = await build_report()
    _print_report(report)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"\nDetailed JSON report: {output}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--quiet", action="store_true", help="suppress connector logs")
    args = parser.parse_args()
    if args.quiet:
        logging.disable(logging.INFO)
    asyncio.run(main(args.output))
