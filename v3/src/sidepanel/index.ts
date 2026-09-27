/**
 * Context Passport Side Panel Logic
 */

import { ContextPassportApiClient, BackendMemoryDoc, BackendPreferenceDoc } from '../core/api';
import { applyMemoryBlockToDraft, formatMemoryBlock, MemoryItem, PreferenceItem } from '../core/injection';
import { containsSecret } from '../core/screener';
import { screenRecall } from '../core/recall';
import { DEFAULT_BACKEND_URL, getAllSettings, getSetting, isSiteCaptureEnabled, setSetting, setSiteCaptureEnabled } from '../core/storage';

let apiClient: ContextPassportApiClient;

async function syncAccountView() {
  const token = await getSetting('auth_token');
  const email = await getSetting('user_email');
  const signedIn = document.getElementById('cp-signed-in');
  const signIn = document.getElementById('cp-sign-in-form');
  if (signedIn) signedIn.hidden = !token;
  if (signIn) signIn.hidden = Boolean(token);
  const label = document.getElementById('cp-account-email');
  if (label) label.textContent = email || 'Your account';
}

async function ensureBackendPermission(rawUrl: string): Promise<void> {
  let url: URL;
  try { url = new URL(rawUrl); } catch { throw new Error('Enter a valid backend URL.'); }
  if (url.pathname !== '/' || url.search || url.hash) throw new Error('Enter the backend origin without a path or query.');
  const local = url.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(url.hostname) && url.port === '8000';
  if (!local && url.protocol !== 'https:') throw new Error('Use HTTPS for a hosted backend.');
  if (local) return;
  const origins = [`${url.origin}/*`];
  if (!(await chrome.permissions.request({ origins }))) {
    throw new Error('Allow access to this backend to continue.');
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  const settings = await getAllSettings();
  apiClient = new ContextPassportApiClient(settings.backend_url, settings.auth_token);

  initTabs();
  initSettings(settings);
  initConnectionStatus();
  initVault();
  initPreferences();
  initFallback();
  await syncAccountView();
  if (!settings.auth_token) switchToTab('settings');
  chrome.storage?.onChanged?.addListener((changes, area) => {
    if (area !== 'local' || !changes.auth_token) return;
    apiClient.setToken(changes.auth_token.newValue || '');
    void syncAccountView();
    if (!changes.auth_token.newValue) {
      void loadVaultMemories();
      void loadPreferences();
    }
  });
  document.getElementById('open-dashboard-btn')?.addEventListener('click', () => {
    chrome.tabs.create({ url: chrome.runtime.getURL('dashboard.html') });
  });
  document.getElementById('open-onboarding-btn')?.addEventListener('click', () => {
    chrome.tabs.create({ url: chrome.runtime.getURL('onboarding.html') });
  });
});

// Tab Navigation & Keyboard Switching
function switchToTab(target: string) {
  const tabBtns = document.querySelectorAll<HTMLButtonElement>('.cp-nav-btn');
  const tabPanes = document.querySelectorAll<HTMLElement>('.cp-tab-pane');

  tabBtns.forEach((btn) => {
    const isTarget = btn.getAttribute('data-tab') === target;
    btn.classList.toggle('active', isTarget);
    btn.setAttribute('aria-selected', isTarget ? 'true' : 'false');
  });

  tabPanes.forEach((pane) => {
    pane.classList.toggle('active', pane.id === `tab-${target}`);
  });

  if (target === 'vault') loadVaultMemories();
  if (target === 'preferences') loadPreferences();
}

function initTabs() {
  const tabBtns = document.querySelectorAll<HTMLButtonElement>('.cp-nav-btn');

  tabBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = btn.getAttribute('data-tab');
      if (target) switchToTab(target);
    });

    btn.addEventListener('keydown', (e: KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        const target = btn.getAttribute('data-tab');
        if (target) switchToTab(target);
      }
    });
  });
}

