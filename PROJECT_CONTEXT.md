# ExpertAP - Project Context

## Status (March 2025) - 10 Decisions Imported, Testing Phase

**Application is live and functional with 10 test decisions.**

| Component | Status | Notes |
|-----------|--------|-------|
| Cloud Run | Running | europe-west1 |
| Database | 10 decisions | PostgreSQL with pgvector |
| AI Assistant | Working | Gemini 2.5 Flash via RAG |
| Data Lake | Working | Browse/search all decisions |
| Jurisprudență RAG | Working | Semantic search over decisions |
| Embeddings | Generated | HNSW indexes on ArgumentareCritica |
| Import Script | Ready | Can scale to ~3000 decisions |

**Data Pipeline:**
- **GCS Bucket:** `date-expert-app/decizii-cnsc` (~3000 CNSC decisions)
- **Imported:** 10 decisions (test batch)
- **LLM Analysis:** Extracts structured ArgumentareCritica per criticism
- **Embeddings:** Generated via Gemini embedding-001 (768 dims)

**Next Step:** Validate data quality on 10 decisions, then import full ~3000.

---

## Project Purpose

**ExpertAP** is a business intelligence platform for Romanian public procurement. It transforms unstructured CNSC decisions into actionable intelligence via semantic search and AI-powered analysis.

### Target Users

| Segment | Pain Point | Feature |
|---------|-----------|---------|
| Economic Operators | Complaint drafting | Auto-generate with citations |
| Contracting Authorities | Documentation gaps | Red flags detector |
| CNSC Counselors | Precedent search | Semantic search + memo generation |

---

## Tech Stack

### Backend
- **Framework:** FastAPI (Python 3.11+), async
- **ORM:** SQLAlchemy 2.0 (async)
- **Vector Search:** pgvector with HNSW indexes
- **LLM:** Google Gemini 2.5 Flash (via google-genai SDK)
- **Embeddings:** Gemini embedding-001 (768 dims)

### Frontend
- **Framework:** React 19 + Vite (single index.tsx)
- **Styling:** TailwindCSS (utility classes)
- **Icons:** lucide-react

### Infrastructure
- **Cloud:** Google Cloud Platform
- **Database:** PostgreSQL (Cloud SQL) with pgvector
- **Storage:** Google Cloud Storage (raw decision files)
- **Deployment:** Cloud Run (auto-triggered from GitHub main branch)

---

## Architecture

### Data Flow
1. Raw `.txt` decisions in GCS bucket
2. Import script parses → `DecizieCNSC` table
3. LLM analyzes full text → `ArgumentareCritica` table (per-criticism chunks)
4. Embeddings generated for each ArgumentareCritica row
5. User queries → vector cosine search on ArgumentareCritica
6. Matched chunks provide context → Gemini generates response
7. Citations extracted and verified against database

### Key Tables
- **DecizieCNSC:** Main decision (metadata + full text)
- **ArgumentareCritica:** Per-criticism argumentation (primary RAG search unit)
- **SectiuneDecizie:** Decision sections (alternative chunking)
- **CitatVerbatim:** Exact quotes
- **ReferintaArticol:** Legal article references
- **NomenclatorCPV:** CPV code reference table

### Search Strategy
1. Direct BO lookup when query contains BO references (e.g., BO2025_1011)
2. Vector cosine search on ArgumentareCritica embeddings
3. Keyword ILIKE fallback when no embeddings available

---

## Directory Structure

```
APP-AI/
├── backend/                    # Python FastAPI backend
│   ├── app/
│   │   ├── api/v1/            # API routes (chat, decisions, search, ragmemo, etc.)
│   │   ├── core/              # Config, logging, security
│   │   ├── models/            # SQLAlchemy models (decision.py)
│   │   ├── schemas/           # Pydantic schemas
│   │   ├── services/          # Business logic
│   │   │   ├── llm/           # LLM providers (gemini.py, base.py)
│   │   │   ├── rag.py         # RAG pipeline (search + generation)
│   │   │   ├── embedding.py   # Embedding generation
│   │   │   ├── analysis.py    # LLM decision analysis
│   │   │   └── parser.py      # Decision text parser
│   │   ├── db/                # Database session management
│   │   └── main.py            # FastAPI app entry
│   ├── alembic/               # DB migrations
│   ├── requirements.txt
│   └── Dockerfile
├── scripts/                    # Import & utility scripts
│   ├── import_decisions_from_gcs.py  # Main import pipeline
│   └── generate_embeddings.py        # Batch embedding generation
├── index.tsx                   # Frontend (React single-file app)
├── package.json
├── vite.config.ts
├── archive/                    # Archived session notes
├── docs/                       # Deployment & setup guides
└── PROJECT_CONTEXT.md          # This file
```

---

## Key Conventions

### Naming
- **Python:** snake_case functions/variables, PascalCase classes
- **TypeScript:** camelCase functions/variables, PascalCase components
- **Database:** snake_case, descriptive Romanian names (e.g., `decizii_cnsc`)

### Git
- **Commits:** Conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`)
- **Deployment:** Merge PR to `main` → auto Cloud Build → Cloud Run

### API Design
- RESTful, prefix `/api/v1/`
- OpenAPI docs at `/docs`

---

## Features

### Working
- [x] AI Assistant Chat (RAG with vector search + direct BO lookup)
- [x] Data Lake (browse/search/filter decisions)
- [x] Jurisprudență RAG (semantic search memo generation)
- [x] Red Flags Detector (analyze procurement docs)
- [x] Drafter Contestații (complaint structure generation)
- [x] Clarificări (clarification requests)
- [x] Dashboard (stats overview)
- [x] Decision import from GCS
- [x] LLM analysis (ArgumentareCritica extraction)
- [x] Embedding generation (Gemini embedding-001)

### Pending
- [ ] Full import of ~3000 decisions
- [ ] Authentication (Firebase)
- [ ] Hybrid search (semantic + keyword combined)
- [ ] Performance optimization for large dataset

---

## Environment Variables

```bash
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/expertap
GEMINI_API_KEY=...           # Google AI Studio API key
GCS_BUCKET=date-expert-app   # GCS bucket name
```

---

## References

- [Legea 98/2016](https://legislatie.just.ro/Public/DetaliiDocument/178667) - Public Procurement Law
- [Legea 101/2016](https://legislatie.just.ro/Public/DetaliiDocument/178669) - Remedies Law
- [CNSC Portal](http://www.cnsc.ro/) - Official CNSC website
