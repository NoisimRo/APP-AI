Context
Modulul actual de generare contestații (backend/app/api/v1/drafter.py) este basic: un singur prompt monolitic, 8 chunk-uri RAG truncate la 400 caractere, fără pre-analiză, fără strategie juridică, fără căutare directă de legislație. Nu folosește capabilitățile RAGService existente (query expansion, reranking, RRF merge).
Obiectiv: Transformare într-un pipeline skills-based multi-pass care replică procesul unui avocat real: analizează faptele → cercetează jurisprudență + legislație → redactează contestația cu strategie clară.
Constrângeri de la utilizator:

Pipeline cu un singur LLM call de pre-analiză (combine analyze + plan), nu 3 separate
Agnostic — nu orientat spre un domeniu specific, ci generic, bazat pe modele de argumentare
Structurare numerică clară per critică
Încadrare juridică precisă (articol specific + lege)
Avertismente procedurale obligatorii (termen 10 zile, cauțiune 5 zile)

Modele de Argumentare ale Contestatorului (din 9 decizii CNSC reale)
Analizate doar secțiunile contestatorului (de la început până la "punctul de vedere al autorității contractante"). Fiecare model = un pattern reutilizabil de structurare a criticilor.
7 Modele Generice
#ModelStructura contestatoruluiDecizie model1Specificații tehnice restrictiveCritici numerotate 1-12+, fiecare: "Se solicită eliminarea cerinței X întrucât este restrictivă" + argument tehnic + "Jurisprudențial, invocă Decizia CNSC nr..."BO2025_1337, BO2025_34182Combatere "clarificări neconcludente"Per critică: a) cerințe DA relevante, b) solicitarea de clarificări, c) răspunsul prezentat, d) nelegalitatea — pattern 4-beat repetatBO2025_11283Apărare contra cerințelor neprevăzuteContraargument per motiv de respingere (lit. a, b, c, d): "în documentația de atribuire nu există nicio cerință..." + distincții terminologice (ex: timp intervenție vs remediere)BO2025_18384Cerințe ambigue și neclarePuncte numerotate simple "Punctul 1 – se solicită X – consideră că noțiunea este ambiguă"BO2025_18785Demontare cost/financiarăCritici numerotate pe componente cost: spor noapte, spor weekend, concediu, fond handicap, cheltuieli indirecte, profit — cu calcul matematic + Codul MunciiBO2025_15966Atac multi-ofertant structuratSecțiuni per ofertant: IV. Critici comune, V. Oferta X, VI. Oferta Y — sub fiecare: 1. Preț scăzut, 2. Completare nepermisă, etc. + acces dosar + repunere în termenBO2025_32747Demontare transparență + modus operandiStrategie layered: (1) încălcare art. 63 L98 (nepublicare expert), (2) dovadă tipar comportament din procedură anterioară, (3) demontare tehnică per cerință cu sub-secțiuni (A., B., (1), (2)...)BO2026_85
Tehnici concrete ale contestatorului (din decizii reale)
Tehnica "cerință nescrisă" (clauză nescrisă): — BO2025_1337 :75, BO2025_1838 :341

"Potrivit art. 30 alin. (6) din HG 395/2016, cerința X este clauză nescrisă deoarece nu figurează în anunțul de participare."

Tehnica "copy-paste + metadata": — BO2025_3418 :36-61

Contestatorul demonstrează că documentația e copiată din altă procedură: erori rămase, metadate PDF, referințe la alt amplasament. + citează Decizia CNSC anterioară ca precedent.

Tehnica "calcul matematic demolator": — BO2025_1596 :84-125, BO2025_3274 :136-189

Descompunere preț pe componente, demonstrare matematică a imposibilității (ore manoperă insuficiente, spor noapte sub minimul legal, etc.)

Tehnica "distincție terminologică": — BO2025_1838 :293-313

Contestatorul definește precis termenii (timp intervenție ≠ timp remediere) și demonstrează că AC confundă noțiunile.

Tehnica "completare nepermisă pe calea clarificărilor": — BO2025_3274 :237-267