// Connection Status
async function initConnectionStatus() {
  const badge = document.getElementById('connection-badge');
  if (!badge) return;

  const health = await apiClient.checkHealth();
  if (!health.ok) {
    badge.textContent = 'Offline';
    badge.className = 'cp-badge cp-badge-error';
    return;
  }
  const ready = await apiClient.checkReady();
  if (!ready.ready) {
    badge.textContent = 'Backend not ready';
    badge.className = 'cp-badge cp-badge-error';
    return;
  }
  if (!(await getSetting('auth_token'))) {
    badge.textContent = 'Sign in';
    badge.className = 'cp-badge cp-badge-neutral';
    return;
  }
  try {
    await apiClient.getPreferences();
    badge.textContent = 'Connected';
    badge.className = 'cp-badge cp-badge-success';
  } catch (err: any) {
    badge.textContent = /HTTP 401|HTTP 403|token_expired|Session expired/i.test(err?.message || '') ? 'Sign in again' : 'Backend error';
    badge.className = 'cp-badge cp-badge-error';
  }
}

// Settings & Per-Site Toggles
async function initSettings(settings: any) {
  const toggleChatGPT = document.getElementById('toggle-chatgpt') as HTMLInputElement | null;
  const toggleClaude = document.getElementById('toggle-claude') as HTMLInputElement | null;
  const toggleGemini = document.getElementById('toggle-gemini') as HTMLInputElement | null;

  const urlInput = document.getElementById('setting-backend-url') as HTMLInputElement | null;
  const tokenInput = document.getElementById('setting-auth-token') as HTMLInputElement | null;
  const emailInput = document.getElementById('setting-email') as HTMLInputElement | null;
  const passwordInput = document.getElementById('setting-password') as HTMLInputElement | null;
  const signInBtn = document.getElementById('sign-in-btn') as HTMLButtonElement | null;
  const signOutBtn = document.getElementById('sign-out-btn') as HTMLButtonElement | null;
  const authMessage = document.getElementById('auth-message');
  const saveBtn = document.getElementById('save-settings-btn') as HTMLButtonElement | null;

  if (toggleChatGPT) {
    toggleChatGPT.checked = await isSiteCaptureEnabled('chatgpt');
    toggleChatGPT.addEventListener('change', async () => {
      await setSiteCaptureEnabled('chatgpt', toggleChatGPT.checked);
    });
  }

  if (toggleClaude) {
    toggleClaude.checked = await isSiteCaptureEnabled('claude');
    toggleClaude.addEventListener('change', async () => {
      await setSiteCaptureEnabled('claude', toggleClaude.checked);
    });
  }

  if (toggleGemini) {
    toggleGemini.checked = await isSiteCaptureEnabled('gemini');
    toggleGemini.addEventListener('change', async () => {
      await setSiteCaptureEnabled('gemini', toggleGemini.checked);
    });
  }

  if (urlInput) urlInput.value = settings.backend_url || DEFAULT_BACKEND_URL;
  if (tokenInput) tokenInput.value = settings.auth_token || '';
  if (emailInput) emailInput.value = settings.user_email || '';

  signInBtn?.addEventListener('click', async () => {
    const email = emailInput?.value.trim() || '';
    const password = passwordInput?.value || '';
    if (!email || !password) {
      if (authMessage) {
        authMessage.textContent = 'Enter your email and password.';
        authMessage.className = 'cp-hint-text cp-hint-error';
      }
      return;
    }
    signInBtn.disabled = true;
    try {
      const backendUrl = urlInput?.value.trim() || DEFAULT_BACKEND_URL;
      await ensureBackendPermission(backendUrl);
      apiClient.setBackendUrl(backendUrl);
      const token = await apiClient.login(email, password);
      await setSetting('backend_url', backendUrl);
      await setSetting('auth_token', token);
      await setSetting('user_email', email);
      await syncAccountView();
      if (tokenInput) tokenInput.value = token;
      if (authMessage) {
        authMessage.textContent = 'Signed in successfully. Your password was not saved.';
        authMessage.className = 'cp-hint-text cp-hint-success';
      }
      await initConnectionStatus();
      await loadVaultMemories();
      await loadPreferences();
    } catch (err: any) {
      if (authMessage) {
        authMessage.textContent = `Sign-in failed: ${err.message || 'Try again.'}`;
        authMessage.className = 'cp-hint-text cp-hint-error';
      }
    } finally {
      if (passwordInput) passwordInput.value = '';
      signInBtn.disabled = false;
    }
  });

  signOutBtn?.addEventListener('click', async () => {
    await apiClient.logout();
    await syncAccountView();
    if (tokenInput) tokenInput.value = '';
    if (passwordInput) passwordInput.value = '';
    if (emailInput) emailInput.value = '';
    if (authMessage) {
      authMessage.textContent = 'Signed out successfully.';
      authMessage.className = 'cp-hint-text cp-hint-success';
    }
    await initConnectionStatus();
    await loadVaultMemories();
    await loadPreferences();
  });

  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      const newUrl = urlInput?.value.trim() || DEFAULT_BACKEND_URL;
      const newToken = tokenInput?.value.trim() || '';

      try { await ensureBackendPermission(newUrl); }
      catch (error: any) {
        if (authMessage) {
          authMessage.textContent = error?.message || 'Backend permission was not granted.';
          authMessage.className = 'cp-hint-text cp-hint-error';
        }
        return;
      }

      await setSetting('backend_url', newUrl);
      await setSetting('auth_token', newToken);

      apiClient.setBackendUrl(newUrl);
      apiClient.setToken(newToken);
      if (!newToken) await setSetting('refresh_token', '');
      await syncAccountView();

      saveBtn.textContent = 'Saved!';
      setTimeout(() => (saveBtn.textContent = 'Save Settings'), 1500);

      await initConnectionStatus();
      await loadVaultMemories();
      await loadPreferences();
    });
  }
}

