import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    executable = shutil.which("gitleaks")
    if executable is None:
        print(
            "Gitleaks is required but was not found on PATH. Install Gitleaks, then rerun "
            "`pre-commit run gitleaks --all-files`.",
            file=sys.stderr,
        )
        return 1

    command = [executable, "git", "--staged", "--redact"]
    config = ROOT / ".gitleaks.toml"
    if config.is_file():
        command.extend(["--config", str(config)])
    result = subprocess.run(command, cwd=ROOT, check=False)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
