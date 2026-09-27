import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';

const tick = () => new Promise((resolve) => setTimeout(resolve, 25));
const response = (body) => ({ ok: true, status: 200, json: async () => body });

test('V3 vault renders evidence and sends correction and removal actions', async () => {
  const html = await readFile(new URL('../dist/sidepanel.html', import.meta.url), 'utf8');
  const dom = new JSDOM(html, { url: 'https://extension.test/sidepanel.html' });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.HTMLElement = dom.window.HTMLElement;
  globalThis.HTMLInputElement = dom.window.HTMLInputElement;
  globalThis.HTMLTextAreaElement = dom.window.HTMLTextAreaElement;
  globalThis.chrome = {
    storage: {
      local: {
        get: (_keys, callback) => callback({ backend_url: 'http://localhost:8000', auth_token: 'test-token' }),
        set: (_value, callback) => callback(),
      },
    },
  };
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET', body: options.body });
    if (url.endsWith('/health')) return response({ status: 'ok' });
    if (url.endsWith('/ready')) return response({ status: 'ready' });
    if (url.endsWith('/api/v1/memories')) return response([{
      id: 'mem-1', text: 'Uses Python', classification: 'general', status: 'active',
      source: 'chatgpt.com', evidence_excerpt: 'Uses Python',
    }]);
    if (url.endsWith('/api/v1/preferences')) return response([{
      id: 'pref-1', preference_key: 'concise', preference_text: 'Prefers concise answers',
      status: 'active', locked: false, evidence_count: 3, conversation_count: 2,
      supporting_observations: [{ id: 'obs-1', excerpt: 'be concise', conversation_id: 'chat-1' }],
    }]);
    return response({ ok: true });
  };
  await import('../dist/sidepanel.js');
  document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
  await tick();
  assert.match(document.querySelector('#vault-list').textContent, /chatgpt\.com/);
  assert.match(document.querySelector('#vault-list').textContent, /Evidence: Uses Python/);
  assert.match(document.querySelector('#preferences-list').textContent, /be concise/);
  const memoryCard = document.querySelector('#vault-list .cp-card');
  [...memoryCard.querySelectorAll('button')].find((button) => button.textContent === 'Correct').click();
  await tick();
  memoryCard.querySelector('textarea').value = 'Uses Rust';
  [...memoryCard.querySelectorAll('button')].find((button) => button.textContent === 'Save correction').click();
  await tick();
  assert(calls.some((call) => call.url.endsWith('/api/v1/memories/mem-1') && call.method === 'PUT' && JSON.parse(call.body).text === 'Uses Rust'));
  const preferenceCard = document.querySelector('#preferences-list .cp-card');
  [...preferenceCard.querySelectorAll('button')].find((button) => button.textContent === 'Remove').click();
  await tick();
  assert(calls.some((call) => call.url.endsWith('/api/v1/preferences/pref-1/observations/obs-1') && call.method === 'DELETE'));
  dom.window.close();
});
