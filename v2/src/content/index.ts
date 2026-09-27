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
import { CaptureGate } from '../core/capture.ts';
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
  if (btn.disabled) return;
  btn.disabled = true;
  const currentDraft = adapter.getComposerDraft();
  const btnLabel = btn.querySelector('span');

  // Strip any previously injected memory block so search doesn't send old memory context in the query!
  const cleanDraft = stripExtensionInjectedContext(currentDraft).trim();

  // CRITICAL: Secret screening before sending query to backend
  if (cleanDraft && containsSecret(cleanDraft)) {
    if (btnLabel) btnLabel.textContent = 'Secret in prompt!';
    alert('Context Passport: Recognizable secret or credential detected in draft. Memory recall cancelled for privacy.');
    setTimeout(() => {
      if (btnLabel) btnLabel.textContent = 'Use Memory';
    }, 2500);
    btn.disabled = false;
    return;
  }

  try {
    if (btnLabel) btnLabel.textContent = 'Recalling...';
    btn.classList.add('loading');

    // Query backend using only the clean user draft, never old injected memories
    const query = cleanDraft || 'General user preferences and active context';
    const response = await apiClient.queryMemories(query, 3);

    const { general: finalGeneral, sensitive: sensitiveCandidates, preferences } = screenRecall(response);

    // CRITICAL: If no memories found, strip any old injected block from composer!
    if (finalGeneral.length === 0 && sensitiveCandidates.length === 0 && preferences.length === 0) {
      adapter.setComposerDraft(stripExtensionInjectedContext(adapter.getComposerDraft()));
      if (btnLabel) btnLabel.textContent = 'No memories found';
      setTimeout(() => {
        if (btnLabel) btnLabel.textContent = 'Use Memory';
        btn.classList.remove('loading');
      }, 2000);
      return;
    }

    let approvedSensitive: MemoryItem[] = [];

    // Privacy Firewall gate: If sensitive candidates exist, pause for user approval!
    if (sensitiveCandidates.length > 0) {
      const allowed = await promptSensitiveApproval(sensitiveCandidates);
      if (allowed) {
        approvedSensitive = sensitiveCandidates;
      }
      // If user denied, approvedSensitive remains empty! Sensitive memory is kept out.
    }

    // Format approved memory block and inject into composer, cleanly replacing any prior block
    const memoryBlock = formatMemoryBlock(finalGeneral, approvedSensitive, preferences);
    const latestDraft = stripExtensionInjectedContext(adapter.getComposerDraft()).trim();
    if (latestDraft !== cleanDraft) {
      adapter.setComposerDraft(latestDraft);
      if (btnLabel) btnLabel.textContent = 'Draft changed—retry';
      return;
    }
    const updatedComposerText = applyMemoryBlockToDraft(latestDraft, memoryBlock);

    adapter.setComposerDraft(updatedComposerText);
    await new Promise((resolve) => setTimeout(resolve, 50));
    if (adapter.getComposerDraft().trim() !== updatedComposerText.trim()) {
      adapter.setComposerDraft(latestDraft);
      if (btnLabel) btnLabel.textContent = 'Use fallback panel';
      return;
    }

    if (btnLabel) btnLabel.textContent = 'Attached!';
    setTimeout(() => {
      if (btnLabel) btnLabel.textContent = 'Use Memory';
      btn.classList.remove('loading');
    }, 2000);
  } catch (err: any) {
    console.error('Use Memory failed:', err);
    if (btnLabel) btnLabel.textContent = 'Error';
    setTimeout(() => {
      if (btnLabel) btnLabel.textContent = 'Use Memory';
      btn.classList.remove('loading');
    }, 2500);
  } finally {
    btn.disabled = false;
  }
}

/**
 * Renders the Privacy Firewall approval modal for sensitive memories
 */