// Memory Vault
function initVault() {
  const refreshBtn = document.getElementById('vault-refresh-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => loadVaultMemories());
  }
  loadVaultMemories();
}

async function loadVaultMemories() {
  const listEl = document.getElementById('vault-list');
  if (!listEl) return;

  const token = await getSetting('auth_token');
  if (!token) {
    listEl.innerHTML = `
      <div class="cp-empty-state" role="status">
        <div class="cp-empty-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.5">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
            <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
          </svg>
        </div>
        <div class="cp-empty-title">Sign in to view your vault</div>
        <p class="cp-empty-desc">Open the Settings tab to sign in or supply your Supabase auth token.</p>
        <button type="button" class="cp-btn cp-btn-secondary cp-empty-action" id="vault-open-settings-btn">Open Settings</button>
      </div>
    `;
    listEl.querySelector('#vault-open-settings-btn')?.addEventListener('click', () => switchToTab('settings'));
    return;
  }

  listEl.innerHTML = `
    <div class="cp-loading-state" role="status" aria-live="polite">
      <div class="cp-spinner" aria-hidden="true"></div>
      <p class="cp-loading-text">Loading memories...</p>
    </div>
  `;

  try {
    const memories = await apiClient.getAllMemories();
    if (!memories || memories.length === 0) {
      listEl.innerHTML = `
        <div class="cp-empty-state" role="status">
          <div class="cp-empty-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
          </div>
          <div class="cp-empty-title">No memories stored yet</div>
          <p class="cp-empty-desc">Enable capture in Settings and converse on ChatGPT, Claude, or Gemini to automatically distill facts.</p>
        </div>
      `;
      return;
    }

    listEl.innerHTML = '';
    memories.forEach((mem) => listEl.appendChild(renderMemoryCard(mem)));
  } catch (err: any) {
    const isAuthError = /HTTP 401|HTTP 403|token_expired|Session expired/i.test(err?.message || '');
    const isOffline = /failed to fetch|offline|networkerror|econnrefused/i.test(err?.message || '');
    let title = 'Error loading memories';
    let desc = escapeHtml(err?.message || 'An unexpected error occurred.');
    let actionLabel = 'Retry';
    let actionCallback: () => void = () => loadVaultMemories();

    if (isAuthError) {
      title = 'Authentication expired';
      desc = 'Your session has expired. Please sign in again from the Settings tab.';
      actionLabel = 'Go to Settings';
      actionCallback = () => switchToTab('settings');
    } else if (isOffline) {
      title = 'Backend server offline';
      desc = 'Could not reach the configured backend. Check the backend URL in Settings and verify it is running.';
    }

    listEl.innerHTML = `
      <div class="cp-error-state" role="alert" aria-live="assertive">
        <div class="cp-error-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"/>
            <line x1="12" y1="8" x2="12" y2="12"/>
            <line x1="12" y1="16" x2="12.01" y2="16"/>
          </svg>
        </div>
        <div class="cp-error-title">${title}</div>
        <div class="cp-error-desc">${desc}</div>
        <div class="cp-error-actions">
          <button type="button" class="cp-btn cp-btn-secondary cp-error-btn" id="vault-error-action-btn">${actionLabel}</button>
        </div>
      </div>
    `;
    listEl.querySelector('#vault-error-action-btn')?.addEventListener('click', actionCallback);
  }
}

