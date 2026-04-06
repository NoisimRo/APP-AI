# CLAUDE.md - Development Guide for Claude Code

## Project Overview

ExpertAP is a **DaaS (Data as a Service)** platform targeting **Romanian law firms specializing in public procurement litigation** and **public procurement specialists**. Backend is FastAPI (Python), frontend is a single React file (`index.tsx`), deployed on GCP Cloud Run with PostgreSQL + pgvector.

### Business Model — DaaS with Tiered Access

| Role | Features | LLM Queries/Day |
|------|----------|-----------------|
| **Free (registered)** | Chat + Dashboard + Data Lake + RAG | 5 |
| **paid_basic** | + Drafter + Red Flags + Clarificări | 20 |
| **paid_pro** | + Training + Export | 100 |
| **paid_enterprise** | Everything + API access | Unlimited |
| **admin** | Everything + admin panel | Unlimited |

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
- **LLM:** Multi-provider (Gemini + Claude + OpenAI + Groq + OpenRouter) via factory pattern (`backend/app/services/llm/factory.py`). Groq = modele open-source gratuite (Llama, GPT-OSS, Qwen, Llama 4 Scout). OpenRouter = 400+ modele, multe gratuite (suffix `:free`). Fiecare provider cu token-aware context truncation (estimare ~4 chars/token, truncare proporțională automată). Embeddings always on Gemini.
- **RAG Pipeline:** `backend/app/services/rag.py` - vector search on ArgumentareCritica + LegislatieFragmente + SpeteANAP → LLM generation
- **Auth:** JWT-based (python-jose + bcrypt), `backend/app/core/auth.py` + `deps.py`. Role-based feature gating + in-memory rate limiting per role.
- **Database Models:** `backend/app/models/decision.py` - DecizieCNSC, ArgumentareCritica, User, Conversatie, MesajConversatie, DocumentGenerat, RedFlagsSalvate, TrainingMaterial, etc.

### ⚠️ asyncpg Gotchas (PostgreSQL async driver)

**Regulile de mai jos sunt OBLIGATORII** pentru a evita erori 500 greu de depanat:

1. **NICIODATĂ `datetime.now(timezone.utc)`** pentru coloane `timestamp without time zone`. asyncpg aruncă `InterfaceError` dacă primește un datetime timezone-aware într-o coloană timezone-naive. Folosește `datetime.utcnow()` sau `func.now()` (server-side).
   ```python
   # ❌ GREȘIT — crash cu asyncpg
   user.last_login = datetime.now(timezone.utc)

   # ✅ CORECT — naive UTC, compatibil cu coloana
   user.last_login = datetime.utcnow()

   # ✅ CORECT — server-side default în model
   created_at = Column(DateTime, server_default=func.now())
   ```

2. **UUID-urile din asyncpg** sunt obiecte `uuid.UUID`, nu string-uri. Când le pui în dict-uri sau JWT payloads, folosește `str(user.id)`. Pydantic v2 le serializează automat, dar `json.dumps()` și `jose.jwt.encode()` nu.
   ```python
   # ❌ GREȘIT — jwt.encode nu știe să serializeze UUID
   token_data = {"sub": user.id}

   # ✅ CORECT
   token_data = {"sub": str(user.id)}
   ```

3. **Preferă `server_default=func.now()`** în modelele SQLAlchemy pentru `created_at`/`updated_at` în loc de `default=datetime.utcnow` — lasă PostgreSQL să genereze timestamp-ul.

## Key Files

