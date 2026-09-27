# Context Passport V3

Context Passport is an opt-in, self-improving, privacy-first browser memory extension for **ChatGPT** (`chatgpt.com`), **Claude** (`claude.ai`), and **Gemini Web** (`gemini.google.com`).

With capture enabled, it distills useful facts and learns your explanation style preferences from user-authored messages. On each supported site, a **Use Memory** button inspects your current prompt draft, automatically selects relevant general memories (up to 3), prompts for sensitive candidates via a two-phase Privacy Firewall, and injects structured context directly into the chat prompt.

---

## 1. System Architecture & Boundaries

- **Pelican extension (`v3/dist`)**: Built with TypeScript for Chrome and Brave (Manifest V3). Includes first-run memory import, a full dashboard, inline prompt insertion, a side-panel vault with Correct, Block, and Forget controls, and a copy/paste fallback.
- **Local or Render Runtime**: FastAPI binds to `127.0.0.1:8000` by default. The `render.yaml` Blueprint enables explicit public binding for one free HTTPS service that also serves the website and extension ZIP. See [DEPLOY.md](DEPLOY.md).
- **Single Application Database**: MongoDB Atlas stores Mem0 vector memories and all app-control collections (`observations`, `preferences`, `dedup_events`, `forgotten_preferences`, `forgotten_memory_sources`).
- **Supabase Auth for Sign-In Only**: Supabase supplies JWT token issuance and validation. No dual-database synchronization; identity is derived solely from verified tokens (`payload.user_id`).
- **Provider-Aware Model Routing**:
  - Primary Fact Extractor: OpenRouter GLM 5.3 Flash (`z-ai/glm-5.3-flash`).
  - Fallback Fact Extractor: Gemini 2.5 Flash Lite via OpenRouter (`google/gemini-2.5-flash-lite`), activated automatically upon 429 rate limit, 5xx error, or provider timeout.
  - Embedder: `openai/text-embedding-3-small` (1536 dimensions) via OpenRouter.
  - Validation Pass: Feature-flagged `typesafe/jev-router` through OpenRouter using the same key. It reviews ambiguous candidate memories; live behavior remains to be verified.
