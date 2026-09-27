# Context Passport V3 — Browser Extension & Vault

Context Passport V3 is a Manifest V3 browser extension built with TypeScript for **ChatGPT** (`chatgpt.com`), **Claude** (`claude.ai`), and **Gemini Web** (`gemini.google.com`).

The local build uses `http://127.0.0.1:8000`. The hosted ZIP from the Pelican website is paired with the Render HTTPS backend; the URL can also be changed in Settings. See [deployment instructions](../DEPLOY.md).

---

## 1. Extension Artifact & Packaging

- **Single V3 Extension Artifact**: `v3/dist/` is the sole packaged extension directory.
- Built from source in `v3/src/`:
  - `src/background/index.ts` -> `dist/background.js` (Background Service Worker)
  - `src/content/index.ts` -> `dist/content.js` (In-page Capture & Composer Injection)
  - `src/sidepanel/index.ts` -> `dist/sidepanel.js` (Side Panel Vault & Settings)
  - Static HTML/CSS assets and icons copied directly to `dist/`.
- No `.env`, secret keys, or Python backend code are packaged into `v3/dist`.

---

## 2. Installation & Quickstart

### Build the Extension
From the repository root:

```bash
npm run build:v3
```

This compiles TypeScript and packages all extension assets cleanly into `v3/dist`.

### Load into Chrome or Brave
1. Navigate to `chrome://extensions` or `brave://extensions`.
2. Toggle **Developer mode** to ON in the top right.
3. Click **Load unpacked**.
4. Select the directory:
   `<path-to-repo>/context-passport/v3/dist`
5. Pin the **Context Passport** icon to your browser toolbar.

---

## 3. Configuration & Authentication

1. Click the Context Passport toolbar icon to open the Side Panel.
2. Navigate to the **Settings** tab:
   - **Backend URL**: `http://localhost:8000` (or `http://127.0.0.1:8000`).
   - **Authentication**: Sign in using your Supabase account email and password, or provide a test Bearer token.
   - **Site Capture Toggles**: Capture starts **OFF by default** for privacy. Enable it per site (ChatGPT, Claude, Gemini Web).
3. Click **Sign out** at any time to clear the session, auth tokens, and all cached memory views immediately.

---

## 4. Permissions & Privacy Rationale

Context Passport requests minimal, transparent permissions:

| Permission | Purpose |
|---|---|
| `storage` | Persists per-site capture toggles and local auth tokens in `chrome.storage.local`. Passwords are never stored. |
| `sidePanel` | Displays the compact Memory Vault, Learned Preferences, and Settings UI. |
| `tabs` | Identifies active tab URL to apply per-site capture policies on ChatGPT, Claude, and Gemini Web. |
| Host permissions | ChatGPT, Claude, Gemini, local loopback, and the exact hosted backend stamped into a downloaded ZIP. Optional HTTPS permission supports switching to another backend. |

---

## 5. Automated Verification

Run TypeScript typechecking and unit test suites:

```bash
npm run test:v3
```
