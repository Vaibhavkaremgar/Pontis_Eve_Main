import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
COVERAGE_TMP = FRONTEND / "coverage" / ".tmp"
COVERAGE_LOCK = Path(tempfile.gettempdir()) / "pontis-eve-frontend-vitest.lock.d"

COMMANDS = {
    "typecheck": [
        FRONTEND / "node_modules" / "typescript" / "bin" / "tsc",
        "--noEmit",
        "-p",
        "tsconfig.json",
    ],
    "test": [
        FRONTEND / "node_modules" / "vitest" / "vitest.mjs",
        "run",
        "--coverage",
    ],
}


@contextmanager
def coverage_run_lock():
    """Serialize covered Vitest runs and discard interrupted temp artifacts."""
    while True:
        try:
            COVERAGE_LOCK.mkdir()
            break
        except FileExistsError:
            time.sleep(0.1)

    try:
        if COVERAGE_TMP.exists():
            for artifact in COVERAGE_TMP.iterdir():
                if artifact.is_file() or artifact.is_symlink():
                    artifact.unlink()
                elif artifact.is_dir():
                    import shutil

                    shutil.rmtree(artifact)
        yield
    finally:
        COVERAGE_LOCK.rmdir()


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in (*COMMANDS, "staged-test"):
        print("Usage: frontend_quality.py {typecheck|staged-test|test}", file=sys.stderr)
        return 2

    if sys.argv[1] == "staged-test":
        staged = [Path(name).as_posix() for name in sys.argv[2:] if ".test." in name or ".spec." in name]
        if not staged:
            return 0
        command = [
            FRONTEND / "node_modules" / "vitest" / "vitest.mjs",
            "run",
            *[Path(name).relative_to("frontend").as_posix() for name in staged],
        ]
    else:
        command = COMMANDS[sys.argv[1]].copy()
    launcher = command[0]
    if not launcher.is_file():
        print(
            "Frontend dependencies are not installed. Run "
            "`corepack yarn --cwd frontend install --frozen-lockfile`.",
            file=sys.stderr,
        )
        return 1

    run = coverage_run_lock() if sys.argv[1] == "test" else nullcontext()
    with run:
        result = subprocess.run(
            ["node", *(str(part) for part in command)],
            cwd=FRONTEND,
            check=False,
        )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