- **Privacy Firewall**:
  - `general`: Automatically eligible for prompt injection (up to 3 general memories).
  - `sensitive`: Demoted to two-phase approval modal (**Allow once** vs **Don't use**).
  - `secret`: API keys, bearer tokens, and credentials are screened and skipped client-side and backend; never stored, never embedded.
- **Narrow Learning Behavior**: Automatically learns only explanation style. Promotes preferences only after **3 distinct observations across at least 2 distinct conversations**. User corrections lock the wording and prevent automatic AI overwrites.

---

## 2. Prerequisites

1. **Python 3.11.x**:
   Verify with `python3.11 --version`.
2. **Node.js >= 20.x**:
   Verify with `node --version`.
3. **MongoDB Atlas Account**:
   A free MongoDB Atlas cluster (M0 or higher).
4. **Supabase Account**:
   A Supabase project with Email Auth enabled (Authentication -> Providers -> Email).
5. **OpenRouter API Key** *(for live extraction/embeddings at Step 10; mock runs use synthetic providers)*.

---

## 3. Environment Configuration

Copy the template to `.env` in the repository root:

```bash
cp .env.example .env
```

Open `.env` and configure the following variables (do NOT commit `.env` to git):

| Environment Variable | Description | Default / Example |
|---|---|---|
| `LLM_PROVIDER` | Extractor provider (`openrouter` or `gemini`) | `openrouter` |
| `OPENROUTER_API_KEY` | OpenRouter API authentication key | `sk-or-v1-...` |
| `OPENROUTER_BASE_URL` | OpenRouter API base endpoint | `https://openrouter.ai/api/v1` |
| `EXTRACTION_PRIMARY_MODEL` | Primary LLM fact extraction model | `z-ai/glm-5.3-flash` |
| `EXTRACTION_FALLBACK_MODEL` | Fallback LLM fact extraction model | `google/gemini-2.5-flash-lite` |
| `EMBEDDING_PROVIDER` | Embeddings provider (`openrouter`) | `openrouter` |
| `EMBEDDING_MODEL` | 1536-dimensional embedding model | `openai/text-embedding-3-small` |
| `EMBEDDING_DIMS` | Vector embedding dimension count | `1536` |
| `ENABLE_JEV_VALIDATION` | Enable selective Jev review via OpenRouter | `false` |
| `JEV_MODEL` | OpenRouter Jev router ID | `typesafe/jev-router` |
| `JEV_SPENDING_CAP` | Local known-cost Jev sublimit in USD | `1.00` |
| `JEV_MAX_CALLS` | Jev request limit per backend session | `20` |
| `OPENROUTER_SPENDING_CAP` | Local estimated spending guard in USD; also set a hard OpenRouter key limit | `2.00` |
| `PROVIDER_TIMEOUT_SECONDS` | Provider HTTP timeout in seconds | `30.0` |
| `PROVIDER_MAX_RETRIES` | Max retries before triggering fallback | `2` |
| `MONGODB_URI` | MongoDB Atlas connection string | `mongodb+srv://user:pass@cluster.mongodb.net/...` |
| `MONGODB_DB_NAME` | Application database name | `context_passport` |
| `MONGODB_COLLECTION_NAME` | Vector memories collection | `memories` |
| `MONGODB_VECTOR_INDEX_NAME` | Atlas Vector Search index name | `memories_vector_index_scoped` |
| `SUPABASE_URL` | Supabase project API URL | `https://<project-ref>.supabase.co` |
| `SUPABASE_ANON_KEY` | Supabase project anonymous public key | `eyJhbGciOi...` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key (for backend auth verification) | `eyJhbGciOi...` |
| `HOST` | Backend listener IP; public binding requires `PUBLIC_HOSTING=true` | `127.0.0.1` |
| `PORT` | Backend listener port | `8000` |
| `MEM0_HISTORY_DB_PATH` | Local SQLite path for Mem0 history | `backend/mem0_history.db` |

### MongoDB Atlas Vector Search Index Setup
In MongoDB Atlas, go to **Atlas Search & Vector Search** on the `memories` collection in database `context_passport` and create a Vector Search Index named `memories_vector_index_scoped`:

```json
{
  "fields": [
    {
      "type": "vector",
      "path": "embedding",
      "numDimensions": 1536,
      "similarity": "cosine"
    },
    {
      "type": "filter",
      "path": "payload.user_id"
    },
    {
      "type": "filter",
      "path": "payload.agent_id"
    },
    {
      "type": "filter",
      "path": "payload.run_id"
    }
  ]
}
```

Atlas Free has a small search-index quota. Reuse or repair the named scoped vector index when possible; inspect existing indexes before creating another. Do not delete an existing index automatically. A passing `/health` alone does not prove vector search works.

---

## 4. Installation & Build

### Step 1: Backend Setup
Create and activate a Python 3.11 virtual environment, then install dependencies:

```bash
python3.11 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
```

### Step 2: Extension Setup & Build
Install Node dependencies from the repository root:

```bash
npm install
npm run build:v3
```

This compiles TypeScript source from `v3/src/` into the clean extension artifact directory: `v3/dist/`.
> **Note**: `v3/dist` is the **only** extension artifact for Context Passport V3.

---

## 5. Running the Backend

Start the local loopback backend from the repository root:

```bash
npm run start
```

Or directly via Python:

```bash
HOST=127.0.0.1 PORT=8000 PYTHONPATH=backend backend/.venv/bin/python backend/server.py
```

### Confirm Health Check
Open `http://127.0.0.1:8000/health` in your browser. You should receive:

```json
{
  "status": "ok",
  "version": "3.0.0"
}
```

Open `http://127.0.0.1:8000/ready` to verify that both MongoDB Atlas and Supabase Auth are connected.

---

## 6. Installing the Extension in Chrome / Brave

1. Open your browser and navigate to:
   - **Chrome**: `chrome://extensions`
   - **Brave**: `brave://extensions`
2. In the top right corner, switch on **Developer mode**.
3. Click the **Load unpacked** button.
4. Select the directory:
   `<path-to-repo>/context-passport/v3/dist`
5. **Pelican** appears in your extensions list and opens the first-run memory setup page.
6. Click the Extensions puzzle icon in your browser toolbar and pin **Pelican** for easy access.

For a hosted demo, download the ZIP from the live site's **Try Pelican** section, unzip it, and load that folder. It is stamped with the live backend URL. See [DEPLOY.md](DEPLOY.md) for the full release flow and self-test.

---

## 7. Extension Usage & Configuration

1. **Open the Side Panel**:
   Click the Pelican icon in the browser toolbar. Use **My dashboard** for the full Pelican view or **Add my memories** to revisit setup.
2. **Configure Settings**:
   - Go to the **Settings** tab.
   - **Backend URL**: Use the website's Render HTTPS URL for the hosted release, or `http://127.0.0.1:8000` locally. A ZIP downloaded from the live website is preconfigured.
   - **Authentication**: Sign in using your Supabase account email and password, or provide a test Bearer token.
   - **Site Capture Toggles**: Capture is **OFF by default**. Turn ON capture for the sites you wish to monitor (**ChatGPT**, **Claude**, or **Gemini Web**).
3. **Capture Memories**:
   - Visit `chatgpt.com`, `claude.ai`, or `gemini.google.com`.
   - Send regular user prompts. Completed user messages are distilled into atomic facts and explanation preferences.
   - Assistant responses, old chat history, and injected memory blocks are never captured.
4. **Use Memory**:
   - Type a prompt in the message composer.
   - Click the inline **Use Memory** button (or use the side-panel Fallback tab).
   - Up to 3 relevant general memories are automatically formatted into the prompt.
   - Any sensitive memories trigger the Privacy Firewall consent prompt ("Allow once" vs "Don't use").
5. **Manage Your Vault**:
   - In the **Vault** tab, view your distilled memories with evidence excerpts.
   - Click **Correct** to edit wording (re-embedded immediately).
   - Click **Block** to exclude from retrieval.
   - Click **Forget** to delete completely and tombstone the source against retry duplication.
6. **Sign Out**:
   - Click **Sign out** in the Settings tab. All tokens, email, and on-screen memories are immediately purged from local state.

### First-run import and dashboard

Open `http://127.0.0.1:8000/onboarding.html` or the extension's first-run tab. Enter an account email and password, then add your details or paste one memory per line from an AI export. Create a Supabase account from the same form if needed; if email confirmation is enabled, confirm the email and sign in. Setup reports success only after the backend confirms each new memory is in the vault. Name and age are sensitive. Recognizable credentials are skipped. Capture remains off until you enable each site from the extension.

Open the website's `/dashboard.html` or click **My dashboard** in the side panel. The dashboard reads the authenticated vault, supports correction, block, forget, search, learned preferences, and manual recall with a separate choice for sensitive details. A local session requires the local backend to stay running; the hosted release uses the Render backend.

Run `npm run test:v3` and `APP_ENV=test ALLOW_TEST_AUTH=true PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests -q` before a demo. The first-run flow and dashboard have browser DOM tests, and the import endpoint has vault, recall, duplicate, isolation, and secret-screening tests. Complete a final unpacked Chrome/Brave run with a demo account before presenting.

---

## 8. Troubleshooting Guide

### 1. "Backend server offline" or Connection Refused
- Ensure the backend is running with `npm run start`.
- Confirm FastAPI is listening on `127.0.0.1:8000` (`http://localhost:8000`).
- Ensure no other service is occupying port 8000 (`lsof -i :8000`).

### 2. "Authentication expired" or HTTP 401
- Supabase Auth tokens expire periodically.
- Open the extension side panel, go to **Settings**, and sign in again with your email and password.

### 3. MongoDB Atlas Connection Timeout or ServerSelectionTimeoutError
- Check your MongoDB Atlas Network Access settings.
- Ensure your current IP address is whitelisted in MongoDB Atlas (or `0.0.0.0/0` during development).
- Check that `MONGODB_URI` contains valid credentials.

### 4. OpenRouter Rate Limit (429) or Spending Cap Exceeded
- If OpenRouter returns a 429 rate limit or 5xx server error, the backend automatically transitions to Gemini 2.5 Flash Lite fallback.
- If the spending cap ($1.00) is hit, `UsageTracker` halts further provider calls with HTTP 503. Check usage with `/ready`.

### 5. "No memories captured yet"
- Capture is **OFF by default** to preserve privacy.
- Open the side panel, go to **Settings**, and enable the toggle for the current website (`ChatGPT`, `Claude`, or `Gemini Web`).
- Only user-authored messages sent after enabling capture will be processed.

---

## 9. Automated Testing & Verification

Run the full V3 verification suites from the repository root:

```bash
# Run TypeScript typechecks, extension unit tests, and core pytest suites
npm run test:v3

# Run the complete backend test suite offline
APP_ENV=test ALLOW_TEST_AUTH=true ALLOW_OFFLINE_EMBEDDINGS=true PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests/ -q

# Optional, explicit live integration run after credentials are configured
npm run test:integration

# Live model comparison only after setting an OpenRouter key spending limit
PYTHONPATH=backend backend/.venv/bin/python backend/benchmark_routing.py
```
