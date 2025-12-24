# Contributing to ExpertAP

## Development Workflow

### Getting Started

1. Clone the repository
2. Copy `.env.example` to `.env` and configure
3. Start the development environment:
   ```bash
   docker-compose up -d
   ```
4. Access services:
   - Backend API: http://localhost:8000
   - API Docs: http://localhost:8000/docs
   - PostgreSQL: localhost:5432
   - Redis: localhost:6379

### Development Cycle

1. **Pick a task** from TODO.md or create an issue
2. **Create a branch** from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Implement** with tests
4. **Test locally**:
   ```bash
   # Backend
   cd backend && pytest

   # Frontend
   cd frontend && npm test
   ```
5. **Commit** using conventional commits
6. **Push** and create PR

---

## Git Branching Strategy

### Branch Types

| Prefix | Purpose | Example |
|--------|---------|---------|
| `feature/` | New functionality | `feature/semantic-search` |
| `fix/` | Bug fixes | `fix/citation-verification` |
| `refactor/` | Code improvements | `refactor/llm-abstraction` |
| `docs/` | Documentation | `docs/api-endpoints` |
| `test/` | Test additions | `test/search-service` |
| `chore/` | Maintenance | `chore/update-deps` |

### Branch Flow

```
main
  │
  ├── feature/semantic-search
  │     └── PR → main
  │
  ├── fix/citation-bug
  │     └── PR → main
  │
  └── release/v1.0.0 (future)
```

### Rules

- `main` is always deployable
- All changes go through PRs
- PRs require at least one review
- Squash merge to keep history clean

---

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

### Types

| Type | Description |
|------|-------------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation |
| `style` | Formatting (no code change) |
| `refactor` | Code change (no feature/fix) |
| `test` | Adding tests |
| `chore` | Maintenance |

### Examples

```bash
feat(search): add semantic search endpoint
fix(generation): correct citation verification logic
docs(api): document chat endpoints
refactor(llm): extract provider abstraction
test(search): add unit tests for ranking
chore(deps): update fastapi to 0.109
```

---

## Testing Approach

### Backend (Python)

```
backend/
└── tests/
    ├── unit/           # Isolated function tests
    │   ├── services/
    │   └── utils/
    ├── integration/    # API endpoint tests
    │   └── api/
    └── conftest.py     # Shared fixtures
```

#### Running Tests

```bash
# All tests
pytest

# With coverage
pytest --cov=app --cov-report=html

# Specific test file
pytest tests/unit/services/test_search.py

# Verbose
pytest -v
```

#### Test Conventions

- Test file: `test_<module>.py`
- Test function: `test_<functionality>_<scenario>`
- Use fixtures for reusable setup
- Mock external services (LLM, database)

#### Example Test

```python
# tests/unit/services/test_search.py
import pytest
from app.services.search import SearchService

@pytest.fixture
def search_service():
    return SearchService(vector_db=MockVectorDB())

def test_semantic_search_returns_relevant_results(search_service):
    """Semantic search should return top-k relevant documents."""
    results = search_service.search("experiență similară", limit=5)

    assert len(results) <= 5
    assert all(r.score > 0.5 for r in results)

def test_semantic_search_empty_query_raises_error(search_service):
    """Empty query should raise ValueError."""
    with pytest.raises(ValueError):
        search_service.search("")
```

### Frontend (TypeScript)

```
frontend/
└── __tests__/
    ├── components/
    ├── hooks/
    └── utils/
```

---

## Code Review Guidelines

### For Authors

- [ ] Code follows project conventions
- [ ] Tests added for new functionality
- [ ] Documentation updated if needed
- [ ] No hardcoded secrets or credentials
- [ ] Error handling is appropriate
- [ ] Logging is meaningful

### For Reviewers

- [ ] Code is readable and maintainable
- [ ] Logic is correct
- [ ] Edge cases are handled
- [ ] Tests are meaningful (not just for coverage)
- [ ] No security vulnerabilities
- [ ] Performance is acceptable

### Review Checklist for ExpertAP-Specific

- [ ] LLM responses are grounded (no hallucinations)
- [ ] Citations are verified against database
- [ ] User permissions are checked
- [ ] Rate limits are respected
- [ ] Romanian language is handled correctly

---

## Multi-Session Consistency with Claude Code

When using Claude Code across multiple sessions:

### Starting a New Session

1. Read PROJECT_CONTEXT.md for project overview
2. Check TODO.md for current tasks and priorities
3. Review recent git commits for context:
   ```bash
   git log --oneline -20
   ```

### During Development

1. Update TODO.md when completing tasks
2. Commit frequently with clear messages
3. Document architectural decisions in PROJECT_CONTEXT.md

### Ending a Session

1. Commit all work in progress
2. Update TODO.md with:
   - Completed items (move to Completed section)
   - New items discovered
   - Current status notes
3. Push changes to remote

### Key Files to Reference

| File | Purpose |
|------|---------|
| `PROJECT_CONTEXT.md` | Architecture, decisions, conventions |
| `CONTRIBUTING.md` | This file - development workflow |
| `TODO.md` | Task tracking and priorities |
| `README.md` | Setup and run instructions |

---

## Architecture Notes

### Adding a New LLM Provider

1. Create provider class in `backend/app/services/llm/`:
   ```python
   # new_provider.py
   from .base import LLMProvider

   class NewProvider(LLMProvider):
       async def complete(self, prompt, context, temperature=0.1):
           # Implementation
           pass
   ```

2. Register in factory:
   ```python
   # factory.py
   providers["new_provider"] = NewProvider
   ```

3. Add config in `.env`:
   ```bash
   NEW_PROVIDER_API_KEY=...
   ```

### Adding a New API Endpoint

1. Create route in `backend/app/api/v1/`:
   ```python
   # new_endpoint.py
   from fastapi import APIRouter

   router = APIRouter()

   @router.get("/new-endpoint")
   async def new_endpoint():
       return {"message": "Hello"}
   ```

2. Register in `backend/app/api/v1/__init__.py`
3. Add tests in `backend/tests/integration/api/`
4. Document in OpenAPI (automatic with FastAPI)

### Database Migrations

```bash
# Create migration
cd backend
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

---

## Security Considerations

### Never Commit

- API keys or secrets
- Database credentials
- Private keys
- `.env` files (use `.env.example`)

### Always Do

- Validate user input
- Check permissions before actions
- Use parameterized queries
- Log security-relevant events
- Handle errors without exposing internals

---

## Questions?

- Check PROJECT_CONTEXT.md for architecture
- Check TODO.md for task status
- Open an issue for discussion
