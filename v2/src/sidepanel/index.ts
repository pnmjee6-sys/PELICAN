/**
 * Context Passport Side Panel Logic
 */

import { ContextPassportApiClient } from '../core/api';
import { applyMemoryBlockToDraft, formatMemoryBlock, MemoryItem, PreferenceItem } from '../core/injection';
import { containsSecret } from '../core/screener';
import { screenRecall } from '../core/recall';
import { getAllSettings, getSetting, isSiteCaptureEnabled, setSetting, setSiteCaptureEnabled } from '../core/storage';

let apiClient: ContextPassportApiClient;

document.addEventListener('DOMContentLoaded', async () => {
  const settings = await getAllSettings();
  apiClient = new ContextPassportApiClient(settings.backend_url, settings.auth_token);

  initTabs();
  initSettings(settings);
  initConnectionStatus();
  initVault();
  initPreferences();
  initFallback();
});

// Tab Navigation
function initTabs() {
  const tabBtns = document.querySelectorAll<HTMLButtonElement>('.cp-nav-btn');
  const tabPanes = document.querySelectorAll<HTMLElement>('.cp-tab-pane');

  tabBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = btn.getAttribute('data-tab');
      tabBtns.forEach((b) => b.classList.remove('active'));
      tabPanes.forEach((p) => p.classList.remove('active'));

      btn.classList.add('active');
      const pane = document.getElementById(`tab-${target}`);
      if (pane) pane.classList.add('active');

      if (target === 'vault') loadVaultMemories();
      if (target === 'preferences') loadPreferences();
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
    badge.textContent = /HTTP 401|HTTP 403/.test(err?.message || '') ? 'Sign in again' : 'Backend error';
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

  if (urlInput) urlInput.value = settings.backend_url || 'http://localhost:8000';
  if (tokenInput) tokenInput.value = settings.auth_token || '';
  if (emailInput) emailInput.value = settings.user_email || '';

  signInBtn?.addEventListener('click', async () => {
    const email = emailInput?.value.trim() || '';
    const password = passwordInput?.value || '';
    if (!email || !password) {
      if (authMessage) authMessage.textContent = 'Enter your email and password.';
      return;
    }
    signInBtn.disabled = true;
    try {
      apiClient.setBackendUrl(urlInput?.value.trim() || 'http://localhost:8000');
      const token = await apiClient.login(email, password);
      await setSetting('backend_url', urlInput?.value.trim() || 'http://localhost:8000');
      await setSetting('auth_token', token);
      await setSetting('user_email', email);
      if (tokenInput) tokenInput.value = token;
      if (authMessage) authMessage.textContent = 'Signed in. Your password was not saved.';
      await initConnectionStatus();
      await loadVaultMemories();
      await loadPreferences();
    } catch (err: any) {
      if (authMessage) authMessage.textContent = `Sign-in failed: ${err.message || 'Try again.'}`;
    } finally {
      if (passwordInput) passwordInput.value = '';
      signInBtn.disabled = false;
    }
  });

  signOutBtn?.addEventListener('click', async () => {
    await setSetting('auth_token', '');
    apiClient.setToken('');
    if (tokenInput) tokenInput.value = '';
    if (passwordInput) passwordInput.value = '';
    if (authMessage) authMessage.textContent = 'Signed out.';
    await initConnectionStatus();
    await loadVaultMemories();
    await loadPreferences();
  });

  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      const newUrl = urlInput?.value.trim() || 'http://localhost:8000';
      const newToken = tokenInput?.value.trim() || '';

      await setSetting('backend_url', newUrl);
      await setSetting('auth_token', newToken);

      apiClient.setBackendUrl(newUrl);
      apiClient.setToken(newToken);

      saveBtn.textContent = 'Saved!';
      setTimeout(() => (saveBtn.textContent = 'Save Settings'), 1500);

      await initConnectionStatus();
      await loadVaultMemories();
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

  listEl.innerHTML = '<div class="cp-empty-state">Loading memories...</div>';

  try {
    const memories = await apiClient.getAllMemories();
    if (!memories || memories.length === 0) {
      listEl.innerHTML = '<div class="cp-empty-state">No memories stored yet.</div>';
      return;
    }

    listEl.innerHTML = '';
    memories.forEach((mem) => {
      const card = document.createElement('div');
      card.className = 'cp-card';

      const isSensitive = mem.classification === 'sensitive';
      const isBlocked = mem.status === 'blocked';

      card.innerHTML = `
        <div class="cp-card-top">
          <span class="cp-badge ${isSensitive ? 'cp-badge-error' : 'cp-badge-neutral'}">
            ${mem.classification || 'general'}
          </span>
          <span class="cp-badge ${isBlocked ? 'cp-badge-error' : 'cp-badge-success'}">
            ${mem.status || 'active'}
          </span>
        </div>
        <div class="cp-card-text">${escapeHtml(mem.text)}</div>
        <div class="cp-card-footer">
          <span>${formatDate(mem.created_at)}</span>
          <div class="cp-card-actions">
            ${
              !isBlocked
                ? `<button class="cp-card-btn block-btn" data-id="${mem.id}">Block</button>`
                : ''
            }
            <button class="cp-card-btn cp-card-btn-danger delete-btn" data-id="${mem.id}">Forget</button>
          </div>
        </div>
      `;

      card.querySelector('.block-btn')?.addEventListener('click', async () => {
        await apiClient.blockMemory(mem.id);
        loadVaultMemories();
      });

      card.querySelector('.delete-btn')?.addEventListener('click', async () => {
        await apiClient.deleteMemory(mem.id);
        loadVaultMemories();
      });

      listEl.appendChild(card);
    });
  } catch (err: any) {
    listEl.innerHTML = `<div class="cp-empty-state">Failed to load memories: ${escapeHtml(err.message)}</div>`;
  }
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

  listEl.innerHTML = '<div class="cp-empty-state">Loading preferences...</div>';

  try {
    const preferences = await apiClient.getPreferences();
    if (!preferences || preferences.length === 0) {
      listEl.innerHTML = '<div class="cp-empty-state">No preferences promoted yet.<br><small>Promotes after 3 observations across 2+ chats.</small></div>';
      return;
    }

    listEl.innerHTML = '';
    preferences.forEach((pref: any) => {
      const card = document.createElement('div');
      card.className = 'cp-card';

      const obsCount = (pref.supporting_observation_ids || []).length;
      const isLocked = Boolean(pref.user_locked);

      card.innerHTML = `
        <div class="cp-card-top">
          <span class="cp-badge cp-badge-success">${pref.status || 'active'}</span>
          <span class="cp-badge ${isLocked ? 'cp-badge-neutral' : 'cp-badge-neutral'}">
            ${isLocked ? 'User Locked' : `${obsCount} evidence obs`}
          </span>
        </div>
        <div class="cp-card-text">${escapeHtml(pref.preference_text)}</div>
        <div class="cp-card-footer">
          <span>${pref.preference_key || 'style'}</span>
        </div>
      `;
      listEl.appendChild(card);
    });
  } catch (err: any) {
    listEl.innerHTML = `<div class="cp-empty-state">Failed to load preferences: ${escapeHtml(err.message)}</div>`;
  }
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
