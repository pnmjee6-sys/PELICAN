# Context Passport — Agent Operating Guide & Final Plan

> **MANDATORY FOR ALL AI AGENTS**
>
> This file is the single source of truth for product scope, version roadmap, safety boundaries,
> and technical architecture. All prior drafts, obsolete version plans (V0-V5), and discarded
> features (MCP and Cursor integrations) are superseded by this plan. The user explicitly added a Pelican dashboard and first-run import for the release.
> Do not declare a version complete until its required gates pass. V3 development is in progress; live browser verification is still pending.

---

## 1. Product Summary & Architecture

Context Passport is a **self-improving, privacy-first browser memory** product.

- **One browser extension**: Works on **ChatGPT (`chatgpt.com`)**, **Claude (`claude.ai`)**, and **Gemini web (`gemini.google.com`)**. Includes a Pelican side panel, a packaged dashboard, first-run memory import, and a copy/paste fallback route.
- **One backend, two run modes**: FastAPI uses `127.0.0.1:8000` locally and the free Render HTTPS service for the public demo. Public binding requires `PUBLIC_HOSTING=true`. Render serves the website, dashboard, onboarding, API, and extension ZIP from one origin. See `DEPLOY.md`.
- **Model routing (provisional plan & transition)**:
  - *Current baseline code*: Directly wired to Gemini extraction (`gemini-2.5-flash`) and Gemini embeddings (`models/gemini-embedding-001`, 1536 dims), requiring `GEMINI_PAID_TIER_CONFIRMED`.
  - *Target provider-aware abstraction*: Provisional primary extractor is OpenRouter GLM 5.3 Flash (`z-ai/glm-5.3-flash`); candidate fallback is Gemini 2.5 Flash Lite via OpenRouter (`google/gemini-2.5-flash-lite`); provisional 1536-dimensional embedder is `openai/text-embedding-3-small` via OpenRouter.
  - *Validation pass*: `typesafe/jev-router` uses the same OpenRouter key for selective review of ambiguous memory candidates. Its live behavior and variable routing cost remain unverified.
  - *Benchmarking*: Benchmarking (Step 10) will be conducted before finalizing model selection. Real keys are entered only at Step 10; Steps 1–9 use mocks and synthetic data only.
- **One application database**: **MongoDB Atlas stores both Mem0 vector memories and app-control collections** (`observations`, `preferences`, `dedup_events`, `forgotten_preferences`, `forgotten_memory_sources`). Supabase supplies **sign-in only** (JWT issuance/validation), eliminating dual-database coordination.
- **One narrow learning behavior**: Automatically learns **how the user likes answers explained**. Promotes an explanation-style preference only after **three distinct user-authored observations across at least two conversations**. User corrections lock the wording and take priority over later automatic guesses.
- **Privacy Firewall**: Memories are classified as:
  - `general`: eligible for automatic use when relevant (up to 3 general memories selected automatically by the backend).
  - `sensitive/uncertain`: requires an explicit **"Allow once"** or **"Don't use"** prompt before prompt preparation.
  - `secret`: recognizable credentials, API keys, passwords, and private tokens are screened and skipped entirely. Never stored or logged.

### Explicit Out-of-Scope Items
- The Pelican dashboard and onboarding are packaged extension pages and served by the same backend origin on local and Render deployments.
- No MCP servers or MCP client adapters.
- No Cursor or coding-assistant extensions.
- No additional native websites beyond ChatGPT, Claude, and Gemini web.
- Public hosting is explicitly included for the demo; never expose a backend without production auth and provider spending limits.

---

## 2. Core Trust Boundaries & Privacy Rules

1. **Capture is OFF by default**: Starts disabled. The user enables it per site and can pause it at any time.
2. **User-authored messages only**: Only new, completed messages written by the user are capture candidates. Never capture assistant responses.
3. **No self-referential capture**: Extension-inserted memory blocks or previously injected context never count as new user evidence.
4. **Defense-in-depth secret screening**: Recognized secrets and credentials are screened in the extension before upload, and screened again on the backend. Recognizable credentials are completely skipped.
5. **No raw transcript retention**: The backend does not log message bodies or retain full conversation transcripts. Only distilled facts, explanation observations, and deduplication hashes are persisted.
6. **Strict tenant isolation**: All vector searches use `ScopedMongoDB` placing `payload.user_id` inside the `$vectorSearch.filter`. Identity is derived strictly from verified Supabase Auth JWTs, never from client-provided user IDs.
7. **Two-phase approval for sensitive context**: When "Use Memory" runs, general memories are attached automatically, but sensitive candidates require explicit user consent ("Allow once"). Denying consent excludes the sensitive memory while keeping the prompt ready.

---

## 3. Acceptance Checklist & Verification Gates

