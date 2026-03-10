# Plan v2: Îmbunătățiri RAG Pipeline

**Data:** 2026-03-10
**Status:** De implementat
**Lecții din v1:** Implementarea anterioară (commit 02ab334) a eșuat din cauza supraîncărcării — toate 5 features într-un singur commit, fără testare pe producție între pași.

---

## Analiză cauze eșec v1

### Ce s-a întâmplat
Commit-ul 02ab334 (+934 linii, 7 fișiere) a introdus simultan: hybrid search, query expansion, trigram search, reranking, saved scopes, legislation linking. Rezultat: aplicația a devenit inutilizabilă (30-40s per cerere, timeout-uri, crash-uri).

### Cauze tehnice

| Problemă | Cauza reală | Lecția |
|---|---|---|
| 30-40s per cerere | 5× embedding calls + 4× trigram full-scan pe 78MB | Fiecare apel API extern adaugă 2-5s pe Cloud Run |
| AsyncSession crash | `asyncio.gather` pe queries paralele cu aceeași sesiune | AsyncSession nu e safe pentru coroutine concurente |
| Trigram lent | `similarity(text_integral, ...)` fără index GIN pg_trgm | `text_integral` e TEXT lung — trigram e O(n) fără index |
| Toate modulele lente | `hybrid_search(expand=True)` propagat la drafter, clarification, red flags | Un parametru default greșit a afectat tot sistemul |
| Fix-uri stratificate | Fiecare fix a introdus noi probleme (try/catch → hang → circuit breaker → AbortController → "signal aborted") | Nu adăuga complexitate ca fix — simplifică |

### Cauze de proces

1. **Commit prea mare** — imposibil de testat incremental
2. **Fără test pe producție între pași** — toate features deodată
3. **Estimări greșite** — planul zicea "+0.5s per query variant", realitatea: +8-12s
4. **asyncio.gather pe AsyncSession** — nu funcționează, trebuie sequential
5. **Nu s-a verificat dacă pg_trgm index există** pe `text_integral`

---

## Ce funcționează acum (pe main)

