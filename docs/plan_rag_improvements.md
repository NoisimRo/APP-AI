# Plan: Îmbunătățiri RAG Pipeline + Saved Scopes

**Data:** 2026-03-09
**Status:** De implementat într-o sesiune viitoare
**Decizii utilizator:** Filtre salvate în tabel DB nou (nu localStorage). Reranking opțional cu toggle UI.

---

## Context

Calitatea răspunsurilor chat RAG depinde de cât de bine sunt găsite deciziile relevante din baza de date. Problemele actuale:

1. **Vector search și keyword search sunt mutual exclusive** — cascadă Tier 4 → Tier 6, nu combinate. Un query care conține termeni exacti + sens semantic pierde una din cele două direcții.
2. **Nu există query expansion** — utilizatorul scrie "99% materie primă" dar termenul juridic este "preț aparent neobișnuit de scăzut" (art. 132 Legea 98/2016). Vector search poate rata match-ul semantic.
3. **Nu există reranking** — top 15 chunks din vector search sunt deduplicate la 5 decizii fără re-scoring. Chunk-uri mai puțin relevante pot „câștiga" doar fiindcă vin din decizii diferite.
4. **Legislația citată în ArgumentareCritica nu e auto-inclusă** — dacă un chunk menționează "art. 132 din Legea 98/2016", fragmentul legislativ nu e adăugat automat în contextul LLM.
5. **Utilizatorul nu poate restrânge căutarea** — nu poate filtra pe Data Lake (CPV, cod critică, soluție) și apoi folosi acel subset în Chat.

---

## Ordine de implementare (pe dependențe)

```
Independent: [1] Legislation Linking (~1h)
Secvențial:  [2] Hybrid Search (~3h) → [3] Query Expansion (~2h) → [4] Reranking (~2h)
Independent: [5] Saved Scopes (~4h, cel mai complex)
```

**Total estimat: ~12 ore**

---

## 1. Legislation Linking în RAG Context

**Fișiere:** `backend/app/services/rag.py`

**Ce face:** După ce `search_decisions()` găsește `ArgumentareCritica` chunks, scanează textul lor pentru referințe legislative (art. X din Legea Y), caută `legislatie_fragmente` matching, și le adaugă automat în context.

**Implementare:**

```python
# Metodă nouă în RAGService
async def _extract_legislation_from_chunks(
    self,
    matched_chunks: list[tuple[ArgumentareCritica, float]],
    session: AsyncSession,
    existing_fragments: list[tuple[LegislatieFragment, str]],
) -> list[tuple[LegislatieFragment, str]]:
    """Extrage referințe legislative din chunks și adaugă fragmente lipsă."""
```

**Pași:**
1. Concatenează textul tuturor matched_chunks (argumentatie_cnsc + argumente_contestator + argumente_ac)
2. Aplică `_parse_article_query()` existent pe textul concatenat
3. Lookup `legislatie_fragmente` per referință (refolosește logica din `_search_legislation_fragments`)
4. Deduplică vs. fragmentele deja găsite de căutarea legislativă independentă
5. Cap la max 8 fragmente totale

**Integrare în `prepare_context()` (~linia 1054):**
```python
# După search_decisions:
auto_legislation = await self._extract_legislation_from_chunks(
    matched_chunks, session, legislation_fragments
)
legislation_fragments.extend(auto_legislation)
```

**Zero impact pe performanță:** Refolosește indexul `ix_frag_lookup` existent (btree pe act_id, numar_articol, alineat, litera).

---

## 2. Hybrid Search (Vector + Keyword/Trigram)

**Fișiere:** `backend/app/services/rag.py`

**Ce face:** Rulează vector search ȘI keyword/trigram search în paralel, fuzionează rezultatele cu Reciprocal Rank Fusion (RRF).

**De ce RRF:** Normalizează scoruri din surse diferite (cosine distance 0-2 vs. trigram similarity 0-1) fără a necesita calibrare manuală.

**Metode noi:**

```python
async def _trigram_search(
    self, query: str, session: AsyncSession, limit: int = 15
) -> list[tuple[ArgumentareCritica, float]]:
    """Search ArgumentareCritica via parent decision text_integral trigram similarity."""
    # JOIN argumentare_critica → decizii_cnsc
    # WHERE similarity(text_integral, query) > 0.1
    # ORDER BY similarity DESC
    # Folosește indexul GIN trigram ix_decizii_fulltext
```