function actionButton(label: string, action: () => Promise<void>, danger = false): HTMLButtonElement {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = `cp-card-btn${danger ? ' cp-card-btn-danger' : ''}`;
  button.textContent = label;
  button.addEventListener('click', async () => {
    button.disabled = true;
    try { await action(); } catch (error: any) {
      const status = button.closest('.cp-card')?.querySelector<HTMLElement>('.cp-card-status');
      if (status) status.textContent = error?.message || 'Action failed. Try again.';
    } finally { button.disabled = false; }
  });
  return button;
}

function editCard(card: HTMLElement, initialText: string, save: (value: string) => Promise<void>) {
  const editor = document.createElement('div');
  editor.className = 'cp-card-editor';
  const input = document.createElement('textarea');
  input.className = 'cp-textarea';
  input.value = initialText;
  input.rows = 3;
  input.setAttribute('aria-label', 'Correct wording');
  const actions = document.createElement('div');
  actions.className = 'cp-card-actions';
  actions.append(actionButton('Save correction', async () => {
    const value = input.value.trim();
    if (!value) throw new Error('Wording cannot be empty.');
    if (containsSecret(value)) throw new Error('Correction cannot contain credentials or secrets.');
    await save(value);
  }), actionButton('Cancel', async () => { editor.remove(); }));
  editor.append(input, actions);
  card.appendChild(editor);
  input.focus();
}

function cardStatus(card: HTMLElement): HTMLElement {
  const status = document.createElement('div');
  status.className = 'cp-card-status';
  status.setAttribute('role', 'status');
  card.appendChild(status);
  return status;
}

