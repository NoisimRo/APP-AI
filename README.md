# ExpertAP

**Business Intelligence Platform for Romanian Public Procurement**

ExpertAP transforms unstructured CNSC (National Council for Solving Complaints) decisions into actionable intelligence for public procurement professionals.

## Features

- **AI Chatbot** - Ask questions in natural language, get answers grounded in CNSC jurisprudence
- **Semantic Search** - Find relevant decisions using meaning, not just keywords
- **Legal Drafter** - Auto-generate complaints with verified citations
- **Red Flags Detector** - Identify restrictive clauses in procurement documentation

## 🚀 Cloud Deployment (Production)

**Live Application:** https://expertap-api-850584928584.europe-west1.run.app/

The application is deployed on Google Cloud Platform (Cloud Run). To set up the database and import data:

**📖 See [QUICKSTART.md](QUICKSTART.md) for complete setup instructions (15-20 minutes)**

Quick overview:
1. Run `./scripts/setup_cloud_sql.sh` to create Cloud SQL instance
2. Connect Cloud Run to database
3. Import ~3000 CNSC decisions: `python scripts/import_decisions_from_gcs.py`

For detailed instructions, see:
- [QUICKSTART.md](QUICKSTART.md) - Quick 3-step guide
- [docs/SETUP_DATABASE.md](docs/SETUP_DATABASE.md) - Detailed setup
- [docs/CLOUD_RUN_DATABASE_CONFIG.md](docs/CLOUD_RUN_DATABASE_CONFIG.md) - Cloud Run configuration

## Quick Start (Local Development)

### Prerequisites

- Docker & Docker Compose
- Node.js 18+ (for frontend development)
- Python 3.11+ (for backend development without Docker)

### Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/your-org/APP-AI.git
   cd APP-AI
   ```

2. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your API keys
   ```

3. **Start with Docker**
   ```bash
   docker-compose up -d
   ```

4. **Access the application**
   - API: http://localhost:8000
   - API Docs: http://localhost:8000/docs
   - Health Check: http://localhost:8000/health

### Development Setup (without Docker)

#### Backend
```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the server
uvicorn app.main:app --reload
```

#### Frontend (MVP - Google AI Studio export)
```bash
# The frontend MVP is in the root directory
npm install
npm run dev
```

## Project Structure

```
APP-AI/
├── backend/                 # Python FastAPI backend
│   ├── app/
│   │   ├── api/            # API routes
│   │   ├── core/           # Configuration
│   │   ├── models/         # Database models
│   │   ├── services/       # Business logic
│   │   └── main.py
│   ├── tests/
│   └── Dockerfile
├── frontend/                # Next.js frontend (future)
├── data/                    # Local data for development
│   └── decisions/          # CNSC decision files
├── docker-compose.yml
├── PROJECT_CONTEXT.md       # Architecture & decisions
├── CONTRIBUTING.md          # Development workflow
└── TODO.md                  # Task tracking
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/v1/chat` | POST | Chat with AI |
| `/api/v1/search/semantic` | POST | Semantic search |
| `/api/v1/decisions` | GET | List decisions |
| `/api/v1/decisions/{id}` | GET | Get decision by ID |

See full API documentation at http://localhost:8000/docs

## Configuration

Key environment variables (see `.env.example` for all options):

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `GEMINI_API_KEY` | Google Gemini API key |
| `OPENAI_API_KEY` | OpenAI API key (alternative) |

## Running Tests

```bash
cd backend
pytest
pytest --cov=app --cov-report=html  # With coverage
```

## Documentation

### Getting Started
- **[QUICKSTART.md](QUICKSTART.md)** - Quick setup guide for cloud deployment ⭐
- [docs/SETUP_DATABASE.md](docs/SETUP_DATABASE.md) - Detailed database setup
- [docs/CLOUD_RUN_DATABASE_CONFIG.md](docs/CLOUD_RUN_DATABASE_CONFIG.md) - Cloud Run configuration

### Development
- [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) - Architecture, tech stack, conventions
- [CONTRIBUTING.md](CONTRIBUTING.md) - Development workflow, git strategy
- [TODO.md](TODO.md) - Feature backlog and progress

## Tech Stack

**Backend**
- FastAPI (Python 3.11+)
- PostgreSQL + pgvector
- SQLAlchemy 2.0
- LangChain

**Frontend**
- Next.js 14 (planned)
- React 19
- TailwindCSS

**LLM Providers** (abstracted)
- Google Gemini (primary)
- OpenAI (fallback)
- Anthropic (alternative)

## License

Proprietary - All rights reserved

## Contact

For questions about this project, please open an issue.