```python
def _rrf_merge(
    self,
    vector_results: list[tuple[ArgumentareCritica, float]],
    keyword_results: list[tuple[ArgumentareCritica, float]],
    k_vector: int = 60,
    k_keyword: int = 70,
) -> list[tuple[ArgumentareCritica, float]]:
    """Reciprocal Rank Fusion merge."""
    # score(doc) = 1/(k_vector + rank_vector) + 1/(k_keyword + rank_keyword)
    # k_vector < k_keyword → slight preference for vector (semantic) results
```

**Integrare în `search_decisions()` Tier 4 (~linia 608):**

```python
if has_embeddings and has_embeddings > 0:
    # Rulează ambele în paralel
    vector_task = self.search_by_vector(query, session, limit=limit * 3)
    trigram_task = self._trigram_search(query, session, limit=limit * 3)
    vector_results, keyword_results = await asyncio.gather(vector_task, trigram_task)

    # Merge cu RRF
    matched_chunks = self._rrf_merge(vector_results, keyword_results)

    # CPV boosting rămâne la fel
    if cpv_codes:
        ...
```

**Dependențe DB:** Folosește indexul `ix_decizii_fulltext` (GIN trigram) existent. Necesită `pg_trgm` extension (deja instalată).

**Funcția SQL trigram:**
```sql
SELECT ac.*, similarity(dc.text_integral, :query) AS sim
FROM argumentare_critica ac
JOIN decizii_cnsc dc ON dc.id = ac.decizie_id
WHERE dc.text_integral % :query  -- trigram similarity > pg_trgm.similarity_threshold
ORDER BY sim DESC
LIMIT :limit
```

**Notă:** `similarity()` din pg_trgm funcționează cel mai bine pe string-uri scurte. Pentru text_integral lung, o alternativă e search pe câmpurile scurte din ArgumentareCritica (cod_critica, argumentatie_cnsc truncat).

---

## 3. Query Expansion

**Fișiere:** `backend/app/services/rag.py`

**Ce face:** LLM-ul generează 2-3 reformulări ale query-ului (cu termeni juridici echivalenți) înainte de search. Fiecare reformulare generează un set de vector results, apoi se fuzionează.

**Prompt expansion (~140 tokens):**
```
Reformulează următoarea întrebare în 3 variante scurte pentru căutare
în jurisprudența CNSC. Folosește termeni juridici din achizițiile publice
din România. Răspunde DOAR cu cele 3 variante, una per linie.

Întrebare: {query}
```

**Implementare:**

```python
async def _expand_query(self, query: str) -> list[str]:
    """Generează variante de căutare folosind LLM."""
    # Skip pentru queries simple sau BO references
    if len(query.split()) < 4 or self._extract_bo_references(query):
        return [query]

    response = await self.llm.complete(
        prompt=EXPANSION_PROMPT.format(query=query),
        temperature=0.3,
        max_tokens=200,
    )
    variants = [line.strip() for line in response.strip().split('\n') if line.strip()]
    return [query] + variants[:3]  # Original + max 3 expansions
```

**Integrare în Tier 4:**

```python
# Expandează query (paralel cu prima căutare)
expanded_queries = await self._expand_query(query)

# Vector search pe toate variantele (paralel)
all_vector_tasks = [self.search_by_vector(q, session, limit=limit*2) for q in expanded_queries]
all_trigram_tasks = [self._trigram_search(q, session, limit=limit*2) for q in expanded_queries]
all_results = await asyncio.gather(*all_vector_tasks, *all_trigram_tasks)

# Merge toate cu RRF, deduplică per ArgumentareCritica.id
```

**Latență:** Expansion LLM call (~1-2s) rulează paralel cu prima vector search. Queries suplimentare adaugă ~0.5s total (sunt batch).

**Feasibility pe Groq:** ~140 tokens input + ~100 output = ~240 tokens. Încape confortabil chiar și pe llama-3.1-8b (TPM 6000).

---

## 4. Reranking (Opțional cu Toggle UI)

**Fișiere:**
- `backend/app/services/rag.py`
- `backend/app/api/v1/chat.py` (ChatRequest + parametru)
- `index.tsx` (toggle UI)

**Ce face:** După retrieval (hybrid search + expansion), LLM re-scorează relevanța fiecărui chunk vs. query. Utilizatorul poate activa/dezactiva din UI.