"Ofertantul nu a clarificat, ci a depus fișele tehnice care nu se regăseau în oferta inițială" + jurisprudență CJUE (Manova C-336/12) + decizii CNSC.

Tehnica "în principal/în subsidiar": — Toate contestațiile

"1. În principal: obligarea AC la reevaluare; 2. În subsidiar: anularea procedurii dacă viciile nu pot fi remediate."

Tehnica "acces dosar + repunere în termen": — BO2025_3274 :56-90

Contestatorul declară că n-a avut acces la ofertele competitorilor, contestă pe baza informațiilor publice, și cere repunere în termen de contestare după obținerea accesului.

Structura reală a contestației (din secțiunile contestatorilor)
1. ANTET FORMAL
   - Datele contestatorului (SRL, sediu, CUI, Registrul Comerțului, reprezentant legal)
   - Reprezentare convențională (avocat, cabinet, sediu procesual ales)
   - Datele AC (denumire, sediu)
   - Procedura (tip, obiect, cod CPV, anunț SEAP nr./data)
   - Actul contestat (adresa comunicare rezultat nr./data SAU documentația de atribuire)

2. SOLICITĂRI ("s-a solicitat:")
   - În principal:
     * anulare raport procedură + comunicare rezultat + acte subsecvente
     * obligare AC la reevaluare ofertă contestator
   - În subsidiar:
     * anulare procedură (dacă viciile nu pot fi remediate)
   - Accesoriu:
     * acces dosar achiziție (art. 21 L101/2016)
     * repunere în termen de contestare (art. 186 CPC)
     * cheltuieli judecată (art. 26 L101/2016)
     * suspendare procedură (art. 14 L101/2016)

3. INTERESUL LEGITIM (opțional, dar comun)
   - "Interesul nostru este reprezentat de posibilitatea atribuirii contractului societății noastre"

4. SITUAȚIA DE FAPT (cronologie detaliată)
   - Publicare anunț SEAP → Depunere oferte → Prima evaluare
   - Istoricul contestațiilor anterioare (dacă există)
   - Solicitări de clarificări (date, numere adrese) → Răspunsuri → Rezultat comunicat
   - Clasament final (cine pe ce loc)

5. MOTIVELE CONTESTAȚIEI (numerotate)
   Fiecare critică = structură repetitivă:
   a) Cerințe documentație relevante — citat EXACT din fișa de date/caiet de sarcini
   b) Solicitarea de clarificări (nr./data) — ce a cerut comisia
   c) Răspunsul prezentat — ce a furnizat contestatorul/ce a făcut ofertantul criticat
   d) Nelegalitatea:
      - Articol SPECIFIC încălcat (art. X alin. (Y) lit. Z) din Legea/HG)
      - Argumentul juridic (de ce încalcă)
      - Jurisprudență CNSC de susținere ("în sensul celor arătate, invocă Decizia CNSC nr. X din data de Y")
      - Dovezi/probe concrete

6. DISPOZITIV
   - Reiterare solicitări din secțiunea 2
Reguli obligatorii (din feedback utilizator + art. 10 Legea 101/2016)

Structurare numerică — fiecare critică = punct separat, clar delimitat
Încadrare juridică precisă — orice critică legată de articol specific (ex: art. 50 L98, art. 155 L98, art. 134 HG395)
Avertismente procedurale obligatorii:

Termen decădere: 10 zile (art. 8 alin. (1) lit. a) din L101/2016)
Cauțiune: max 5 zile de la sesizare (art. 61^1 alin. (1) din L101/2016) — lipsă = respingere automată


Obiectul contestației — art. 10 lit. e) din L101/2016
Motivarea în fapt și în drept — art. 10 lit. f) din L101/2016
Mijloace de probă — art. 10 lit. g) din L101/2016

Reguli de Performanță (din docs/PERFORMANCE.md) — OBLIGATORII
Aceste reguli sunt obligatorii pentru implementare:

