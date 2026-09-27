# Context Passport V3 — Antigravity execution plan

This is the current user-approved build plan. Follow the steps in order. Each step has its own test and pass condition. Do not claim V3 complete until Test 12 passes. The model and local-hosting decisions below supersede conflicting older assumptions in `AGENTS.md`; update that file in Step 1 so the repository instructions agree.

## Context Antigravity must read before editing

- Read `AGENTS.md`, `README.md`, `v3/README.md`, `MEM0_AUDIT.md`, `render.yaml`, `backend/settings.py`, `backend/memory_manager.py`, `backend/server.py`, `backend/preference_engine.py`, `backend/scoped_mongodb.py`, `v3/src/manifest.json`, and the existing tests. Treat those files as evidence of the current implementation, not proof that live gates have passed.
- Existing stack: Python 3.11/FastAPI, Mem0 OSS 2.2.0, MongoDB Atlas for vector memories and app collections, Supabase Auth for identity, and a TypeScript Manifest V3 extension for ChatGPT, Claude, and Gemini web. V3 vault/lifecycle code exists. V2's six simulated contract gates passed; real-browser verification is pending.
- Current backend is directly wired to Gemini extraction and embeddings. `GEMINI_PAID_TIER_CONFIRMED` is required by `/ready` and capture/recall. This must become provider-aware before using OpenRouter. Do not assume changing an environment variable is sufficient.
- User choice: run the backend on this laptop at `http://localhost:8000`, bound to `127.0.0.1`. No Render/cloud deployment in this plan. Atlas, Supabase, model APIs, and the three AI websites still require internet. Judges using another computer cannot reach this laptop's `localhost` without a later hosted deployment.
- Model plan: OpenRouter GLM 5.3 Flash is the provisional extraction primary; Gemini 2.5 Flash Lite via OpenRouter is a candidate fallback; `openai/text-embedding-3-small` via OpenRouter is the provisional 1536-dimensional embedder. Jev uses `typesafe/jev-router` through the same OpenRouter key for selective review, subject to live API and cost verification. The user has $5 OpenRouter credit. They did not receive Gemini API credit; Google Cloud's $300 welcome credit excludes Gemini API.
- The user will connect real model accounts/keys after the build is complete. Steps 1–9 use mocks and synthetic data only. Step 10 connects models; Steps 11–12 are live-browser and demo gates.

## Rules for every step

1. Inspect existing code before changing it. Preserve working behavior; do not rebuild V3 from scratch or modify V2 except when a shared contract requires it. Make one reviewable change set per step and report changed files, test output, and unresolved risks.
2. Scope stays one extension (ChatGPT, Claude, Gemini web), one FastAPI backend, one side-panel vault. No dashboard, MCP, Cursor integration, or extra sites.
3. Capture starts OFF per site. Process only newly completed user-authored messages. Never capture assistant replies, old rendered history, or extension-inserted context.
4. Screen recognizable secrets both in the extension and backend. Never store or log secrets, raw transcripts, model keys, access tokens, or message bodies. Store distilled facts, redacted evidence, and necessary deduplication state only. Fail closed when classification or provider output is invalid.
5. Derive user identity only from verified Supabase JWTs. Keep `payload.user_id` inside the Atlas `$vectorSearch.filter`. Ownership checks apply to list, search, edit, block, forget, and history.
6. Recall at most three relevant general memories automatically. Sensitive/uncertain candidates require explicit Allow once before prompt preparation; denial excludes them. Inserted context must be visible, replaceable, and excluded from later capture.
7. Promote an explanation preference only after three distinct user-authored observations across at least two conversations. User corrections lock wording. Removing evidence re-evaluates inferred preferences.
8. Correction must update classification and embedding as appropriate. Block immediately excludes recall. Forget must delete vector and plaintext Mem0 history and tombstone the source so queued retries cannot recreate it. Do not erase existing user data or mix embeddings from different models during migration.
9. Keep provider keys in backend environment only. Mock tests use no paid API and no real personal data. Never silently substitute deterministic test embeddings in a real run. Before any paid tests, add usage counters and a spending cap. Keep the real backend bound to loopback and disable test auth/offline embedding flags.
10. A step passes only after its named test passes. Automated tests are not a substitute for Tests 11–12 on real browser sites. If a test fails, fix that step before proceeding.

## Step 1 — Reconcile the plan and record baseline

**Build:** Create a working branch/checkpoint. Inspect the listed files and identify existing implementation versus missing work. Update `AGENTS.md`, `README.md`, and `v3/README.md` to record the current local-host and provisional-provider decisions without falsely claiming they are implemented. Write a concise acceptance checklist and mark live gates pending.