**Prompt reranking (~700 tokens total):**
```
Ordonează următoarele fragmente juridice după relevanță pentru întrebarea:
"{query}"

{for i, chunk in enumerate(chunks)}
[{i+1}] {chunk.cod_critica}: {chunk.argumentatie_cnsc[:200]}
{endfor}

Răspunde DOAR cu numerele în ordinea relevanței, separate prin virgulă.
Exemplu: 3,1,5,2,4
```

**Implementare:**

```python
async def _rerank_chunks(
    self,
    query: str,
    chunks: list[tuple[ArgumentareCritica, float]],
    top_k: int = 10,
) -> list[tuple[ArgumentareCritica, float]]:
    """LLM-based reranking of retrieved chunks."""
    if len(chunks) <= top_k:
        return chunks

    # Build compact prompt
    prompt = self._build_rerank_prompt(query, chunks[:15])

    try:
        response = await self.llm.complete(prompt=prompt, temperature=0.0, max_tokens=50)
        order = [int(x.strip()) - 1 for x in response.strip().split(',')]
        reranked = [chunks[i] for i in order if 0 <= i < len(chunks)]
        # Fallback: adaugă chunks neordonate
        seen = set(i for i in order if 0 <= i < len(chunks))
        for i, chunk in enumerate(chunks):
            if i not in seen:
                reranked.append(chunk)
        return reranked[:top_k]
    except Exception:
        # Fallback: keep original order
        return chunks[:top_k]
```

**Toggle UI (frontend):**

```typescript
// State nou
const [chatReranking, setChatReranking] = useState(false);

// În chat input area, toggle mic:
<label className="flex items-center gap-1.5 text-xs text-slate-400">
  <input type="checkbox" checked={chatReranking} onChange={...} className="..." />
  Reranking
</label>

// Trimis în request:
{ message: userMsg, history: [...], rerank: chatReranking }
```

**Backend (chat.py):**

```python
class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    history: list[ChatMessage] = Field(default_factory=list)
    rerank: bool = False  # NOU
```

**Feasibility pe Groq:** 15 chunks × ~40 tokens = ~600 tokens input + ~30 output = ~630 tokens. Încape pe toate modelele.

---

## 5. Saved Scopes (Filtre Salvate în DB)

**Fișiere:**
- `backend/app/models/decision.py` (model SQLAlchemy nou)
- `backend/app/api/v1/scopes.py` (endpoint-uri CRUD noi)
- `backend/app/api/v1/chat.py` (ChatRequest + scope_id)
- `backend/app/services/rag.py` (search_decisions + WHERE clauses)
- `index.tsx` (UI save scope + scope selector pe Chat)
- `docs/expertap_db.md` (documentare schema)

### 5.1 Tabel DB nou

```sql
CREATE TABLE search_scopes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    description TEXT,
    filters JSONB NOT NULL DEFAULT '{}',
    -- filters example: {
    --   "ruling": "ADMIS",
    --   "tip_contestatie": "rezultat",
    --   "year": "2025",
    --   "coduri_critici": ["D3", "R2"],
    --   "cpv_codes": ["55520000-1"],
    --   "search": "catering"
    -- }
    decision_count INTEGER DEFAULT 0,  -- cached count of matching decisions
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);
```

### 5.2 Model SQLAlchemy

```python
# În backend/app/models/decision.py
class SearchScope(Base):
    __tablename__ = "search_scopes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    filters: Mapped[dict] = mapped_column(JSON, nullable=False, server_default=text("'{}'::jsonb"))
    decision_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
```

### 5.3 API Endpoints (backend/app/api/v1/scopes.py)

```
POST   /api/v1/scopes           — Creare scope din filtrele curente
GET    /api/v1/scopes           — Lista toate scope-urile
GET    /api/v1/scopes/{id}      — Detalii scope
PUT    /api/v1/scopes/{id}      — Update scope
DELETE /api/v1/scopes/{id}      — Ștergere scope
```

### 5.4 Integrare în Chat

**ChatRequest extins:**
```python
class ChatFilters(BaseModel):
    """Filtre opționale pentru restricționarea căutării RAG."""
    scope_id: uuid.UUID | None = None
    ruling: str | None = None
    tip_contestatie: str | None = None
    year: str | None = None
    coduri_critici: list[str] | None = None
    cpv_codes: list[str] | None = None

class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    history: list[ChatMessage] = Field(default_factory=list)
    rerank: bool = False
    filters: ChatFilters | None = None
```