function renderMemoryCard(mem: BackendMemoryDoc): HTMLElement {
  const card = document.createElement('article');
  card.className = 'cp-card';
  card.setAttribute('data-id', mem.id);
  const top = document.createElement('div');
  top.className = 'cp-card-top';
  for (const [value, tone] of [[mem.classification || 'general', mem.classification === 'sensitive' ? 'error' : 'neutral'], [mem.status || 'active', mem.status === 'blocked' ? 'error' : 'success']]) {
    const badge = document.createElement('span');
    badge.className = `cp-badge cp-badge-${tone}`;
    badge.textContent = value;
    top.appendChild(badge);
  }
  const wording = document.createElement('div');
  wording.className = 'cp-card-text';
  wording.textContent = mem.text;
  const evidence = document.createElement('p');
  evidence.className = 'cp-evidence';
  evidence.textContent = `Evidence: ${mem.evidence_excerpt || 'No excerpt available'}`;
  const meta = document.createElement('div');
  meta.className = 'cp-card-meta';
  meta.textContent = `${mem.source || 'Unknown source'} · ${formatDate(mem.created_at) || 'Date unavailable'}`;
  const actions = document.createElement('div');
  actions.className = 'cp-card-actions';
  actions.appendChild(actionButton('Correct', async () => {
    card.querySelector('.cp-card-editor')?.remove();
    editCard(card, mem.text, async (value) => { await apiClient.updateMemory(mem.id, value); await loadVaultMemories(); });
  }));
  if (mem.status !== 'blocked') actions.appendChild(actionButton('Block', async () => {
    if (!(await apiClient.blockMemory(mem.id))) throw new Error('Could not block memory.');
    await loadVaultMemories();
  }));
  actions.appendChild(actionButton('Forget', async () => {
    if (!window.confirm('Forget this memory and its stored history?')) return;
    if (!(await apiClient.deleteMemory(mem.id))) throw new Error('Could not forget memory.');
    await loadVaultMemories();
  }, true));
  card.append(top, wording, evidence, meta, actions);
  cardStatus(card);
  return card;
}

// Learned Preferences
function initPreferences() {
  const refreshBtn = document.getElementById('pref-refresh-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => loadPreferences());
  }
  loadPreferences();
}

async function loadPreferences() {
  const listEl = document.getElementById('preferences-list');
  if (!listEl) return;

  const token = await getSetting('auth_token');
  if (!token) {
    listEl.innerHTML = `
      <div class="cp-empty-state" role="status">
        <div class="cp-empty-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
          </svg>
        </div>
        <div class="cp-empty-title">Sign in to view preferences</div>
        <p class="cp-empty-desc">Open the Settings tab to sign in or supply your Supabase auth token.</p>
        <button type="button" class="cp-btn cp-btn-secondary cp-empty-action" id="pref-open-settings-btn">Open Settings</button>
      </div>
    `;
    listEl.querySelector('#pref-open-settings-btn')?.addEventListener('click', () => switchToTab('settings'));
    return;
  }

  listEl.innerHTML = `
    <div class="cp-loading-state" role="status" aria-live="polite">
      <div class="cp-spinner" aria-hidden="true"></div>
      <p class="cp-loading-text">Loading preferences...</p>
    </div>
  `;

  try {
    const preferences = await apiClient.getPreferences();
    if (!preferences || preferences.length === 0) {
      listEl.innerHTML = `
        <div class="cp-empty-state" role="status">
          <div class="cp-empty-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/>
            </svg>
          </div>
          <div class="cp-empty-title">No preferences promoted yet</div>
          <p class="cp-empty-desc">Context Passport learns how you like explanations after 3 distinct observations across at least 2 separate chats.</p>
        </div>
      `;
      return;
    }

    listEl.innerHTML = '';
    preferences.forEach((pref) => listEl.appendChild(renderPreferenceCard(pref)));
  } catch (err: any) {
    const isAuthError = /HTTP 401|HTTP 403|token_expired|Session expired/i.test(err?.message || '');
    const isOffline = /failed to fetch|offline|networkerror|econnrefused/i.test(err?.message || '');
    let title = 'Error loading preferences';
    let desc = escapeHtml(err?.message || 'An unexpected error occurred.');
    let actionLabel = 'Retry';
    let actionCallback: () => void = () => loadPreferences();

    if (isAuthError) {
      title = 'Authentication expired';
      desc = 'Your session has expired. Please sign in again from the Settings tab.';
      actionLabel = 'Go to Settings';
      actionCallback = () => switchToTab('settings');
    } else if (isOffline) {
      title = 'Backend server offline';
      desc = 'Could not reach the configured backend. Check the backend URL in Settings and verify it is running.';
    }

    listEl.innerHTML = `
      <div class="cp-error-state" role="alert" aria-live="assertive">
        <div class="cp-error-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"/>
            <line x1="12" y1="8" x2="12" y2="12"/>
            <line x1="12" y1="16" x2="12.01" y2="16"/>
          </svg>
        </div>
        <div class="cp-error-title">${title}</div>
        <div class="cp-error-desc">${desc}</div>
        <div class="cp-error-actions">
          <button type="button" class="cp-btn cp-btn-secondary cp-error-btn" id="pref-error-action-btn">${actionLabel}</button>
        </div>
      </div>
    `;
    listEl.querySelector('#pref-error-action-btn')?.addEventListener('click', actionCallback);
  }
}

