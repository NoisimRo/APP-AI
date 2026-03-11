# Performance Best Practices — ExpertAP RAG Pipeline

> Documentat: 2026-03-11 | Rezultate validate în producție

## Rezultate (producție, martie 2026)

| Metric | Înainte | După | Îmbunătățire |
|--------|---------|------|-------------|
| `decisions_s` | 8.92s | 0.13s | **69x** |
| `decision_load` | ~2s | 0.01s | **200x** |
| `prepare_context` total | 9.36s | 0.77s | **12x** |
| `text_integral` loaded | 5 × 39KB = 195KB | 0KB | **∞** |
| Decizii în context | 5 | 10-15+ (din 20 chunks) | **3x** |
| Calitate răspunsuri | Limitată (5 decizii, mult garbage) | Excelentă (sinteze LLM) | Semnificativ |

---

## 1. `defer(text_integral)` — OBLIGATORIU

`DecizieCNSC.text_integral` conține ~39KB/decizie (text brut cu header-e, formatare, conținut irelevant). La 20 decizii = ~780KB transfer inutil din PostgreSQL.

**Regulă:** TOATE query-urile pe `DecizieCNSC` TREBUIE să folosească:

```python
from sqlalchemy.orm import defer

stmt = select(DecizieCNSC).options(defer(DecizieCNSC.text_integral)).where(...)
```

**Excepție unică:** Când `text_integral` e necesar explicit (ex: BO lookup direct care returnează decizia completă).

**Fișiere actualizate:**
- `backend/app/services/rag.py` — search_decisions(), _keyword_search()
- `backend/app/api/v1/drafter.py` — decision load
- `backend/app/api/v1/clarification.py` — decision load
- `backend/app/services/redflags_analyzer.py` — jurisprudence_search()
- `backend/app/services/training_generator.py` — decision load

---

## 2. Vector Search pe ArgumentareCritica (nu pe text_integral)

`ArgumentareCritica` conține **sinteze LLM structurate** — mult mai valoroase decât `text_integral`:

| Câmp | Conținut |
|------|----------|
| `argumente_contestator` | Argumente sintetizate ale contestatorului |
| `jurisprudenta_contestator` | Jurisprudență invocată de contestator (ARRAY) |
| `argumente_ac` | Argumente autoritate contractantă |
| `jurisprudenta_ac` | Jurisprudență invocată de AC (ARRAY) |
| `argumente_intervenienti` | Argumente intervenienți (JSON array) |
| `elemente_retinute_cnsc` | Fapte reținute de CNSC |
| `argumentatie_cnsc` | Raționamentul CNSC |
| `jurisprudenta_cnsc` | Jurisprudență citată de CNSC (ARRAY) |
| `castigator_critica` | Câștigător: contestator/autoritate/partial/unknown |

**Regulă:** Vector search cu `limit=20` pe `ArgumentareCritica.embedding` returnează 10-15+ decizii diferite, cu conținut concentrat și relevant.

**Anti-pattern:** NU încărca `text_integral` în context LLM — conține garbage (header-e repetitive, formatare juridică, secțiuni irelevante).

---

## 3. Embed ONCE — reutilizare query_vector

`embed_query()` costă ~0.25s (un apel API Gemini).

**Regulă:** Embed o singură dată, pasează `query_vector` la toate funcțiile de search.

```python
# CORECT
query_vector = await embedding_service.embed_query(query)
results_juris = await vector_search(query_vector, ...)
results_legis = await vector_search_legislation(query_vector, ...)

# GREȘIT — embed de 2 ori pe același text
results_juris = await search(query)  # embed intern
results_legis = await search_legis(query)  # embed din nou
```

---

## 4. Timing Logs — OBLIGATORII

Fiecare etapă măsurabilă trebuie logată cu prefix `timing_`:

```python
import time

t0 = time.monotonic()
# ... operație ...
logger.info("timing_embed_query", duration_s=round(time.monotonic() - t0, 2))
```

**Metrici standard:**