**Test 1:** Run `npm run build:v3`, `npm run test:v3`, and the backend pytest suite with test flags. Record exact commands, counts, and failures. Confirm V2's report is simulated and not a live-browser pass. Confirm no key or `.env` content appears in output.

**Pass:** There is a reproducible baseline, contradictory instructions are resolved, and all current failures are recorded for later steps. Do not require old failing tests to pass before documenting them.

## Step 2 — Provider interfaces and mock implementations

**Build:** Introduce server-side interfaces for fact extraction, embeddings, and optional validation. Configure primary/fallback model names and provider keys without hard-coding them. Preserve Mem0 behavior. Add timeouts, bounded retries, output/schema validation, safe errors, and usage/token counters. Make readiness check the selected provider setup rather than the old Gemini paid-tier flag. Add a feature flag for Jev; do not call it by default or assume an unverified capability.

**Test 2:** Use synthetic fake responses for valid output, invalid JSON/schema, empty output, timeout, 429, provider outage, and fallback. Verify no key or message body is logged. Verify missing configuration fails visibly and mock tests make zero external model calls.

**Pass:** Provider selection is testable without real credentials, and provider failures cannot silently create false memories or use fake embeddings in production mode.

## Step 3 — Embeddings and safe migration

**Build:** Support 1536-dimensional `text-embedding-3-small`. Keep the embedding model identity with the index/data configuration. Implement a repeatable migration or new collection/index for existing Gemini vectors; do not search a collection containing mixed Gemini and OpenAI vectors. Preserve data and allow rollback until verification passes. Revisit the similarity threshold after real-model testing.

**Test 3:** With synthetic vectors and test collections, verify dimensions, model identity, exact migration count, duplicate-safe reruns, rollback, ownership filter inside `$vectorSearch`, and rejection of mixed indexes. Test a recent write before index catch-up.

**Pass:** Search uses one compatible embedding space per index and remains tenant scoped. No existing memory is deleted as a shortcut.

## Step 4 — Backend ingest, privacy, and preference learning

**Build:** Integrate the provider layer into authenticated ingestion and search. Preserve dedup/retry state, secret screening, source metadata, general/sensitive classification, recent-write recall, and preference learning. Keep extraction separate from deterministic explanation-style evidence detection.

**Test 4:** Test user-only ingest; assistant, old/injected context, and secrets skipped; idempotent duplicate events; failed write then retry; classification fail-closed; two-account isolation; one observation and three in one chat not promoted; three distinct observations across two chats promoted.

**Pass:** All backend behavior works with mock models, and none of these paths can expose another user's memory.

## Step 5 — Three-site capture

**Build:** Finish the existing ChatGPT, Claude, and Gemini adapters, send detection, conversation IDs, SPA navigation handling, and per-site capture toggles. Do not capture historical DOM content or assistant text.

**Test 5:** Use saved DOM fixtures for all three sites. Test capture OFF, one new user turn, first turn after navigation, refreshed page, nested message elements, assistant replies, old history, repeated DOM updates, and an injected memory block. Verify exactly one eligible event is sent per user turn.

**Pass:** The extension's simulated three-site capture contract passes without paid APIs.

## Step 6 — Recall, consent, and composer insertion

**Build:** Finish Use Memory, query handling, up-to-three general selection, Allow once/Don't use sensitive modal, composer insertion, replacement of old blocks, and manual copy/paste fallback when a site composer cannot be controlled.

**Test 6:** With mocked backend results, verify general selection, secret/sensitive fail-closed screening, denial and one-time allowance, no duplicate block after repeated clicks, preservation of the user's draft, and no capture of the inserted block. Exercise missing composer and backend failure UI.

**Pass:** The prepared prompt visibly contains only approved context and remains usable if a site adapter fails.

## Step 7 — Vault, corrections, block, and forget

**Build:** Finish existing memory/preference cards and controls. Ensure cards show wording, classification, status, source, and short redacted evidence. Corrected memory gets a compatible new embedding. User-corrected preference stays locked. Evidence removal demotes unsupported inferred preferences. Forget purges Mem0 plaintext history and uses source tombstones.

**Test 7:** Correct memory and search new wording; reject a correction containing a secret; correct and lock preference; remove evidence; block and immediately query; forget then refresh/retry/restart; verify sibling memories are preserved; attempt each action with another account's ID.

**Pass:** No blocked or forgotten item leaks into recall or resurrects, and controls remain tenant scoped.

## Step 8 — Local runtime and reliability

**Build:** Make the default demo runtime `127.0.0.1:8000` with explicit environment configuration. Keep real auth enabled. Improve expired-token handling, provider error messages, rate limiting, restart behavior, and SQLite history path/permissions for one local process. Review CORS and extension host permissions for the three sites and localhost; do not open a public tunnel or LAN listener.

