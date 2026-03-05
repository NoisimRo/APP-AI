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

### Embedding Dimensions

- **Model:** `gemini-embedding-001` (native output: 3072 dimensions, capped to 2000)
- **DB columns:** `Vector(2000)` on `argumentare_critica`, `sectiuni_decizie`, `citate_verbatim`
- **Why 2000?** pgvector HNSW indexes have a 2000 dimension limit. We use `output_dimensionality=2000` in the Gemini API call. This is 2.6x better than the original 768 while keeping HNSW index support.
- **History:** Started at 768 (text-embedding-004 convention) → tried 3072 (native) but hit pgvector HNSW limit → settled on 2000.
- **Migration SQL** (run once, then regenerate all embeddings):
  ```sql
  -- Drop old HNSW indexes
  DROP INDEX IF EXISTS ix_arg_embedding_hnsw;
  DROP INDEX IF EXISTS ix_sectiuni_embedding_hnsw;
  DROP INDEX IF EXISTS ix_citate_embedding_hnsw;

  -- Alter columns to 2000
  ALTER TABLE argumentare_critica ALTER COLUMN embedding TYPE vector(2000);
  ALTER TABLE sectiuni_decizie ALTER COLUMN embedding TYPE vector(2000);
  ALTER TABLE citate_verbatim ALTER COLUMN embedding TYPE vector(2000);

  -- Recreate HNSW indexes
  CREATE INDEX ix_arg_embedding_hnsw ON argumentare_critica
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
  CREATE INDEX ix_sectiuni_embedding_hnsw ON sectiuni_decizie
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
  CREATE INDEX ix_citate_embedding_hnsw ON citate_verbatim
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
  ```
- After migration, regenerate embeddings: `python scripts/generate_embeddings.py --force`

### Key Tables

| Table | Purpose | RAG? |
|-------|---------|------|
| `decizii_cnsc` | Main decision table | No |
| `argumentare_critica` | Per-criticism argumentation (PRIMARY RAG unit) | Yes (2000-dim) |
| `sectiuni_decizie` | Decision sections | Yes (2000-dim) |
| `citate_verbatim` | Verbatim quotes | Yes (2000-dim) |
| `referinte_articole` | Legal article references | No |
| `nomenclator_cpv` | CPV codes nomenclator | No |

### ArgumentareCritica Fields (populated by LLM analysis)

- `argumente_contestator` - contestant's arguments (text)
- `jurisprudenta_contestator` - court decisions invoked by contestant (ARRAY)
- `argumente_ac` - contracting authority's arguments (text)
- `jurisprudenta_ac` - court decisions invoked by AC (ARRAY)
- `argumente_intervenienti` - intervenient arguments (JSON: `[{"nr": 1, "argumente": "...", "jurisprudenta": [...]}]`)
- `elemente_retinute_cnsc` - facts retained by CNSC (text)
- `argumentatie_cnsc` - CNSC reasoning (text)
- `jurisprudenta_cnsc` - court decisions cited by CNSC (ARRAY)
- `castigator_critica` - winner: `contestator`, `autoritate`, `partial`, `unknown`

## Deployment

- Push to `main` branch triggers Cloud Build → Cloud Run
- Never use `gcloud builds submit` manually
- Cloud Run URL: `https://expertap-api-850584928584.europe-west1.run.app/`

## Testing Considerations

- 10 test decisions currently imported (of ~3000 total in GCS)
- Check both vector search and keyword fallback paths
- Verify direct BO lookup works (e.g., "analizeaza BO2025_1011")
- Frontend changes: test in Vite dev server before deploying
