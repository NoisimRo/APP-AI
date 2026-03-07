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
| `backend/app/services/redflags_analyzer.py` | Red Flags Detector (two-pass: detect → ground) |
| `backend/app/api/v1/redflags.py` | Red Flags API endpoint |
| `scripts/import_decisions_from_gcs.py` | GCS → database import pipeline |
| `scripts/import_legislatie.py` | Legislation .md → DB import (alineat-level) |

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
- **DB columns:** `Vector(2000)` on `argumentare_critica`, `sectiuni_decizie`, `citate_verbatim`, `articole_legislatie`
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
| `articole_legislatie` | Legislation articles at alineat level (Red Flags grounding) | Yes (2000-dim) |

### ArticoleLegislatie Fields (populated by import_legislatie.py)

Stores legislation at **alineat** granularity — one row per alineat. Enables exact citations like `art. 2 alin. (2) lit. a) și b) din Legea nr. 98/2016`.

- `act_normativ` - legislative act: "Legea 98/2016", "HG 395/2016" (VARCHAR 100)
- `numar_articol` - article number as integer for sorting (INTEGER)
- `articol` - article label: "art. 2", "art. 178" (VARCHAR 50)
- `alineat` - alineat number: 1, 2, 3... or NULL if no alineats (INTEGER)
- `alineat_text` - formatted: "alin. (1)", "alin. (2)" (VARCHAR 20)
- `litere` - litere within alineat (JSON): `[{"litera": "a", "text": "nediscriminarea"}, ...]`
- `text_integral` - full text of the alineat including litere (TEXT)
- `citare` - canonical citation: "art. 2 alin. (2)" or "art. 1" (VARCHAR 100, UNIQUE per act)
- `capitol` - chapter context: "I - Dispoziții generale" (VARCHAR 500)
- `sectiune` - section context: "1 - Obiect, scop și principii" (VARCHAR 500)
- `embedding` - Vector(2000) with HNSW index

**Import:** `python scripts/import_legislatie.py --dir date-expert-app/legislatie-ap`
**Source files:** .md files in `date-expert-app/legislatie-ap/` (Legea 98/2016, HG 395/2016, etc.)

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

- ~2014 decisions imported so far (of ~3000 total in GCS), ~1000 remaining
- Check both vector search and keyword fallback paths
- Verify direct BO lookup works (e.g., "analizeaza BO2025_1011")
- Frontend changes: test in Vite dev server before deploying

## Current Progress & Next Steps (2026-03-06)

### What's been done
1. **Import script optimized** (`scripts/import_decisions_from_gcs.py`):
   - Pre-loads existing filenames from DB to skip already-imported files instantly (no GCS download)
   - Parallel GCS downloads using ThreadPoolExecutor (10 concurrent threads per batch)
   - ~2014 of ~3000 decisions imported so far

2. **Backend `/stats/overview` endpoint** implemented (`backend/app/api/v1/decisions.py`):
   - Returns real DB counts: total_decisions, by_ruling (ADMIS/RESPINS/etc.), by_type (rezultat/documentatie), last_updated
   - Used by frontend Dashboard + Data Lake for global stats

3. **Backend `DecisionSummary`** now includes `cpv_descriere` and `contestator` fields

4. **Frontend Dashboard** (`index.tsx`): uses `/stats/overview` API for all stat cards (not `apiDecisions.length`)

5. **Frontend Data Lake** (`index.tsx`) redesigned:
   - Global stats bar from `/stats/overview` (Total, Documentație, Rezultat, Ultima actualizare)
   - Server-side pagination (20/page with prev/next + page numbers)
   - Tile redesign: BO reference as title, CPV + description, contestator vs. autoritate, ruling badge

6. **Data Lake search** now server-side: backend `search` query param on `/api/v1/decisions/` with ILIKE across multiple columns, frontend debounced at 300ms

7. **Data pipeline scripts** (`scripts/`):
   - `generate_analysis.py` — Standalone LLM analysis with retry (3x exponential backoff), per-decision commit, progress reporting
   - `generate_embeddings.py` — Per-batch commit (crash-safe), retry on API errors, progress reporting
   - `pipeline.py` — Unified orchestrator: `import → analyze → embed` in one command

### Data Pipeline Commands

```bash
# Full pipeline (import new from GCS → analyze → embed)
DATABASE_URL="..." python scripts/pipeline.py

# Individual steps
DATABASE_URL="..." python scripts/pipeline.py --step analyze
DATABASE_URL="..." python scripts/pipeline.py --step embed
DATABASE_URL="..." python scripts/pipeline.py --step import

# Skip import (when GCS not available, just process what's in DB)
DATABASE_URL="..." python scripts/pipeline.py --skip-import

# Standalone scripts with more options
DATABASE_URL="..." python scripts/generate_analysis.py --dry-run   # Preview what would be analyzed
DATABASE_URL="..." python scripts/generate_analysis.py --limit 10  # Test with 10
DATABASE_URL="..." python scripts/generate_embeddings.py --force   # Regenerate all
```

### What still needs to be done
1. **Run LLM analysis** on all decisions: `python scripts/generate_analysis.py` (or `pipeline.py --step analyze`)
2. **Generate embeddings**: `python scripts/generate_embeddings.py` (or `pipeline.py --step embed`)
3. **CPV descriptions**: `cpv_descriere` column is currently NULL for most decisions — need to populate from `nomenclator_cpv` table or during import
4. **Deploy**: Push to `main` to trigger Cloud Build → Cloud Run

### Future: Daily Automation (Cloud Run Job + Cloud Scheduler)

Currently all pipeline steps are manual CLI commands. For continuous updates:

1. **Create a Cloud Run Job** using the existing Dockerfile with entrypoint override:
   ```bash
   gcloud run jobs create expertap-daily-pipeline \
     --image=gcr.io/gen-lang-client-0706147575/expertap-api:latest \
     --command="python" \
     --args="scripts/pipeline.py,--daily" \
     --set-secrets="DATABASE_URL=DATABASE_URL:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest" \
     --set-cloudsql-instances=gen-lang-client-0706147575:europe-west1:expertap-db \
     --memory=1Gi \
     --task-timeout=3600s \
     --region=europe-west1
   ```

2. **Schedule with Cloud Scheduler** (daily at 02:00 Bucharest time):
   ```bash
   gcloud scheduler jobs create http expertap-daily-trigger \
     --schedule="0 2 * * *" \
     --time-zone="Europe/Bucharest" \
     --uri="https://europe-west1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/gen-lang-client-0706147575/jobs/expertap-daily-pipeline:run" \
     --http-method=POST \
     --oauth-service-account-email=<SERVICE_ACCOUNT>@gen-lang-client-0706147575.iam.gserviceaccount.com
   ```

3. **What happens daily**: New GCS files get imported → analyzed by LLM → embeddings generated → RAG search updated. No user intervention needed.