Din branch-ul `claude/add-decision-prefiltering-5bL6i` (PRs #77-#79):
- **Scopes CRUD** — tabel `search_scopes`, API endpoints, model SQLAlchemy
- **Pre-filtering pe toate paginile RAG** — Chat, Drafter, Clarification, Red Flags, RAG Memo, Training
- **Scope selector UI** — dropdown "Domeniu" în Chat + toate paginile
- **Scope save din Data Lake** — buton salvare când filtre active
- **Multi-year filter** — suport selecție multipla ani
- **Sidebar mobil retractabil** — hamburger menu pe ecrane mici
- **Mobile responsive** — optimizări pe toate paginile
- **Vite proxy** — `/api` → `localhost:8000` pentru dev local

---

## Ce rămâne de implementat

### Punctul 1: Legislation Linking (~1h) ✅ SAFE
**Risc: ZERO** — nu face apeluri API externe, doar DB lookups pe indexuri existente.

Scanează textul `ArgumentareCritica` chunks pentru referințe legislative, le adaugă automat în context.

**Implementare:** O singură metodă nouă în `rag.py` + 5 linii în `prepare_context()`.

**Regula:** Testare pe producție ÎNAINTE de a trece la punctul 2.

---

### Punctul 2: Hybrid Search — Trigram ca FALLBACK, nu paralel (~2h) ⚠️ NEEDS REDESIGN

**Problema v1:** Trigram rula în PARALEL cu vector search, pe FIECARE expanded query. 4× trigram scan = catastrofă.

**Design v2:** Trigram devine **fallback** (nu paralel):

```
Strategia search_decisions:
1. BO lookup (instant)
2. Legal article lookup (instant)
3. CPV domain lookup (instant)
4. Vector search (1 embedding call, ~2-3s)     ← PRIMAR
5. Trigram search (doar dacă #4 returnează <3 rezultate) ← FALLBACK
6. Keyword ILIKE (doar dacă #4 și #5 eșuează)  ← ULTIMUL RESORT
```

**Pre-condiție CRITICĂ:** Verificare în producție:
```sql
-- Verifică dacă pg_trgm e instalat
SELECT * FROM pg_extension WHERE extname = 'pg_trgm';

-- Verifică dacă index GIN trigram există pe text_integral
SELECT indexname, indexdef FROM pg_indexes
WHERE tablename = 'decizii_cnsc' AND indexdef LIKE '%trgm%';

-- Dacă NU există, CREEAZĂ:
CREATE INDEX CONCURRENTLY ix_decizii_trgm
ON decizii_cnsc USING gin (text_integral gin_trgm_ops);
-- ATENȚIE: pe 78MB, asta durează ~30s-1min
```

**Fără index GIN, trigram NU se implementează.** E O(n) full-scan.

**Implementare:** Trigram NUMAI pe `argumentatie_cnsc` (text scurt per chunk), NU pe `text_integral` (78MB). Sau pe un câmp tsvector dedicat.

**Regula:** Testare pe producție ÎNAINTE de a trece la punctul 3.

---

### Punctul 3: Query Expansion — OPȚIONAL, cu toggle UI (~1.5h) ⚠️ CONDITIONAL

**Problema v1:** Expansion era ON by default, adăugând 1 LLM call + 3 embedding calls pe FIECARE cerere.

**Design v2:**
- **OFF by default** (ca reranking-ul)
- Toggle explicit în UI: `☐ Expansiune query`
- Când ON: 1 LLM call (max 5s timeout) → max 2 variante suplimentare
- Fiecare variantă = 1 vector search (refolosește embedding)
- **Total extra cost:** 1 LLM call + 2 embedding calls = ~5-8s extra
- **Utilizator avertizat:** "Activarea expansiunii îmbunătățește rezultatele dar adaugă ~5-10s"

**Regula:** NU se implementează decât DUPĂ ce punctele 1 și 2 funcționează stabil.

---

### Punctul 4: Reranking — păstrat exact ca în plan v1 (~1h) ✅ LOW RISK

- Toggle UI (deja proiectat)
- 1 LLM call suplimentar (~630 tokens)
- Fallback la ordinea originală pe eroare
- Deja a fost implementat în v1 (cod-ul poate fi recuperat din git history)

---

## Reguli obligatorii pentru implementare

### R1: Un singur feature per commit
Fiecare punct (1, 2, 3, 4) = un commit separat, testat pe producție.

### R2: Nu modifica mai mult de 2 fișiere per commit
Dacă trebuie modificate 3+, împarte în sub-commits.

### R3: Testare pe producție între fiecare pas
User-ul merge-uiește, deploy-uiește, testează, confirmă. Abia apoi se trece la pasul următor.

### R4: Niciun default care adaugă latență
Orice face un apel API extern (LLM, embedding) trebuie să fie OPT-IN, nu default.

### R5: Niciun asyncio.gather pe AsyncSession
Toate DB queries sequential. Doar API calls externe pot fi paralele (dar nu share-uiesc session).

### R6: Timeout pe orice apel extern
- Embedding: max 15s
- LLM expansion: max 5s
- LLM reranking: max 10s
- Fallback la rezultatele existente pe timeout

### R7: Verifică precondițiile DB ÎNAINTE de implementare
Indexuri, extensii, dimensiuni coloane — verifică output real din producție.

---

## Ordine de implementare

```
Pas 1: [1] Legislation Linking      (~1h)  — SAFE, zero API calls extra
  └── merge + deploy + test producție

Pas 2: [2] Trigram fallback          (~2h)  — DOAR după verificare pg_trgm index
  └── merge + deploy + test producție

Pas 3: [4] Reranking toggle          (~1h)  — opt-in, low risk
  └── merge + deploy + test producție

Pas 4: [3] Query Expansion toggle    (~1.5h) — opt-in, conditional
  └── merge + deploy + test producție
```

**Total estimat: ~5.5h** (vs. 12h planul v1 — am tăiat complexitatea)

---

## Fișiere afectate per pas

| Pas | Fișiere | Linii estimate |
|-----|---------|---------------|
| 1. Legislation Linking | `rag.py` | +40 linii |
| 2. Trigram fallback | `rag.py` | +50 linii |
| 3. Reranking | `rag.py`, `chat.py`, `index.tsx` | +60 linii |
| 4. Query Expansion | `rag.py`, `index.tsx` | +50 linii |