| File | Purpose |
|------|---------|
| `index.tsx` | Entire frontend (single-file React app) |
| `backend/app/services/rag.py` | RAG search + response generation |
| `backend/app/services/llm/gemini.py` | Gemini LLM provider |
| `backend/app/services/llm/anthropic.py` | Anthropic Claude LLM provider |
| `backend/app/services/llm/openai.py` | OpenAI LLM provider (GPT-4.1, o3, etc.) |
| `backend/app/services/llm/groq.py` | Groq provider — modele open-source gratuite (Llama, DeepSeek, Qwen) |
| `backend/app/services/llm/openrouter.py` | OpenRouter provider — 400+ modele, multe gratuite (suffix `:free`) |
| `backend/app/services/llm/factory.py` | LLM provider factory + `get_active_llm_provider()` |
| `backend/app/core/encryption.py` | Fernet encryption for API keys |
| `backend/app/api/v1/settings.py` | LLM Settings API (GET/PUT/test) |
| `backend/app/services/embedding.py` | Embedding generation service |
| `backend/app/services/analysis.py` | LLM decision analysis (ArgumentareCritica extraction + rezumat + CPV sugerat) |
| `backend/app/services/parser.py` | CNSC decision parser (regex-based, 0 LLM calls) — extracts obiect_contract |
| `backend/app/models/decision.py` | All database models |
| `backend/app/api/v1/chat.py` | Chat API endpoint |
| `backend/app/api/v1/decisions.py` | Decisions CRUD API |
| `backend/app/api/v1/ragmemo.py` | RAG memo generation API |
| `backend/app/services/redflags_analyzer.py` | Red Flags Detector (two-pass: detect → ground) |
| `backend/app/api/v1/redflags.py` | Red Flags API endpoint |
| `scripts/import_decisions_from_gcs.py` | GCS → database import pipeline |
| `scripts/generate_summaries.py` | Retroactive lightweight summary generation (2-3 sentences per decision) |
| `scripts/extract_obiect_contract.py` | Retroactive obiect_contract extraction (regex, no LLM) |
| `scripts/deduce_cpv.py` | CPV deduction via embedding similarity with nomenclator_cpv |
| `scripts/import_legislatie.py` | Legislation .md → DB import (alineat-level) |
| `scripts/import_spete_anap.py` | ANAP spete JSON → DB import (427 active spete, with embeddings) |
| `backend/app/services/training_generator.py` | TrainingAP: generare materiale didactice (RAG + LLM) |
| `backend/app/services/export_service.py` | Export materiale DOCX/PDF/MD |
| `backend/app/api/v1/training.py` | TrainingAP API endpoints (generate, stream, export) |
| `backend/app/api/v1/saved.py` | Saved content CRUD API (conversations, documents, red flags, training materials) |
| `backend/app/core/auth.py` | JWT token creation + bcrypt password hashing |
| `backend/app/core/deps.py` | Auth dependencies (get_current_user, require_role, require_feature) |
| `backend/app/core/rate_limiter.py` | In-memory daily rate limiter per role |
| `backend/app/api/v1/auth.py` | Auth API (register, login, refresh, me, change-password) |
| `backend/app/api/v1/users.py` | Admin user management CRUD |
| `scripts/create_admin.py` | Bootstrap admin user script |
| `backend/app/services/strategy_generator.py` | Strategy Generator: statistici + RAG + LLM → strategie contestare |
| `backend/app/api/v1/strategy.py` | Strategy API endpoint (POST /generate) |
| `backend/app/services/compliance_checker.py` | Compliance Checker: verificare doc vs legislație (two-pass) |
| `backend/app/api/v1/compliance.py` | Compliance API endpoint (POST /check, accepts file/text) |
| `backend/app/services/multi_document_analyzer.py` | Multi-Document Analyzer: red flags + cross-doc consistency |
| `backend/app/api/v1/multi_document.py` | Multi-Document API endpoint (POST /analyze, 2-5 files) |
| `backend/app/services/entity_extractor.py` | NER: extragere CPV, procedură, valoare din documente |
| `backend/app/api/v1/analytics.py` | Analytics API (panel profiles, predictor, compare) |
| `backend/app/api/v1/dosare.py` | Dosare Digitale CRUD API (case management) |
| `backend/app/api/v1/alerts.py` | Alert Rules CRUD API (decision alerts) |
| `backend/app/api/v1/comments.py` | Document Comments API (inline comments, resolve/unresolve) |

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

### Key Tables (19 în producție)

