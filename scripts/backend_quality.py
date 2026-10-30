import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
BASELINE = BACKEND / "ruff-baseline.json"

# Selects the Python interpreter for running development tools.
# It prefers the backend virtual environment and falls back to the
# currently running Python interpreter if the backend environment is unavailable.
def tool_python() -> str:
    """Prefer the backend virtual environment for developer tools."""
    candidates = (
        BACKEND / ".venv" / "Scripts" / "python.exe",
        BACKEND / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable

# Executes the given command from the repository root and returns its exit code.
# A zero exit code generally indicates success, while a nonzero code indicates
# a failure or another result that the calling function must handle.
def run_command(command: list[str]) -> int:
    return subprocess.run(command, cwd=ROOT, check=False).returncode

# Converts a file path into a path relative to the backend directory,
# using forward slashes to keep the path format consistent across operating systems.
def relative_backend_path(filename: str) -> str:
    return Path(filename).resolve().relative_to(BACKEND.resolve()).as_posix()

# Creates a stable fingerprint for a Ruff violation using its backend-relative
# file path, rule code, and normalized source line. This helps compare current
# violations with the existing baseline.
def violation_key(item: dict) -> str:
    path = relative_backend_path(item["filename"])
    row = item["location"]["row"]
    source = Path(item["filename"]).read_text(encoding="utf-8").splitlines()[row - 1]
    normalized_source = " ".join(source.split())
    return f"{path}|{item['code']}|{normalized_source}"


def ruff_lint_counts(files: list[str]) -> tuple[Counter[str], int]:
    result = subprocess.run(
        [tool_python(), "-m", "ruff", "check", "--output-format", "json", "--config", str(BACKEND / "pyproject.toml"), *files],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if result.returncode not in (0, 1):
        print(result.stderr, file=sys.stderr, end="")
        return Counter(), result.returncode
    diagnostics = json.loads(result.stdout or "[]")
    return Counter(violation_key(item) for item in diagnostics), 0


def load_baseline() -> Counter[str]:
    with BASELINE.open(encoding="utf-8") as stream:
        data = json.load(stream)
    return Counter({str(key): int(value) for key, value in data["violations"].items()})


def new_lint_violations(current: Counter[str], baseline: Counter[str]) -> Counter[str]:
    return Counter({key: count - baseline[key] for key, count in current.items() if count > baseline[key]})


def run_ruff_lint(files: list[str]) -> int:
    current, returncode = ruff_lint_counts(files)
    if returncode != 0:
        return returncode
    new_violations = new_lint_violations(current, load_baseline())
    if new_violations:
        print("New Ruff violations detected:")
        for key, count in sorted(new_violations.items()):
            print(f"  {key}: {count}")
        return 1
    print("Ruff lint passed against the checked-in baseline.")
    return 0


def update_baseline() -> int:
    files = [str(BACKEND)]
    counts, returncode = ruff_lint_counts(files)
    if returncode != 0:
        return returncode
    data = {
        "version": "0.14.3",
        "strategy": "file-rule-source-fingerprint",
        "violations": dict(sorted(counts.items())),
    }
    BASELINE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Updated Ruff baseline at {BASELINE}.")
    return 0


def staged_python_files() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR", "--", "backend"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    files = []
    backend_root = BACKEND.resolve()
    for name in result.stdout.split("\0"):
        if not name:
            continue
        path = Path(name)
        absolute_path = (ROOT / path).resolve()
        if path.suffix == ".py" and backend_root in absolute_path.parents and absolute_path.is_file():
            files.append(path.as_posix())
    return files


def run_ruff(files: list[str]) -> int:
    if not files:
        print("No backend Python files to check; skipping Ruff.")
        return 0
    lint_result = run_ruff_lint(files)
    if lint_result != 0:
        return lint_result
    return run_command([tool_python(), "-m", "ruff", "format", "--check", "--config", str(BACKEND / "pyproject.toml"), *files])


def run_mypy() -> int:
    files = staged_python_files()
    if not files:
        print("No staged backend Python files; skipping mypy.")
        return 0
    print(f"Running mypy on {len(files)} staged backend Python file(s)...")
    return run_command([tool_python(), "-m", "mypy", "--config-file", str(BACKEND / "mypy.ini"), *files])


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/backend_quality.py [ruff|mypy|update-ruff-baseline]", file=sys.stderr)
        return 2
    action = sys.argv[1]
    if action == "ruff":
        return run_ruff(sys.argv[2:])
    if action == "mypy":
        return run_mypy()
    if action == "update-ruff-baseline":
        return update_baseline()
    print(f"Unknown action: {action}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
