# ExpertAP - TODO

## URGENT - READY TO DEPLOY! 🚀

### ✅ SCRIPTURILE SUNT GATA - Rulează manual (Vezi QUICKSTART.md)

**Status:** Toate scripturile și documentația sunt create. Trebuie doar rulate manual!

**URL-uri:**
- Frontend: https://expertap-api-850584928584.europe-west1.run.app/ (se afișează, dar fără date)
- Health: https://expertap-api-850584928584.europe-west1.run.app/health ✅ (indică "healthy")
- API Docs: https://expertap-api-850584928584.europe-west1.run.app/docs

**Situație:** Aplicația rulează cu `SKIP_DB=true` - trebuie configurată baza de date.

**Soluție pregătită - Vezi QUICKSTART.md pentru instrucțiuni complete!**

### 📋 Pași pentru finalizare (MANUAL - 15-20 minute total):

1. [ ] **Rulează setup Cloud SQL** (5 min) - Vezi QUICKSTART.md sau docs/SETUP_DATABASE.md
   ```bash
   ./scripts/setup_cloud_sql.sh
   ```

2. [ ] **Conectează Cloud Run** (2 min) - Vezi docs/CLOUD_RUN_DATABASE_CONFIG.md
   ```bash
   gcloud run services update expertap-api \
       --add-cloudsql-instances=CONNECTION_NAME \
       --update-env-vars="DATABASE_URL=...,SKIP_DB=false"
   ```

3. [ ] **Importă datele** (10-15 min)
   ```bash
   python scripts/import_decisions_from_gcs.py --create-tables
   ```

4. [ ] **Testare completă**
   ```bash
   curl https://expertap-api-850584928584.europe-west1.run.app/health
   ```