defer(text_integral) — TOATE query-urile pe DecizieCNSC TREBUIE să excludă text_integral (39KB/decizie)
Vector search pe ArgumentareCritica — NU pe text_integral. Câmpurile sintetizate (argumente_contestator, argumentatie_cnsc, etc.) sunt mult mai valoroase
Embed ONCE — embed query o singură dată, pasează query_vector la toate funcțiile de search (embed_query() = ~0.25s)
Timing logs — prefix timing_* pe fiecare etapă (timing_embed_query, timing_vector_search, timing_legislation_search, timing_llm_first_token, etc.)
Status SSE events — feedback dinamic utilizator pe streaming (status per skill, nu doar "Se generează...")
Citations — TOATE deciziile matched, ordonate by vector relevance. NU filtra verificând dacă ref apare în text
asyncio.gather NU e safe cu aceeași AsyncSession — query-uri secvențiale pe aceeași sesiune (nu gather)
Include argumente_intervenienti — JSON field [{"nr": 1, "argumente": "...", "jurisprudenta": [...]}] în context building

Checklist PERFORMANCE.md pentru ComplaintDrafter:

 defer(DecizieCNSC.text_integral) pe toate SELECT-urile (Skill 2 research)
 Vector search pe ArgumentareCritica.embedding, nu pe text_integral
 Embed fiecare search_query o singură dată, reutilizează query_vector în search_by_vector() + _search_legislation_fragments()
 Timing logs: timing_analyze_plan, timing_research_embed, timing_research_vector, timing_research_legislation, timing_draft_stream
 Status SSE events: 3 status messages (analyze, research, draft) + metadata counts
 Citations: toate deciziile matched din research, ordonate by distance
 Queries DB secvențiale (nu asyncio.gather pe aceeași session)
 Context include argumente_intervenienti din ArgumentareCritica

Arhitectura: Pipeline cu 3 Skills
Input (DrafterRequest)
    │
    ▼
┌─────────────────────────────┐
│  Skill 1: AnalyzeAndPlan    │  ← 1 singur LLM call (~5s)
│  classify + extract issues  │     System prompt CACHEABLE (static)
│  + plan strategy            │
└──────────┬──────────────────┘
           │ ComplaintBlueprint (JSON)
           ▼
┌─────────────────────────────┐
│  Skill 2: Research          │  ← Doar DB queries (~2-5s)
│  multi-strategy search      │     Zero LLM calls
│  jurisprudență + legislație │
└──────────┬──────────────────┘
           │ ResearchResults
           ▼
┌─────────────────────────────┐
│  Skill 3: Draft             │  ← LLM streaming (~20-40s)
│  contestație completă       │     Cu tot contextul din Skill 1+2
└─────────────────────────────┘
           │
           ▼
      SSE Stream → client
Total: 2 LLM calls (Skill 1 non-streaming + Skill 3 streaming)
Detalii Skills
Skill 1: analyze_and_plan() — Un singur LLM call
Input: facts, authority_args, legal_grounds, complaint_type (opțional override)
Output: ComplaintBlueprint (JSON structurat):
python@dataclass
class ComplaintBlueprint:
    # Clasificare
    complaint_type: str  # contestatie_rezultat | contestatie_documentatie | cerere_anulare
    argumentation_models: list[str]  # din cele 7 modele identificate

    # Extracții
    parties: dict  # {contestator, autoritate, procedura, cod_cpv, tip_procedura}
    timeline: list[str]  # evenimente cronologice cheie
    identified_legal_refs: list[str]  # referințe juridice din input

    # Plan strategic
    criticisms: list[dict]  # [{
    #   title: str,           — titlul criticii
    #   search_query: str,    — query optimizat pentru RAG (terminologie CNSC)
    #   legal_basis: str,     — articolul principal (ex: "art. 155 alin. (1) din Legea 98/2016")
    #   argumentation: str,   — rezumatul argumentului planificat
    #   evidence_needed: str,  — ce probe sunt necesare
    # }]

    # Solicitări planificate
    relief_sought: list[str]  # ce se solicită CNSC-ului

    # Avertismente procedurale
    procedural_warnings: list[str]  # termen, cauțiune, etc.
