import test, { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const distDir = path.resolve(__dirname, '../dist');
const repoRootDir = path.resolve(__dirname, '../../');

const tick = (ms = 30) => new Promise((resolve) => setTimeout(resolve, ms));
const response = (body: any, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

describe('Step 9 — UX, Packaging & Written Setup Suite', () => {
  it('1. Manifest permissions and schema inspection', async () => {
    const manifestPath = path.join(distDir, 'manifest.json');
    assert(existsSync(manifestPath), 'manifest.json must exist in v3/dist');

    const manifestRaw = await readFile(manifestPath, 'utf8');
    const manifest = JSON.parse(manifestRaw);

    assert.equal(manifest.manifest_version, 3, 'Must be Manifest V3');
    assert.equal(manifest.name, 'Pelican — Your Memory for AI');
    assert.equal(manifest.version, '3.0.0');
    assert.match(manifest.description, /ChatGPT, Claude, and Gemini/i);

    // Minimal and understandable permissions
    assert.deepEqual(manifest.permissions.sort(), ['sidePanel', 'storage', 'tabs'].sort());

    // Host permissions strictly limited to the 3 AI sites and local loopback
    const expectedHosts = [
      'https://chatgpt.com/*',
      'https://*.chatgpt.com/*',
      'https://claude.ai/*',
      'https://*.claude.ai/*',
      'https://gemini.google.com/*',
      'https://*.gemini.google.com/*',
      'http://localhost:8000/*',
      'http://127.0.0.1:8000/*',
    ];
    assert.deepEqual(manifest.host_permissions.sort(), expectedHosts.sort());

    // Side panel path
    assert.equal(manifest.side_panel?.default_path, 'sidepanel.html');
  });

  it('2. Dist packaging hygiene: only valid extension files, zero .env or secrets', async () => {
    assert(existsSync(distDir), 'v3/dist must exist');
    assert(!existsSync(path.join(distDir, '.env')), 'v3/dist/.env must NOT exist');
    assert(!existsSync(path.join(distDir, '.env.local')), 'v3/dist/.env.local must NOT exist');

    const files = await readdir(distDir);
    const allowedExtensions = ['.js', '.map', '.html', '.css', '.json', '.png'];

    for (const file of files) {
      if (file === 'icons' || file === 'assets') continue; // asset directories
      const ext = path.extname(file);
      assert(
        allowedExtensions.includes(ext),
        `Unexpected file type in v3/dist: ${file}`
      );
      assert(!file.endsWith('.py'), `No Python files should be packaged into v3/dist: ${file}`);

      // Inspect text content of packaged assets for leaked secrets or private keys
      if (['.js', '.html', '.css', '.json'].includes(ext)) {
        const content = await readFile(path.join(distDir, file), 'utf8');
        assert(!content.includes('BEGIN PRIVATE KEY'), `Leaked private key in ${file}`);
        assert(!content.includes('mongodb+srv://'), `Leaked MongoDB URI in ${file}`);
        // Ensure no populated live API keys are bundled (screening regexes are allowed, but not live values)
        assert(!/sk-[a-zA-Z0-9_\-]{40,}/.test(content), `Leaked populated live API key in ${file}`);
        assert(!content.includes('eyJhbGciOi'), `Leaked populated JWT in ${file}`);
      }
    }

    // Confirm background.js has V3 installed message and zero V2 references
    const bgContent = await readFile(path.join(distDir, 'background.js'), 'utf8');
    assert.match(bgContent, /Context Passport V3 installed successfully\./);
    assert(!bgContent.includes('Context Passport V2 installed'), 'background.js must not contain V2 installed message');
  });

  it('2a. Side-panel stylesheet resolves inside the packaged extension', async () => {
    const html = await readFile(path.join(distDir, 'sidepanel.html'), 'utf8');
    const dom = new JSDOM(html);
    for (const link of dom.window.document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]')) {
      assert(existsSync(path.join(distDir, link.getAttribute('href') || '')), `Missing side-panel stylesheet: ${link.getAttribute('href')}`);
    }
    assert.match(html, /href="sidepanel\.css"/);
    dom.window.close();
  });

  it('3. Side-panel loading, empty, and error states rendering with proper roles and accessible text', async () => {
    const html = await readFile(path.join(distDir, 'sidepanel.html'), 'utf8');
    const dom = new JSDOM(html, { url: 'https://extension.test/sidepanel.html' });

    globalThis.window = dom.window as any;
    globalThis.document = dom.window.document;
    globalThis.HTMLElement = dom.window.HTMLElement;
    globalThis.HTMLInputElement = dom.window.HTMLInputElement;
    globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;

    let storageState: Record<string, any> = {
      backend_url: 'http://localhost:8000',
      auth_token: '', // Unauthenticated initially
    };

    globalThis.chrome = {
      storage: {
        local: {
          get: (keys: any, callback: any) => {
            if (typeof keys === 'string') {
              callback({ [keys]: storageState[keys] });
            } else if (Array.isArray(keys)) {
              const res: Record<string, any> = {};
              for (const k of keys) res[k] = storageState[k];
              callback(res);
            } else {
              callback({ ...storageState });
            }
          },
          set: (obj: any, callback: any) => {
            Object.assign(storageState, obj);
            if (callback) callback();
          },
        },
      },
      permissions: {
        request: async () => true,
      },
    } as any;

    globalThis.fetch = (async (url: string | URL | Request) => {
      const urlStr = url.toString();
      if (urlStr.endsWith('/health')) return response({ status: 'ok', version: '3.0.0' });
      if (urlStr.endsWith('/ready')) return response({ status: 'ready' });
      if (urlStr.endsWith('/api/v1/memories')) return response([]);
      if (urlStr.endsWith('/api/v1/preferences')) return response([]);
      return response({ ok: true });
    }) as any;

    await import(`../dist/sidepanel.js?update=${Date.now()}`);
    document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    await tick(50);

    assert.equal(document.getElementById('tab-btn-settings')?.getAttribute('aria-selected'), 'true', 'Signed-out users should see sign-in settings first');

    // Initial state without auth_token -> Displays unauthenticated empty state
    const vaultList = document.querySelector('#vault-list')!;
    assert(vaultList.querySelector('.cp-empty-state'), 'Vault should render empty state when unauthenticated');
    assert.match(vaultList.textContent!, /Sign in to view your vault/i);
    assert(vaultList.querySelector('#vault-open-settings-btn'), 'Should provide action button to open settings');

    // Simulate login and reload memories -> empty list
    storageState.auth_token = 'valid-token';
    const refreshVaultBtn = document.querySelector('#vault-refresh-btn') as HTMLButtonElement;
    refreshVaultBtn.click();
    await tick(50);

    assert(vaultList.querySelector('.cp-empty-state'), 'Vault should render empty state when 0 memories returned');
    assert.match(vaultList.textContent!, /No memories stored yet/i);
    assert.match(vaultList.textContent!, /Enable capture in Settings/i);

    // Simulate backend network outage (server offline)
    globalThis.fetch = (async (url: string | URL | Request) => {
      const urlStr = url.toString();
      if (urlStr.endsWith('/health')) return response({ status: 'offline' }, 503);
      if (urlStr.endsWith('/api/v1/memories')) throw new Error('Failed to fetch: Connection refused');
      return response({ ok: true });
    }) as any;

    refreshVaultBtn.click();
    await tick(50);

    const errorState = vaultList.querySelector('.cp-error-state');
    assert(errorState, 'Vault must render error state upon backend failure');
    assert.equal(errorState.getAttribute('role'), 'alert', 'Error state must have role="alert"');
    assert.match(errorState.textContent!, /Backend server offline/i);
    assert.match(errorState.textContent!, /configured backend/i);
    assert(errorState.querySelector('#vault-error-action-btn'), 'Error state must include an action button');

    dom.window.close();
  });

  it('4. Sign-out clears auth state, tokens, email, and resets memory views immediately', async () => {
    const html = await readFile(path.join(distDir, 'sidepanel.html'), 'utf8');
    const dom = new JSDOM(html, { url: 'https://extension.test/sidepanel.html' });

    globalThis.window = dom.window as any;
    globalThis.document = dom.window.document;
    globalThis.HTMLElement = dom.window.HTMLElement;
    globalThis.HTMLInputElement = dom.window.HTMLInputElement;
    globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;

    let storageState: Record<string, any> = {
      backend_url: 'http://localhost:8000',
      auth_token: 'active-session-jwt',
      user_email: 'tester@example.com',
    };

    globalThis.chrome = {
      storage: {
        local: {
          get: (keys: any, callback: any) => {
            if (typeof keys === 'string') {
              callback({ [keys]: storageState[keys] });
            } else if (Array.isArray(keys)) {
              const res: Record<string, any> = {};
              for (const k of keys) res[k] = storageState[k];
              callback(res);
            } else {
              callback({ ...storageState });
            }
          },
          set: (obj: any, callback: any) => {
            Object.assign(storageState, obj);
            if (callback) callback();
          },
        },
      },
      permissions: { request: async () => true },
    } as any;

    globalThis.fetch = (async (url: string | URL | Request) => {
      const urlStr = url.toString();
      if (urlStr.endsWith('/health')) return response({ status: 'ok' });
      if (urlStr.endsWith('/ready')) return response({ status: 'ready' });
      if (urlStr.endsWith('/api/v1/memories')) {
        return response([{
          id: 'mem-1',
          text: 'Confidential preference notes',
          classification: 'general',
          status: 'active',
          source: 'chatgpt.com',
          created_at: '2026-03-20T10:00:00Z',
        }]);
      }
      if (urlStr.endsWith('/api/v1/preferences')) return response([]);
      return response({ ok: true });
    }) as any;

    await import(`../dist/sidepanel.js?update=${Date.now() + 1}`);
    document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    await tick(50);

    // Verify initially logged in: 1 memory rendered
    const vaultList = document.querySelector('#vault-list')!;
    assert.equal(vaultList.querySelectorAll('.cp-card').length, 1);
    assert.equal((document.querySelector('#setting-email') as HTMLInputElement).value, 'tester@example.com');

    // Click Sign Out
    const signOutBtn = document.querySelector('#sign-out-btn') as HTMLButtonElement;
    signOutBtn.click();
    await tick(50);

    // Verify storage cleared
    assert.equal(storageState.auth_token, '', 'auth_token in chrome.storage must be cleared');
    assert.equal(storageState.user_email, '', 'user_email in chrome.storage must be cleared');

    // Verify form inputs cleared
    assert.equal((document.querySelector('#setting-auth-token') as HTMLInputElement).value, '');
    assert.equal((document.querySelector('#setting-password') as HTMLInputElement).value, '');
    assert.equal((document.querySelector('#setting-email') as HTMLInputElement).value, '');

    // Verify feedback message
    const authMessage = document.querySelector('#auth-message')!;
    assert.match(authMessage.textContent!, /Signed out successfully/i);

    // Verify memory views immediately display unauthenticated empty state (no lingering cards)
    assert.equal(vaultList.querySelectorAll('.cp-card').length, 0, 'No memory cards must remain after sign out');
    assert.match(vaultList.textContent!, /Sign in to view your vault/i);

    dom.window.close();
  });

  it('5. Keyboard accessibility and understandable permissions guide in Settings', async () => {
    const html = await readFile(path.join(distDir, 'sidepanel.html'), 'utf8');
    const dom = new JSDOM(html, { url: 'https://extension.test/sidepanel.html' });

    const nav = dom.window.document.querySelector('.cp-nav')!;
    assert.equal(nav.getAttribute('role'), 'tablist');

    const tabBtns = dom.window.document.querySelectorAll('.cp-nav-btn');
    assert.equal(tabBtns.length, 4);
    tabBtns.forEach((btn) => {
      assert.equal(btn.getAttribute('role'), 'tab');
      assert(btn.hasAttribute('aria-selected'));
      assert(btn.hasAttribute('aria-controls'));
      assert.equal(btn.getAttribute('tabindex'), '0');
    });

    const panes = dom.window.document.querySelectorAll('.cp-tab-pane');
    assert.equal(panes.length, 4);
    panes.forEach((pane) => {
      assert.equal(pane.getAttribute('role'), 'tabpanel');
      assert(pane.hasAttribute('aria-labelledby'));
    });

    // Verify permissions guide exists in Settings tab
    const permCard = dom.window.document.querySelector('#tab-settings .cp-permissions-card');
    assert(permCard, 'Permissions guide card must be present in Settings tab');
    assert.match(permCard.textContent!, /Local Storage/i);
    assert.match(permCard.textContent!, /chatgpt\.com/i);
    assert.match(permCard.textContent!, /configured HTTPS service/i);
    assert.match(permCard.textContent!, /Secret Screener/i);

    dom.window.close();
  });

  it('6. Documentation and written setup covers all required setup and troubleshooting sections', async () => {
    const readme = await readFile(path.join(repoRootDir, 'README.md'), 'utf8');
    const envExample = await readFile(path.join(repoRootDir, '.env.example'), 'utf8');
    const v3Readme = await readFile(path.join(repoRootDir, 'v3/README.md'), 'utf8');

    // Python and Node setup
    assert.match(readme, /python3\.11 -m venv/i);
    assert.match(readme, /pip install -r backend\/requirements\.txt/i);
    assert.match(readme, /npm run build:v3/i);

    // MongoDB Atlas and Supabase Auth prerequisites
    assert.match(readme, /memories_vector_index_scoped/);
    assert.match(readme, /1536/);
    assert.match(readme, /cosine/);
    assert.match(readme, /payload\.user_id/);
    assert.match(readme, /Supabase Auth for Sign-In Only/i);

    // Startup command and localhost URL
    assert.match(readme, /npm run start/i);
    assert.match(readme, /127\.0\.0\.1:8000/);
    assert.match(readme, /http:\/\/localhost:8000/);

    // Clean unpacked installation from v3/dist
    assert.match(readme, /v3\/dist/);
    assert.match(readme, /Load unpacked/i);
    assert.match(v3Readme, /v3\/dist/);

    // Troubleshooting
    assert.match(readme, /Backend server offline/i);
    assert.match(readme, /Authentication expired/i);
    assert.match(readme, /MongoDB Atlas/i);
    assert.match(readme, /Spending Cap/i);

    // Environment variables without secrets
    assert.match(envExample, /LLM_PROVIDER=openrouter/);
    assert.match(envExample, /OPENROUTER_API_KEY=/);
    assert.match(envExample, /EXTRACTION_PRIMARY_MODEL=z-ai\/glm-5.3-flash/);
    assert.match(envExample, /EMBEDDING_MODEL=openai\/text-embedding-3-small/);
    assert.match(envExample, /MONGODB_URI=/);
    assert.match(envExample, /SUPABASE_URL=/);
    assert.match(envExample, /HOST=127\.0\.0\.1/);
    assert.match(envExample, /PUBLIC_HOSTING=false/, 'local development must default to loopback-only hosting');
    assert(!envExample.includes('RENDER_EXTERNAL_URL'), '.env.example must not contain RENDER_EXTERNAL_URL');
  });
});
