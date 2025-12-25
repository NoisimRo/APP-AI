# ExpertAP - Project Context

## ⚠️ STATUS CURENT (2025-12-25) - READY TO DEPLOY! 🚀

**Aplicația funcționează, TOATE scripturile database sunt create!**

| Component | Status | URL |
|-----------|--------|-----|
| Cloud Run | ✅ Running | https://expertap-api-850584928584.europe-west1.run.app/ |
| Health Check | ✅ OK | /health (indică "healthy") |
| API Docs | ✅ OK | /docs |
| Frontend UI | ✅ Se vede | / |
| Frontend Functions | ⚠️ Fără date | Aplicația funcționează, dar DB goală |
| Baza de Date | ✅ SCRIPTS READY | Scripturile create, trebuie rulate manual |
| Import Scripts | ✅ COMPLETE | Toate scripturile și docs gata |

**SOLUȚIE PREGĂTITĂ:** Toate scripturile pentru setup sunt create!
- ✅ Script Cloud SQL: `scripts/setup_cloud_sql.sh`
- ✅ Script import GCS: `scripts/import_decisions_from_gcs.py`
- ✅ Alembic migrations: `backend/alembic/versions/20251225_0001_initial_schema.py`
- ✅ Documentație completă: `QUICKSTART.md`, `docs/SETUP_DATABASE.md`
- ⏳ Trebuie rulate **manual** (vezi QUICKSTART.md)

**DATE DISPONIBILE:**
- **GCS Bucket:** `date-ap-raw/decizii-cnsc`
- **Conținut:** ~3000 decizii CNSC în format text
- **Format:** Conform convenției `BO{AN}_{NR_BO}_{COD_CRITICI}_CPV_{COD_CPV}_{SOLUTIE}.txt`

**PAȘI URMĂTORI (MANUAL - 15-20 min total):**
1. ✅ **DONE**: Scripturile create → Vezi `QUICKSTART.md`
2. ⏳ **TODO**: Rulează `./scripts/setup_cloud_sql.sh` (5 min)
3. ⏳ **TODO**: Conectează Cloud Run (2 min) → Vezi `docs/CLOUD_RUN_DATABASE_CONFIG.md`
4. ⏳ **TODO**: Importă date: `python scripts/import_decisions_from_gcs.py --create-tables` (10-15 min)
5. ⏳ **TODO**: Testare cu date reale

---

## Project Purpose and Scope

**ExpertAP** is a business intelligence platform for the Romanian public procurement ecosystem. It transforms unstructured data (CNSC decisions, legislation, jurisprudence) into competitive advantage for domain actors.

### Target Users

| Segment | Primary Pain Point | Killer Feature |
|---------|-------------------|----------------|
| Economic Operators | Time-consuming complaint drafting, rejection risk | Auto-generate complaints with exact citations |
| Contracting Authorities | Vulnerable documentation, weak counter-arguments | Red flags detector + validated counter-arguments |
| CNSC Counselors | Manual precedent search, repetitive drafting | Decision drafting assistant + semantic search |

### Business Model

**Freemium** differentiated by:
- Volume (queries/day)
- Features (free vs premium)
- Role (economic operator, authority, CNSC)

---

## Tech Stack

### Backend
| Component | Technology | Reasoning |
|-----------|------------|-----------|
| Framework | FastAPI (Python 3.11+) | Async, auto-docs, type hints |
| ORM | SQLAlchemy 2.0 | Modern async, mature ecosystem |
| Vector Search | pgvector / Qdrant | pgvector for MVP, Qdrant for scale |
| RAG Orchestration | LangChain / LlamaIndex | Flexible, LLM-agnostic |
| Task Queue | Celery + Redis | Background processing |

### Frontend
| Component | Technology | Reasoning |
|-----------|------------|-----------|
| Framework | Next.js 14 (App Router) | SSR, SEO, React ecosystem |
| UI Components | shadcn/ui | Accessible, customizable, OSS |
| Styling | TailwindCSS | Utility-first, rapid development |
| State | Zustand / React Query | Lightweight, smart caching |

### Infrastructure
| Component | Technology | Reasoning |
|-----------|------------|-----------|
| Cloud | Google Cloud Platform | Cost-effective, Vertex AI integration |
| Containers | Docker + Docker Compose | Portable, consistent environments |
| Database | PostgreSQL (Cloud SQL) | Reliable, pgvector support |
| Object Storage | Google Cloud Storage | Raw files, backups |
| Auth | Firebase Auth (MVP) / Keycloak (Production) | GCP integration / OSS enterprise |

### LLM Strategy (Agnostic)
| Provider | Use Case |
|----------|----------|
| Vertex AI (Gemini) | Primary - GCP native |
| OpenAI | Fallback |
| Anthropic | Alternative |
| Ollama | Local development/testing |

---

## Architecture Decisions

### 1. LLM-Agnostic Design
All LLM interactions go through an abstract interface, allowing provider switching without code changes.