| Metric | Ce măsoară |
|--------|-----------|
| `timing_embed_query` | Durata embed via Gemini API |
| `timing_vector_search` | Durata vector search HNSW |
| `timing_decision_load` | Durata încărcare decizii din DB |
| `timing_legislation_search` | Durata căutare legislație |
| `timing_llm_first_token` | TTFT (time to first token) — cel mai important pentru UX |
| `timing_llm_stream_total` | Durata totală streaming LLM |

---

## 5. Status SSE Events — Feedback dinamic utilizator

LLM TTFT poate fi 5-25s. Utilizatorul trebuie informat despre progres.

**Backend:** Trimite `{"status": "message"}` events ÎNAINTE de stream-ul LLM:

```python
from app.services.llm.streaming import create_sse_response

status_msgs = []
if decision_refs:
    status_msgs.append(f"Am identificat {len(decision_refs)} decizii CNSC relevante")
status_msgs.append("Se generează răspunsul...")

return await create_sse_response(
    llm=llm, prompt=prompt,
    status_messages=status_msgs,
    ...
)
```

**Frontend:** Ascultă `data.status` în SSE stream:

```typescript
fetchStream(url, body, onChunk, onDone, onError, (status) => setStreamStatus(status));
```

---

## 6. Citations — Toate deciziile matched, ordonate by relevance

**Regulă:** Citations includ TOATE deciziile cu chunks potrivite, ordonate după distanța vectorială (cele mai relevante primele).

**Anti-pattern:** NU filtra citations verificând dacă `decision_ref in response_text` — LLM-ul poate reformula sau omite referințe.

```python
def _build_citations(self, decisions, matched_chunks):
    """Build citations from ALL matched decisions, ordered by vector relevance."""
    citations = []
    decision_map = {d.id: d for d in decisions}
    seen_dec_ids = set()
    for arg, _dist in matched_chunks:  # already ordered by distance
        if arg.decizie_id in seen_dec_ids:
            continue
        seen_dec_ids.add(arg.decizie_id)
        dec = decision_map.get(arg.decizie_id)
        if not dec:
            continue
        citation_text = (
            arg.argumentatie_cnsc[:300] + "..."
            if arg.argumentatie_cnsc
            else f"Decizia {dec.external_id} — {dec.solutie_contestatie or 'N/A'}"
        )
        citations.append(Citation(decision_id=dec.external_id, text=citation_text, verified=True))
    return citations
```

---

## 7. asyncio.gather NU e safe cu aceeași AsyncSession

`asyncpg` nu suportă query-uri concurente pe aceeași conexiune.

```python
# GREȘIT — va da eroare asyncpg
await asyncio.gather(
    session.execute(query1),
    session.execute(query2),
)

# CORECT — secvențial pe aceeași sesiune
result1 = await session.execute(query1)
result2 = await session.execute(query2)
```

---

## 8. Context Building — Include argumente_intervenienti

Când construiești context LLM din `ArgumentareCritica`, include TOATE câmpurile relevante:

```python
if arg.argumente_contestator:
    part += f"  Argumente contestator: {arg.argumente_contestator}\n"
if arg.argumente_ac:
    part += f"  Argumente AC: {arg.argumente_ac}\n"
if arg.argumente_intervenienti:
    # JSON field: [{"nr": 1, "argumente": "...", "jurisprudenta": [...]}]
    for interv in arg.argumente_intervenienti:
        part += f"  Intervenient {interv.get('nr', '?')}: {interv.get('argumente', '')[:300]}\n"
if arg.elemente_retinute_cnsc:
    part += f"  Elemente reținute CNSC: {arg.elemente_retinute_cnsc}\n"
if arg.argumentatie_cnsc:
    part += f"  Argumentație CNSC: {arg.argumentatie_cnsc}\n"
```

---

## Checklist pentru module noi

Când adaugi un modul nou care accesează `DecizieCNSC`:

- [ ] `defer(DecizieCNSC.text_integral)` pe toate SELECT-urile
- [ ] Vector search pe `ArgumentareCritica`, nu pe `text_integral`
- [ ] Embed query o singură dată, reutilizează `query_vector`
- [ ] Timing logs pe fiecare etapă (`timing_*`)
- [ ] Status SSE events pe endpoint-urile de streaming
- [ ] Citations includ toate deciziile matched, ordonate by relevance
- [ ] Include `argumente_intervenienti` în context
