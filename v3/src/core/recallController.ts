import type { SiteAdapter } from '../adapters/types.ts';
import type { MemoryItem, PreferenceItem } from './injection.ts';
import { applyMemoryBlockToDraft, formatMemoryBlock, stripExtensionInjectedContext } from './injection.ts';
import { containsSecret } from './screener.ts';
import { screenRecall } from './recall.ts';
import type { RecallCandidates } from './recall.ts';


export interface UseMemoryOptions {
  adapter: SiteAdapter;
  apiClient: {
    queryMemories(query: string, limit?: number): Promise<RecallCandidates>;
  };
  button: HTMLButtonElement;
  onSensitivePrompt?: (sensitive: MemoryItem[]) => Promise<boolean>;
  onFallback?: (preparedText: string) => void;
  onError?: (error: Error) => void;
}

export interface UseMemoryResult {
  success: boolean;
  preparedPrompt?: string;
  reason?: 'secret_detected' | 'no_memories' | 'draft_changed' | 'backend_error' | 'auth_required' | 'attached';
  fallbackTriggered?: boolean;
  approvedSensitiveCount?: number;
  generalCount?: number;
}

function escapeHtml(str: string): string {
  if (!str) return '';
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

let settleSensitiveCard: ((allowed: boolean) => void) | null = null;

function privatePreview(text: string): string {
  return escapeHtml(text.replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi, '[email]')
    .replace(/\b\+?\d[\d .()\-]{8,}\d\b/g, '[number]')
    .slice(0, 90));
}

/** A compact, non-modal decision card; no sensitive item is used by default. */
export function promptSensitiveApproval(sensitiveMemories: MemoryItem[]): Promise<boolean> {
  return new Promise((resolve) => {
    if (typeof document === 'undefined' || !document.body) {
      resolve(false);
      return;
    }

    settleSensitiveCard?.(false);
    const card = document.createElement('section');
    card.id = 'cp-sensitive-consent';
    card.className = 'cp-sensitive-consent';
    card.setAttribute('role', 'group');
    card.setAttribute('aria-label', 'Private memory available');

    const itemsHtml = sensitiveMemories
      .map(
        (m) => `
        <div class="cp-sensitive-item">
          <span class="cp-sensitive-badge">Sensitive</span>
          <span>${privatePreview(m.text)}</span>
        </div>`
      )
      .join('');

    card.innerHTML = `
      <div class="cp-consent-card">
        <div class="cp-modal-header">
          <div class="cp-modal-title">Private memory available</div>
          <button type="button" id="cp-close-btn" aria-label="Use general only and close">×</button>
        </div>
        <div class="cp-modal-desc">
          ${sensitiveMemories.length} private ${sensitiveMemories.length === 1 ? 'memory' : 'memories'} found. General context is ready.
        </div>
        <div class="cp-sensitive-preview">
          ${itemsHtml}
        </div>
        <div class="cp-modal-actions">
          <button type="button" class="cp-btn-secondary" id="cp-deny-btn">Use general only</button>
          <button type="button" class="cp-btn-primary" id="cp-allow-btn">Allow once</button>
        </div>
      </div>
    `;

    document.body.appendChild(card);

    const cleanup = (allowed: boolean) => {
      if (settleSensitiveCard === cleanup) settleSensitiveCard = null;
      card.remove();
      resolve(allowed);
    };
    settleSensitiveCard = cleanup;
    card.querySelector('#cp-allow-btn')?.addEventListener('click', () => cleanup(true));
    card.querySelector('#cp-deny-btn')?.addEventListener('click', () => cleanup(false));
    card.querySelector('#cp-close-btn')?.addEventListener('click', () => cleanup(false));
  });
}

/**
 * Displays the universal manual copy fallback modal when composer cannot be controlled directly
 */
