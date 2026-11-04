import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
BIOME_LAUNCHER = FRONTEND / "node_modules" / "@biomejs" / "biome" / "bin" / "biome"

files = []
for name in sys.argv[1:]:
    candidate = Path(name)
    if not candidate.is_absolute():
        candidate = ROOT / candidate

    try:
        relative = candidate.resolve().relative_to(FRONTEND.resolve())
    except ValueError:
        continue

    if candidate.is_file():
        files.append((Path("frontend") / relative).as_posix())

if not files:
    sys.exit(0)

if not BIOME_LAUNCHER.is_file():
    print(
        "Biome is not installed. Run "
        "`corepack yarn --cwd frontend install --frozen-lockfile`.",
        file=sys.stderr,
    )
    sys.exit(1)

result = subprocess.run(
    ["node", str(BIOME_LAUNCHER), "check", *files],
    cwd=ROOT,
    check=False,
)

sys.exit(result.returncode)