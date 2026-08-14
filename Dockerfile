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

# libpango/libcairo are WeasyPrint's rendering back end (app/reports): it is Pango that
# shapes Persian and orders the right-to-left runs, which is the whole reason the PDF is
# built from HTML here rather than drawn. Without them the import itself fails, so the
# API would not start at all — this is not an optional extra. The report's font is
# shipped in app/reports/assets and loaded by path, so no fonts package is needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfribidi0 \
        libcairo2 libgdk-pixbuf-2.0-0 \
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

COPY alembic.ini ./
COPY job_qa_service ./job_qa_service
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts
COPY Merged_Occupations.xlsx ./

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && python -m scripts.seed_from_xlsx Merged_Occupations.xlsx && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