export function showComposerFallbackUI(preparedText: string): HTMLElement {
  if (typeof document === 'undefined' || !document.body) {
    return null as any;
  }

  const existing = document.getElementById('cp-composer-fallback-backdrop');
  if (existing) existing.remove();

  const backdrop = document.createElement('div');
  backdrop.id = 'cp-composer-fallback-backdrop';
  backdrop.className = 'cp-modal-backdrop cp-fallback-backdrop';

  backdrop.innerHTML = `
    <div class="cp-modal-card cp-fallback-card" role="dialog" aria-modal="true" aria-label="Manual Copy Fallback">
      <div class="cp-modal-header">
        <div class="cp-modal-icon cp-fallback-icon">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>
            <rect x="8" y="2" width="8" height="4" rx="1" ry="1"/>
          </svg>
        </div>
        <div class="cp-modal-title">Manual Copy Fallback</div>
      </div>
      <div class="cp-modal-desc">
        The site composer could not be controlled directly. Your prepared prompt with verified memory context is ready:
      </div>
      <div class="cp-fallback-body">
        <textarea class="cp-fallback-textarea" readonly rows="8">${escapeHtml(preparedText)}</textarea>
      </div>
      <div class="cp-modal-actions">
        <button type="button" class="cp-btn-secondary" id="cp-fallback-dismiss-btn">Dismiss</button>
        <button type="button" class="cp-btn-primary" id="cp-fallback-copy-btn">Copy to Clipboard</button>
      </div>
    </div>
  `;

  document.body.appendChild(backdrop);

  const copyBtn = backdrop.querySelector<HTMLButtonElement>('#cp-fallback-copy-btn');
  const dismissBtn = backdrop.querySelector<HTMLButtonElement>('#cp-fallback-dismiss-btn');
  const textarea = backdrop.querySelector<HTMLTextAreaElement>('.cp-fallback-textarea');

  copyBtn?.addEventListener('click', async () => {
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(preparedText);
      } else if (textarea) {
        textarea.select();
        document.execCommand('copy');
      }
      if (copyBtn) copyBtn.textContent = 'Copied!';
      setTimeout(() => {
        backdrop.remove();
      }, 1200);
    } catch {
      if (copyBtn) copyBtn.textContent = 'Failed to copy';
    }
  });

  dismissBtn?.addEventListener('click', () => {
    backdrop.remove();
  });

  return backdrop;
}

/**
 * Displays an error toast message in the bottom corner of the viewport
 */
export function showErrorToast(message: string): HTMLElement {
  if (typeof document === 'undefined' || !document.body) {
    return null as any;
  }

  const existing = document.getElementById('cp-error-toast');
  if (existing) existing.remove();

  const toast = document.createElement('div');
  toast.id = 'cp-error-toast';
  toast.className = 'cp-error-toast';
  toast.textContent = message;
  document.body.appendChild(toast);

  setTimeout(() => {
    toast.remove();
  }, 3500);

  return toast;
}

/**
 * Orchestrates the full "Use Memory" workflow:
 * query handling, up-to-three general selection, Privacy Firewall approval modal,
 * composer insertion, replacement of old blocks, and fallback copy UI.
 */