function promptSensitiveApproval(sensitiveMemories: MemoryItem[]): Promise<boolean> {
  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'cp-modal-backdrop';

    const itemsHtml = sensitiveMemories
      .map(
        (m) => `
        <div class="cp-sensitive-item">
          <span class="cp-sensitive-badge">Sensitive</span>
          <span>${escapeHtml(m.text)}</span>
        </div>`
      )
      .join('');

    backdrop.innerHTML = `
      <div class="cp-modal-card">
        <div class="cp-modal-header">
          <div class="cp-modal-icon">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
            </svg>
          </div>
          <div class="cp-modal-title">Privacy Firewall: Sensitive Context</div>
        </div>
        <div class="cp-modal-desc">
          Relevant sensitive memories were found for this prompt. Do you want to allow them once for this request?
        </div>
        <div class="cp-sensitive-preview">
          ${itemsHtml}
        </div>
        <div class="cp-modal-actions">
          <button type="button" class="cp-btn-secondary" id="cp-deny-btn">Don't use</button>
          <button type="button" class="cp-btn-primary" id="cp-allow-btn">Allow once</button>
        </div>
      </div>
    `;

    document.body.appendChild(backdrop);

    const cleanup = (allowed: boolean) => {
      backdrop.remove();
      resolve(allowed);
    };

    backdrop.querySelector('#cp-allow-btn')?.addEventListener('click', () => cleanup(true));
    backdrop.querySelector('#cp-deny-btn')?.addEventListener('click', () => cleanup(false));
  });
}

function setupCaptureObserver(adapter: SiteAdapter, apiClient: ContextPassportApiClient, initiallyEnabled: boolean) {
  const gate = new CaptureGate();
  let captureEnabled = initiallyEnabled;
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    const change = changes[`capture_${adapter.name}`];
    if (!change) return;
    captureEnabled = change.newValue === true;
    if (!captureEnabled) gate.clear();
  });

  const recordSend = () => {
    if (!captureEnabled) return;
    gate.arm(adapter.getComposerDraft(), crypto.randomUUID());
  };

  document.addEventListener('submit', (event) => {
    const composer = adapter.getComposer();
    if (composer && event.target instanceof Element && event.target.contains(composer)) recordSend();
  }, true);
  document.addEventListener('keydown', (event) => {
    const composer = adapter.getComposer();
    if (composer && event.key === 'Enter' && !event.shiftKey && !event.isComposing &&
        event.target instanceof Node && composer.contains(event.target)) recordSend();
  }, true);
  document.addEventListener('click', (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const button = target.closest('button');
    if (!button || button.classList.contains('context-passport-btn')) return;
    const label = `${button.getAttribute('aria-label') || ''} ${button.getAttribute('data-testid') || ''} ${button.textContent || ''}`;
    if (/\b(send|submit|run prompt)\b/i.test(label) || button.matches('button[type="submit"], button.send-button')) {
      recordSend();
    }
  }, true);

  adapter.observeUserMessages(async (msg) => {
    if (!captureEnabled) return;

    // Only the user's own words become evidence, even when memory was attached.
    const cleanText = stripExtensionInjectedContext(msg.text).trim();
    if (!cleanText) return;

    // 2. Secret Screening: Skip recognized credentials immediately
    const screenResult = screenText(msg.text);
    if (screenResult.classification === 'secret' || containsSecret(cleanText)) {
      console.warn('[Context Passport] Skipping capture: Recognizable credential detected');
      return;
    }

    const eventId = gate.take(msg.text);
    if (!eventId) return;

    // 4. Ingest user message to backend
    try {
      await apiClient.ingestMessage({
        conversation_id: msg.conversationId,
        text: cleanText,
        role: 'user',
        is_extension_context: false,
        event_id: eventId,
      });
      console.info(`[Context Passport] Successfully ingested message for ${adapter.name}`);
    } catch (err) {
      console.warn('[Context Passport] Message ingestion failed:', err);
    }
  });
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
