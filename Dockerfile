FROM python:3.11-slim

WORKDIR /srv

# torch comes from PyTorch's own index and BEFORE requirements.txt: this wheel already
# satisfies torch>=2.6,<2.8, so pip never resolves torch off PyPI on its own. Keep the
# ordering when touching dependencies.
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
COPY requirements.txt .

RUN pip install --no-cache-dir torch==2.7.1 --index-url ${TORCH_INDEX}
RUN pip install --no-cache-dir -r requirements.txt

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

COPY alembic.ini ./
COPY job_qa_service ./job_qa_service
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts
COPY Merged_Occupations.xlsx ./

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && python -m scripts.seed_from_xlsx Merged_Occupations.xlsx && uvicorn app.main:app --host 0.0.0.0 --port 8000"]