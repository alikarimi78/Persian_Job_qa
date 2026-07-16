FROM python:3.11-slim

WORKDIR /srv

# CPU torch keeps the image small; single-query encoding does not need a GPU.
COPY requirements.txt .

RUN pip install --no-cache-dir torch==2.7.1 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

COPY job_qa_service.py alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts
COPY Merged_Occupations.xlsx ./

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && python -m scripts.seed_from_xlsx Merged_Occupations.xlsx && uvicorn app.main:app --host 0.0.0.0 --port 8000"]