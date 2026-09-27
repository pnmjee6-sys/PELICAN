# Pelican V3: deploy, install, test, and debug

## One free public service

Use **Render Blueprint** with this repository's `render.yaml`. The free Python web service serves the landing page, welcome-back dashboard, onboarding, API, and extension ZIP from one HTTPS origin. MongoDB Atlas stores memories and Supabase handles sign-in. Render's free service sleeps when idle; wake it before a demo by opening `/ready` and waiting for the response. Free hosting is suitable for a demo, not a reliability guarantee.

1. Use the public [Pelican release repository](https://github.com/Radioactive012/Pelican), which contains `render.yaml`, `website/`, `backend/`, and `release/pelican-v3.zip`. Never push `.env`.
2. Open the [Pelican Render Blueprint link](https://render.com/deploy?repo=https://github.com/Radioactive012/Pelican), connect GitHub if prompted, select `main`, and apply `render.yaml` using the **Free** plan.
3. Enter the `sync: false` values in Render's environment settings: `OPENROUTER_API_KEY`, `MONGODB_URI`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY`. Copy them from your local `.env` yourself; do not paste keys into chat or GitHub. The Blueprint sets all nonsecret settings, including production mode and the explicit public binding flag.
4. In MongoDB Atlas **Network Access**, allow the Render service to connect. The free service does not provide a fixed outbound IP, so the demo option is `0.0.0.0/0` with a dedicated, least-privilege database user, a strong password, and Atlas TLS. Remove that broad rule when you no longer need the public demo.
5. In Supabase **Authentication → URL Configuration**, set **Site URL** to your Render URL's `/dashboard.html` and allow redirects for your Render origin. If email confirmation is enabled, the user must click the confirmation link before signing in. Supabase's default email sending may be limited; check the Authentication logs or configure SMTP if mail does not arrive.
6. Open `https://YOUR-SERVICE.onrender.com/health` and then `/ready`. `/health` proves the app started. `/ready` must return `{"status":"ready"}` and confirms Atlas, Supabase, and required configuration. Render provides `RENDER_EXTERNAL_URL` automatically; if the extension download says `PUBLIC_BASE_URL` is missing, set `PUBLIC_BASE_URL=https://YOUR-SERVICE.onrender.com` in Render and redeploy.

The website and dashboard call the API on their own origin. The `/download/pelican-v3.zip` response inserts that same HTTPS origin into the extension's manifest and setup page. The website and extension sign in separately with the **same Supabase account** and read the **same Atlas vault**. Capture remains off until enabled per site.

## Install the demo extension

1. On the live website, click **Download extension ZIP** and unzip it.
2. If an older local Pelican extension is loaded, disable or remove it first to avoid duplicate capture. Open `brave://extensions` or `chrome://extensions`, enable **Developer mode**, select **Load unpacked**, and choose the unzipped folder containing `manifest.json`.
3. Pin Pelican. Its first-run memory setup opens automatically. Sign in with the same email and password as the website.

Chrome and Brave block ordinary one-click installation from a website. A Chrome Web Store listing is required for store-style installation; submission needs a developer account and review. The ZIP route is the shortest self-hosted demo path.

## Run locally

From the repository root on this computer:

```bash
npm run start
```

Open `http://127.0.0.1:8000/health`, `http://127.0.0.1:8000/ready`, and `http://127.0.0.1:8000/`. Stop the server with `Ctrl+C`. On a new computer, first copy `.env.example` to `.env`, fill its private values, install Python 3.11 and Node 20+, and run:

```bash
python3.11 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.lock
npm install
npm run package:v3
npm run start
```

For an unpacked **local** extension, choose the built `v3/dist/` folder. The local build defaults to `http://127.0.0.1:8000`. The release ZIP downloaded from the live site points to Render. You can switch between them in the extension's **Settings → Backend URL**.

## Self-test after deployment

1. Open the live home page, click **My memories**, and verify the welcome-back sign-in page appears. Click the Pelican logo there and verify it returns home.
2. Sign in with a confirmed Supabase account. From onboarding, save a harmless fact such as `I prefer concise answers with examples.` The success screen must show a saved count. Open the dashboard and verify the same fact appears in the vault.
3. Download and install the extension. Open its side panel and sign in with the same account. Verify **Connected** and the same fact in its vault/dashboard. The backend URL in Settings should be the Render HTTPS URL.
4. On ChatGPT, Claude, and Gemini, verify capture is initially **off**. Enable one site, write a harmless user message, wait for ingestion, and refresh the vault. Assistant replies and old messages should not become memories.
5. Type a new prompt, click **Use Memory**, and verify a relevant general memory is added once. If the site composer cannot be edited, use the side-panel copy fallback. Add a sensitive test detail only if needed; verify it requires **Allow once** and remains excluded on **Don't use**.
6. Correct, block, and forget a test memory in the dashboard; verify its state in the extension. Keep the real OpenRouter key capped at the provider, because live capture and recall use paid calls.

Offline checks that do **not** call paid models:

```bash
npm run --prefix v3 typecheck
npm run --prefix v3 test
APP_ENV=test ALLOW_TEST_AUTH=true PYTHONPATH=backend backend/.venv/bin/python -m pytest backend/tests -q
```

## Debug quickly

- `/health` fails: check Render **Logs** for build/start errors. Locally, check `lsof -i :8000` and restart `npm run start`.
- `/ready` returns 503: read its `missing` or `disconnected` list. Fix Render environment variables, Atlas Network Access, or Supabase settings. `/health` alone is not enough.
- Signup works but sign-in says confirm email: check the confirmation link and Supabase Authentication logs. Do not keep creating the same account. Supabase may rate-limit emails.
- Website has memories but extension says Offline: inspect **Settings → Backend URL**, check the live `/ready`, and reload the extension. Both surfaces need the same account and backend URL.
- Extension shows no new memory: verify capture toggle, sign-in, browser site URL, provider spending limit, and Render logs. Test with a harmless user-authored message; do not paste credentials into AI chats.
- If Render sleeps, the first request can take longer. Wake it via `/ready` before the judge demo.
