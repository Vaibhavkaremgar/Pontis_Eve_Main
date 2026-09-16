import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import qdrant_service


class _QueryClient:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def query_points(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(points=self.hits)


def test_query_points_deduplicates_by_stable_job_id(monkeypatch):
    client = _QueryClient([
        SimpleNamespace(payload={"job_id": "job-1"}, score=.4),
        SimpleNamespace(payload={"job_id": "job-2"}, score=.7),
        SimpleNamespace(payload={"job_id": "job-1"}, score=.9),
    ])
    monkeypatch.setattr(qdrant_service, "_get_client", lambda: client)

    assert qdrant_service.search_job_chunks([.1], limit=150) == [("job-1", .9), ("job-2", .7)]
    assert client.calls[0]["limit"] == 150
    assert "score_threshold" not in client.calls[0]


def test_query_points_keeps_all_distinct_available_jobs(monkeypatch):
    client = _QueryClient([
        SimpleNamespace(payload={"job_id": f"job-{index}"}, score=1 - index / 100)
        for index in range(5)
    ])
    monkeypatch.setattr(qdrant_service, "_get_client", lambda: client)

    assert len(qdrant_service.search_job_chunks([.1], limit=150)) == 5