### 5.5 Integrare în RAG search_decisions()

**Vector search cu filtre:** Adaugă JOIN + WHERE la query-ul vector:

```python
async def search_by_vector(
    self, query, session, limit=10,
    filters: dict | None = None,  # NOU
):
    query_vector = await self.embedding_service.embed_query(query)

    stmt = (
        select(
            ArgumentareCritica,
            ArgumentareCritica.embedding.cosine_distance(query_vector).label("distance"),
        )
        .where(ArgumentareCritica.embedding.isnot(None))
    )

    # Aplică filtre prin JOIN la DecizieCNSC
    if filters:
        stmt = stmt.join(DecizieCNSC, DecizieCNSC.id == ArgumentareCritica.decizie_id)
        if filters.get("ruling"):
            stmt = stmt.where(DecizieCNSC.solutie_contestatie == filters["ruling"])
        if filters.get("tip_contestatie"):
            stmt = stmt.where(DecizieCNSC.tip_contestatie == filters["tip_contestatie"])
        if filters.get("year"):
            stmt = stmt.where(DecizieCNSC.an_bo == int(filters["year"]))
        if filters.get("coduri_critici"):
            stmt = stmt.where(DecizieCNSC.coduri_critici.overlap(filters["coduri_critici"]))
        if filters.get("cpv_codes"):
            cpv_conditions = [DecizieCNSC.cod_cpv.ilike(f"{c}%") for c in filters["cpv_codes"]]
            stmt = stmt.where(or_(*cpv_conditions))

    stmt = stmt.order_by("distance").limit(limit)
```

**Performanță:** Toate filtrele folosesc indexuri existente (btree pe solutie_contestatie, tip_contestatie, an_bo; GIN pe coduri_critici; btree pe cod_cpv). HNSW vector search + btree filter = efficient.

### 5.6 Frontend UI

**Data Lake — buton "Salvează filtre":**
```
[Când există filtre active, apare buton "💾 Salvează ca Scope"]
→ Modal: Nume scope + Descriere (opțional)
→ POST /api/v1/scopes cu filtrele curente
→ Toast "Scope salvat!"
```

**Chat — selector scope:**
```
[Deasupra input-ului chat, dropdown opțional]
📋 Domeniu căutare: [Toate deciziile ▾]
  - Toate deciziile (default)
  - Catering ADMISE 2024 (23 decizii)
  - Rezultat IT R2+D3 (45 decizii)
  [+ Gestionează...]
```

**Indicator activ:** Când un scope e selectat, apare pill sub header:
```
🔍 Căutare restricționată la: "Catering ADMISE 2024" (23 decizii) [✕]
```

---

## Fișiere critice de modificat

| Fișier | Modificări |
|--------|-----------|
| `backend/app/services/rag.py` | Toate cele 5 îmbunătățiri: legislation linking, hybrid search, query expansion, reranking, filters |
| `backend/app/api/v1/chat.py` | ChatFilters, rerank param |
| `backend/app/api/v1/scopes.py` | **NOU** — CRUD endpoints pentru search_scopes |
| `backend/app/models/decision.py` | Model SearchScope |
| `index.tsx` | Toggle reranking, scope save/select UI |
| `docs/expertap_db.md` | Documentare tabel search_scopes |
| `backend/app/main.py` | Include scopes router |

---

## Verificare / Testare

1. **Legislation Linking:** Chat "Ce spune art. 132?" → verifică că apare text legislativ + chunks care citează art. 132
2. **Hybrid Search:** Chat "preț aparent neobișnuit de scăzut" → compară rezultate cu/fără trigram (log distances)
3. **Query Expansion:** Chat "99% materie primă" → verifică în logs că a generat variante cu "preț neobișnuit de scăzut"
4. **Reranking:** Toggle ON → verifică ordine diferită a rezultatelor, check logs
5. **Saved Scopes:** Filtrează pe Data Lake → Salvează → Selectează în Chat → Verifică că doar deciziile filtrate apar în context

---

## Migrare SQL necesară

```sql
-- search_scopes table
CREATE TABLE search_scopes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    description TEXT,
    filters JSONB NOT NULL DEFAULT '{}'::jsonb,
    decision_count INTEGER DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX ix_scopes_name ON search_scopes (name);
CREATE INDEX ix_scopes_created ON search_scopes (created_at DESC);
```
