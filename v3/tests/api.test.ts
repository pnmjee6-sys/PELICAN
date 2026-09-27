import test from 'node:test';
import assert from 'node:assert/strict';
import { ContextPassportApiClient } from '../src/core/api.ts';
import { getAllSettings, getSetting, setSetting } from '../src/core/storage.ts';

test('a freshly installed hosted ZIP reads its backend from the stamped manifest', async () => {
  const previousChrome = (globalThis as any).chrome;
  (globalThis as any).chrome = {
    runtime: { getManifest: () => ({homepage_url: 'https://pelican-demo.onrender.com'}) },
    storage: {local: {get: (_keys: unknown, callback: (data: object) => void) => callback({})}},
  };
  try {
    assert.equal(await getSetting('backend_url'), 'https://pelican-demo.onrender.com');
    assert.equal((await getAllSettings()).backend_url, 'https://pelican-demo.onrender.com');
  } finally {
    (globalThis as any).chrome = previousChrome;
  }
});

test('expired access token refreshes once and retries concurrent requests with rotated tokens', async () => {
  const originalFetch = globalThis.fetch;
  await setSetting('auth_token', 'expired-access');
  await setSetting('refresh_token', 'old-refresh');
  let refreshes = 0;
  let retries = 0;
  globalThis.fetch = async (url, options) => {
    if (String(url).endsWith('/auth/refresh')) {
      refreshes++;
      await new Promise(resolve => setTimeout(resolve, 10));
      return new Response(JSON.stringify({access_token:'new-access',refresh_token:'new-refresh'}), {status:200});
    }
    const auth = new Headers(options?.headers).get('Authorization');
    if (auth === 'Bearer expired-access') return new Response(JSON.stringify({detail:'token_expired'}), {status:401});
    assert.equal(auth, 'Bearer new-access');
    retries++;
    return new Response(JSON.stringify([]), {status:200});
  };
  try {
    const a = new ContextPassportApiClient('http://localhost:8000', 'expired-access');
    const b = new ContextPassportApiClient('http://localhost:8000', 'expired-access');
    await Promise.all([a.getPreferences(), b.getAllMemories()]);
    assert.equal(refreshes, 1);
    assert.equal(retries, 2);
    assert.equal(await getSetting('auth_token'), 'new-access');
    assert.equal(await getSetting('refresh_token'), 'new-refresh');
  } finally { globalThis.fetch = originalFetch; }
});

test('failed refresh clears access, refresh, and account identity', async () => {
  const originalFetch = globalThis.fetch;
  await setSetting('auth_token', 'expired-access');
  await setSetting('refresh_token', 'invalid-refresh');
  await setSetting('user_email', 'demo@example.com');
  globalThis.fetch = async url => String(url).endsWith('/auth/refresh')
    ? new Response(JSON.stringify({detail:'invalid'}), {status:401})
    : new Response(JSON.stringify({detail:'token_expired'}), {status:401});
  try {
    const client = new ContextPassportApiClient('http://localhost:8000', 'expired-access');
    await assert.rejects(client.getPreferences(), /Sign in again/);
    assert.equal(await getSetting('auth_token'), '');
    assert.equal(await getSetting('refresh_token'), '');
    assert.equal(await getSetting('user_email'), '');
  } finally { globalThis.fetch = originalFetch; }
});

test('logout clears every local credential even when backend revocation is unavailable', async () => {
  const originalFetch = globalThis.fetch;
  await setSetting('auth_token', 'old-access');
  await setSetting('refresh_token', 'old-refresh');
  await setSetting('user_email', 'old@example.com');
  globalThis.fetch = async () => { throw new Error('Backend unavailable'); };
  try {
    const client = new ContextPassportApiClient('http://localhost:8000', 'old-access');
    await client.logout();
    assert.equal(await getSetting('auth_token'), '');
    assert.equal(await getSetting('refresh_token'), '');
    assert.equal(await getSetting('user_email'), '');
  } finally { globalThis.fetch = originalFetch; }
});

test('manual Bearer prefix is normalized before authenticated requests', async () => {
  const originalFetch = globalThis.fetch;
  let authorization = '';
  globalThis.fetch = async (_url, options) => {
    authorization = new Headers(options?.headers).get('Authorization') || '';
    return new Response(JSON.stringify([]), { status: 200 });
  };
  try {
    const client = new ContextPassportApiClient('http://localhost:8000', 'Bearer test-bearer-user');
    await client.getPreferences();
    assert.equal(authorization, 'Bearer test-bearer-user');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('email/password sign-in returns an access token for later requests', async () => {
  const originalFetch = globalThis.fetch;
  const seen: string[] = [];
  globalThis.fetch = async (url, options) => {
    seen.push(String(url));
    if (String(url).endsWith('/auth/token')) {
      assert.deepEqual(JSON.parse(String(options?.body)), { email: 'test@example.com', password: 'sample-pass' });
      return new Response(JSON.stringify({ access_token: 'signed-in-token' }), { status: 200 });
    }
    assert.equal(new Headers(options?.headers).get('Authorization'), 'Bearer signed-in-token');
    return new Response(JSON.stringify([]), { status: 200 });
  };
  try {
    const client = new ContextPassportApiClient('http://localhost:8000');
    assert.equal(await client.login('test@example.com', 'sample-pass'), 'signed-in-token');
    await client.getPreferences();
    assert.equal(seen.length, 2);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
