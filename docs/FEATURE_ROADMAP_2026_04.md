# ExpertAP - Feature Roadmap (Decision Record)

> **Date:** 2026-04-04
> **Context:** Feature exploration and decision-making session. Each feature was evaluated against actual production data (party names are anonymized, but complet/CPV/criticism codes/dates/procedure types are available).

---

## Data Reality Check

Before proposing features, we verified what data CNSC decisions actually contain:

| Field | Status | Coverage |
|-------|--------|----------|
| `autoritate_contractanta` | **ANONYMIZED** (always NULL) | 0% - dots replace names |
| `contestator` | **ANONYMIZED** (always NULL) | 0% - dots replace names |
| `complet` (CNSC panel) | **AVAILABLE** | 95%+ (C1-C20) |
| `coduri_critici` | **AVAILABLE** | 100% (from filename) |
| `cod_cpv` | **AVAILABLE** | 100% (from filename/text) |
| `solutie_contestatie` | **AVAILABLE** | 100% (ADMIS/RESPINS/ADMIS_PARTIAL) |
| `tip_procedura` | **AVAILABLE** | ~85% |
| `criteriu_atribuire` | **AVAILABLE** | ~90% |
| `data_decizie` | **AVAILABLE** | ~90% |
| `valoare_estimata` | **AVAILABLE** | ~70% |
| `motiv_respingere` | **AVAILABLE** | when RESPINS |
| `castigator_critica` (per criticism) | **AVAILABLE** | from LLM analysis |
| `argumentare_critica` (full reasoning) | **AVAILABLE** | from LLM analysis |

**Consequence:** "Authority Profile" feature is IMPOSSIBLE with anonymized data. Replaced with "CNSC Panel Profile" which uses the available `complet` field.

---

## APPROVED FEATURES (19) - To Build

### Sprint 1: Infrastructure Foundation
*Quick wins that improve reliability and performance*

#### 6.2 Redis Rate Limiting (Complexity: S)
- **What:** Replace in-memory rate limiting with Redis-backed counters
- **Why:** Current `rate_limiter.py` uses in-memory dict that resets on every Cloud Run restart and doesn't work across instances. This is a production bug.
- **How:** Modify `backend/app/core/rate_limiter.py` to use Redis INCR + EXPIRE. Fallback to in-memory if Redis unavailable.
- **Files:** `backend/app/core/rate_limiter.py`, new `backend/app/core/redis.py`

#### 6.1 Redis Cache Layer (Complexity: M)
- **What:** Activate Redis (already in docker-compose) for caching embeddings, stats, search results
- **Why:** Every query re-computes embeddings and re-executes full RAG pipeline. Redis is defined but NOT USED anywhere in code.
- **How:** Add `aioredis` client init. Cache decorator for: embedding vectors by query hash (biggest cost saving), stats endpoints (TTL 5min), search results (TTL 1h).
- **Files:** new `backend/app/core/redis.py`, `backend/app/services/rag.py`, `backend/app/services/embedding.py`, `backend/app/api/v1/decisions.py` (stats)

#### 6.5 Deep Health Check (Complexity: S)
- **What:** Health endpoint that validates DB, Redis, LLM provider, and embedding service
- **Why:** Current `/health` just returns "healthy" without checking anything. Cloud Run needs proper readiness probes.
- **How:** New `GET /api/v1/health/deep` with per-component status + latency. Tests: DB SELECT 1, Redis PING, embedding dry-run.
- **Files:** `backend/app/main.py` or new `backend/app/api/v1/health.py`

#### 6.6 DB Connection Pooling (Complexity: S)
- **What:** SQLAlchemy pool tuning + slow query logging + index review
- **Why:** Default pool settings not optimized for Cloud Run cold starts and concurrency patterns.
- **How:** Configure pool_size, max_overflow, pool_recycle in `session.py`. Add slow query logging (>500ms).
- **Files:** `backend/app/db/session.py`

---

### Sprint 2: Intelligence & Analytics
*Competitive moat through data-driven insights*

#### 1.2 Profil Complet CNSC - Panel Profile (Complexity: M)
- **What:** Per-panel statistics for each CNSC complet (C1-C20)
- **Why:** Lawyers want to know tendencies of the panel assigned to their case. Data supports it: `complet` is populated 95%+, already indexed.
- **How:** Aggregate queries over `decizii_cnsc.complet`. Stats: ADMIS/RESPINS rates, CPV domains handled, criticism code tendencies. `GET /api/v1/analytics/panel-profile/{complet}`
- **Data:** `complet` (95%+), `solutie_contestatie` (100%), `cod_cpv` (100%), `coduri_critici` (100%)
- **Files:** new `backend/app/api/v1/analytics.py`, `index.tsx` (new analytics page)