System prompt (STATIC — cacheable):

Expert avocat achiziții publice România
Cele 7 modele de argumentare cu descriere scurtă (din tabelul de mai sus)
Cele 7 tehnici concrete de argumentare (cerință nescrisă, calcul matematic, distincție terminologică, etc.)
Schema JSON de output (ComplaintBlueprint)
Regulile de structurare (art. 10 L101/2016: obiect, motivare fapt+drept, probe)
Instrucțiuni de a genera search_query în terminologie CNSC (ex: "respingere ofertă clarificări neconcludente art. 134 HG 395" nu "the offer was rejected")
NU hallucina articole — doar cele pe care le cunoaște cu certitudine; pentru restul pune "legal_basis": "de identificat prin RAG"
Identifică TOATE criticile din fapte — nu le rezuma într-una singură

User prompt (dinamic):

Faptele complete (fără trunchiere)
Argumentele AC (dacă există)
Temeiul legal indicat de utilizator
complaint_type override (dacă specificat)

LLM: temp=0.1, max_tokens=4096, JSON output
Skill 2: research() — Doar DB, zero LLM
Input: ComplaintBlueprint + session + scope_ids
Output: ResearchResults:
python@dataclass
class ResearchResults:
    # Jurisprudență — full text, NU truncat
    chunks_by_criticism: dict[int, list[tuple]]  # criticism_index → [(ArgumentareCritica, distance, DecizieCNSC)]
    all_chunks: list[tuple]  # deduplicate, sorted by distance
    winning_chunks: list[tuple]  # subset cu castigator_critica == 'contestator'

    # Legislație — full text
    legislation_fragments: list[tuple]  # (LegislatieFragment, act_name)
    legislation_by_article: dict[str, tuple]  # "art. 155" → (fragment, act_name)

    # Metadata
    decision_refs: list[str]  # external_ids
    legislation_refs: list[str]  # citări
    stats: dict  # {decisions, chunks, legislation, winning}
```

**Strategie căutare (per critică din blueprint) — respectând PERFORMANCE.md:**

1. **Embed ONCE per search_query** — `query_vector = await embedding_service.embed_query(search_query)`, reutilizare în vector search + legislație
2. **Vector search SECVENȚIAL** pe `ArgumentareCritica` via `rag_service.search_by_vector(query_vector=query_vector, ...)`:
   - limit=15 per query (vs 8 actual)
   - distance < 0.5 threshold
   - **NU asyncio.gather** — query-uri secvențiale per critică (aceeași AsyncSession)
3. **Căutare directă legislație** via `rag_service._search_legislation_fragments(query, session, query_vector=query_vector)`:
   - Reutilizează `query_vector` deja calculat
   - Pentru `legal_basis` din fiecare critică + `identified_legal_refs` din blueprint
4. **Legislație din chunk-uri** via `rag_service._find_legislation_for_chunks(matched_chunks, session)`
5. **Deduplicare + sortare** — dedup pe `ArgumentareCritica.id`, sort by distance, limit 25
6. **Prioritizare winning arguments** — `castigator_critica == 'contestator'` marcate separat
7. **defer(text_integral)** — DecizieCNSC loaded FĂRĂ text_integral
8. **Timing logs** — timing_research_embed, timing_research_vector, timing_research_legislation per critică
9. **Context include argumente_intervenienti** — JSON field inclus complet

**Funcții RAGService reutilizate:**
- `search_by_vector()` (`backend/app/services/rag.py:56`)
- `_search_legislation_fragments()` (`rag.py:390`)
- `_find_legislation_for_chunks()` (`rag.py:630`)
- `_build_legislation_context()` (`rag.py:517`)
- `_parse_article_query()` (`rag.py:320`)

### Skill 3: `build_draft_prompt()` + LLM Streaming

**Input:** ComplaintBlueprint + ResearchResults + request original

**Output:** Contestație completă, streamed ca markdown

**System prompt (EXPERT LAWYER):**
```
Ești un avocat expert în achiziții publice din România. Redactezi o contestație
către CNSC pe baza planului strategic și a contextului juridic furnizat.

