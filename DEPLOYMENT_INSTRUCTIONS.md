# 🚀 Instrucțiuni Deployment - ExpertAP Fix Chat API

## 📋 Ce s-a rezolvat

### ✅ Fix-ul aplicat (commit `db4d0aa`)
**Problema:** Pydantic validation error în `/api/v1/chat/` endpoint
- Eroare: "Input should be a valid dictionary or instance of Citation"
- Cauză: Citation class duplicată în `chat.py` și `rag.py` (clase diferite în memorie)

**Soluție:** Convertire Citation objects → dicts înainte de ChatResponse
- Permite Pydantic să valideze corect
- Nu mai apare eroarea HTTP 500

### 🎉 Veste bună - GEMINI_API_KEY funcționează!
Din logs-urile de test:
```
2025-12-30 21:38:51 [info] rag_response_generated citations=3 confidence=1.0
```
✅ Gemini AI generează răspunsuri cu succes!
✅ RAG service funcționează perfect!
✅ Database connection OK (7 decizii CNSC)

**Concluzie:** GEMINI_API_KEY NU mai are problema `\n` menționată în TODO.md!

---

## 🚀 Deployment în Google Cloud

### Opțiunea 1: Deploy Manual (Recomandat pentru fix rapid)

**În Google Cloud Shell:**

```bash
# 1. Navighează în directorul proiectului
cd ~/APP-AI  # sau unde ai clonat repo-ul

# 2. Pull latest changes
git checkout claude/review-and-test-w0nEo
git pull origin claude/review-and-test-w0nEo

# 3. Deploy cu Cloud Build
gcloud builds submit --config cloudbuild.yaml \
  --region=europe-west1 \
  --project=gen-lang-client-0706147575

# 4. Monitorizează build-ul (va dura ~3-5 minute)
# Sau vezi logs în: https://console.cloud.google.com/cloud-build/builds

# 5. După succes, verifică deployment
gcloud run services describe expertap-api \
  --region=europe-west1 \
  --format="table(status.url,status.latestReadyRevisionName)"
```

**Așteptat:**
```
URL: https://expertap-api-850584928584.europe-west1.run.app
Revision: expertap-api-00xxx-xxx (nou!)
```

---

### Opțiunea 2: Deploy via GitHub PR (Dacă există Cloud Build Trigger)

**⚠️ DOAR dacă există trigger configurat în Cloud Build!**

```bash
# 1. Verifică dacă există trigger
gcloud builds triggers list --project=gen-lang-client-0706147575

# Dacă există trigger pentru branch-ul curent:

# 2. Creează Pull Request în GitHub
# URL: https://github.com/NoisimRo/APP-AI/pull/new/claude/review-and-test-w0nEo

# 3. Merge PR → Cloud Build se triggere automat

# 4. Monitorizează
# https://console.cloud.google.com/cloud-build/builds
```

**⚠️ NOTĂ:** Dacă nu există trigger, folosește Opțiunea 1.

---

## ✅ Re-Testare După Deployment

### Test 1: Health Check (fără `~`!)

```bash
curl https://expertap-api-850584928584.europe-west1.run.app/health
```

**Așteptat:**
```json
{"status": "healthy", "version": "0.1.0"}
```

---

### Test 2: Database Connection (ar trebui să meargă deja)

```bash
curl https://expertap-api-850584928584.europe-west1.run.app/api/v1/decisions/?limit=3
```

**Așteptat:**
```json
{"decisions": [...], "total": 7}
```

---

### Test 3: Chat Assistant (FIX-UL PRINCIPAL!)

**Test în browser:**
1. Deschide: https://expertap-api-850584928584.europe-west1.run.app/
2. Tab "Intreaba ExpertAP"
3. Întrebare: **"fa un rezumat al Decizia nr. 446 / 2025"**
4. Click "Trimite"

**Așteptat:**
- ✅ Răspuns generat cu succes
- ✅ 3 citations afișate
- ✅ Fără erori HTTP 500
- ✅ Confidence score vizibil

