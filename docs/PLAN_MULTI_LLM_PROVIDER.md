# Plan: Multi-Provider LLM Support (Gemini + Claude + future providers)

**Status:** Future feature — not yet started
**Created:** 2026-03-07
**Priority:** Medium (to implement after current pipeline/import tasks are complete)

---

## Context

ExpertAP currently hardcodes Google Gemini as the sole LLM provider. The `GeminiProvider` is imported directly in 6+ files, bypassing the existing (but unused) factory pattern. The goal is:
- A **provider-agnostic architecture** where switching LLM is seamless
- A **Settings page** in the frontend where users select provider, model, and enter API keys
- Start with **Gemini + Claude**, designed so adding OpenAI/others later is trivial
- **Embeddings stay on Gemini** always (Claude has no embedding API; switching embedding models would require re-generating all DB vectors)
- **API keys stored in database** (encrypted) for persistence across sessions

---

## Step 1: Database — Settings Table + Encryption

**File:** `backend/app/models/decision.py` (add new model)

Add a `LLMSettings` table:
```python
class LLMSettings(Base):
    __tablename__ = "llm_settings"
    id = Column(Integer, primary_key=True, default=1)
    active_provider = Column(String(30), default="gemini")  # "gemini", "anthropic", "openai"
    active_model = Column(String(100), nullable=True)  # e.g. "claude-sonnet-4-20250514", null = provider default
    gemini_api_key_enc = Column(Text, nullable=True)  # encrypted
    anthropic_api_key_enc = Column(Text, nullable=True)
    openai_api_key_enc = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
```

- Single-row table (id=1 always) — global app settings
- API keys encrypted with Fernet symmetric encryption
- Encryption key from env var `LLM_SETTINGS_KEY` (auto-generated on first run if missing)

**File:** `backend/app/core/encryption.py` (new)
- `encrypt_value(plaintext) -> str` and `decrypt_value(ciphertext) -> str`
- Uses `cryptography.fernet.Fernet` with key from `LLM_SETTINGS_KEY` env var
- If env var not set, generate a key and log a warning (dev mode)

**File:** `backend/requirements.txt`
- Add `cryptography` package

---

## Step 2: Backend — Claude (Anthropic) Provider

**File:** `backend/app/services/llm/anthropic.py` (new)

Implement `AnthropicProvider(LLMProvider)`:
- Constructor: takes `model` (default: `"claude-sonnet-4-20250514"`), `api_key` (optional, falls back to env var)
- Uses `anthropic.AsyncAnthropic` client
- `complete()`: Uses `client.messages.create()` with proper `system` parameter and `messages` format
- `stream()`: Uses `client.messages.stream()` and yields text deltas
- `embed()`: **Raises NotImplementedError** — embeddings always use Gemini
- `_build_messages()`: Converts the (prompt, context, system_prompt) interface into Claude's native messages format (system as top-level param, user message with context + query)

Key differences from Gemini:
- Claude API uses structured `messages` array with roles, not a single concatenated string
- Claude has a native `system` parameter (no need for `<system>` tags)
- Claude uses `max_tokens` (required), not `max_output_tokens`

**File:** `backend/requirements.txt`
- Add `anthropic>=0.40.0`

---

## Step 3: Backend — Refactor Factory + Provider Resolution

**File:** `backend/app/services/llm/factory.py`

Refactor `get_llm_provider()` to:
1. Accept optional `api_key` parameter (for per-request keys from DB)
2. Add `"anthropic"` branch that creates `AnthropicProvider`
3. Add a new function `get_active_llm_provider(session)` that:
   - Reads `LLMSettings` from DB
   - Decrypts the relevant API key
   - Calls `get_llm_provider(provider_type, model=..., api_key=...)`
   - Falls back to env var-based detection if no DB settings exist
4. Add `get_embedding_provider(api_key=None)` that always returns `GeminiProvider` (embeddings always on Gemini)
5. **Clear the provider cache** when settings change (add `clear_provider_cache()` function)

**File:** `backend/app/services/llm/__init__.py`
- Export new functions: `get_active_llm_provider`, `get_embedding_provider`, `clear_provider_cache`

---

## Step 4: Backend — Settings API Endpoint

**File:** `backend/app/api/v1/settings.py` (new)

Three endpoints:

### `GET /api/v1/settings/llm`
Returns current LLM settings (without decrypted keys):
```json
{
  "active_provider": "gemini",
  "active_model": null,
  "providers": {
    "gemini": {"configured": true, "models": ["gemini-2.5-flash", "gemini-2.5-pro"]},
    "anthropic": {"configured": false, "models": ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-haiku-4-5-20251001"]},
    "openai": {"configured": false, "models": ["gpt-4o", "gpt-4o-mini"]}
  }
}
```
- `configured: true` means an API key exists (either in DB or env var)
- Model lists are hardcoded per provider (simple, no API call needed)

### `PUT /api/v1/settings/llm`
Request body:
```json
{
  "active_provider": "anthropic",
  "active_model": "claude-sonnet-4-20250514",
  "api_key": "sk-ant-..."
}
```
- Validates provider name
- Encrypts and stores API key in DB
- Sets active provider + model
- Clears provider cache
- Returns updated settings (without key)