export async function handleUseMemoryAction(options: UseMemoryOptions): Promise<UseMemoryResult> {
  const { adapter, apiClient, button } = options;
  if (button.disabled) return { success: false, reason: 'draft_changed' };

  button.disabled = true;
  const btnLabel = button.querySelector('span');

  const currentDraft = adapter.getComposerDraft();
  const cleanDraft = stripExtensionInjectedContext(currentDraft).trim();

  // Screen for recognizable secret in user draft before sending to backend
  if (cleanDraft && containsSecret(cleanDraft)) {
    if (btnLabel) btnLabel.textContent = 'Secret in prompt!';
    button.disabled = false;
    button.classList.remove('loading');
    if (options.onError) {
      options.onError(new Error('Secret in prompt'));
    } else {
      showErrorToast('Context Passport: Recognizable secret detected in prompt. Memory recall cancelled for privacy.');
    }
    setTimeout(() => {
      if (btnLabel && btnLabel.textContent === 'Secret in prompt!') {
        btnLabel.textContent = 'Use Memory';
      }
    }, 2500);
    return { success: false, reason: 'secret_detected' };
  }

  try {
    if (btnLabel) btnLabel.textContent = 'Recalling...';
    button.classList.add('loading');

    const query = cleanDraft || 'General user preferences and active context';
    const response = await apiClient.queryMemories(query, 3);

    const { general: finalGeneral, sensitive: sensitiveCandidates, preferences } = screenRecall(response);

    // If no memories found, strip any old injected block and notify user
    if (finalGeneral.length === 0 && sensitiveCandidates.length === 0 && preferences.length === 0) {
      adapter.setComposerDraft(stripExtensionInjectedContext(adapter.getComposerDraft()));
      if (btnLabel) btnLabel.textContent = 'No memories found';
      button.classList.remove('loading');
      button.disabled = false;
      setTimeout(() => {
        if (btnLabel && btnLabel.textContent === 'No memories found') {
          btnLabel.textContent = 'Use Memory';
        }
      }, 2000);
      return {
        success: true,
        preparedPrompt: cleanDraft,
        reason: 'no_memories',
        generalCount: 0,
        approvedSensitiveCount: 0,
      };
    }

    let approvedSensitive: MemoryItem[] = [];
    if (sensitiveCandidates.length > 0) {
      const promptFn = options.onSensitivePrompt || promptSensitiveApproval;
      const allowed = await promptFn(sensitiveCandidates);
      if (allowed) {
        approvedSensitive = sensitiveCandidates;
      }
    }

    // Format approved memory block and apply to draft
    const memoryBlock = formatMemoryBlock(finalGeneral, approvedSensitive, preferences);
    const latestDraft = stripExtensionInjectedContext(adapter.getComposerDraft()).trim();

    // Guard against draft modifications while query was in flight
    if (latestDraft !== cleanDraft) {
      adapter.setComposerDraft(latestDraft);
      if (btnLabel) btnLabel.textContent = 'Draft changed—retry';
      button.classList.remove('loading');
      button.disabled = false;
      setTimeout(() => {
        if (btnLabel && btnLabel.textContent === 'Draft changed—retry') {
          btnLabel.textContent = 'Use Memory';
        }
      }, 2000);
      return { success: false, reason: 'draft_changed' };
    }

    const updatedComposerText = applyMemoryBlockToDraft(latestDraft, memoryBlock);

    // Attempt composer write
    adapter.setComposerDraft(updatedComposerText);
    await new Promise((resolve) => setTimeout(resolve, 50));

    const composer = adapter.getComposer();
    const currentComposerText = adapter.getComposerDraft();
    const isComposerUpdated = Boolean(composer) && currentComposerText.trim() === updatedComposerText.trim();

    if (!isComposerUpdated) {
      // Site composer could not be controlled: trigger manual copy/paste fallback
      if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        try {
          await navigator.clipboard.writeText(updatedComposerText);
        } catch {}
      }

      if (options.onFallback) {
        options.onFallback(updatedComposerText);
      } else {
        showComposerFallbackUI(updatedComposerText);
      }

      if (btnLabel) btnLabel.textContent = 'Copied to clipboard';
      button.classList.remove('loading');
      button.disabled = false;
      setTimeout(() => {
        if (btnLabel && btnLabel.textContent === 'Copied to clipboard') {
          btnLabel.textContent = 'Use Memory';
        }
      }, 2500);

      return {
        success: true,
        preparedPrompt: updatedComposerText,
        fallbackTriggered: true,
        generalCount: finalGeneral.length,
        approvedSensitiveCount: approvedSensitive.length,
        reason: 'attached',
      };
    }

    // Successfully attached directly into composer
    if (btnLabel) btnLabel.textContent = 'Attached!';
    button.classList.remove('loading');
    button.disabled = false;
    setTimeout(() => {
      if (btnLabel && btnLabel.textContent === 'Attached!') {
        btnLabel.textContent = 'Use Memory';
      }
    }, 2000);

    return {
      success: true,
      preparedPrompt: updatedComposerText,
      fallbackTriggered: false,
      generalCount: finalGeneral.length,
      approvedSensitiveCount: approvedSensitive.length,
      reason: 'attached',
    };
  } catch (err: any) {
    const rawMsg = err?.message || 'Backend error';
    const isAuthErr = /HTTP 401|HTTP 403|token_expired|AuthRequiredError|Session expired/i.test(rawMsg);
    if (btnLabel) btnLabel.textContent = isAuthErr ? 'Sign in again' : 'Backend error';
    button.classList.add('cp-btn-error');
    button.classList.remove('loading');
    button.disabled = false;
    if (options.onError) {
      options.onError(err);
    } else {
      const userMsg = isAuthErr
        ? 'Your session expired. Click Sign in again to open Pelican; your draft is safe.'
        : `Failed to retrieve memories. ${rawMsg}`;
      showErrorToast(`Context Passport: ${userMsg}`);
    }
    setTimeout(() => {
      if (btnLabel && btnLabel.textContent === 'Backend error') {
        btnLabel.textContent = 'Use Memory';
        button.classList.remove('cp-btn-error');
      }
    }, 3000);
    return { success: false, reason: isAuthErr ? 'auth_required' : 'backend_error' };
  }
}
