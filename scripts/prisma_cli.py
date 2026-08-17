"""The `prisma` CLI, run with `DATABASE_URL` filled in and the generator on PATH.

    venv/bin/python3 -m scripts.prisma_cli migrate deploy
    venv/bin/python3 -m scripts.prisma_cli migrate dev --name add_something
    venv/bin/python3 -m scripts.prisma_cli generate

Two things it does that `python -m prisma …` cannot do for itself:

1. **`DATABASE_URL`.** The deployment does not set one — compose's `env_file` carries the
   five `POSTGRES_*` / `DATABASE_*` parts, and `src/config.py` is what assembles them,
   quoting a password that might hold an `@`. The CLI reads only `DATABASE_URL`, so the
   bare command would find nothing to connect to. Importing `src.config` is what puts the
   assembled URL in the environment (see `_assemble_database_url` there).

2. **The generator's own executable.** `prisma generate` runs the Node CLI, which looks
   for a `prisma-client-py` program on `PATH` and dies with `/bin/sh: 1: prisma-client-py:
   not found` when the venv is not activated — which is how everything else in this repo
   is run (`venv/bin/python3 -m …`). The interpreter's own `bin` directory is prepended
   here, so the two styles agree.

`--schema` is supplied too: the CLI resolves `prisma/schema.prisma` against the current
directory, and this way the command does not depend on being run from the repo root.
"""

import os
import sys
from pathlib import Path

from prisma.cli import main as prisma_main

from src.config import settings  # noqa: F401  (imported for the os.environ side effect)

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "prisma" / "schema.prisma"

# Subcommands that take no schema; passing `--schema` to one of these is an error.
_NO_SCHEMA = {"version", "--version", "--help", "-h", "py"}


def main(argv: list[str]) -> None:
    os.environ["PATH"] = os.pathsep.join(
        [str(Path(sys.executable).parent), os.environ.get("PATH", "")])

    if not argv:
        argv = ["--help"]
    if argv[0] not in _NO_SCHEMA and not any(a.startswith("--schema") for a in argv):
        argv = [*argv, f"--schema={SCHEMA}"]
    # Exits the process itself — it is the CLI's own `main`, not a helper.
    prisma_main(["prisma", *argv], use_handler=False)


if __name__ == "__main__":
    main(sys.argv[1:])
