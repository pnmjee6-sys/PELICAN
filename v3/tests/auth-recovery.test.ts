import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

test('in-page sign-in action opens the side panel and has a dashboard fallback', async () => {
  const source = readFileSync(new URL('../src/background/index.ts', import.meta.url), 'utf8');
  let listener: ((message: {type: string}, sender: {tab?: {id: number}}) => void) | undefined;
  let openedTab = -1;
  let dashboardUrl = '';
  const chrome = {
    runtime: {
      onInstalled: {addListener: () => {}},
      onMessage: {addListener: (callback: typeof listener) => { listener = callback; }},
      getURL: (path: string) => `chrome-extension://pelican/${path}`,
    },
    sidePanel: {
      setPanelBehavior: async () => {},
      open: async ({tabId}: {tabId: number}) => { openedTab = tabId; },
    },
    tabs: {create: ({url}: {url: string}) => { dashboardUrl = url; }},
  };
  runInNewContext(source, {chrome, console});
  assert.ok(listener);
  listener!({type:'open-pelican-sign-in'}, {tab:{id:42}});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(openedTab, 42);
  assert.equal(dashboardUrl, '');
  chrome.sidePanel.open = async () => { throw new Error('Panel unavailable'); };
  listener!({type:'open-pelican-sign-in'}, {tab:{id:42}});
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(dashboardUrl, 'chrome-extension://pelican/dashboard.html');
});