function renderPreferenceCard(pref: BackendPreferenceDoc): HTMLElement {
  const card = document.createElement('article');
  card.className = 'cp-card';
  card.setAttribute('data-id', pref.id);
  const top = document.createElement('div');
  top.className = 'cp-card-top';
  const classBadge = document.createElement('span');
  classBadge.className = 'cp-badge cp-badge-neutral';
  classBadge.textContent = 'general';
  const state = document.createElement('span');
  let statusTone = 'neutral';
  if (pref.status === 'active') statusTone = 'success';
  else if (pref.status === 'blocked') statusTone = 'error';
  else if (pref.status === 'insufficient_evidence') statusTone = 'warning';
  state.className = `cp-badge cp-badge-${statusTone}`;
  state.textContent = pref.status || 'active';
  const evidenceCount = document.createElement('span');
  evidenceCount.className = 'cp-badge cp-badge-neutral';
  evidenceCount.textContent = pref.locked ? 'Your wording · locked' : `${pref.evidence_count || 0} observations`;
  top.append(classBadge, state, evidenceCount);
  const wording = document.createElement('div');
  wording.className = 'cp-card-text';
  wording.textContent = pref.preference_text;
  const meta = document.createElement('div');
  meta.className = 'cp-card-meta';
  meta.textContent = `${pref.preference_key.replaceAll('_', ' ')} · ${pref.conversation_count || 0} chats`;
  const evidence = document.createElement('div');
  evidence.className = 'cp-evidence-list';
  for (const observation of pref.supporting_observations || []) {
    const row = document.createElement('div');
    row.className = 'cp-evidence-row';
    const excerpt = document.createElement('span');
    excerpt.textContent = observation.excerpt || '[Evidence redacted]';
    row.append(excerpt, actionButton('Remove', async () => {
      if (!(await apiClient.removePreferenceEvidence(pref.id, observation.id))) throw new Error('Could not remove evidence.');
      await loadPreferences();
    }));
    evidence.appendChild(row);
  }
  const actions = document.createElement('div');
  actions.className = 'cp-card-actions';
  actions.appendChild(actionButton('Correct', async () => {
    card.querySelector('.cp-card-editor')?.remove();
    editCard(card, pref.preference_text, async (value) => {
      await apiClient.updatePreference(pref.id, value);
      await loadPreferences();
    });
  }));
  if (pref.status !== 'blocked') actions.appendChild(actionButton('Block', async () => {
    if (!(await apiClient.blockPreference(pref.id))) throw new Error('Could not block preference.');
    await loadPreferences();
  }));
  actions.appendChild(actionButton('Forget', async () => {
    if (!window.confirm('Forget this preference and remove its supporting evidence?')) return;
    if (!(await apiClient.forgetPreference(pref.id))) throw new Error('Could not forget preference.');
    await loadPreferences();
  }, true));
  card.append(top, wording, meta, evidence, actions);
  cardStatus(card);
  return card;
}

