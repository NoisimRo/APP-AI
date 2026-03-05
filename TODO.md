# ExpertAP - TODO

## Current Phase: Data Quality Validation (10 decisions)

Before scaling to ~3000 decisions, validate the following on the 10 test decisions.

### Data Quality Checks
- [ ] Verify all 10 decisions have correct metadata (numar_decizie, data, parties)
- [ ] Verify ArgumentareCritica extraction quality (per-criticism breakdown)
- [ ] Verify embeddings are generated for all ArgumentareCritica rows
- [ ] Test AI Assistant can find and analyze each decision by BO number
- [ ] Test semantic search returns relevant results for common procurement topics
- [ ] Verify citation extraction accuracy in chat responses

### Import Full Dataset
- [ ] Import all ~3000 decisions from GCS: `python scripts/import_decisions_from_gcs.py --analyze`
- [ ] Generate embeddings: `python scripts/generate_embeddings.py`
- [ ] Verify Data Lake shows all decisions
- [ ] Test search performance with full dataset

---

## Pending Features

### P0 - Critical
- [ ] Full dataset import (~3000 decisions)
- [ ] Validate response quality with full dataset

### P1 - Important
- [ ] Authentication (Firebase Auth)
- [ ] Hybrid search (combine semantic + keyword scores)
- [ ] Streaming responses in chat (SSE)

### P2 - Nice to Have
- [ ] Performance optimization for large dataset
- [ ] Export functionality (PDF memos)
- [ ] Conversation history persistence
- [ ] User feedback on AI responses

---

## Deployment

**Production URL:** https://expertap-api-850584928584.europe-west1.run.app/

**Deploy process:** Merge PR to `main` → auto Cloud Build → Cloud Run

---

_Last updated: 2025-03-05_