| Table | Purpose | RAG? |
|-------|---------|------|
| `decizii_cnsc` | Main decision table | No |
| `argumentare_critica` | Per-criticism argumentation (PRIMARY RAG unit) | Yes (2000-dim) |
| `nomenclator_cpv` | CPV codes nomenclator | No |
| `acte_normative` | Master table acte legislative (Legea 98/2016, HG 395/2016, etc.) | No |
| `legislatie_fragmente` | Fragmente legislație la granularitate maximă (articol/alineat/literă) | Yes (2000-dim) |
| `spete_anap` | Spețe ANAP — cazuistică oficială (întrebare + răspuns ANAP) | Yes (2000-dim) |
| `llm_settings` | LLM provider config (single-row: active provider, model, encrypted API keys for Gemini/Anthropic/OpenAI/Groq/OpenRouter) | No |
| `search_scopes` | Saved filter presets for RAG pre-filtering (name, JSONB filters, cached decision_count) | No (pre-filter) |
| `users` | Conturi utilizatori cu JWT auth (password_hash, roluri: admin, registered, paid_basic/pro/enterprise) | No |
| `user_context` | Memorie persistentă AI per utilizator (fapte, preferințe, expertiză — extrase din conversații) | No |
| `conversatii` | Conversații chat salvate (titlu, nr mesaje, scope_id, dosar_id FK) | No |
| `mesaje_conversatie` | Mesajele individuale din conversații (rol, conținut, citations, ordine) | No |
| `documente_generate` | Documente generate salvate: contestații, clarificări, RAG memo (tip_document, conținut, dosar_id FK) | No |
| `red_flags_salvate` | Analize Red Flags salvate (rezultate JSONB, statistici severitate, dosar_id FK) | No |
| `training_materials` | Materiale didactice salvate (tip, temă, nivel, secțiuni parsate, dosar_id FK) | No |
| `dosare` | Dosare digitale — case management (client, AC, CPV, termene, status, artefacte linkuite) | No |
| `dosar_documents` | Documente sursă atașate la dosare (text extras stocat, metadata, stats) | No |
| `alert_rules` | Reguli alerte decizii noi (filtre JSONB, frecvență, status activ/inactiv) | No |
| `document_comments` | Comentarii inline pe documente generate (anchor text, resolved tracking) | No |

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

### spete_anap (populated by import_spete_anap.py)

Cazuistică oficială ANAP (Autoritatea Națională pentru Achiziții Publice). 427 spețe active — fiecare conține o întrebare practică + răspunsul oficial ANAP, cu referințe la legislația aplicabilă. Spețele sunt a 3-a sursă RAG (alături de legislație și decizii CNSC).

- `numar_speta` - UNIQUE, identificator numeric al speței
- `versiune` - versiunea speței (default 1)
- `data_publicarii` - data publicării pe achizitiipublice.gov.ro
- `categorie` - categoria tematică (ex: "7.1 Garanția de participare", "12.2 Verificarea și evaluarea ofertelor")
- `intrebare` - întrebarea practică (plain text)
- `raspuns` - răspunsul oficial ANAP (plain text)
- `taguri` - array de taguri (ex: ["CISA", "DUAE"])
- `embedding` - Vector(2000) cu HNSW index pentru RAG

**Sursa:** JSON exportat din https://achizitiipublice.gov.ro, stocat pe GCS la `gs://date-expert-app/spete-anap/`
**Filtrare:** Se importă doar spețele active (non-arhivate) excluzând categoria "24. Spete care nu mai sunt valabile"
**Import:** `python scripts/import_spete_anap.py` (idempotent, skip-uri dacă `numar_speta` există)

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
DATABASE_URL="..." python scripts/generate_analysis.py --provider gemini --model gemini-2.5-pro  # Switch model
DATABASE_URL="..." python scripts/generate_analysis.py --provider groq --model llama-3.3-70b-versatile  # Free model
DATABASE_URL="..." python scripts/generate_embeddings.py --force   # Regenerate all

# Retroactive summaries (lightweight, ~1600 tokens/decision vs ~500K for re-analysis)
DATABASE_URL="..." python scripts/generate_summaries.py --dry-run   # Preview
DATABASE_URL="..." python scripts/generate_summaries.py --limit 10  # Test
DATABASE_URL="..." python scripts/generate_summaries.py              # All without rezumat

# Extract obiect_contract retroactively (regex only, no LLM, instant)
DATABASE_URL="..." python scripts/extract_obiect_contract.py --dry-run    # Preview
DATABASE_URL="..." python scripts/extract_obiect_contract.py --limit 10   # Test
DATABASE_URL="..." python scripts/extract_obiect_contract.py              # All missing
DATABASE_URL="..." python scripts/extract_obiect_contract.py --force      # Re-extract all

# CPV deduction via embedding similarity (for decisions without CPV)
DATABASE_URL="..." python scripts/deduce_cpv.py --dry-run            # Preview matches
DATABASE_URL="..." python scripts/deduce_cpv.py --limit 10 --top-k 5 # Test with top 5 candidates
DATABASE_URL="..." python scripts/deduce_cpv.py --threshold 0.75     # Stricter matching

