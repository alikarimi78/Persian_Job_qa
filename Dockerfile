FROM python:3.11.16

WORKDIR /srv

ENV PIP_RETRIES=10 \
    PIP_TIMEOUT=120 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfribidi0 \
        libcairo2 libgdk-pixbuf-2.0-0 \
        nodejs npm \
        vim iputils-ping curl \
    && rm -rf /var/lib/apt/lists/*

ARG TORCH_INDEX=https://download.pytorch.org/whl/cu126

RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install torch==2.7.1 --index-url ${TORCH_INDEX}

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install -r requirements.txt

COPY prisma ./prisma
RUN prisma py fetch \
 && prisma generate --schema=prisma/schema.prisma

COPY job_qa_service ./job_qa_service
COPY src ./src
COPY scripts ./scripts
COPY main.py ./
COPY Merged_Occupations.xlsx ./

EXPOSE 8000

CMD ["sh", "-c", "python -m scripts.prisma_cli migrate deploy && python -m scripts.seed_from_xlsx Merged_Occupations.xlsx && uvicorn main:app --host 0.0.0.0 --port 8000"]
