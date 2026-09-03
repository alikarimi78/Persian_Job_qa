import os
import sys
from pathlib import Path

from prisma.cli import main as prisma_main

from src.config import settings

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "prisma" / "schema.prisma"

_NO_SCHEMA = {"version", "--version", "--help", "-h", "py"}


def main(argv: list[str]) -> None:
    os.environ["PATH"] = os.pathsep.join(
        [str(Path(sys.executable).parent), os.environ.get("PATH", "")])

    if not argv:
        argv = ["--help"]
    if argv[0] not in _NO_SCHEMA and not any(a.startswith("--schema") for a in argv):
        argv = [*argv, f"--schema={SCHEMA}"]
    prisma_main(["prisma", *argv], use_handler=False)


if __name__ == "__main__":
    main(sys.argv[1:])