// Universal Fallback Route
function initFallback() {
  const draftInput = document.getElementById('fallback-input') as HTMLTextAreaElement | null;
  const retrieveBtn = document.getElementById('fallback-retrieve-btn') as HTMLButtonElement | null;
  const sensitivePanel = document.getElementById('fallback-sensitive-panel') as HTMLElement | null;
  const sensitiveText = document.getElementById('fallback-sensitive-text') as HTMLElement | null;
  const allowSensitiveCb = document.getElementById('fallback-allow-sensitive') as HTMLInputElement | null;
  const outputText = document.getElementById('fallback-output') as HTMLTextAreaElement | null;
  const copyBtn = document.getElementById('fallback-copy-btn') as HTMLButtonElement | null;

  let lastGeneralMemories: MemoryItem[] = [];
  let lastSensitiveMemories: MemoryItem[] = [];
  let lastPreferences: PreferenceItem[] = [];

  const updatePreparedOutput = () => {
    if (!outputText || !draftInput) return;
    const includeSensitive = allowSensitiveCb ? allowSensitiveCb.checked : false;
    const approvedSensitive = includeSensitive ? lastSensitiveMemories : [];

    const block = formatMemoryBlock(lastGeneralMemories, approvedSensitive, lastPreferences);
    const prepared = applyMemoryBlockToDraft(draftInput.value, block);
    outputText.value = prepared;
  };

  if (retrieveBtn) {
    retrieveBtn.addEventListener('click', async () => {
      const query = draftInput?.value.trim() || 'General user preferences';
      if (containsSecret(query)) {
        if (outputText) outputText.value = '';
        if (sensitivePanel) sensitivePanel.style.display = 'none';
        alert('Recognizable secret in draft. Memory recall cancelled for privacy.');
        return;
      }
      retrieveBtn.textContent = 'Retrieving...';

      try {
        const res = await apiClient.queryMemories(query, 3);
        if (draftInput?.value.trim() !== query) {
          retrieveBtn.textContent = 'Draft changed—retrieve again';
          return;
        }
        const screened = screenRecall(res);
        lastGeneralMemories = screened.general;
        lastSensitiveMemories = screened.sensitive;
        lastPreferences = screened.preferences;

        // Check for sensitive memories
        if (lastSensitiveMemories.length > 0 && sensitivePanel && sensitiveText) {
          sensitivePanel.style.display = 'block';
          sensitiveText.textContent = lastSensitiveMemories.map((m) => m.text).join('; ');
          if (allowSensitiveCb) allowSensitiveCb.checked = false; // default unchecked
        } else if (sensitivePanel) {
          sensitivePanel.style.display = 'none';
        }

        updatePreparedOutput();
        retrieveBtn.textContent = 'Memories Retrieved!';
        setTimeout(() => (retrieveBtn.textContent = 'Retrieve Relevant Memories'), 2000);
      } catch (err: any) {
        alert(`Error querying memories: ${err.message}`);
        retrieveBtn.textContent = 'Retrieve Relevant Memories';
      }
    });
  }

  draftInput?.addEventListener('input', () => {
    lastGeneralMemories = [];
    lastSensitiveMemories = [];
    lastPreferences = [];
    if (allowSensitiveCb) allowSensitiveCb.checked = false;
    if (sensitivePanel) sensitivePanel.style.display = 'none';
    if (outputText) outputText.value = draftInput.value;
  });

  if (allowSensitiveCb) {
    allowSensitiveCb.addEventListener('change', () => {
      updatePreparedOutput();
    });
  }

  if (copyBtn) {
    copyBtn.addEventListener('click', async () => {
      if (!outputText || !outputText.value.trim()) return;
      await navigator.clipboard.writeText(outputText.value);
      copyBtn.textContent = 'Copied to Clipboard!';
      setTimeout(() => (copyBtn.textContent = 'Copy Prepared Prompt'), 2000);
    });
  }
}

function escapeHtml(str: string): string {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function formatDate(iso?: string): string {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch {
    return '';
  }
}