PLANUL STRATEGIC:
{blueprint JSON serialized — criticisms, relief_sought, argumentation_models}

REGULI ABSOLUTE:
1. Structurare numerică — fiecare critică = punct separat, numerotate I, II, III (sau 1, 2, 3)
2. Fiecare critică TREBUIE să conțină:
   a) Cerințele documentației de atribuire relevante — citat EXACT din fișa de date/caiet de sarcini
   b) Situația de fapt concretă — ce s-a întâmplat efectiv
   c) Argumentația juridică: articol SPECIFIC (art. X alin. (Y) lit. Z) din Legea/HG) + de ce e încălcat
   d) Jurisprudență CNSC — DOAR din cele furnizate ("Invocăm Decizia CNSC nr. X din data de Y")
   e) Dovezi/probe — ce susține afirmația
3. Citează DOAR deciziile CNSC furnizate în context — NU inventa referințe
4. Citează articolele de lege cu text EXACT din legislația furnizată — NU aproxima
5. Include OBLIGATORIU la final avertismente procedurale:
   - Termen decădere: 10 zile de la comunicare (art. 8 alin. (1) lit. a) din L101/2016)
   - Cauțiune obligatorie: max 5 zile de la sesizare (art. 61^1 alin. (1) din L101/2016)
   - Lipsa cauțiunii = respingere automată, indiferent de calitatea argumentelor
6. Limba română juridică formală
7. Contestația poate avea până la 20 pagini — spațiul e pentru SUBSTANȚĂ, nu formulări generice
8. Solicitări structurate: în principal + în subsidiar + accesoriu (acces dosar, cheltuieli)

TEHNICI DE ARGUMENTARE DISPONIBILE (selectează conform planului strategic):
- "Cerință nescrisă" (art. 30 alin. (6) HG 395/2016) — cerință nepublicată în anunț
- "Calcul matematic demolator" — descompunere preț/cost pe componente vs minimum legal
- "Distincție terminologică" — definirea precisă a termenilor confundați de AC
- "Completare nepermisă" — depunere documente lipsă pe calea clarificărilor (CJUE Manova C-336/12)
- "Copy-paste + metadata" — demonstrare copiere documentație din altă procedură
- "În principal/în subsidiar" — layering solicitări
- "Acces dosar + repunere în termen" — când nu ai avut acces la oferte competitori

STRUCTURA OBLIGATORIE:
## CONTESTAȚIE (antet: către CNSC, date părți, procedură, act contestat)
## SOLICITĂRI (în principal / în subsidiar / accesoriu)
## INTERESUL LEGITIM (dacă relevant)
## SITUAȚIA DE FAPT (cronologie cu date și numere adrese)
## MOTIVELE CONTESTAȚIEI (I, II, III — fiecare cu sub-secțiuni a, b, c, d)
## AVERTISMENTE PROCEDURALE (termen + cauțiune)
## DISPOZITIV (reiterare solicitări)
Context (list[str]):

Full text ArgumentareCritica per critică (grupate pe decizie, NETRUNCAT)
Full text legislație per articol referit
Decision metadata (soluție, contestator, AC, tip)
Winning arguments evidențiate

LLM: temp=0.3, max_tokens=24576 (mai mare — contestații pot fi 15-20 pagini), streaming
Fișiere de Creat/Modificat
1. backend/app/services/complaint_drafter.py — CREATE
Serviciu principal, ~400-500 linii. Pattern: RedFlagsAnalyzer.
pythonclass ComplaintDrafter:
    def __init__(self, llm_provider, embedding_service, rag_service)

    # Skill 1
    async def analyze_and_plan(self, facts, authority_args, legal_grounds, complaint_type_override) -> ComplaintBlueprint

    # Skill 2
    async def research(self, blueprint, session, scope_decision_ids) -> ResearchResults

    # Skill 3
    def build_draft_prompt(self, blueprint, research, facts, authority_args, legal_grounds) -> tuple[str, str, list[str]]
    # Returns (system_prompt, user_prompt, context_list)

    # Full pipeline (non-streaming)
    async def draft(self, facts, authority_args, legal_grounds, session, ...) -> tuple[str, list[str], list[str], ComplaintBlueprint]

    # Helpers
    def _build_jurisprudence_context(self, research) -> list[str]
    def _build_legislation_context(self, research) -> list[str]
    def _parse_blueprint_json(self, response) -> ComplaintBlueprint
