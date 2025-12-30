# Plan de Testare - Google Cloud Shell

## 📋 Context
Aplicația ExpertAP este deployed în Google Cloud Run la:
**URL:** https://expertap-api-850584928584.europe-west1.run.app/

**⚠️ PROBLEMA CUNOSCUTĂ:** GEMINI_API_KEY conține `\n` → testele AI vor eșua cu "Illegal header value"

---

## 🚀 Testare în Google Cloud Shell

### Pregătire Mediu

```bash
# 1. Deschide Google Cloud Shell
# URL: https://console.cloud.google.com/

# 2. Verifică project-ul activ
gcloud config get-value project
# Așteptat: gen-lang-client-0706147575

# 3. Verifică că serviciul rulează
gcloud run services list --region=europe-west1 | grep expertap-api
```

---

## ✅ Test 1: Health Check

**Scop:** Verifică că aplicația răspunde la cereri HTTP

```bash
curl https://expertap-api-850584928584.europe-west1.run.app/health
```

**Rezultat așteptat:**
```json
{"status": "healthy", "version": "0.1.0"}
```

**✅ PASS dacă:** Primești JSON cu status "healthy"
**❌ FAIL dacă:** Eroare conexiune, timeout, sau status diferit

---

## ✅ Test 2: Database Connection

**Scop:** Verifică conectarea la PostgreSQL și citirea datelor

```bash
curl https://expertap-api-850584928584.europe-west1.run.app/api/v1/decisions/?limit=3
```

**Rezultat așteptat:**
```json
{
  "decisions": [
    {
      "id": "...",
      "numar_decizie": "...",
      "data_decizie": "...",
      "parti": "...",
      "solutie": "...",
      ...
    }
  ],
  "total": 7,
  "limit": 3,
  "offset": 0
}
```

**✅ PASS dacă:** Primești array cu 3 decizii CNSC (total: 7)
**❌ FAIL dacă:** Eroare database, array gol, sau eroare 500

---

## ✅ Test 3: Chat Assistant (Browser + Cloud Shell)

**Scop:** Verifică funcționalitatea AI chat cu RAG

### Part A: Test în Browser

1. Deschide: https://expertap-api-850584928584.europe-west1.run.app/
2. Click tab "Intreaba ExpertAP"
3. Scrie întrebare: **"Ce decizii CNSC ai în baza de date?"**
4. Click "Trimite"

**Rezultat așteptat:**
- Răspuns generat cu Gemini AI
- Citări din cele 7 decizii
- NU erori console

**⚠️ AȘTEPTAT SĂ EȘUEZE** din cauza GEMINI_API_KEY invalid!

### Part B: Test API Direct (Cloud Shell)

```bash
curl -X POST https://expertap-api-850584928584.europe-west1.run.app/api/v1/chat/ \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Ce decizii CNSC ai în baza de date?",
    "session_id": "test-session-123"
  }'
```

**Rezultat așteptat (cu API key valid):**
```json
{
  "response": "...",
  "citations": [...],
  "session_id": "test-session-123"
}
```

**Rezultat așteptat (cu API key INVALID - CURRENT STATE):**
```json
{
  "error": "Illegal header value"
}
```

---

## ✅ Test 4: Red Flags Analyzer (Browser + Cloud Shell)

**Scop:** Verifică detectarea clauzelor restrictive

### Part A: Test în Browser

1. Deschide: https://expertap-api-850584928584.europe-west1.run.app/
2. Click tab "Red Flags"
3. Paste text în textarea:
```
Operatorul economic trebuie să aibă o cifră de afaceri de minimum 10 milioane EUR în ultimii 3 ani și să fi realizat minimum 5 contracte similare cu valoare de peste 2 milioane EUR fiecare.
```
4. Click "Analizează"

**Rezultat așteptat:**
- Lista red flags detectate
- Categorii, severitate, recomandări
- Referințe CNSC

**⚠️ AȘTEPTAT SĂ EȘUEZE** din cauza GEMINI_API_KEY invalid!

### Part B: Test API Direct (Cloud Shell)

```bash
curl -X POST https://expertap-api-850584928584.europe-west1.run.app/api/v1/redflags/ \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Operatorul economic trebuie să aibă o cifră de afaceri de minimum 10 milioane EUR în ultimii 3 ani."
  }'
```

**Rezultat așteptat (cu API key valid):**
```json
{
  "redflags": [...],
  "summary": {...},
  "recommendations": [...]
}
```

---