**Test 8:** Start the backend with mock providers on loopback. Check `/health`, `/ready`, login success/failure, expired/invalid JWT, disconnected Atlas/Supabase, model timeout, restart, and memory edit/forget after restart. Confirm it is not reachable over the laptop's LAN address. Confirm no test bypass or deterministic embedding is active in real configuration.

**Pass:** A clean local start is repeatable, failures are visible and recoverable, and the backend is not publicly exposed.

## Step 9 — UX, packaging, and written setup

**Build:** Fix remaining V2 labels, improve side-panel loading/empty/error states, ensure sign-out clears auth state, and make permissions understandable. Document Python/Node setup, environment variable names without values, Atlas/Supabase prerequisites, startup command, `localhost` extension URL, clean installation, and troubleshooting. Prepare `v3/dist` as the only V3 extension artifact.

**Test 9:** Build/typecheck, run all mock suites, install from a clean unpacked build, inspect manifest permissions, check keyboard access and readable state/error text, sign out/reopen, and verify no secrets or `.env` are packaged. Confirm the written setup works from a clean checkout with fake providers.

**Pass:** The code is ready for user-supplied model credentials and a clean install; no paid or real-browser test has been claimed yet.

## Step 10 — Connect models and choose actual routing

**Build:** User enters one OpenRouter credential into the backend environment. Do not paste keys into code or chat. Run a 30–50-case synthetic benchmark of GLM versus Gemini Flash Lite. Evaluate Jev only on a defined subset of ambiguous/conflicting general cases and verify its OpenRouter behavior and variable cost before enabling it. Measure actual usage. Tune embedding score threshold with relevant and irrelevant queries. Choose fallback triggers from results.

**Test 10:** Verify real extraction, 1536-dimensional embeddings, retrieval, valid output, false-memory rate, missed facts, timeouts, fallback, Jev gating, and actual cost. Confirm `/ready` passes only with required real configuration. Check logs and stored records for accidental raw secrets. Set an OpenRouter spending cap below the $5 balance and a separate Jev cap.

**Pass:** Selected routing meets the quality threshold agreed from benchmark results and stays within the measured budget. If GLM fails quality, change primary model; do not declare it better merely because it is cheaper.

## Step 11 — Real browser journey on localhost

**Build:** Run the backend locally with real providers. Load V3 in Chrome and Brave and test live ChatGPT, Claude, and Gemini web. Fix selectors or workflows that differ from fixtures. Use synthetic account data.

**Test 11:** On each site verify capture OFF, one user turn only, no assistant/history capture, cross-site fact recall, sensitive Allow once and Don't use, repeated Use Memory, fallback route, 3-observation/2-chat preference promotion, correction and lock, evidence removal, block, forget, second-account isolation, refresh, retry, and backend restart. Repeat failure recovery for internet loss and expired auth.

**Pass:** Every live scenario works in the actual browsers. Record browser versions, backend build marker, test account IDs (not credentials), and results.

## Step 12 — Final release and judge rehearsal

**Build:** Produce a fresh `v3/dist`, final setup/privacy docs, a clean synthetic judge account, and a short demo script. Recheck model spend and local startup. No cloud deployment is part of this gate.

**Test 12:** From a clean browser profile, install V3, sign in, enable capture, create evidence across two chats, show promoted preference and vault evidence, recall on another provider, deny sensitive context, correct/lock preference, then block/forget a memory. Do it without developer tools or manual database edits. Rehearse immediately before presenting.

**Pass:** Demo runs end to end without hidden repair. V3 can then be called demo-ready on this laptop. Independent judge installation on another device would require a hosted backend and is a separate later deployment gate.

## Commands and local-demo notes

- Existing baseline commands: `npm run build:v3`; `npm run test:v3`; `APP_ENV=test ALLOW_TEST_AUTH=true PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests -q`.
- Real local start after credentials: `HOST=127.0.0.1 npm start`. Extension backend URL: `http://localhost:8000`. Check `/health` and `/ready`; `/` is not a product webpage.
- Real demo must not set `APP_ENV=test`, `ALLOW_TEST_AUTH=true`, or `ALLOW_OFFLINE_EMBEDDINGS=true`. Keep the laptop awake and online; confirm Atlas permits the current network before judging.
- Local FastAPI hosting has no Render fee. Atlas/Supabase free plans have their own limits. Mock steps cost no model calls. Paid testing begins at Step 10; actual token usage and routed model prices determine the bill. Set a hard spending limit on the OpenRouter key because local usage estimates can be incomplete. The earlier illustrative 625-operation scenario was about $0.27 for GLM plus OpenAI small embeddings at then-current prices, excluding Jev, retries, and price changes.