#### 1.1 Predictor Rezultat Contestatie (Complexity: L)
- **What:** Predict ADMIS/RESPINS probability given case parameters
- **Why:** The most valuable intelligence feature - tells lawyers their chances before filing.
- **How:** Feature-engineer over available dimensions (complet, cod_critica, cpv, tip_procedura, criteriu_atribuire). Inject historical stats into LLM prompt for reasoning. `POST /api/v1/analytics/predict-outcome`
- **Data:** All non-anonymized fields (complet, CPV, criticism codes, procedure type, award criteria, historical win rates per dimension)
- **Files:** new `backend/app/services/predictor.py`, new `backend/app/api/v1/analytics.py`

#### 1.4 Analiza Comparativa Decizii (Complexity: M)
- **What:** Side-by-side comparison of 2-3 decisions on the same legal issue
- **Why:** Lawyers need to understand why similar cases had different outcomes.
- **How:** Load `argumentare_critica` for selected decisions, send to LLM with structured diff prompt highlighting reasoning divergences. `POST /api/v1/analytics/compare-decisions`
- **Data:** Full `argumentare_critica` per decision (arguments, reasoning, winner per criticism)
- **Files:** `backend/app/api/v1/analytics.py`, `index.tsx`

---

### Sprint 3: AI Capabilities
*Differentiation through advanced AI features*

#### 4.2 Generare Strategie Contestare (Complexity: L)
- **What:** AI proposes full contestation strategy: criticism codes, articles, precedents, success estimates
- **Why:** Transforms the platform from research tool to strategic advisor.
- **How:** Combines outcome prediction (1.1) + RAG search + historical win rates. Per-criticism recommendation with supporting decisions and confidence. `POST /api/v1/strategy/generate`
- **Files:** new `backend/app/services/strategy_generator.py`, `backend/app/api/v1/` new endpoint, `index.tsx`

#### 4.5 Verificator Conformitate (Complexity: L)
- **What:** Upload procurement documentation, AI checks against applicable legal requirements
- **Why:** Proactive compliance checking saves clients from filing defective procedures.
- **How:** Build checklist from `legislatie_fragmente`. Two-pass: (1) identify applicable requirements by procedure type, (2) verify document compliance. Output: compliance matrix with pass/fail per legal article.
- **Files:** new `backend/app/services/compliance_checker.py`, new endpoint, `index.tsx`

#### 4.1 Analiza Multi-Document (Complexity: L)
- **What:** Upload entire procurement dossier (multiple files) for unified cross-document analysis
- **Why:** Red flags can span across documents (documentation vs evaluation report inconsistencies).
- **How:** Extend `document_processor.py` for multi-file. New service that: (1) extract text from each, (2) run red flags per doc, (3) cross-document consistency LLM prompt. `POST /api/v1/analysis/multi-document`
- **Files:** `backend/app/services/document_processor.py`, new `backend/app/services/multi_document_analyzer.py`, new endpoint

#### 4.3 Extragere Automata Entitati (Complexity: M)
- **What:** NER pipeline extracting structured metadata from uploaded procurement documents
- **Why:** Auto-populates drafter/red flags forms, saving manual data entry.
- **How:** LLM-based structured extraction with JSON schema prompt. Extract: CPV, procedure type, estimated value, award criteria, deadlines. Store in document `metadata` JSONB.
- **Files:** `backend/app/services/document_processor.py`, `backend/app/api/v1/documents.py`

#### 4.4 Asistent cu Memorie Persistenta (Complexity: M)
- **What:** Chat remembers context across sessions
- **Why:** Personalization increases stickiness. Also fixes existing 501 TODO on conversation retrieval.
- **How:** New `user_context` table. Load user facts and prepend to system prompt. Summarize conversation history into persistent context entries.
- **Files:** `backend/app/models/decision.py` (new model), `backend/app/api/v1/chat.py`, `backend/app/services/rag.py`

---

### Sprint 4: Workflow & Case Management
*Sticky workflows that create daily habits*

#### 2.2 Dosar Digital - Case Management (Complexity: L)
- **What:** Digital case files linking all work artifacts under one case
- **Why:** Foundation for all workflow features. Makes the platform indispensable for case organization.
- **How:** New `dosare` table (user_id, case metadata, client info, deadlines). Add nullable `dosar_id` FK to `conversatii`, `documente_generate`, `red_flags_salvate`, `training_materials`. CRUD under `/api/v1/dosare/`.
- **Files:** `backend/app/models/decision.py`, new `backend/app/api/v1/dosare.py`, `index.tsx`

#### 2.3 Alerte Noi Decizii (Complexity: M)
- **What:** Email alerts when new CNSC decisions match user-defined criteria
- **Why:** Keeps users engaged daily. Critical for tracking relevant jurisprudence evolution.
- **How:** New `alert_rules` table (user_id, filters JSONB, active, last_notified). Extend daily pipeline to match new decisions against rules. Use existing `email_service.py`.
- **Depends on:** Daily automation pipeline (already planned in CLAUDE.md)
- **Files:** `backend/app/models/decision.py`, new `backend/app/api/v1/alerts.py`, `scripts/pipeline.py`