## ✅ Test 5: RAG Memo (Browser + Cloud Shell)

**Scop:** Verifică generarea memo-uri juridice din database

### Part A: Test în Browser

1. Deschide: https://expertap-api-850584928584.europe-west1.run.app/
2. Click tab "RAG Memo"
3. Topic: **"experiență similară în achiziții publice"**
4. Max decisions: **5**
5. Click "Generează Memo"

**Rezultat așteptat:**
- Memo juridic structurat
- Citări din decizii CNSC
- Confidence score

**⚠️ AȘTEPTAT SĂ EȘUEZE** din cauza GEMINI_API_KEY invalid!

### Part B: Test API Direct (Cloud Shell)

```bash
curl -X POST https://expertap-api-850584928584.europe-west1.run.app/api/v1/ragmemo/ \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "experiență similară în achiziții publice",
    "max_decisions": 5
  }'
```

---

## ✅ Test 6: Data Lake (Browser)

**Scop:** Verifică afișarea deciziilor din database

1. Deschide: https://expertap-api-850584928584.europe-west1.run.app/
2. Click tab "Data Lake"
3. Verifică că se afișează decizii (ar trebui 7)
4. Search: **"CNSC"**
5. Verifică că filter funcționează

**Rezultat așteptat:**
- Display cu toate cele 7 decizii
- Metadata: număr, părți, soluție, CPV
- Search funcțional

**✅ PASS dacă:** Toate deciziile sunt afișate și search funcționează
**❌ FAIL dacă:** Eroare loading, array gol, sau search broken

---

## 📊 Verificare Cloud Run Logs

```bash
# Verifică log-urile recente pentru erori
gcloud run services logs read expertap-api \
  --region=europe-west1 \
  --limit=50
```

**Caută după:**
- ❌ `Illegal header value` → confirmare GEMINI_API_KEY invalid
- ❌ `Database connection failed` → probleme PostgreSQL
- ✅ `200 OK` → requests successful
- ✅ `Health check passed` → aplicație healthy

---

## 🔑 Fix GEMINI_API_KEY (După testare)

**Dacă testele AI eșuează (așteptat), fix-ul este:**

```bash
# 1. Verifică problema
gcloud secrets versions access latest --secret="expertap-gemini-api-key" | od -c
# Ar trebui să vezi ... \n la final (problema!)

# 2. Obține API key nou de la:
# https://aistudio.google.com/app/apikey

# 3. Recreează secretul CORECT (ÎNLOCUIEȘTE YOUR_API_KEY_HERE)
printf "YOUR_API_KEY_HERE" | gcloud secrets versions add expertap-gemini-api-key --data-file=-

# 4. Verifică fix-ul
gcloud secrets versions access latest --secret="expertap-gemini-api-key" | od -c
# NU ar trebui să mai vezi \n!

# 5. Verifică lungimea (ar trebui ~39 caractere)
gcloud secrets versions access latest --secret="expertap-gemini-api-key" | wc -c

# 6. Redeploy serviciul (opțional - Cloud Run va prelua automat la restart)
gcloud run services update expertap-api --region=europe-west1
```

---

## 📝 Raportare Rezultate

După rularea testelor, notează:

| Test | Status | Observații |
|------|--------|------------|
| 1. Health Check | ⬜ PASS / ⬜ FAIL | |
| 2. Database Connection | ⬜ PASS / ⬜ FAIL | |
| 3. Chat Assistant | ⬜ PASS / ⬜ FAIL | |
| 4. Red Flags Analyzer | ⬜ PASS / ⬜ FAIL | |
| 5. RAG Memo | ⬜ PASS / ⬜ FAIL | |
| 6. Data Lake | ⬜ PASS / ⬜ FAIL | |

**Așteptări realiste:**
- ✅ Test 1, 2, 6 → **PASS** (nu depind de Gemini)
- ❌ Test 3, 4, 5 → **FAIL** (depind de GEMINI_API_KEY invalid)

---

## 🎯 Next Steps După Testare

1. ✅ Confirmă că Test 1, 2, 6 trec → infrastructura OK
2. ❌ Confirmă că Test 3, 4, 5 eșuează → GEMINI_API_KEY invalid
3. 🔑 Fix GEMINI_API_KEY (vezi secțiunea de mai sus)
4. 🔄 Re-testează Test 3, 4, 5
5. 📈 Dacă toate trec → Import complet ~3000 decizii

---

_Created: 2025-12-30 | Pentru rulare în Google Cloud Shell_
