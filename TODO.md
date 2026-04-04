# ExpertAP - TODO

## Feature Roadmap (2026-04-04)

Full details in `docs/FEATURE_ROADMAP_2026_04.md`.

### Sprint 1 — Infrastructure Foundation
- [ ] 6.2 Redis Rate Limiting — replace in-memory with Redis INCR + EXPIRE
- [ ] 6.1 Redis Cache Layer — activate Redis for embeddings, stats, search caching
- [ ] 6.5 Deep Health Check — validate DB, Redis, LLM, embedding service
- [ ] 6.6 DB Connection Pooling — pool tuning + slow query logging

### Sprint 2 — Intelligence & Analytics
- [ ] 1.2 Profil Complet CNSC — per-panel stats (C1-C20): win rates, CPV, criticism tendencies
- [ ] 1.1 Predictor Rezultat — predict ADMIS/RESPINS probability from case parameters
- [ ] 1.4 Analiza Comparativa — side-by-side comparison of 2-3 decisions

### Sprint 3 — AI Capabilities
- [ ] 4.2 Strategie Contestare — AI-proposed contestation strategy with success estimates
- [ ] 4.5 Verificator Conformitate — compliance checklist against legal requirements
- [ ] 4.1 Multi-Document Analysis — cross-document red flags and inconsistencies
- [ ] 4.3 NER Entitati — auto-extract metadata from procurement documents
- [ ] 4.4 Memorie Persistenta — persistent chat context across sessions

### Sprint 4 — Workflow & Case Management
- [ ] 2.2 Dosar Digital — case management linking all work artifacts
- [ ] 2.3 Alerte Decizii — email alerts for new matching CNSC decisions
- [ ] 3.3 Comentarii Documente — inline comments for collaborative review

### Sprint 5 — Data Moat
- [ ] 5.1 Import Curtea de Apel — court of appeal decisions (complete litigation lifecycle)
- [ ] 1.5 Harta Jurisprudentiala — knowledge graph of articles/decisions/criticisms

### Sprint 6 — Frontend & UX
- [ ] 8.1 Frontend Modular — split 6755-line index.tsx into multi-file React app
- [ ] 8.4 Dark Mode — full dark theme with system-preference detection

---

## Data Pipeline (existing)
- [ ] Run LLM analysis on all decisions: `python scripts/generate_analysis.py`
- [ ] Generate embeddings: `python scripts/generate_embeddings.py`
- [ ] Generate retroactive summaries: `python scripts/generate_summaries.py`
- [ ] Extract obiect_contract: `python scripts/extract_obiect_contract.py`
- [ ] Deduce CPV codes: `python scripts/deduce_cpv.py`
- [ ] Deploy: Push to `main` to trigger Cloud Build -> Cloud Run

---

## Deferred Features (build later)
See `docs/FEATURE_ROADMAP_2026_04.md` for full list (15 deferred features).

---

_Last updated: 2026-04-04_