#### 3.3 Comentarii pe Documente (Complexity: M)
- **What:** Inline comments on generated documents for collaborative review
- **Why:** Law firms need collaborative review before filing contestations.
- **How:** New `document_comments` table (document_id, user_id, anchor start/end, text, resolved). Polling for real-time updates. CRUD under `/api/v1/documents/{id}/comments/`.
- **Files:** `backend/app/models/decision.py`, `backend/app/api/v1/documents.py` or new file, `index.tsx`

---

### Sprint 5: Data Moat
*Irreplaceable data advantage*

#### 5.1 Import Decizii Curtea de Apel (Complexity: XL)
- **What:** Court of appeal decisions (plangeri against CNSC) creating complete litigation lifecycle
- **Why:** Strongest possible data moat. Users see if CNSC decisions were upheld/overturned. Nobody else has this.
- **How:** New `decizii_curtea_apel` table with FK to `decizii_cnsc`. New parser for court decision format. New import script. Extend RAG to search both CNSC and court decisions.
- **Files:** `backend/app/models/decision.py`, new `backend/app/services/court_parser.py`, new `scripts/import_court_decisions.py`, `backend/app/services/rag.py`

#### 1.5 Harta Jurisprudentiala (Complexity: L)
- **What:** Visual knowledge graph of legal articles, decisions, and criticism codes
- **Why:** Unique visualization that no competitor offers. Shows how jurisprudence interconnects.
- **Depends on:** Planned `referinte_legislative` table (already in CLAUDE.md Future Plan)
- **How:** Aggregate co-occurrence matrices from referinte_legislative. Expose as graph data. D3.js/vis.js force-directed graph in frontend.
- **Files:** new `backend/app/api/v1/analytics.py` endpoints, `index.tsx` (graph visualization)

---

### Sprint 6: Frontend & UX
*Developer productivity + user experience*

#### 8.1 Frontend Modular (Complexity: L)
- **What:** Split 6,755-line `index.tsx` into multi-file React app
- **Why:** Prerequisite for all future frontend features. Current monolith is hard to maintain.
- **How:** React Router for navigation. Extract each page (Dashboard, DataLake, Chat, Drafter, RedFlags, Training, Settings, etc.) into separate component files. React.lazy + Suspense for code splitting. Shared components in `/components`.
- **Files:** `index.tsx` -> split into `src/pages/*.tsx`, `src/components/*.tsx`, `src/App.tsx`

#### 8.4 Dark Mode (Complexity: S)
- **What:** Full dark theme with system-preference auto-detection
- **Why:** Lawyers work late hours. TailwindCSS already supports dark mode.
- **How:** Add `class="dark"` toggle on root element. Persist to localStorage. Auto-detect system preference. Audit all color classes for dark variants.
- **Files:** `index.tsx` (or split component files after 8.1)

---

## DEFERRED FEATURES (15) - Build Later

| # | Feature | Reason for Deferral |
|---|---------|-------------------|
| 1.3 | Trend Analytics | Good but lower priority than panel profiles and prediction |
| 2.4 | Audit Trail | Build after core features stabilize |
| 2.5 | Sabloane Personalizate | Build after drafter usage patterns are clear |
| 2.6 | Notite pe Decizii | Nice-to-have, not critical path |
| 3.1 | Organizatii & Echipe | XL complexity, build after individual user features mature |
| 3.2 | Partajare Documente | Depends on Dosar Digital (2.2) |
| 4.6 | Rezumat Executiv | Existing rezumat sufficient for now |
| 5.2 | Monitorizare SEAP | Out of scope for current litigation focus |
| 5.3 | API Enterprise | Build when enterprise customers exist |
| 5.4 | Legislatie Auto-update | Build after daily automation pipeline |
| 6.3 | Observabilitate | Build after Redis infra is solid |
| 6.4 | Background Task Queue | Build when features that need it are done |
| 7.1 | Registru Clienti (CRM) | Build after Dosar Digital |
| 7.2 | Rapoarte Client | Build after Dosar Digital |
| 8.2+8.3 | PWA + Command Palette | Polish features for later |

## REJECTED FEATURES (2)

| # | Feature | Reason |
|---|---------|--------|
| 2.1 | Calculator Termene Legale | User decided not to build |
| 5.5 | Export Structurat CSV/JSON | User decided not to build |

## INVALIDATED FEATURES (1)

| # | Feature | Reason |
|---|---------|--------|
| -- | Profil Autoritate Contractanta | **IMPOSSIBLE** - `autoritate_contractanta` is 100% anonymized in CNSC decisions (party names replaced with "........." dots). Field is always NULL in DB. Replaced by 1.2 Panel Profile. |

---

## Implementation Order Summary

```
Sprint 1 (Foundation):   6.2 -> 6.1 -> 6.5 -> 6.6
Sprint 2 (Intelligence): 1.2 -> 1.1 -> 1.4
Sprint 3 (AI):           4.2 -> 4.5 -> 4.1 -> 4.3 -> 4.4
Sprint 4 (Workflow):      2.2 -> 2.3 -> 3.3
Sprint 5 (Data Moat):    5.1 -> 1.5
Sprint 6 (UX):           8.1 -> 8.4
```

**Total: 19 features across 6 sprints**