# Import ANAP spete (cazuistică oficială, 427 active spete)
DATABASE_URL="..." python scripts/import_spete_anap.py --dry-run                        # Preview
DATABASE_URL="..." python scripts/import_spete_anap.py --local docs/biblioteca_spete_anap.json  # From local file
DATABASE_URL="..." python scripts/import_spete_anap.py                                  # From GCS (default)
DATABASE_URL="..." python scripts/import_spete_anap.py --force                          # Delete all + reimport
```

### Sprint 4 — Workflow & Case Management (2026-04-04)

8. **Dosar Digital (2.2)** — full case management system
   - New `dosare` table (user_id, titlu, client, AC, CPV, termene, status)
   - Added nullable `dosar_id` FK to `conversatii`, `documente_generate`, `red_flags_salvate`, `training_materials`
   - CRUD API: `/api/v1/dosare/` with link/unlink artifact endpoints
   - Frontend: pagină completă cu lista dosarelor, formular CRUD, detaliu cu artefacte linkuite
   - Status tracking: activ → in_lucru → depus → finalizat → arhivat
   - Urgent indicator for deadlines < 3 days

9. **Alerte Decizii Noi (2.3)** — email alerts for new matching decisions
   - New `alert_rules` table (user_id, nume, filters JSONB, activ, frecventa, ultima_notificare)
   - CRUD API: `/api/v1/alerts/` with toggle endpoint
   - Filters: cod_cpv, coduri_critici, complet, tip_procedura, solutie, keywords
   - Frontend: pagină cu lista regulilor, formular CRUD, toggle activ/inactiv
   - Max 20 rules per user

10. **Comentarii pe Documente (3.3)** — inline collaborative review
    - New `document_comments` table (document_id, user_id, anchor positions, text, resolved)
    - CRUD API: `/api/v1/documents/{id}/comments/` with resolve/unresolve
    - Comment stats endpoint per document
    - Backend ready for frontend integration in document viewer

### Sprint 4 SQL Migration (trebuie executat în producție)

```sql
-- 1. Dosare Digitale
CREATE TABLE dosare (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  titlu VARCHAR(300) NOT NULL,
  descriere TEXT,
  numar_dosar VARCHAR(100),
  client VARCHAR(300),
  autoritate_contractanta VARCHAR(300),
  numar_procedura VARCHAR(100),
  cod_cpv VARCHAR(20),
  valoare_estimata NUMERIC(15,2),
  tip_procedura VARCHAR(80),
  status VARCHAR(30) NOT NULL DEFAULT 'activ',
  termen_depunere TIMESTAMP,
  termen_solutionare TIMESTAMP,
  note TEXT,
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  updated_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX ix_dosare_user ON dosare(user_id);
CREATE INDEX ix_dosare_status ON dosare(status);
CREATE INDEX ix_dosare_created ON dosare(created_at DESC);

-- 2. FK dosar_id pe tabelele existente
ALTER TABLE conversatii ADD COLUMN dosar_id UUID REFERENCES dosare(id) ON DELETE SET NULL;
ALTER TABLE documente_generate ADD COLUMN dosar_id UUID REFERENCES dosare(id) ON DELETE SET NULL;
ALTER TABLE red_flags_salvate ADD COLUMN dosar_id UUID REFERENCES dosare(id) ON DELETE SET NULL;
ALTER TABLE training_materials ADD COLUMN dosar_id UUID REFERENCES dosare(id) ON DELETE SET NULL;

-- 3. Alert Rules
CREATE TABLE alert_rules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  nume VARCHAR(200) NOT NULL,
  descriere TEXT,
  filters JSONB NOT NULL DEFAULT '{}',
  activ BOOLEAN NOT NULL DEFAULT true,
  frecventa VARCHAR(20) NOT NULL DEFAULT 'zilnic',
  ultima_notificare TIMESTAMP,
  total_notificari INTEGER NOT NULL DEFAULT 0,
  metadata JSONB DEFAULT '{}',
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  updated_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX ix_alert_user ON alert_rules(user_id);
CREATE INDEX ix_alert_activ ON alert_rules(activ);

-- 4. Document Comments
CREATE TABLE document_comments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID NOT NULL REFERENCES documente_generate(id) ON DELETE CASCADE,
  user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  anchor_start INTEGER,
  anchor_end INTEGER,
  anchor_text TEXT,
  text TEXT NOT NULL,
  resolved BOOLEAN NOT NULL DEFAULT false,
  resolved_by UUID,
  resolved_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  updated_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX ix_dc_document ON document_comments(document_id);
CREATE INDEX ix_dc_user ON document_comments(user_id);
CREATE INDEX ix_dc_resolved ON document_comments(resolved);

-- 5. Dosar Documents (attached source documents with extracted text)
CREATE TABLE dosar_documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dosar_id UUID NOT NULL REFERENCES dosare(id) ON DELETE CASCADE,
  filename VARCHAR(500) NOT NULL,
  mime_type VARCHAR(100),
  file_size INTEGER,
  extracted_text TEXT NOT NULL,
  text_preview VARCHAR(500),
  text_stats JSONB,
  ordine INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  updated_at TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX ix_dosardoc_dosar ON dosar_documents(dosar_id);
```

### What still needs to be done
1. **Run LLM analysis** on all decisions: `python scripts/generate_analysis.py` (or `pipeline.py --step analyze`)
2. **Generate embeddings**: `python scripts/generate_embeddings.py` (or `pipeline.py --step embed`)
3. **Generate retroactive summaries**: `python scripts/generate_summaries.py` (lightweight, ~1600 tokens/decision)
4. **Extract obiect_contract**: `python scripts/extract_obiect_contract.py` (regex, instant, prerequisite for CPV deduction)
5. **Deduce CPV codes**: `python scripts/deduce_cpv.py` (for decisions without CPV, uses embedding similarity)
6. **Deploy**: Push to `main` to trigger Cloud Build → Cloud Run

### What's been done (complete)
- ✅ Sprint 4 SQL migration executed in production (2026-04-05): dosare, alert_rules, document_comments + dosar_id FK on 4 tables
- ✅ dosar_documents table created in production (2026-04-06): source documents attached to dosare, with extracted text storage, active dosar system + banner on tool pages

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

## TrainingAP Module (adăugat 2026-03-08)

Modul pentru generarea de materiale didactice destinate formării specialiștilor în achiziții publice. Materialele sunt fundamentate pe legislație reală (`legislatie_fragmente`) și jurisprudență CNSC reală (`argumentare_critica`) prin RAG.

### Fișiere cheie TrainingAP

| Fișier | Scop |
|--------|------|
| `backend/app/services/training_generator.py` | Serviciu principal: RAG context search + system prompts per tip material + generare LLM |
| `backend/app/services/export_service.py` | Export materiale în DOCX (python-docx), PDF (fpdf2), MD |
| `backend/app/api/v1/training.py` | API: `GET /types`, `POST /generate`, `POST /generate/stream`, `POST /export` |

### Tipuri de materiale (10)

| Cod | Tip | Descriere |
|-----|-----|-----------|
| `speta` | Speță practică | Scenariu realist cu analiză juridică |
| `studiu_caz` | Studiu de caz | Analiză aprofundată cu multiple perspective |
| `situational` | Întrebări situaționale | Scenarii decizionale "Ce ați face dacă..." |
| `palarii` | Pălăriile Gânditoare | 6 perspective (de Bono) |
| `dezbatere` | Dezbatere Pro & Contra | Argumente pro/contra cu temei legal |
| `quiz` | Quiz cu variante | Întrebări MCQ (A/B/C/D) cu explicații |
| `joc_rol` | Joc de rol | Scenarii cu roluri și instrucțiuni per participant |
| `erori` | Identificare erori | Document cu greșeli deliberate de identificat |
| `comparativ` | Analiză comparativă | Compararea a două abordări pe aceeași temă |
| `cronologie` | Cronologie procedurală | Ordonarea pașilor unei proceduri |

### Parametri: 4 niveluri dificultate (`usor`/`mediu`/`dificil`/`foarte_dificil`), 4 lungimi (`scurt`/`mediu`/`lung`/`extins`)

### Structura output-ului: Enunț → Cerințe → Rezolvare (cu referințe legale + jurisprudență) → Note Trainer

### Frontend: Secțiune "Formare" în sidebar, pagina cu layout 2 panouri (formular + output cu 3 tab-uri), butoane export DOCX/PDF/MD

### Starea curentă (Faza 1 — MVP)
- ✅ Generare individuală cu streaming SSE
- ✅ 10 tipuri de materiale cu prompt-uri specializate
- ✅ Integrare RAG (legislație + jurisprudență din DB)
- ✅ Export DOCX / PDF / MD
- ✅ 4 niveluri dificultate + 4 opțiuni lungime
- ✅ UI complet cu formular, tabs, export, citations

### Faza 2 — Generare în lot (batch) [✅ IMPLEMENTAT]
- ✅ Posibilitate de a genera un pachet de materiale: ex. "5 spețe + 10 quiz-uri pe tema X"
- ✅ UI: selector cantitate per tip material (1-50), generare secvențială cu progress bar
- ✅ 3 moduri: Individual, Batch (Lot), Program Formare
- ⚠️ Fără endpoint dedicat `/batch` — batch-ul se face client-side prin apeluri secvențiale la `/generate/stream`
- ⚠️ Export pachet complet într-un singur DOCX — neimplementat (se exportă materialele individual)

### Faza 3 — Salvare în baza de date și reutilizare [✅ IMPLEMENTAT]
- ✅ **Tabel `training_materials`** în PostgreSQL (creat în producție, 18 coloane incl. `dosar_id` FK)
- ✅ **Model SQLAlchemy** în `backend/app/models/decision.py` (`TrainingMaterial`)
- ✅ **Istoric materiale**: buton Istoric în UI, listare materialelor salvate, filtrare pe tip
- ✅ **Salvare/Încărcare**: POST/GET/DELETE prin `/api/v1/saved/training`
- ✅ **Organizare pe dosare**: `dosar_id` FK permite linkuirea materialelor la dosare digitale
- ⚠️ **Căutare full-text** — neimplementat (doar filtrare pe `tip_material`)
- ⚠️ **Re-generare cu parametri modificați** — neimplementat (se poate deschide materialul salvat, dar nu re-genera)
- ⚠️ **Tab "Materialele mele" dedicat** — neimplementat (materialele sunt accesibile prin butonul Istoric global)

### Faza 4 — Îmbunătățiri UX [PARȚIAL IMPLEMENTAT]
- ⚠️ **Undo/Regenerare**: neimplementat (nu există buton de regenerare cu aceiași parametri)
- ✅ **Editare manuală**: posibilitatea de a edita materialul generat înainte de export/salvare
- ⚠️ **Teme predefinite**: neimplementat (nu există dropdown cu teme populare)
- ⚠️ **Template-uri personalizate**: neimplementat
- ⚠️ **Preview print**: neimplementat (doar export DOCX/PDF/MD)

## Feature Roadmap (2026-04-04)

Planificare detaliată a funcționalităților noi, documentată în `docs/FEATURE_ROADMAP_2026_04.md`.

### Data Reality Check — Anonimizare

Toate deciziile CNSC au datele părților anonimizate (`autoritate_contractanta` și `contestator` sunt întotdeauna NULL în DB — numele sunt înlocuite cu "........."). Orice funcționalitate care depinde de identificarea părților este **imposibilă** cu datele actuale. Câmpurile disponibile: `complet` (C1-C20), `coduri_critici`, `cod_cpv`, `solutie_contestatie`, `tip_procedura`, `criteriu_atribuire`, `data_decizie`, `valoare_estimata`.

### Funcționalități Aprobate (19) — 6 Sprinturi

| Sprint | Funcționalități | Temă | Status |
|--------|----------------|------|--------|
| **1 — Foundation** | Redis Rate Limiting (6.2), Redis Cache (6.1), Deep Health Check (6.5), DB Pool Tuning (6.6) | Infrastructură | ✅ DONE |
| **2 — Intelligence** | Profil Complet CNSC (1.2), Predictor Rezultat (1.1), Analiză Comparativă (1.4) | Analytics | ✅ DONE |
| **3 — AI** | Strategie Contestare (4.2), Verificator Conformitate (4.5), Multi-Document (4.1), NER Entități (4.3), Memorie Persistentă (4.4) | AI Avansat | ✅ DONE |
| **4 — Workflow** | Dosar Digital (2.2), Alerte Decizii (2.3), Comentarii Documente (3.3) | Flux de lucru | ✅ DONE |
| **5 — Data Moat** | Import Curtea de Apel (5.1), Hartă Jurisprudențială (1.5) | Date | |
| **6 — UX** | Frontend Modular (8.1), Dark Mode (8.4) | Experiență | |

### Funcționalități Amânate (15)

Trend Analytics, Audit Trail, Șabloane Personalizate, Notițe pe Decizii, Organizații & Echipe, Partajare Documente, Rezumat Executiv, Monitorizare SEAP, API Enterprise, Legislație Auto-update, Observabilitate, Background Task Queue, Registru Clienți, Rapoarte Client, PWA + Command Palette.

### Funcționalități Respinse (2)

Calculator Termene Legale, Export Structurat CSV/JSON.

### Funcționalitate Invalidată (1)

**Profil Autoritate Contractantă** — imposibil din cauza anonimizării datelor.
