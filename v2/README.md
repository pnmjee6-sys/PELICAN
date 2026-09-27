# Context Passport — Version 2: Three-Site Extension & Privacy Firewall

Context Passport V2 is a Manifest V3 browser extension with shared TypeScript logic, native site adapters for **ChatGPT**, **Claude**, and **Gemini Web**, and a client-side Privacy Firewall.

---

## 1. Directory Structure

All Version 2 code, assets, and tests are isolated within the `v2/` subfolder:

```text
v2/
├── package.json           # V2 dependencies, build, typecheck, and test scripts
├── tsconfig.json          # TypeScript bundler configuration
├── build.js               # esbuild bundler script (background, content, sidepanel)
├── verify_v2.py           # Backend contract simulation; not a live browser test
├── dist/                  # Built Manifest V3 extension ready to load in Chrome/Brave
│   ├── manifest.json
│   ├── background.js
│   ├── content.js
│   ├── content.css
│   ├── sidepanel.html
│   ├── sidepanel.js
│   ├── sidepanel.css
│   └── icons/
├── src/
│   ├── manifest.json      # Manifest V3 specification
│   ├── icons/             # 16x16, 48x48, 128x128 icons
│   ├── adapters/          # Native site adapters
│   │   ├── types.ts       # SiteAdapter interface & types
│   │   ├── chatgpt.ts     # ChatGPT (chatgpt.com) adapter
│   │   ├── claude.ts      # Claude (claude.ai) adapter
│   │   ├── gemini.ts      # Gemini Web (gemini.google.com) adapter
│   │   └── index.ts       # Adapter resolver
│   ├── core/              # Shared core logic
│   │   ├── api.ts         # Backend API client (/api/v1)
│   │   ├── screener.ts    # Client secret & sensitivity classifier
│   │   ├── injection.ts   # Memory block formatting & idempotent injection
│   │   └── storage.ts     # chrome.storage helper with per-site toggles
│   ├── content/           # Content script & styles
│   │   ├── index.ts       # Coordinator for Use Memory & capture observer
│   │   └── styles.css     # Injected button & Privacy Firewall modal styles
│   ├── background/        # Service worker
│   │   └── index.ts       # Side panel trigger and background events
│   └── sidepanel/         # Side panel UI
│       ├── sidepanel.html # Single side panel with vault, preferences, fallback
│       ├── styles.css     # Glassmorphic dark design system
│       └── index.ts       # Interactive controller
└── tests/                 # Unit & regression test suite
    ├── screener.test.ts   # Secret & sensitivity screening tests
    ├── injection.test.ts  # Idempotent memory block tests
    └── adapters.test.ts   # URL matching and adapter tests
```

---

## 2. Core Capabilities

### Per-Site Capture (Default: OFF)
- Capture starts disabled across all sites (`capture_chatgpt: false`, `capture_claude: false`, `capture_gemini: false`).
- The user can enable or disable capture individually per site via the side panel toggles.

### Native Site Adapters
- **ChatGPT (`chatgpt.com`)**: Discovers prompt textarea/contenteditable composer, isolates completed user turn elements (`[data-message-author-role="user"]`), strictly ignores assistant turns.
- **Claude (`claude.ai`)**: Discovers ProseMirror contenteditable composer, isolates completed user turn elements (`[data-testid="user-message"]`), strictly ignores assistant turns.
- **Gemini (`gemini.google.com`)**: Discovers Quill contenteditable rich-textarea, isolates completed user turns (`user-query`), strictly ignores `<model-response>`.

### Client-Side Defense-in-Depth Privacy Firewall
- **Secret screening**: Recognized API keys (Google, OpenAI, Anthropic, Supabase), tokens, SSH private keys, and passwords are fully skipped on the client. Never sent to the backend, stored, or logged.
- **Two-phase approval for sensitive context**: When "Use Memory" finds sensitive candidates (e.g. food allergies, medical history), the extension pauses with a modal dialog:
  - **"Allow once"**: Injects sensitive context into this specific prompt.
  - **"Don't use"**: Injects general context and preferences only, keeping sensitive facts out of the prepared prompt.

### Single Extension Side Panel
- **Vault**: Displays stored memories, classification badges (`general` / `sensitive`), and action controls (`Block`, `Forget`).
- **Learned Preferences**: Shows inferred explanation-style preferences with supporting evidence observation counts.
- **Universal Fallback Route**: Allows preparing memory-injected prompts for unsupported websites or if a site changes its DOM. Includes a 1-click clipboard copy button.
- **Settings & Toggles**: Controls per-site capture, backend endpoint, and Supabase Auth credentials.

---

## 3. Running & Verifying V2

```bash
# 1. Install dependencies
cd v2 && npm install

# 2. Build the Manifest V3 extension
npm run build

# 3. Run type checking
npm run typecheck

# 4. Run unit tests
npm test

# 5. Run backend contract simulation (requires configured live services)
cd .. && npm run gate:v2
```

---

## 4. Loading the Extension in Chrome / Brave

1. Open `chrome://extensions` (or `brave://extensions`).
2. Enable **Developer mode** in the top-right corner.
3. Click **Load unpacked**.
4. Select the `context-passport/v2/dist` directory.
5. Click the extension action icon to open the Side Panel.

In Settings, enter the backend URL and sign in with an existing Supabase account.
The extension stores the access token locally and clears the password after sign-in.
The token field is an advanced route for a test token or an already-issued JWT.

The unit tests and `gate:v2` do not prove that the current live Claude and Gemini
editors accept injected text. Confirm that separately in logged-in Brave/Chrome
tabs using synthetic messages, including a send-and-recall round trip, capture
OFF, sensitive denial, and a page refresh.