| Step | Scope | Gate / Test Description | Status |
|---|---|---|---|
| Step 1 | Baseline & Documentation | Working branch, audit baseline, update docs, run existing suites | Passed |
| Step 2 | Provider Interfaces & Mocks | Server-side extraction/embedder interfaces, mock providers, fail-closed handling, token counters | Passed |
| Step 3 | Embeddings & Safe Migration | 1536-dim `text-embedding-3-small`, model tagging, vector migration tool, tenant scoping | Passed |
| Step 4 | Ingest, Privacy & Preferences | Provider integration with safe retry state machine, secret screener, 3-obs/2-chat engine | Passed |
| Step 5 | Three-Site Capture | ChatGPT, Claude, Gemini adapters; DOM fixtures; single-turn capture; no assistant/history | Passed |
| Step 6 | Recall, Consent & Injection | Use Memory, up-to-3 general, compact sensitive consent card, composer replacement, fallback UI | Passed |

| Step 7 | Vault Controls & Lifecycle | Card rendering, locked preference corrections, evidence removal, block/forget lifecycles | Passed |
| Step 8 | Local Runtime & Reliability | `127.0.0.1:8000` loopback by default, token expiry, rate limits, restart survival | Passed |
| Step 9 | UX, Packaging & Written Setup | Extension packaging in `v3/dist`, clear empty/error states, reproducible setup documentation | Passed |
| Step 10 | Connect Models & Routing | One OpenRouter key, live 40-case benchmark, Jev route and actual usage validation | PENDING — live key and benchmark |
| Step 11 | Real Browser Journey | Live ChatGPT, Claude, Gemini web tests in Chrome/Brave; cross-site recall; privacy modals | LIVE PENDING |
| Step 12 | Final Release & Judge Rehearsal | Fresh `v3/dist`, clean demo account, end-to-end judge demonstration rehearsal | LIVE PENDING |
| Deployment | Render release | The current `pelican/main` release is live on Render; `/health`, `/ready`, hosted ZIP, and an authenticated dashboard recall passed. A fresh extension sign-in and full three-site journey remain pending. | PARTIAL LIVE PASS |

Emergency session repair (2026-09-26): expired access tokens must be refreshed once using a rotated Supabase refresh token and the original request retried. An expired JWT must never be accepted via an admin user lookup. Logout must clear both tokens, account identity, and visible vault data. The ChatGPT/Claude/Gemini live journey remains a separate gate.

Legacy unpacked installs can have an expired access token without a refresh token. In that case, the in-page Sign in again control must open Pelican's sign-in UI directly; the user's chat draft must remain untouched. A one-time sign-in is still necessary because the extension cannot safely recover a missing refresh credential.

Side-panel packaging regression (2026-09-26): `sidepanel.html` must reference the packaged `sidepanel.css`, not source `styles.css`; otherwise the logo fills the panel and hides sign-in. Signed-out panels open Settings by default. Verify packaged HTML assets resolve before release.

---

## 4. Current & Provisional Compatibility Set

| Component | Pinned Version / Config | Purpose / Status |
|---|---|---|
| Python | `3.11.15` | Backend runtime |
| Node.js | `>= 20.x` | Extension build & script runner |
| Mem0 OSS | `2.2.0` | Memory orchestration |
| Backend Host | `127.0.0.1:8000` local; Render HTTPS for demo | Explicit public binding in Render only |
| Extraction Primary (Provisional) | `z-ai/glm-5.3-flash` | Fast, cost-efficient fact extraction (mocked until Step 10) |
| Extraction Fallback (Provisional) | `google/gemini-2.5-flash-lite` | Robust fallback extraction via OpenRouter |
| Validation Pass (Feature-flagged) | `typesafe/jev-router` via OpenRouter | Optional review of ambiguous candidate memories; live contract pending |
| Embedding Model (Provisional) | `openai/text-embedding-3-small` (1536d) | High-performance 1536-dim embeddings via OpenRouter |
| Legacy Reference Extractor | `gemini-2.5-flash` | Direct Gemini extractor (being transitioned) |
| Legacy Reference Embedder | `models/gemini-embedding-001` (1536d) | Direct Gemini embedder (being transitioned) |
| MongoDB Driver | PyMongo `4.18.2` | MongoDB Atlas driver |
| Vector Index | `memories_vector_index_scoped` | Atlas Vector Search with `payload.user_id` filter |
| Backend Framework | FastAPI `0.141.1` + Uvicorn `0.37.0` | REST API |
| Auth Provider | Supabase Auth | User identity / JWT validation |

---

## 5. Development & Testing Discipline

1. **Test-first for each version**: Implement the code and the verification suite to prove all criteria before declaring completion.
2. **Never commit secrets**: `.env` is git-ignored and contains private API keys and database credentials.
3. **Scope**: Maintain the user-requested dashboard and onboarding alongside the compact side panel. Avoid unrelated MCP or extra platform integrations.
4. **Live gates require real browsers**: Automated unit and contract tests (Steps 1–9) do not replace live browser verification on ChatGPT, Claude, and Gemini web (Steps 11–12).
5. **Living documentation & final checklist updates**: After each step, update `AGENTS.md` and `v3.md`. Record newly discovered failure modes in Section 3 and append any newly identified edge cases or end-to-end verification items to Section 4 of `v3.md` ("Living Checklist: Things We Need to Check at Last").
