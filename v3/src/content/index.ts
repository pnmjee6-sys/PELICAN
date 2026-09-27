/**
 * Context Passport Content Script
 *
 * Coordinates site adapters, per-site capture toggles, inline "Use Memory" injection,
 * client-side secret screening of draft prompts, continuous button reattachment,
 * and the Privacy Firewall two-phase approval modal for sensitive memories.
 */

import { getMatchingAdapter, SiteAdapter } from '../adapters/index.ts';
import { ContextPassportApiClient } from '../core/api.ts';
import {
  applyMemoryBlockToDraft,
  formatMemoryBlock,
  stripExtensionInjectedContext,
  MemoryItem,
} from '../core/injection.ts';
import { containsSecret, screenText } from '../core/screener.ts';
import { screenRecall } from '../core/recall.ts';
import { SiteCaptureController } from '../core/capture.ts';
import { handleUseMemoryAction } from '../core/recallController.ts';
import { getAllSettings } from '../core/storage.ts';


async function init() {
  const adapter = getMatchingAdapter();
  if (!adapter) {
    return;
  }

  const settings = await getAllSettings();
  const apiClient = new ContextPassportApiClient(settings.backend_url, settings.auth_token);

  // Dynamic storage listener to keep auth token and URL updated in real time
  if (typeof chrome !== 'undefined' && chrome.storage && chrome.storage.onChanged) {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area === 'local') {
        if (changes.backend_url) {
          apiClient.setBackendUrl(changes.backend_url.newValue);
        }
        if (changes.auth_token) {
          apiClient.setToken(changes.auth_token.newValue);
          if (changes.auth_token.newValue) {
            const button = document.querySelector<HTMLButtonElement>('.context-passport-btn');
            if (button) {
              delete button.dataset.authRequired;
              button.removeAttribute('aria-label');
              button.removeAttribute('title');
              button.classList.remove('cp-btn-error');
              const label = button.querySelector('span');
              if (label) label.textContent = 'Use Memory';
            }
          }
        }
      }
    });
  }

  // 1. Mount "Use Memory" button with continuous reattachment
  mountUseMemoryButton(adapter, apiClient);

  // 2. Set up completed user message observer
  setupCaptureObserver(adapter, apiClient, settings[`capture_${adapter.name}`]);
}

function mountUseMemoryButton(adapter: SiteAdapter, apiClient: ContextPassportApiClient) {
  const tryAttach = () => {
    // Check if the button is already present and connected in the DOM
    const existing = document.querySelector('.context-passport-action-container');
    if (existing && existing.isConnected) return;

    // Composer must be present
    const composer = adapter.getComposer();
    if (!composer) return;

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'context-passport-btn';
    btn.innerHTML = `
      <svg viewBox="0 0 24 24">
        <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
      </svg>
      <span>Use Memory</span>
    `;

    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (btn.dataset.authRequired === 'true') {
        chrome.runtime.sendMessage({ type: 'open-pelican-sign-in' });
        return;
      }
      await handleUseMemory(adapter, apiClient, btn);
    });

    adapter.attachUseMemoryButton(btn);
  };

  tryAttach();

  // Continuous MutationObserver to reattach whenever the composer or form is redrawn
  const domObserver = new MutationObserver(() => {
    tryAttach();
  });
  domObserver.observe(document.body, { childList: true, subtree: true });

  // Low-frequency fallback poll in case of shadow DOM or non-bubbling updates
  setInterval(tryAttach, 2000);
}

async function handleUseMemory(adapter: SiteAdapter, apiClient: ContextPassportApiClient, btn: HTMLButtonElement) {
  const result = await handleUseMemoryAction({
    adapter,
    apiClient,
    button: btn,
  });
  if (result.reason === 'auth_required') {
    btn.dataset.authRequired = 'true';
    btn.title = 'Open Pelican to sign in';
    btn.setAttribute('aria-label', 'Open Pelican to sign in');
  }
}


function setupCaptureObserver(adapter: SiteAdapter, apiClient: ContextPassportApiClient, initiallyEnabled: boolean): SiteCaptureController {
  const controller = new SiteCaptureController({
    adapter,
    initialEnabled: initiallyEnabled ?? false,
    onCapture: async (payload) => {
      try {
        await apiClient.ingestMessage(payload);
        console.info(`[Context Passport] Successfully ingested message for ${adapter.name}`);
      } catch (err) {
        console.warn('[Context Passport] Message ingestion failed:', err);
      }
    },
  });

  if (typeof chrome !== 'undefined' && chrome?.storage?.onChanged) {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== 'local') return;
      const change = changes[`capture_${adapter.name}`];
      if (!change) return;
      controller.setEnabled(change.newValue === true);
    });
  }

  return controller;
}


function escapeHtml(str: string): string {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
