import test, { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';

const tick = (ms = 30) => new Promise((resolve) => setTimeout(resolve, ms));
const response = (body: any, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => body,
});

describe('Step 7 — Vault Controls, Cards, Corrections, Block, and Forget UI Suite', () => {
  it('1. Memory cards display wording, classification, status, source, and short redacted evidence', async () => {
    const html = await readFile(new URL('../dist/sidepanel.html', import.meta.url), 'utf8');
    const dom = new JSDOM(html, { url: 'https://extension.test/sidepanel.html' });
    globalThis.window = dom.window as any;
    globalThis.document = dom.window.document;
    globalThis.HTMLElement = dom.window.HTMLElement;
    globalThis.HTMLInputElement = dom.window.HTMLInputElement;
    globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
    globalThis.chrome = {
      storage: {
        local: {
          get: (_keys: any, callback: any) => callback({ backend_url: 'http://localhost:8000', auth_token: 'valid-token' }),
          set: (_value: any, callback: any) => callback && callback(),
        },
      },
    } as any;

    globalThis.fetch = (async (url: string | URL | Request) => {
      const urlStr = url.toString();
      if (urlStr.endsWith('/health')) return response({ status: 'ok' });
      if (urlStr.endsWith('/ready')) return response({ status: 'ready' });
      if (urlStr.endsWith('/api/v1/memories')) return response([
        {
          id: 'mem-1',
          text: 'Prefers TypeScript for all web projects',
          classification: 'general',
          status: 'active',
          source: 'chatgpt.com',
          created_at: '2026-03-20T10:00:00Z',
          evidence_excerpt: 'Prefers TypeScript for all web projects',
        },
        {
          id: 'mem-2',
          text: 'Severe peanut allergy',
          classification: 'sensitive',
          status: 'blocked',
          source: 'claude.ai',
          created_at: '2026-03-21T11:00:00Z',
          evidence_excerpt: '[Sensitive supporting detail hidden]',
        },
      ]);
      if (urlStr.endsWith('/api/v1/preferences')) return response([]);
      return response({ ok: true });
    }) as any;

    // Load sidepanel bundle
    await import(`../dist/sidepanel.js?update=${Date.now()}`);
    document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    await tick(50);

    const vaultList = document.querySelector('#vault-list')!;
    assert(vaultList, '#vault-list element must exist');
    const cards = vaultList.querySelectorAll('.cp-card');
    assert.equal(cards.length, 2, 'Should render 2 memory cards');

    // Inspect Card 1 (General, Active)
    const card1 = cards[0];
    assert.match(card1.querySelector('.cp-card-text')!.textContent!, /Prefers TypeScript/);
    assert.match(card1.querySelector('.cp-evidence')!.textContent!, /Evidence: Prefers TypeScript/);
    assert.match(card1.querySelector('.cp-card-meta')!.textContent!, /chatgpt\.com/);
    const badges1 = Array.from(card1.querySelectorAll('.cp-badge')).map((b) => b.textContent);
    assert(badges1.includes('general'), 'Must display classification badge "general"');
    assert(badges1.includes('active'), 'Must display status badge "active"');

    // Inspect Card 2 (Sensitive, Blocked)
    const card2 = cards[1];
    assert.match(card2.querySelector('.cp-card-text')!.textContent!, /Severe peanut allergy/);
    assert.match(card2.querySelector('.cp-evidence')!.textContent!, /Sensitive supporting detail hidden/);
    assert.match(card2.querySelector('.cp-card-meta')!.textContent!, /claude\.ai/);
    const badges2 = Array.from(card2.querySelectorAll('.cp-badge')).map((b) => b.textContent);
    assert(badges2.includes('sensitive'), 'Must display classification badge "sensitive"');
    assert(badges2.includes('blocked'), 'Must display status badge "blocked"');

    dom.window.close();
  });

  it('2. Memory correction allows wording edit and rejects secrets client-side', async () => {
    const html = await readFile(new URL('../dist/sidepanel.html', import.meta.url), 'utf8');
    const dom = new JSDOM(html, { url: 'https://extension.test/sidepanel.html' });
    globalThis.window = dom.window as any;
    globalThis.document = dom.window.document;
    globalThis.HTMLElement = dom.window.HTMLElement;
    globalThis.HTMLInputElement = dom.window.HTMLInputElement;
    globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
    globalThis.chrome = {
      storage: {
        local: {
          get: (_keys: any, callback: any) => callback({ backend_url: 'http://localhost:8000', auth_token: 'valid-token' }),
          set: (_value: any, callback: any) => callback && callback(),
        },
      },
    } as any;

    const recordedCalls: { url: string; method: string; body?: any }[] = [];
    globalThis.fetch = (async (url: string | URL | Request, options: any = {}) => {
      const urlStr = url.toString();
      recordedCalls.push({ url: urlStr, method: options.method || 'GET', body: options.body });
      if (urlStr.endsWith('/health')) return response({ status: 'ok' });
      if (urlStr.endsWith('/ready')) return response({ status: 'ready' });
      if (urlStr.endsWith('/api/v1/memories') && (!options.method || options.method === 'GET')) {
        return response([{
          id: 'mem-1',
          text: 'Uses Python',
          classification: 'general',
          status: 'active',
          source: 'chatgpt.com',
          evidence_excerpt: 'Uses Python',
        }]);
      }
      if (urlStr.endsWith('/api/v1/preferences')) return response([]);
      return response({ id: 'mem-1', text: 'Uses Rust', classification: 'general' });
    }) as any;

    await import(`../dist/sidepanel.js?update=${Date.now() + 1}`);
    document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    await tick(50);

    const card = document.querySelector('#vault-list .cp-card')!;
    const correctBtn = Array.from(card.querySelectorAll('button')).find((b) => b.textContent === 'Correct')!;
    correctBtn.click();
    await tick();

    const textarea = card.querySelector('textarea')!;
    assert(textarea, 'Editor textarea should appear');
    assert.equal(textarea.value, 'Uses Python');

    // Attempt to save a secret -> rejected client-side with error, no PUT fetch made
    textarea.value = 'My password is SuperSecret123!';
    const saveBtn = Array.from(card.querySelectorAll('button')).find((b) => b.textContent === 'Save correction')!;
    saveBtn.click();
    await tick();

    const statusEl = card.querySelector('.cp-card-status')!;
    assert.match(statusEl.textContent!, /credentials or secrets/i);
    assert(!recordedCalls.some((c) => c.method === 'PUT'), 'Must not make PUT request when secret is detected');

    // Now save valid wording -> makes PUT fetch
    textarea.value = 'Uses Rust';
    saveBtn.click();
    await tick();

    const putCall = recordedCalls.find((c) => c.method === 'PUT' && c.url.includes('/api/v1/memories/mem-1'));
    assert(putCall, 'Must send PUT request for valid correction');
    assert.equal(JSON.parse(putCall.body).text, 'Uses Rust');

    dom.window.close();
  });

  it('3. Block and forget memory actions trigger respective API calls', async () => {
    const html = await readFile(new URL('../dist/sidepanel.html', import.meta.url), 'utf8');
    const dom = new JSDOM(html, { url: 'https://extension.test/sidepanel.html' });
    globalThis.window = dom.window as any;
    globalThis.document = dom.window.document;
    globalThis.HTMLElement = dom.window.HTMLElement;
    globalThis.HTMLInputElement = dom.window.HTMLInputElement;
    globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
    globalThis.chrome = {
      storage: {
        local: {
          get: (_keys: any, callback: any) => callback({ backend_url: 'http://localhost:8000', auth_token: 'valid-token' }),
          set: (_value: any, callback: any) => callback && callback(),
        },
      },
    } as any;

    const recordedCalls: { url: string; method: string }[] = [];
    globalThis.fetch = (async (url: string | URL | Request, options: any = {}) => {
      const urlStr = url.toString();
      recordedCalls.push({ url: urlStr, method: options.method || 'GET' });
      if (urlStr.endsWith('/health')) return response({ status: 'ok' });
      if (urlStr.endsWith('/ready')) return response({ status: 'ready' });
      if (urlStr.endsWith('/api/v1/memories') && (!options.method || options.method === 'GET')) {
        return response([{
          id: 'mem-1',
          text: 'Uses Python',
          classification: 'general',
          status: 'active',
          source: 'chatgpt.com',
          evidence_excerpt: 'Uses Python',
        }]);
      }
      if (urlStr.endsWith('/api/v1/preferences')) return response([]);
      return response({ ok: true });
    }) as any;

    // Mock confirm dialog
    globalThis.window.confirm = () => true;

    await import(`../dist/sidepanel.js?update=${Date.now() + 2}`);
    document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    await tick(50);

    const card = document.querySelector('#vault-list .cp-card')!;

    // Block
    const blockBtn = Array.from(card.querySelectorAll('button')).find((b) => b.textContent === 'Block')!;
    blockBtn.click();
    await tick();
    assert(recordedCalls.some((c) => c.method === 'POST' && c.url.includes('/api/v1/memories/mem-1/block')));

    // Forget
    const forgetBtn = Array.from(card.querySelectorAll('button')).find((b) => b.textContent === 'Forget')!;
    forgetBtn.click();
    await tick();
    assert(recordedCalls.some((c) => c.method === 'DELETE' && c.url.includes('/api/v1/memories/mem-1')));

    dom.window.close();
  });

  it('4. Preference cards render classification, status, locked state, and observations with evidence removal', async () => {
    const html = await readFile(new URL('../dist/sidepanel.html', import.meta.url), 'utf8');
    const dom = new JSDOM(html, { url: 'https://extension.test/sidepanel.html' });
    globalThis.window = dom.window as any;
    globalThis.document = dom.window.document;
    globalThis.HTMLElement = dom.window.HTMLElement;
    globalThis.HTMLInputElement = dom.window.HTMLInputElement;
    globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
    globalThis.chrome = {
      storage: {
        local: {
          get: (_keys: any, callback: any) => callback({ backend_url: 'http://localhost:8000', auth_token: 'valid-token' }),
          set: (_value: any, callback: any) => callback && callback(),
        },
      },
    } as any;

    const recordedCalls: { url: string; method: string; body?: any }[] = [];
    globalThis.fetch = (async (url: string | URL | Request, options: any = {}) => {
      const urlStr = url.toString();
      recordedCalls.push({ url: urlStr, method: options.method || 'GET', body: options.body });
      if (urlStr.endsWith('/health')) return response({ status: 'ok' });
      if (urlStr.endsWith('/ready')) return response({ status: 'ready' });
      if (urlStr.endsWith('/api/v1/memories')) return response([]);
      if (urlStr.endsWith('/api/v1/preferences') && (!options.method || options.method === 'GET')) {
        return response([
          {
            id: 'pref-1',
            preference_key: 'concise_bullets',
            preference_text: 'Prefers concise bullet points',
            status: 'active',
            locked: false,
            evidence_count: 3,
            conversation_count: 2,
            supporting_observations: [
              { id: 'obs-1', excerpt: 'use bullet points', conversation_id: 'chatgpt-1' },
              { id: 'obs-2', excerpt: 'keep it brief', conversation_id: 'claude-2' },
            ],
          },
          {
            id: 'pref-2',
            preference_key: 'code_first',
            preference_text: 'Show code first before explanation',
            status: 'active',
            locked: true,
            evidence_count: 5,
            conversation_count: 3,
            supporting_observations: [],
          },
        ]);
      }
      return response({ ok: true });
    }) as any;

    await import(`../dist/sidepanel.js?update=${Date.now() + 3}`);
    document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    await tick(50);

    const prefList = document.querySelector('#preferences-list')!;
    const cards = prefList.querySelectorAll('.cp-card');
    assert.equal(cards.length, 2, 'Should render 2 preference cards');

    // Inspect Card 1: Inferred preference
    const card1 = cards[0];
    assert.match(card1.querySelector('.cp-card-text')!.textContent!, /Prefers concise bullet points/);
    assert.match(card1.querySelector('.cp-card-meta')!.textContent!, /concise bullets · 2 chats/);
    const badges1 = Array.from(card1.querySelectorAll('.cp-badge')).map((b) => b.textContent);
    assert(badges1.includes('general'), 'Preference card must show classification badge "general"');
    assert(badges1.includes('active'), 'Preference card must show status badge "active"');
    assert(badges1.includes('3 observations'), 'Must show observation count for unlocked preference');

    // Verify supporting evidence rows
    const evidenceRows = card1.querySelectorAll('.cp-evidence-row');
    assert.equal(evidenceRows.length, 2);
    assert.match(evidenceRows[0].textContent!, /use bullet points/);

    // Click "Remove" on first observation
    const removeBtn = evidenceRows[0].querySelector('button')!;
    assert.equal(removeBtn.textContent, 'Remove');
    removeBtn.click();
    await tick();

    assert(
      recordedCalls.some((c) => c.method === 'DELETE' && c.url.includes('/api/v1/preferences/pref-1/observations/obs-1')),
      'Must send DELETE request to remove preference evidence'
    );

    // Inspect Card 2: User-corrected locked preference
    const card2 = cards[1];
    assert.match(card2.querySelector('.cp-card-text')!.textContent!, /Show code first/);
    const badges2 = Array.from(card2.querySelectorAll('.cp-badge')).map((b) => b.textContent);
    assert(badges2.includes('Your wording · locked'), 'Must show locked status badge for user-corrected preference');

    dom.window.close();
  });

  it('5. Preference correction updates wording, locks preference, and rejects secrets', async () => {
    const html = await readFile(new URL('../dist/sidepanel.html', import.meta.url), 'utf8');
    const dom = new JSDOM(html, { url: 'https://extension.test/sidepanel.html' });
    globalThis.window = dom.window as any;
    globalThis.document = dom.window.document;
    globalThis.HTMLElement = dom.window.HTMLElement;
    globalThis.HTMLInputElement = dom.window.HTMLInputElement;
    globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
    globalThis.chrome = {
      storage: {
        local: {
          get: (_keys: any, callback: any) => callback({ backend_url: 'http://localhost:8000', auth_token: 'valid-token' }),
          set: (_value: any, callback: any) => callback && callback(),
        },
      },
    } as any;

    const recordedCalls: { url: string; method: string; body?: any }[] = [];
    globalThis.fetch = (async (url: string | URL | Request, options: any = {}) => {
      const urlStr = url.toString();
      recordedCalls.push({ url: urlStr, method: options.method || 'GET', body: options.body });
      if (urlStr.endsWith('/health')) return response({ status: 'ok' });
      if (urlStr.endsWith('/ready')) return response({ status: 'ready' });
      if (urlStr.endsWith('/api/v1/memories')) return response([]);
      if (urlStr.endsWith('/api/v1/preferences') && (!options.method || options.method === 'GET')) {
        return response([{
          id: 'pref-1',
          preference_key: 'concise',
          preference_text: 'Prefers concise answers',
          status: 'active',
          locked: false,
          evidence_count: 3,
          conversation_count: 2,
          supporting_observations: [],
        }]);
      }
      return response({ id: 'pref-1', preference_text: 'Short bullet points only', locked: true });
    }) as any;

    await import(`../dist/sidepanel.js?update=${Date.now() + 4}`);
    document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
    await tick(50);

    const card = document.querySelector('#preferences-list .cp-card')!;
    const correctBtn = Array.from(card.querySelectorAll('button')).find((b) => b.textContent === 'Correct')!;
    correctBtn.click();
    await tick();

    const textarea = card.querySelector('textarea')!;
    assert(textarea);

    // Test secret rejection on preference correction
    textarea.value = 'sk-ant-api03-abcdefghijklmnop1234567890';
    const saveBtn = Array.from(card.querySelectorAll('button')).find((b) => b.textContent === 'Save correction')!;
    saveBtn.click();
    await tick();

    const statusEl = card.querySelector('.cp-card-status')!;
    assert.match(statusEl.textContent!, /credentials or secrets/i);
    assert(!recordedCalls.some((c) => c.method === 'PUT'), 'Must not make PUT request when secret is detected');

    // Valid preference correction
    textarea.value = 'Short bullet points only';
    saveBtn.click();
    await tick();

    const putCall = recordedCalls.find((c) => c.method === 'PUT' && c.url.includes('/api/v1/preferences/pref-1'));
    assert(putCall, 'Must send PUT request for valid preference correction');
    assert.equal(JSON.parse(putCall.body).preference_text, 'Short bullet points only');

    dom.window.close();
  });
});