### 2. RAG-Centric Responses
Every AI response must be grounded in our data (CNSC decisions, legislation). No generic LLM responses.

### 3. Zero Hallucination Policy
- Every citation is verified against the database
- Citations are VERBATIM extracts
- Links to original source provided
- Confidence level indicated

### 4. Microservices-Ready Monolith
Start as a well-structured monolith, designed to split into microservices when needed:
- Search Service
- Generation Service
- Analytics Service
- Admin Service

---

## Directory Structure

```
APP-AI/
├── backend/                    # Python FastAPI backend
│   ├── app/
│   │   ├── api/               # API routes
│   │   │   ├── v1/
│   │   │   │   ├── chat.py
│   │   │   │   ├── search.py
│   │   │   │   ├── generation.py
│   │   │   │   └── admin.py
│   │   │   └── deps.py        # Dependencies (auth, db)
│   │   ├── core/              # Core configuration
│   │   │   ├── config.py
│   │   │   ├── security.py
│   │   │   └── logging.py
│   │   ├── models/            # SQLAlchemy models
│   │   │   ├── decision.py
│   │   │   ├── user.py
│   │   │   └── conversation.py
│   │   ├── schemas/           # Pydantic schemas
│   │   │   ├── decision.py
│   │   │   └── chat.py
│   │   ├── services/          # Business logic
│   │   │   ├── llm/           # LLM abstraction
│   │   │   │   ├── base.py
│   │   │   │   ├── vertex.py
│   │   │   │   ├── openai.py
│   │   │   │   └── factory.py
│   │   │   ├── search.py
│   │   │   ├── generation.py
│   │   │   └── analytics.py
│   │   ├── db/                # Database
│   │   │   ├── session.py
│   │   │   └── init_db.py
│   │   └── main.py            # FastAPI app entry
│   ├── scripts/               # Utility scripts
│   │   ├── parse_decisions.py
│   │   └── seed_db.py
│   ├── tests/
│   ├── alembic/               # DB migrations
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                   # Next.js frontend (future)
│   └── ...
├── data/                       # Local data for development
│   └── decisions/             # Sample CNSC decisions
├── docker-compose.yml
├── .env.example
├── PROJECT_CONTEXT.md          # This file
├── CONTRIBUTING.md
├── TODO.md
└── README.md
```

---

## Key Conventions

### Naming
- **Python**: snake_case for functions/variables, PascalCase for classes
- **TypeScript**: camelCase for functions/variables, PascalCase for components
- **Files**: snake_case for Python, kebab-case for TypeScript/React
- **Database**: snake_case, plural for tables (e.g., `decisions`, `users`)

### Code Style
- **Python**: Follow PEP 8, use type hints everywhere
- **TypeScript**: Strict mode, explicit types for function params/returns
- **Docstrings**: Google style for Python

### Git
- **Branch naming**: `feature/description`, `fix/description`, `refactor/description`
- **Commits**: Conventional commits (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`)
- **PRs**: Require description, link to issue if applicable

### API Design
- RESTful endpoints
- Version prefix: `/api/v1/`
- Consistent error responses with error codes
- OpenAPI documentation auto-generated

---

## Current Focus

### Phase: Project Initialization
- [x] Analyze MVP codebase from Google AI Studio
- [ ] Set up backend project structure
- [ ] Configure Docker development environment
- [ ] Create initial CNSC decision parser
- [ ] Implement basic RAG pipeline

### Next Steps
1. Parse sample CNSC decisions into structured format
2. Set up PostgreSQL with pgvector
3. Create embedding pipeline
4. Implement basic semantic search
5. Build chatbot MVP with verified citations

---

## Data Sources

### CNSC Decisions (Primary)
- 10,000+ decisions in raw .txt format
- Need parsing to extract:
  - Case metadata (number, date, parties)
  - CPV codes
  - Criticism codes (D1-D7, R1-R7)
  - Ruling (ADMIS/RESPINS)
  - Legal arguments
  - Full text for RAG

### Future Data Sources
- Attribution documentation
- Offer examples
- Court decisions (appeals)
- CJUE jurisprudence
- Romanian procurement legislation

---

## Environment Variables

```bash
# Backend
DATABASE_URL=postgresql://user:pass@localhost:5432/expertap
REDIS_URL=redis://localhost:6379

# LLM Providers
VERTEX_AI_PROJECT=your-project
VERTEX_AI_LOCATION=europe-west1
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-...

# Auth
FIREBASE_PROJECT_ID=...
FIREBASE_PRIVATE_KEY=...

# Storage
GCS_BUCKET=expertap-data
```

---

## References

- [Legea 98/2016](https://legislatie.just.ro/Public/DetaliiDocument/178667) - Public Procurement Law
- [Legea 101/2016](https://legislatie.just.ro/Public/DetaliiDocument/178669) - Remedies Law
- [CNSC Portal](http://www.cnsc.ro/) - Official CNSC website