### `POST /api/v1/settings/llm/test`
Tests the configured provider by sending a simple completion:
```json
{"success": true, "provider": "anthropic", "model": "claude-sonnet-4-20250514", "response_time_ms": 450}
```

**File:** `backend/app/api/v1/__init__.py`
- Add settings router: `api_router.include_router(settings.router, prefix="/settings", tags=["settings"])`

---

## Step 5: Backend — Refactor All Services to Use Factory

Replace all direct `GeminiProvider` imports with factory-based resolution.

### Files to refactor:
- `backend/app/services/rag.py` — use factory, separate chat/embedding providers
- `backend/app/services/analysis.py` — use factory
- `backend/app/services/redflags_analyzer.py` — use factory
- `backend/app/services/embedding.py` — default to `get_embedding_provider()` (always Gemini)
- `backend/app/api/v1/drafter.py` — replace `GeminiProvider` → factory
- `backend/app/api/v1/clarification.py` — replace `GeminiProvider` → factory
- `backend/app/api/v1/chat.py` — pass provider to RAGService
- `backend/app/api/v1/redflags.py` — pass provider to RedFlagsAnalyzer

**Pattern for endpoints:**
```python
@router.post("/")
async def endpoint(request: Request, session: AsyncSession = Depends(get_session)):
    llm = await get_active_llm_provider(session)  # reads settings from DB
    embedding = get_embedding_provider()  # always Gemini from env var
    service = SomeService(llm_provider=llm, embedding_provider=embedding)
```

---

## Step 6: Frontend — Settings Page

**File:** `index.tsx`

### 6a. Add 'settings' to AppMode
```typescript
type AppMode = '...' | 'settings';
```

### 6b. Implement Settings page layout
```
┌─────────────────────────────────────────────┐
│  Setări Model LLM                           │
│                                             │
│  Provider activ:  [▼ Gemini / Claude / ...] │
│  Model:           [▼ model dropdown      ]  │
│                                             │
│  ┌─────────────────────────────────────┐    │
│  │ Chei API                            │    │
│  │ Gemini API Key:    [••••] ✓ Config  │    │
│  │ Anthropic API Key: [____] ✗ Neconf  │    │
│  └─────────────────────────────────────┘    │
│                                             │
│  [Salvează]  [Testează conexiunea]          │
│  Status: ✓ Gemini 2.5 Flash - Operațional  │
└─────────────────────────────────────────────┘
```

### 6c. Dynamic sidebar status
Replace hardcoded "Gemini 3 Pro - System Operational" with active provider + model from settings API.

---

## Files Summary

| File | Action |
|------|--------|
| `backend/app/models/decision.py` | Add `LLMSettings` model |
| `backend/app/core/encryption.py` | **New** — Fernet encrypt/decrypt |
| `backend/app/services/llm/anthropic.py` | **New** — Claude provider |
| `backend/app/services/llm/factory.py` | Refactor: add `get_active_llm_provider()`, `get_embedding_provider()` |
| `backend/app/services/llm/__init__.py` | Export new functions |
| `backend/app/api/v1/settings.py` | **New** — Settings CRUD + test endpoint |
| `backend/app/api/v1/__init__.py` | Register settings router |
| `backend/app/api/v1/drafter.py` | Replace `GeminiProvider` → factory |
| `backend/app/api/v1/clarification.py` | Replace `GeminiProvider` → factory |
| `backend/app/api/v1/chat.py` | Pass provider to RAGService |
| `backend/app/api/v1/redflags.py` | Pass provider to RedFlagsAnalyzer |
| `backend/app/services/rag.py` | Use factory, separate chat/embedding providers |
| `backend/app/services/analysis.py` | Use factory |
| `backend/app/services/redflags_analyzer.py` | Use factory |
| `backend/app/services/embedding.py` | Default to `get_embedding_provider()` |
| `backend/requirements.txt` | Add `anthropic`, `cryptography` |
| `index.tsx` | Add Settings page, dynamic status indicator |

---

## Implementation Order

1. `encryption.py` + `LLMSettings` model (foundation)
2. `anthropic.py` provider (core new feature)
3. `factory.py` refactor (enable provider switching)
4. `settings.py` API endpoint (expose to frontend)
5. Refactor all services + endpoints (remove hardcoded Gemini)
6. Frontend Settings page (user-facing)
7. Testing + verification

---

## Verification Checklist

- [ ] Claude provider instantiates and calls `complete()` successfully
- [ ] Settings API stores/retrieves encrypted API keys
- [ ] `POST /api/v1/settings/llm/test` validates connection for both providers
- [ ] After switching to Claude, Chat/Drafter/RedFlags/Clarification work
- [ ] Embeddings still work (always via Gemini, regardless of active provider)
- [ ] Frontend Settings page saves, tests, and displays active provider
- [ ] Sidebar dynamically shows active provider + model
- [ ] Adding a new provider (e.g., OpenAI) requires only: new provider file + factory entry + model list
