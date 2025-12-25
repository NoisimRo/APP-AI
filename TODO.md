# ExpertAP - TODO

## URGENT - Sesiunea Următoare

### ❌ PROBLEMA CURENTĂ: Frontend-ul nu funcționează
**Status Deploy:** Deploy-ul s-a făcut cu succes, dar frontend-ul NU funcționează corect.

**URL-uri:**
- Frontend: https://expertap-api-850584928584.europe-west1.run.app/ (se afișează, dar nu funcționează)
- Health: https://expertap-api-850584928584.europe-west1.run.app/health ✅ (indică "healthy")
- API Docs: https://expertap-api-850584928584.europe-west1.run.app/docs

**Problemă:** Frontend-ul se vede, dar NICIO funcție nu merge - toate dau eroare.

**Cauza probabilă:** BAZA DE DATE NU ESTE FUNCȚIONALĂ
- Aplicația rulează cu `SKIP_DB=true`
- Nu există o bază de date PostgreSQL configurată
- Frontend-ul încearcă să acceseze date care nu există

### Ce trebuie făcut în sesiunea următoare:
1. [ ] Configurare Cloud SQL (PostgreSQL cu pgvector)
2. [ ] Conectare aplicație la baza de date
3. [ ] Conectare la bucket-ul GCS cu decizii CNSC
4. [ ] Import decizii din bucket în baza de date
5. [ ] Testare frontend cu date reale

### Date CNSC disponibile:
- **GCS Bucket:** `date-ap-raw/decizii-cnsc`
- **Conținut:** ~3000 decizii CNSC în format text
- **Format fișiere:** Conform convenției `BO{AN}_{NR_BO}_{COD_CRITICI}_CPV_{COD_CPV}_{SOLUTIE}.txt`

---

## Completed în această sesiune (2024-12-25)

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

#### 🔴 Baza de Date și Date Reale
- [ ] **URGENT**: Configurare Cloud SQL (PostgreSQL + pgvector)
- [ ] Conectare la GCS bucket `date-ap-raw/decizii-cnsc`
- [ ] Import și parsare cele 3000 decizii CNSC
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

_Last updated: 2024-12-25_