Conține:

Dataclasses: ComplaintBlueprint, ResearchResults, CriticismPlan
System prompt STATIC pentru Skill 1 (cacheable) — cu cele 7 modele de argumentare
System prompt template pentru Skill 3 — cu structura obligatorie
Tipuri contestație: COMPLAINT_TYPES dict
Avertismente procedurale standard

2. backend/app/api/v1/drafter.py — MODIFY
Schimbări:

DrafterRequest — adaugă complaint_type: str | None
DrafterResponse — adaugă legislation_refs, complaint_type, issues_found
draft_complaint() — folosește ComplaintDrafter în loc de _build_drafter_context()
draft_complaint_stream() — custom SSE generator cu status per skill
Nou: GET /types endpoint
Șterge _build_drafter_context() (înlocuit de ComplaintDrafter)

Streaming custom (NU create_sse_response):
pythonasync def event_generator():
    yield sse_status("Se analizează faptele și se planifică strategia...")
    blueprint = await drafter.analyze_and_plan(...)
    yield sse_status(f"{blueprint.complaint_type}: {len(blueprint.criticisms)} critici identificate")

    yield sse_status("Se caută jurisprudență CNSC și legislație...")
    research = await drafter.research(blueprint, session, ...)
    yield sse_status(f"{research.stats['decisions']} decizii, {research.stats['legislation']} articole")

    yield sse_status(f"Se redactează contestația ({len(blueprint.criticisms)} secțiuni)...")
    sys_prompt, user_prompt, contexts = drafter.build_draft_prompt(...)
    async for chunk in llm.stream(prompt=user_prompt, system_prompt=sys_prompt, context=contexts, ...):
        yield sse_text(chunk)

    yield sse_done({...})
3. index.tsx — MODIFY
Schimbări minimale:

Adaugă state drafterComplaintType (~line 588)
Adaugă selector tip contestație în renderDrafter() (~line 3038, înainte de buton)

Radio buttons: "Detectare Automată" | "Contestație Rezultat" | "Contestație Documentație" | "Cerere de Anulare"


Update handleDrafting() (~line 1573) — adaugă complaint_type în request body
Adaugă secțiune legislație refs în output (~line 3119) — badges din meta.legislation_refs
Badge complaint type din meta.complaint_type
Status display deja funcționează (streamStatus)

Error Handling & Fallbacks
SkillFallbackSkill 1 (analyze)Default: contestatie_documentatie, 1 search query generic din primele 3000 chars, plan cu 1 criticăSkill 2 (research)Context gol → Skill 3 generează fără jurisprudență (dar cu avertisment)Skill 3 (draft)Returnează eroare la clientEmbedding individualSkip acea critică, continuă cu celelalte
Timeout-uri: Skill 1: 30s, Skill 2: 60s, Skill 3: 120s (via asyncio.wait_for)
Verificare

Import test: cd backend && python -c "from app.services.complaint_drafter import ComplaintDrafter"
Backend startup: uvicorn app.main:app --reload — fără erori
Types endpoint: GET /api/v1/drafter/types → returnează tipurile
Non-streaming: POST /api/v1/drafter/ cu fapte de test → response cu content + decision_refs + legislation_refs + complaint_type
Streaming: POST /api/v1/drafter/stream → SSE cu status per skill, apoi text streamed, apoi done cu metadata
Frontend: Selector tip contestație vizibil, legislație refs afișate, status pipeline în UI
Fallback: Test cu input minimal → output degradat dar funcțional
Clarification module neatins și funcțional