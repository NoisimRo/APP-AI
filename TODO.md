# ExpertAP - TODO

## Current Sprint: Project Foundation

### In Progress
- [ ] Set up Docker development infrastructure
- [ ] Create backend project structure (FastAPI)
- [ ] Implement CNSC decision parser

### Blocked
_(none)_

---

## Backlog

### P0 - MVP Core (Must Have)

#### Infrastructure
- [ ] Docker Compose setup (PostgreSQL, Redis, API)
- [ ] PostgreSQL with pgvector extension
- [ ] Environment configuration (.env)
- [ ] Basic logging and error handling
- [ ] Health check endpoints

#### Data Pipeline
- [ ] CNSC decision parser (extract structured data from .txt)
- [ ] Decision metadata extraction (case number, date, parties)
- [ ] CPV code extraction
- [ ] Criticism code classification (D1-D7, R1-R7)
- [ ] Ruling extraction (ADMIS/RESPINS)
- [ ] Text chunking for RAG
- [ ] Embedding generation pipeline
- [ ] Database schema for decisions

#### Search (Chatbot Foundation)
- [ ] Semantic search endpoint (vector similarity)
- [ ] Hybrid search (semantic + keyword)
- [ ] Filter by metadata (CPV, criticism, ruling, article)
- [ ] Search result ranking
- [ ] Citation extraction and verification

#### Chatbot "Intreaba ExpertAP"
- [ ] Chat endpoint with conversation history
- [ ] RAG pipeline (retrieve → augment → generate)
- [ ] Citation verification (anti-hallucination)
- [ ] Confidence level indicator
- [ ] Suggested follow-up questions
- [ ] Rate limiting per tier (5/20/unlimited)

#### Authentication
- [ ] Firebase Auth integration
- [ ] User registration/login
- [ ] Role-based access control (RBAC)
- [ ] Tier management (free/premium)

### P1 - MVP Features (Should Have)

#### Legal Drafter (Contestation Generator)
- [ ] Input form (facts, authority args, legal grounds)
- [ ] Document upload (attribution docs, authority communication)
- [ ] Contestation type classification (documentation vs result)
- [ ] Criticism classification (D1-D7, R1-R7)
- [ ] Similar winning cases search
- [ ] Contestation structure generation (per Law 101/2016)
- [ ] VERBATIM citation insertion with verification
- [ ] .docx export

#### Red Flags Detector
- [ ] Document upload (terms of reference, data sheet)
- [ ] Restrictive clause identification
- [ ] Jurisprudence matching for each flag
- [ ] Suggested remediation text
- [ ] Risk level categorization (High/Medium/Low)
- [ ] Report generation

### P2 - Post-MVP Features

#### Litigation Predictor
- [ ] ML model for outcome prediction
- [ ] Feature extraction from case description
- [ ] Probability calculation with confidence interval
- [ ] Positive/negative factor analysis
- [ ] Recommended arguments
- [ ] Similar case references

#### Point of View Assistant (for Authorities)
- [ ] Contestation upload and parsing
- [ ] Attribution documentation upload
- [ ] Counter-argument generation per criticism
- [ ] VERBATIM citation from AC-winning cases
- [ ] Weak points identification
- [ ] Structured output generation

#### Decision Drafting Assistant (for CNSC)
- [ ] Multi-document upload (contestation, point of view, interventions)
- [ ] Automatic summarization per section
- [ ] Per-criticism analysis structure
- [ ] Jurisprudence suggestions (CNSC, courts, CJUE)
- [ ] Contradictory precedent identification
- [ ] CNSC template output

### P3 - Future Features

#### Trend Spotter
- [ ] Admission rate evolution per criticism type
- [ ] Automatic alerts for trend changes
- [ ] Comparative charts over time
- [ ] Jurisprudential "reversals" detection

#### Competitor Profile
- [ ] Per-competitor analysis (contestations filed)
- [ ] Success rate per competitor
- [ ] Frequently used arguments
- [ ] Relationship graph (competition patterns)

#### Clarification Assistant
- [ ] Attribution documentation analysis
- [ ] Ambiguous zone identification
- [ ] Clarification question suggestions
- [ ] Jurisprudence for evasive AC responses

#### Offer Drafting Assistant
- [ ] Document checklist generation
- [ ] Technical offer structure suggestion
- [ ] Rejection risk identification
- [ ] Sensitive area formulation suggestions

---

## Technical Debt
- [ ] Add comprehensive test suite
- [ ] Set up CI/CD pipeline
- [ ] Add API rate limiting
- [ ] Implement caching layer
- [ ] Add request/response logging
- [ ] Set up monitoring and alerting

---

## Completed

### 2024-12-24
- [x] Analyze MVP codebase from Google AI Studio
- [x] Create PROJECT_CONTEXT.md
- [x] Create CONTRIBUTING.md
- [x] Create TODO.md

---

## Notes

### MVP Scope
The MVP focuses on:
1. **Chatbot** - Core value proposition, grounded in CNSC decisions
2. **Semantic Search** - Foundation for all features
3. **Legal Drafter** - High-value premium feature for Economic Operators
4. **Red Flags Detector** - High-value premium feature for Authorities

### Key Success Metrics
- Latency: <5s for chat responses (p95)
- Citation accuracy: 100% (verified against database)
- Search latency: <500ms (p95)
- Uptime: 99.5%

### Data Considerations
- 10,000+ CNSC decisions to process
- Need robust parsing for inconsistent document formats
- Consider incremental processing for new decisions
- Plan for growing data volume

---

_Last updated: 2024-12-24_
