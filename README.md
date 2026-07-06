# Persian Occupation Q&A — FastAPI backend

## Run
```bash
cp .env.example .env       # fill in secrets
docker compose up -d --build
docker compose exec api python -m scripts.seed_from_xlsx Merged_Occupations.xlsx
docker compose restart api # engine loads the seeded data on startup
```
Swagger UI: http://localhost:8000/docs

## Endpoints
| Method | Path | Access | Purpose |
|---|---|---|---|
| POST | /search | public | ask a question |
| POST | /auth/register, /auth/login | public | JWT auth |
| POST | /jobs/suggestions | logged-in | suggest a full record (pending) |
| GET | /jobs/suggestions/mine | logged-in | my suggestions + statuses |
| GET | /admin/suggestions | admin | list pending records |
| POST | /admin/suggestions/{id}/approve · /reject | admin | review |
| POST | /admin/jobs | admin | add record directly (approved) |
| POST | /admin/rebuild | admin | rebuild embeddings in background |
| GET | /admin/rebuild/status | admin | rebuild progress/result |

Note: place `job_qa_service.py` (the RAG engine module) in the project root;
it needs the small DataFrame-input patch described in the delivery notes.
