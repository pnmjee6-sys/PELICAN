import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { test } from 'node:test';
import { JSDOM } from 'jsdom';

const dist = path.resolve('dist');

test('hosted website welcome-back page uses its own backend and links both ways', async () => {
  const website = path.resolve('../website');
  const home = await readFile(path.join(website, 'index.html'), 'utf8');
  const html = await readFile(path.join(website, 'dashboard.html'), 'utf8');
  const script = await readFile(path.join(website, 'dashboard.js'), 'utf8');
  assert.match(home, /href="\.\/dashboard\.html"/);
  assert.match(home, /href="\.\/download\/pelican-v3\.zip"/);
  assert.match(html, /href="\.\/index\.html"/);
  const dom = new JSDOM(html, { url: 'https://pelican-demo.onrender.com/dashboard.html', runScripts: 'outside-only' });
  const calls = [];
  dom.window.fetch = async url => {
    calls.push(url);
    if (url.endsWith('/ready')) return {ok:true,json:async()=>({status:'ready'})};
    if (url.endsWith('/api/v1/auth/token')) return {ok:true,json:async()=>({access_token:'test-token'})};
    if (url.endsWith('/api/v1/memories') || url.endsWith('/api/v1/preferences')) return {ok:true,json:async()=>([])};
    throw new Error('Unexpected URL: ' + url);
  };
  dom.window.eval(script);
  dom.window.document.dispatchEvent(new dom.window.Event('DOMContentLoaded'));
  await new Promise(resolve => setTimeout(resolve, 30));
  assert(calls.includes('https://pelican-demo.onrender.com/ready'));
  assert.equal(dom.window.document.getElementById('authGate').hidden, false);
  assert.equal(dom.window.document.getElementById('backendAddress').textContent, 'https://pelican-demo.onrender.com');
  dom.window.close();
});

test('hosted dashboard silently rotates an expired session and exposes global logout', async () => {
  const html = await readFile(path.resolve('../website/dashboard.html'), 'utf8');
  const script = await readFile(path.resolve('../website/dashboard.js'), 'utf8');
  const dom = new JSDOM(html, {url:'https://pelican-demo.onrender.com/dashboard.html',runScripts:'outside-only'});
  const {window} = dom;
  window.localStorage.setItem('pelican_auth_token', 'expired');
  window.localStorage.setItem('pelican_refresh_token', 'refresh-1');
  window.localStorage.setItem('pelican_user_email', 'ada@example.com');
  let refreshCalls = 0;
  window.fetch = async (url, options = {}) => {
    if (url.endsWith('/ready')) return {ok:true,status:200,json:async()=>({status:'ready'})};
    if (url.endsWith('/auth/refresh')) {
      refreshCalls++;
      return {ok:true,status:200,json:async()=>({access_token:'fresh',refresh_token:'refresh-2'})};
    }
    if (url.endsWith('/api/v1/memories') || url.endsWith('/api/v1/preferences')) {
      if (options.headers?.Authorization === 'Bearer expired') return {ok:false,status:401,json:async()=>({detail:'token_expired'})};
      assert.equal(options.headers?.Authorization, 'Bearer fresh');
      return {ok:true,status:200,json:async()=>([])};
    }
    if (url.endsWith('/auth/logout')) return {ok:true,status:200,json:async()=>({ok:true})};
    throw new Error('Unexpected URL: ' + url);
  };
  window.eval(script);
  window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
  await new Promise(resolve => setTimeout(resolve, 50));
  assert.equal(refreshCalls, 1);
  assert.equal(window.localStorage.getItem('pelican_refresh_token'), 'refresh-2');
  assert.equal(window.document.getElementById('globalAccount').hidden, false);
  window.document.getElementById('globalSignOutBtn').click();
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(window.localStorage.getItem('pelican_auth_token'), null);
  assert.equal(window.localStorage.getItem('pelican_refresh_token'), null);
  assert.equal(window.document.getElementById('authGate').hidden, false);
  dom.window.close();
});