### 📚 Documentație completă creată:
- ✅ **QUICKSTART.md** - Ghid rapid în 3 pași
- ✅ **docs/SETUP_DATABASE.md** - Setup detaliat Cloud SQL
- ✅ **docs/CLOUD_RUN_DATABASE_CONFIG.md** - Configurare conexiune
- ✅ **scripts/setup_cloud_sql.sh** - Script automat Cloud SQL
- ✅ **scripts/import_decisions_from_gcs.py** - Script import GCS
- ✅ **scripts/init_database.sql** - SQL inițializare
- ✅ **backend/alembic/** - Migrations configurate

### Date CNSC disponibile:
- **GCS Bucket:** `date-ap-raw/decizii-cnsc`
- **Conținut:** ~3000 decizii CNSC în format text
- **Format fișiere:** Conform convenției `BO{AN}_{NR_BO}_{COD_CRITICI}_CPV_{COD_CPV}_{SOLUTIE}.txt`

---

## Completed în sesiunea curentă (2025-12-25) 🎉

### ✅ Database Setup - Toate scripturile create!
- [x] **Script automat Cloud SQL**: `scripts/setup_cloud_sql.sh`
  - Creare PostgreSQL 15 cu pgvector
  - Configurare automată database și user
  - Generare password securizat
- [x] **Script import GCS**: `scripts/import_decisions_from_gcs.py`
  - Conectare la bucket GCS
  - Download și parsare decizii
  - Import batch în database
  - Suport pentru --limit, --create-tables
- [x] **Alembic configuration**
  - alembic.ini configurat
  - alembic/env.py cu async support
  - Migration inițială completă
- [x] **Migrații database**: `backend/alembic/versions/20251225_0001_initial_schema.py`
  - Toate tabelele (decizii_cnsc, argumentare_critica, etc.)
  - Indexuri optimizate
  - pgvector și pg_trgm extensions
- [x] **SQL inițializare**: `scripts/init_database.sql`
- [x] **Documentație completă**:
  - QUICKSTART.md - Ghid rapid 3 pași
  - docs/SETUP_DATABASE.md - Setup detaliat
  - docs/CLOUD_RUN_DATABASE_CONFIG.md - Configurare
- [x] **Requirements updated**: google-cloud-storage adăugat

## Completed în sesiunea anterioară (2024-12-25)

### ✅ CI/CD Pipeline
- [x] GitHub Actions CI cu:
  - Backend Tests (flake8, pytest)
  - Docker Build & Startup Test
  - Frontend Build Check
- [x] Cloud Build pentru deploy pe Cloud Run
- [x] Health check endpoint funcțional

### ✅ Deploy GCP
- [x] Conectare GitHub cu Cloud Build
- [x] Configurare Cloud Run
- [x] Dockerfile unificat (frontend + backend)
- [x] Deploy reușit la https://expertap-api-850584928584.europe-west1.run.app/

### ✅ CNSC Parser
- [x] Parser cu convenție de denumire: `BO{AN}_{NR_BO}_{COD_CRITICI}_CPV_{COD_CPV}_{SOLUTIE}.txt`
- [x] Coduri critici (D1-D7, R1-R7, DAL, RAL)
- [x] Extracție soluție din "CONSILIUL DECIDE:"
- [x] Schema bază de date

### ✅ Infrastructură
- [x] FastAPI backend cu structură modulară
- [x] Configurație opțională pentru baza de date (SKIP_DB)
- [x] LLM abstraction layer (Gemini provider)

---

## Backlog

### P0 - MVP Core (Must Have)

#### 🟢 Baza de Date și Date Reale - SCRIPTURILE SUNT GATA!
- [x] **DONE**: Scripturile pentru Cloud SQL create (vezi QUICKSTART.md)
- [x] **DONE**: Script import din GCS creat
- [x] **DONE**: Alembic migrations configurate
- [x] **DONE**: Documentație completă
- [ ] **MANUAL**: Rulare setup Cloud SQL (5 min) - Vezi QUICKSTART.md
- [ ] **MANUAL**: Conectare Cloud Run (2 min) - Vezi docs/CLOUD_RUN_DATABASE_CONFIG.md
- [ ] **MANUAL**: Import date (10-15 min) - Rulează `python scripts/import_decisions_from_gcs.py`
- [ ] Generare embeddings pentru semantic search
- [ ] Testare frontend cu date reale

#### Frontend Funcțional
- [ ] Debugging și fix pentru frontend
- [ ] Conectare frontend la API-uri backend cu date reale
- [ ] Testare end-to-end a tuturor funcțiilor

#### Search (Chatbot Foundation)
- [ ] Semantic search endpoint
- [ ] Hybrid search (semantic + keyword)
- [ ] Filter by metadata

#### Chatbot "Intreaba ExpertAP"
- [ ] RAG pipeline complet
- [ ] Citation verification
- [ ] Conversation history

### P1 - MVP Features
- [ ] Legal Drafter
- [ ] Red Flags Detector
- [ ] Authentication (Firebase)

---

## Fișiere Cheie

| Fișier | Scop |
|--------|------|
| `/Dockerfile` | Build unificat frontend + backend |
| `/backend/app/main.py` | Entry point FastAPI, servește static files |
| `/backend/app/services/parser.py` | Parser pentru decizii CNSC |
| `/backend/app/db/session.py` | Conexiune bază de date |
| `/index.tsx` | Frontend React principal |
| `/cloudbuild.yaml` | Configurare Cloud Build |
| `/.github/workflows/ci.yml` | GitHub Actions CI |

---

## GCP Project Info

- **Project Name**: ExpertAPP
- **Project ID**: gen-lang-client-0706147575
- **Project Number**: 850584928584
- **Region**: europe-west1
- **Service URL**: https://expertap-api-850584928584.europe-west1.run.app/

### GCS Bucket cu Date
- **Bucket:** `date-ap-raw`
- **Folder:** `decizii-cnsc`
- **Număr fișiere:** ~3000 decizii CNSC
- **Format:** Text (.txt)

---

_Last updated: 2025-12-25 - Database scripts completed! 🎉_
