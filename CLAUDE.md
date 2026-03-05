# CLAUDE.md - Development Guide for Claude Code

## Project Overview

ExpertAP is a Romanian public procurement BI platform. Backend is FastAPI (Python), frontend is a single React file (`index.tsx`), deployed on GCP Cloud Run with PostgreSQL + pgvector.

## Quick Commands

```bash
# Backend
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend
npm install && npm run dev

# Import decisions (test)
DATABASE_URL="postgresql+asyncpg://..." python scripts/import_decisions_from_gcs.py --limit 10

# Generate embeddings
DATABASE_URL="postgresql+asyncpg://..." python scripts/generate_embeddings.py
```

## Architecture

- **Frontend:** Single `index.tsx` file (React 19 + Vite + TailwindCSS)
- **Backend:** `backend/app/` - FastAPI with async SQLAlchemy
- **LLM:** Google Gemini via `google-genai` SDK (`backend/app/services/llm/gemini.py`)
- **RAG Pipeline:** `backend/app/services/rag.py` - vector search on ArgumentareCritica → LLM generation
- **Database Models:** `backend/app/models/decision.py` - DecizieCNSC, ArgumentareCritica, etc.

## Key Files

| File | Purpose |
|------|---------|
| `index.tsx` | Entire frontend (single-file React app) |
| `backend/app/services/rag.py` | RAG search + response generation |
| `backend/app/services/llm/gemini.py` | Gemini LLM provider |
| `backend/app/services/embedding.py` | Embedding generation service |
| `backend/app/services/analysis.py` | LLM decision analysis (ArgumentareCritica extraction) |
| `backend/app/models/decision.py` | All database models |
| `backend/app/api/v1/chat.py` | Chat API endpoint |
| `backend/app/api/v1/decisions.py` | Decisions CRUD API |
| `backend/app/api/v1/ragmemo.py` | RAG memo generation API |
| `scripts/import_decisions_from_gcs.py` | GCS → database import pipeline |

## Code Conventions

- Python: PEP 8, type hints, Google-style docstrings
- Commits: Conventional (`feat:`, `fix:`, `docs:`, `refactor:`)
- All text content and UI labels are in Romanian
- LLM system prompts are in Romanian
- Never commit secrets or API keys

## Database

- PostgreSQL with pgvector extension
- Primary RAG search unit: `ArgumentareCritica` (per-criticism chunks with embeddings)
- HNSW indexes on embedding columns for fast vector search
- Decision lookup supports: direct BO reference, vector search, keyword ILIKE fallback

## Deployment

- Push to `main` branch triggers Cloud Build → Cloud Run
- Never use `gcloud builds submit` manually
- Cloud Run URL: `https://expertap-api-850584928584.europe-west1.run.app/`

## Testing Considerations

- 10 test decisions currently imported (of ~3000 total in GCS)
- Check both vector search and keyword fallback paths
- Verify direct BO lookup works (e.g., "analizeaza BO2025_1011")
- Frontend changes: test in Vite dev server before deploying