test('first-run form saves explicit memories, verifies the vault, and shares auth with dashboard', async () => {
  const html = await readFile(path.join(dist, 'onboarding.html'), 'utf8');
  const script = await readFile(path.join(dist, 'onboarding.js'), 'utf8');
  const dom = new JSDOM(html, { url: 'chrome-extension://pelican/onboarding.html', runScripts: 'outside-only' });
  const { window } = dom;
  window.scrollTo = () => {};
  const savedStorage = {};
  window.chrome = { storage: { local: {
    get: async key => ({ [key]: savedStorage[key] }),
    set: async data => Object.assign(savedStorage, data),
  } } };
  const requests = [];
  window.fetch = async (url, options = {}) => {
    requests.push({ url, options });
    if (url.endsWith('/api/v1/auth/token')) return { ok: true, json: async () => ({ access_token: 'signed-in-token' }) };
    if (url.endsWith('/api/v1/memories/import')) return { ok: true, json: async () => ({ saved: [{ id: 'memory-1' }], saved_count: 1, duplicate_count: 0, skipped_count: 0 }) };
    if (url.endsWith('/api/v1/memories')) return { ok: true, json: async () => [{ id: 'memory-1', text: 'User likes concise examples.' }] };
    throw new Error('unexpected endpoint ' + url);
  };
  window.eval(script);
  const doc = window.document;
  doc.getElementById('u-name').value = 'Ada';
  doc.getElementById('importData').value = 'Here is your export:\n```text\n- User likes concise examples.\n- User likes concise examples.\n```\nThat is the complete set.';
  doc.getElementById('accountEmail').value = 'ada@example.com';
  doc.getElementById('accountPassword').value = 'safe-test-password';
  doc.querySelector('[data-step="2"]').click();
  doc.querySelector('[data-step="3"]').click();
  doc.querySelector('[data-action="finish-setup"]').click();
  await new Promise(resolve => setTimeout(resolve, 30));

  assert.equal(doc.getElementById('step-done').classList.contains('active'), true, doc.getElementById('setup-error').textContent);
  assert.equal(savedStorage.auth_token, 'signed-in-token');
  assert.equal(savedStorage.user_email, 'ada@example.com');
  const importRequest = requests.find(item => item.url.endsWith('/api/v1/memories/import'));
  assert.deepEqual(JSON.parse(importRequest.options.body).memories, ['User likes concise examples.']);
  assert.equal(importRequest.options.headers.Authorization, 'Bearer signed-in-token');
  assert.match(doc.getElementById('success-message').textContent, /1 new memories saved/);
  dom.window.close();
});

test('onboarding switch account removes both tokens and the visible signed-in identity', async () => {
  const html = await readFile(path.resolve('../website/onboarding.html'), 'utf8');
  const script = await readFile(path.resolve('../website/onboarding.js'), 'utf8');
  const dom = new JSDOM(html, { url: 'chrome-extension://pelican/onboarding.html', runScripts: 'outside-only' });
  const { window } = dom;
  const savedStorage = {auth_token:'old-access',refresh_token:'old-refresh',user_email:'old@example.com'};
  window.chrome = { storage: { local: {
    get: async key => ({[key]:savedStorage[key]}),
    set: async data => Object.assign(savedStorage, data),
  } } };
  window.fetch = async () => { throw new Error('Switching accounts must not call the backend'); };
  window.eval(script);
  await new Promise(resolve => setTimeout(resolve, 30));
  const doc = window.document;
  assert.equal(doc.getElementById('loggedInNotice').hidden, false);
  doc.getElementById('switchAccountBtn').click();
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(savedStorage.auth_token, '');
  assert.equal(savedStorage.refresh_token, '');
  assert.equal(savedStorage.user_email, '');
  assert.equal(doc.getElementById('loggedInNotice').hidden, true);
  dom.window.close();
});

