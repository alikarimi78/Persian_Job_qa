# The base is pinned by **digest**, not by the `3.11-slim` tag.
#
# That tag moves. This machine pulled three different digests of it inside one month —
# 90edbeb8 (2026-07-15), 3c35dbe0 (2026-08-05), a630a63c (2026-08-14) — because the
# official Python images are rebuilt whenever Debian ships a security update. A changed
# base layer invalidates *every* layer under it, so the 4.3 GB CUDA torch wheel below
# was re-downloaded on a rebuild days later even though nothing in this repo had
# changed. The digest is what makes the build static; the tag is kept beside it only so
# a reader can see which one it is.
#
# To move to a newer base, do it deliberately:
#   docker pull python:3.11-slim
#   docker image inspect python:3.11-slim --format '{{index .RepoDigests 0}}'
# and paste the digest here. (This one is Python 3.11.16.)
FROM python:3.11.16

WORKDIR /srv

# download.pytorch.org drops mid-stream from here: the first build of this file died
# with `BrokenPipeError: Connection broken` three minutes into the 4.3 GB wheel set.
# These make pip persistent about it. What makes a retry *cheap* is the cache mount on
# the install below — every wheel that did land is already in /root/.cache/pip, so the
# next attempt fetches only what is still missing rather than starting over.
ENV PIP_RETRIES=10 \
    PIP_TIMEOUT=120 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Everything below is ordered by how often it changes, slowest first, because a layer
# that is invalidated takes every later layer with it. OS packages → torch → the rest of
# the dependencies → the application. The application is what changes every build, so it
# is last and costs seconds.

# libpango/libcairo are WeasyPrint's rendering back end (src/reports): it is Pango that
# shapes Persian and orders the right-to-left runs, which is the whole reason the PDF is
# built from HTML here rather than drawn. Without them the import itself fails, so the
# API would not start at all — this is not an optional extra. The report's font is
# shipped in src/reports/assets and loaded by path, so no fonts package is needed.
#
# `nodejs` and `npm` are Prisma's. The schema parser, the migration engine and the
# generator are the upstream Prisma CLI, which is a Node program — `prisma generate` and
# `migrate deploy` both shell out to it. `node` is therefore a build *and* run dependency:
# migrations are applied by this container's CMD, not baked into the image. Nothing the
# application imports touches Node.
#
# **`npm` is a separate Debian package** and is not optional here: the `prisma` wheel does
# not vendor the Node CLI, it `npm install`s it into ~/.cache/prisma-python on first use.
# The `prisma generate` below is what triggers that, so the CLI lands in an image layer
# instead of being fetched when the container starts.
#
# Both come from Debian rather than through `nodeenv`, which is what the wheel downloads
# when it finds no global node (`PRISMA_USE_GLOBAL_NODE` defaults on).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfribidi0 \
        libcairo2 libgdk-pixbuf-2.0-0 \
        nodejs npm \
        vim iputils-ping curl \
    && rm -rf /var/lib/apt/lists/*

# torch comes from PyTorch's own index and BEFORE requirements.txt: this wheel already
# satisfies torch>=2.6,<2.8, so pip never resolves torch off PyPI on its own. Keep the
# ordering when touching dependencies.
#
# `COPY requirements.txt` used to sit *above* this line, which meant every edit to
# requirements.txt — a line about jinja2 for the PDF reports, say — invalidated the
# torch layer too and re-downloaded 4.3 GB to install a 300 KB package. The copy now
# happens after torch is already in, so the two dependency sets are independent.
#
# cu126 is the CUDA build. It cuts query encoding from ~155 ms to ~22 ms and a cold
# corpus encode from ~31 min to ~4 min, and needs nothing from the application:
# job_qa_service/engine.py picks its device from torch.cuda.is_available(), so this
# same image runs on a GPU-less host too — just on CPU. It costs ~4.3 GB over the CPU
# wheel (torch 1.6 GB + its nvidia-* deps 2.7 GB), so for a host that will never have a
# GPU, build with --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cpu.
# cu126 covers Pascal through Hopper; a Blackwell card would need cu128 instead.
#
# The GPU only reaches the container if the host has nvidia-container-toolkit AND the
# compose service reserves a device (see docker-compose.yml in the deploy repo).
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu126

# The `--mount=type=cache` is the second line of defence, for the times the layer *is*
# deliberately invalidated (a new TORCH_INDEX, a bumped base): pip then finds the wheel
# already downloaded instead of fetching it again. It is a mount, not a layer, so the
# cache never lands in the image — which is what `--no-cache-dir` used to be for, and
# why that flag is gone. No `# syntax=` directive is needed: BuildKit's built-in
# frontend has supported cache mounts for a long time, and pinning a frontend image
# would add one more thing to pull on every build.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install torch==2.7.1 --index-url ${TORCH_INDEX}

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install -r requirements.txt

# The schema comes before the application code and on its own, because `prisma generate`
# writes the client *into the installed package* and only re-runs when the schema
# changes — putting it below `COPY src` would regenerate on every code edit, and putting
# it above `requirements.txt` would not have the `prisma` package to generate with.
#
# `prisma py fetch` downloads the query engine here rather than leaving it to first use,
# which would make *container start* depend on binaries.prisma.sh being reachable — a
# network call at boot, in an image whose whole point is being self-contained. It lands
# in /root/.cache/prisma-python, and deliberately not behind a cache mount: a cache mount
# is not a layer, so the binaries would not be in the image at all.
#
# `prisma generate` writes the client into the installed `prisma` package, which is why
# `from prisma import Prisma` resolves at runtime with no generate step of its own. It is
# also what installs the Node CLI into the same cache directory — see the npm note above.
COPY prisma ./prisma
RUN prisma py fetch \
 && prisma generate --schema=prisma/schema.prisma

COPY job_qa_service ./job_qa_service
COPY src ./src
COPY scripts ./scripts
COPY main.py ./
COPY Merged_Occupations.xlsx ./

EXPOSE 8000

# `scripts.prisma_cli` rather than a bare `prisma migrate deploy`: the CLI reads
# DATABASE_URL out of the environment, and compose's env_file carries the five
# POSTGRES_*/DATABASE_* parts instead — `src/config.py` is what assembles and exports
# them. Everything after that is the ordinary Prisma migration flow.
#
# `migrate deploy` applies whatever in `prisma/migrations/` has not run yet and never
# generates anything, which is what makes it the deployment command; `migrate dev` is for
# a developer's machine and would try to reset a database it found drifted.
CMD ["sh", "-c", "python -m scripts.prisma_cli migrate deploy && python -m scripts.seed_from_xlsx Merged_Occupations.xlsx && uvicorn main:app --host 0.0.0.0 --port 8000"]