**Test API direct:**
```bash
curl -X POST https://expertap-api-850584928584.europe-west1.run.app/api/v1/chat/ \
  -H "Content-Type: application/json" \
  -d '{
    "message": "fa un rezumat al Decizia nr. 446 / 2025",
    "conversation_id": "test-session-456"
  }'
```

**Așteptat:**
```json
{
  "message": "Decizia nr. 446/2025...",
  "conversation_id": "test-session-456",
  "citations": [
    {
      "decision_id": "BO2025_1000_...",
      "text": "...",
      "verified": true
    }
  ],
  "confidence": 1.0,
  "suggested_questions": [...]
}
```

---

### Test 4: Red Flags Analyzer

**Browser:**
1. Tab "Red Flags"
2. Paste text:
```
Operatorul economic trebuie să aibă o cifră de afaceri de minimum 10 milioane EUR în ultimii 3 ani și să fi realizat minimum 5 contracte similare cu valoare de peste 2 milioane EUR fiecare.
```
3. Click "Analizează"

**API test:**
```bash
curl -X POST https://expertap-api-850584928584.europe-west1.run.app/api/v1/redflags/ \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Operatorul economic trebuie să aibă o cifră de afaceri de minimum 10 milioane EUR."
  }'
```

---

### Test 5: RAG Memo

**Browser:**
1. Tab "RAG Memo"
2. Topic: **"experiență similară în achiziții publice"**
3. Max decisions: **5**
4. Click "Generează Memo"

**API test:**
```bash
curl -X POST https://expertap-api-850584928584.europe-west1.run.app/api/v1/ragmemo/ \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "experiență similară în achiziții publice",
    "max_decisions": 5
  }'
```

---

### Test 6: Data Lake

**Browser:**
1. Tab "Data Lake"
2. Verifică afișare 7 decizii
3. Search: **"CNSC"**
4. Verifică filter funcționează

---

## 📊 Verificare Logs Post-Deployment

```bash
# Vezi ultimele 100 linii de logs
gcloud run services logs read expertap-api \
  --region=europe-west1 \
  --limit=100

# Sau filtrează pentru erori
gcloud run services logs read expertap-api \
  --region=europe-west1 \
  --limit=50 | grep -i error
```

**Caută după:**
- ✅ `rag_response_generated` → Chat funcționează
- ✅ `citations_count=3` → Citations create corect
- ✅ `200 OK` → Requests successful
- ❌ `ValidationError` → NU ar trebui să mai apară!

---

## 🎯 Checklist Success

După deployment și testare, toate acestea ar trebui ✅:

- [ ] Health check returnează JSON (nu HTML)
- [ ] Database connection returnează 7 decizii
- [ ] Chat Assistant generează răspunsuri fără erori
- [ ] Chat Assistant returnează citations valide (array de 3 obiecte)
- [ ] Red Flags detectează clauze restrictive
- [ ] RAG Memo generează memo-uri juridice
- [ ] Data Lake afișează toate deciziile

---

## 🔄 Rollback (Dacă ceva merge prost)

```bash
# Verifică revisions anterioare
gcloud run revisions list \
  --service=expertap-api \
  --region=europe-west1 \
  --limit=5

# Rollback la revision anterioară
gcloud run services update-traffic expertap-api \
  --region=europe-west1 \
  --to-revisions=expertap-api-00xxx-xxx=100
```

---

## 📝 Notițe

1. **GEMINI_API_KEY este valid!**
   - NU mai trebuie recreat (problema `\n` nu mai există)
   - Se pare că a fost deja fixat într-o sesiune anterioară

2. **Fix aplicat:**
   - `backend/app/api/v1/chat.py` - conversie Citation → dict
   - Commit: `db4d0aa`
   - Branch: `claude/review-and-test-w0nEo`

3. **Deployment:**
   - Docker build + push → GCR
   - Cloud Run update cu secrets și Cloud SQL
   - Memory: 512Mi, CPU: 1, Min instances: 0, Max: 3

---

_Created: 2025-12-30 | Fix Chat API Pydantic validation error_