test('onboarding refuses to report success when the vault cannot confirm saved IDs', async () => {
  const html = await readFile(path.join(dist, 'onboarding.html'), 'utf8');
  const script = await readFile(path.join(dist, 'onboarding.js'), 'utf8');
  const dom = new JSDOM(html, { url: 'chrome-extension://pelican/onboarding.html', runScripts: 'outside-only' });
  const { window } = dom;
  window.scrollTo = () => {};
  window.chrome = { storage: { local: { get: async () => ({ auth_token: 'token' }), set: async () => {} } } };
  window.fetch = async url => ({ ok: true, json: async () => url.endsWith('/api/v1/memories/import') ? { saved: [{ id: 'missing' }], saved_count: 1, duplicate_count: 0, skipped_count: 0 } : [] });
  window.eval(script);
  const doc = window.document;
  doc.getElementById('u-name').value = 'Ada';
  doc.querySelector('[data-step="2"]').click();
  doc.querySelector('[data-step="3"]').click();
  doc.querySelector('[data-action="finish-setup"]').click();
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(doc.getElementById('step-done').classList.contains('active'), false);
  assert.match(doc.getElementById('setup-error').textContent, /not found in your vault/);
  dom.window.close();
});

test('dashboard renders the real vault and keeps sensitive recall behind a checkbox', async () => {
  const html = await readFile(path.join(dist, 'dashboard.html'), 'utf8');
  const script = await readFile(path.join(dist, 'dashboard.js'), 'utf8');
  const dom = new JSDOM(html, { url: 'chrome-extension://pelican/dashboard.html', runScripts: 'outside-only' });
  const { window } = dom;
  const memory = { id: 'm-1', text: 'User prefers concise TypeScript examples.', classification: 'general', status: 'active', source: 'manual' };
  const privateMemory = { id: 'm-2', text: "User's name is Ada.", classification: 'sensitive', status: 'active', source: 'manual' };
  const savedStorage = { auth_token: 'signed-in-token', user_email:'ada@example.com', capture_chatgpt: false, capture_claude: false, capture_gemini: false };
  const changeListeners = [];
  window.chrome = { storage: { local: {
    get: async () => savedStorage,
    set: async data => Object.assign(savedStorage, data),
  }, onChanged:{addListener: listener => changeListeners.push(listener)} } };
  window.fetch = async url => {
    if (url.endsWith('/ready')) return {ok:true,json:async()=>({status:'ready'})};
    if (url.endsWith('/api/v1/memories')) return {ok:true,json:async()=>[memory,privateMemory]};
    if (url.endsWith('/api/v1/preferences')) return {ok:true,json:async()=>[]};
    if (url.endsWith('/api/v1/memories/query')) return {ok:true,json:async()=>({general_memories:[memory],sensitive_memories:[privateMemory],preferences:[]})};
    throw new Error('unexpected endpoint ' + url);
  };
  window.eval(script);
  window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
  await new Promise(resolve => setTimeout(resolve, 40));
  const doc = window.document;
  assert.equal(doc.getElementById('memoryCount').textContent, '02');
  assert.equal(doc.getElementById('authGate').hidden, true);
  assert.match(doc.getElementById('recentList').textContent, /concise TypeScript/);
  doc.querySelector('[data-view="use"]').click();
  doc.getElementById('recallQuery').value = 'How should you explain TypeScript?';
  doc.getElementById('recallForm').dispatchEvent(new window.Event('submit', {cancelable:true}));
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.match(doc.getElementById('generalResults').textContent, /concise TypeScript/);
  assert.equal(doc.getElementById('sensitiveResults').querySelector('input').checked, false);
  savedStorage.auth_token = '';
  changeListeners.forEach(listener => listener({auth_token:{oldValue:'signed-in-token',newValue:''}}, 'local'));
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(doc.getElementById('authGate').hidden, false);
  assert.equal(doc.getElementById('allMemoryList').textContent, '');
  assert.equal(doc.getElementById('recentList').textContent, '');
  dom.window.close();
});
