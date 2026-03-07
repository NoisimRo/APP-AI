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

### ⚠️ REGULI OBLIGATORII — Schema Producție (`docs/expertap_db.md`)

**`docs/expertap_db.md`** este **singura sursă de adevăr** pentru schema bazei de date din producție. Conține output-uri reale din producție (`\d`, `\dt+`, `\di+`, etc.). Regulile de mai jos sunt **obligatorii** și nu pot fi ignorate:

1. **Înainte de a propune orice modificare SQL** (ALTER TABLE, CREATE TABLE, DROP, CREATE INDEX, etc.), Claude TREBUIE să citească `docs/expertap_db.md` pentru a înțelege starea actuală a producției.

2. **După ce utilizatorul confirmă că a executat o comandă SQL în producție**, Claude TREBUIE **imediat** să actualizeze `docs/expertap_db.md`:
   - Actualizează/adaugă output-ul `\d <table>` pentru tabelul afectat
   - Adaugă o intrare în secțiunea "Changelog Schema Producție" cu data, comanda SQL, și cine a executat-o
   - Actualizează "Ultima sincronizare cu producția" din header

3. **Niciodată** nu se propun modificări SQL bazate doar pe modelele SQLAlchemy — producția poate diferi de cod (coloane adăugate manual, indexuri lipsă, dimensiuni diferite, etc.).

4. **Când se creează o migrare Alembic nouă**, aceasta trebuie să fie consistentă cu `docs/expertap_db.md`, nu invers.

5. **Dacă Claude detectează o discrepanță** între `docs/expertap_db.md`, modele SQLAlchemy, și/sau migrări Alembic, trebuie să semnaleze imediat utilizatorului și să ceară output din producție (`\d <table>`) pentru clarificare.

### Embedding Dimensions

- **Model:** `gemini-embedding-001` (native output: 3072 dimensions, capped to 2000)
- **DB columns:** `Vector(2000)` on `argumentare_critica`, `legislatie_fragmente`
- **Why 2000?** pgvector HNSW indexes have a 2000 dimension limit. We use `output_dimensionality=2000` in the Gemini API call. This is 2.6x better than the original 768 while keeping HNSW index support.
- **History:** Started at 768 (text-embedding-004 convention) → tried 3072 (native) but hit pgvector HNSW limit → settled on 2000.
- After dimension changes, regenerate embeddings: `python scripts/generate_embeddings.py --force`

### Key Tables (5 în producție)

| Table | Purpose | RAG? |
|-------|---------|------|
| `decizii_cnsc` | Main decision table | No |
| `argumentare_critica` | Per-criticism argumentation (PRIMARY RAG unit) | Yes (2000-dim) |
| `nomenclator_cpv` | CPV codes nomenclator | No |
| `acte_normative` | Master table acte legislative (Legea 98/2016, HG 395/2016, etc.) | No |
| `legislatie_fragmente` | Fragmente legislație la granularitate maximă (articol/alineat/literă) | Yes (2000-dim) |

### Tabele eliminate (2026-03-07)

Următoarele tabele au fost eliminate deoarece erau goale și nefolosite:
- `sectiuni_decizie` — funcționalitate acoperită de `argumentare_critica`
- `citate_verbatim` — funcționalitate acoperită de `argumentare_critica`
- `referinte_articole` — va fi reimplementat ulterior (vezi Future Plan)

### Future Plan: Referințe Articole Legislative

Pași necesari pentru a reimplementa funcționalitatea `referinte_articole`:

1. **Extindere `generate_analysis.py`** — LLM-ul care analizează deciziile trebuie să extragă și referințele legislative (art. X din Legea Y) din fiecare ArgumentareCritica, cu informații despre cine a invocat (contestator/AC/CNSC) și dacă argumentul a fost câștigător
2. **Creare tabel nou** cu FK la `legislatie_fragmente` (nu string) + FK la `argumentare_critica`, similar cu:
   ```sql
   CREATE TABLE referinte_legislative (
     id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
     argumentare_id UUID REFERENCES argumentare_critica(id) ON DELETE CASCADE,
     fragment_id UUID REFERENCES legislatie_fragmente(id) ON DELETE SET NULL,
     invocat_de VARCHAR(20),  -- 'contestator', 'ac', 'cnsc'
     argument_castigator BOOLEAN,
     text_context TEXT,
     created_at TIMESTAMP NOT NULL DEFAULT now()
   );
   ```
3. **Populare** — rularea analizei LLM pe toate deciziile existente pentru a extrage referințele
4. **Integrare RAG** — folosirea referințelor pentru a îmbunătăți căutarea (ex: "ce decizii citează art. 57 din Legea 98/2016?")

### legislatie_fragmente (populated by import_legislatie.py)

Stochează legislația la **granularitate maximă** — un rând per cea mai mică unitate juridică:
- Articol (dacă nu are alineate)
- Alineat (dacă nu are litere)
- Literă (cea mai fină granularitate)

Permite citări exacte: `art. 2 alin. (2) lit. a) din Legea nr. 98/2016`

- `act_id` - FK → `acte_normative.id` (UUID)
- `numar_articol` - number for sorting (INTEGER)
- `articol` - label: "art. 2", "art. 178" (VARCHAR 30)
- `alineat` - 1, 2, 3... or NULL (INTEGER)
- `alineat_text` - "alin. (1)", "alin. (2)" (VARCHAR 20)
- `litera` - "a", "b", "c"... or NULL (VARCHAR 5) — **un rând per literă**
- `text_fragment` - text of this specific fragment (TEXT)
- `articol_complet` - full article text for RAG context (TEXT)
- `citare` - canonical: "art. 2 alin. (2) lit. a)" (VARCHAR 150)
- `capitol`, `sectiune` - context (VARCHAR 500)
- `keywords` - TSVECTOR for full-text search legal
- `embedding` - Vector(2000) with HNSW index
- UNIQUE constraint: `(act_id, numar_articol, COALESCE(alineat, 0), COALESCE(litera, ''))`

**Import:** `python scripts/import_legislatie.py --dir legislatie-ap`
**Update (smart upsert):** `python scripts/import_legislatie.py --dir legislatie-ap --update`
**Source files:** .md/.txt files in GCS bucket `date-expert-app/legislatie-ap/` (Legea 98/2016, HG 395/2016, Legea 101/2016, etc.)
**Supported formats:** Markdown (old, with `## ### ####` headers) and plaintext (new, legislație consolidată from legislatie.just.ro)
**Superscript handling:** Articles like `61^1` → `numar_articol=6101`, alineats like `(2^1)` → `alineat=201` (encoding: main*100+sub)
**Update mode (`--update`):** Compares existing DB records with parsed file — inserts new, updates changed text + re-embeds, removes obsolete fragments. Ideal for frequent legislative updates.

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
