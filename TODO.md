# ExpertAP - TODO

## URGENT - Sesiunea Următoare

### ❌ PROBLEMA CURENTĂ: Frontend-ul nu funcționează
**Status Deploy:** Deploy-ul s-a făcut cu succes, dar frontend-ul NU funcționează corect.

**URL-uri:**
- Frontend: https://expertap-api-850584928584.europe-west1.run.app/
- Health: https://expertap-api-850584928584.europe-west1.run.app/health ✅
- API Docs: https://expertap-api-850584928584.europe-west1.run.app/docs

**Problemă:** Frontend-ul se vede, dar NICIO funcție nu merge - toate dau eroare.

**Referință:** La aplicația flashcards, utilizatorul are deploy funcțional unde:
- Frontend: https://flashcards-492967174276.europe-west1.run.app/
- Backend: https://flashcards-492967174276.europe-west1.run.app/api/health
- Ambele funcționează complet!

### Ce trebuie investigat:
1. [ ] Analizează frontend-ul din `index.tsx` - ce API-uri apelează?
2. [ ] Verifică dacă API-urile din backend răspund corect
3. [ ] Verifică dacă frontend-ul apelează URL-uri corecte (relative vs absolute)
4. [ ] Compară cu aplicația flashcards pentru a înțelege structura corectă
5. [ ] Verifică logurile din Cloud Run pentru erori

### Ce NU am înțeles bine:
- Utilizatorul vrea o aplicație COMPLET FUNCȚIONALĂ, nu doar frontend static servit
- Frontend-ul trebuie să comunice corect cu backend-ul
- Modelul de referință este aplicația flashcards

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

#### 🔴 Frontend Funcțional
- [ ] **URGENT**: Debugging și fix pentru frontend
- [ ] Conectare frontend la API-uri backend
- [ ] Testare end-to-end a tuturor funcțiilor

#### Data Pipeline
- [ ] Database schema migration (Alembic)
- [ ] Procesare decizii CNSC reale
- [ ] Generare embeddings
- [ ] Indexare în pgvector

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

---

_Last updated: 2024-12-25_
