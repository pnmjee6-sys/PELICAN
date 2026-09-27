/**
 * Context Passport Backend API Client
 *
 * Provides typed methods for interacting with the FastAPI backend.
 */

import type { MemoryItem, PreferenceItem } from './injection.ts';
import { DEFAULT_BACKEND_URL, getSetting, setSetting } from './storage.ts';

export class AuthRequiredError extends Error {
  constructor() { super('Session expired. Sign in again.'); this.name = 'AuthRequiredError'; }
}

const pendingRefreshes = new Map<string, Promise<string>>();

export interface IngestResponse {
  status: 'processed' | 'skipped' | 'duplicate_skipped';
  reason?: string;
  message?: string;
  facts_extracted?: any[];
  observations_recorded?: any[];
  preference_promoted?: boolean;
}

export interface QueryMemoriesResponse {
  general_memories: MemoryItem[];
  sensitive_memories: MemoryItem[];
  preferences: PreferenceItem[];
}

export interface BackendMemoryDoc {
  id: string;
  text: string;
  created_at?: string;
  status?: string;
  classification?: 'general' | 'sensitive' | 'secret';
  metadata?: Record<string, any>;
  source?: string;
  evidence_excerpt?: string;
}

export interface BackendPreferenceDoc extends PreferenceItem {
  id: string;
  preference_key: string;
  evidence_count?: number;
  conversation_count?: number;
  locked?: boolean;
  supporting_observations?: Array<{ id: string; excerpt: string; conversation_id: string }>;
}

export class ContextPassportApiClient {
  private backendUrl: string;
  private token: string;

  constructor(backendUrl = DEFAULT_BACKEND_URL, token = '') {
    this.backendUrl = backendUrl.replace(/\/+$/, '');
    this.token = token.trim().replace(/^Bearer\s+/i, '');
  }

  setToken(token: string) {
    this.token = token.trim().replace(/^Bearer\s+/i, '');
  }

  setBackendUrl(url: string) {
    this.backendUrl = url.replace(/\/+$/, '');
  }

  private getHeaders(): HeadersInit {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (this.token) {
      headers['Authorization'] = `Bearer ${this.token}`;
    }
    return headers;
  }

  private async request(path: string, init: RequestInit = {}): Promise<Response> {
    const send = () => fetch(`${this.backendUrl}${path}`, {
      ...init, headers: { ...init.headers, ...this.getHeaders() },
    });
    let response = await send();
    if (response.status !== 401) return response;
    const refresh = await getSetting('refresh_token');
    if (!refresh) {
      await this.clearSession();
      throw new AuthRequiredError();
    }
    const key = `${this.backendUrl}:${refresh}`;
    let pending = pendingRefreshes.get(key);
    if (!pending) {
      pending = this.refreshToken(refresh).finally(() => pendingRefreshes.delete(key));
      pendingRefreshes.set(key, pending);
    }
    try {
      this.token = await pending;
      response = await send();
    } catch {
      if ((await getSetting('refresh_token')) === refresh) await this.clearSession();
      throw new AuthRequiredError();
    }
    if (response.status === 401) {
      await this.clearSession();
      throw new AuthRequiredError();
    }
    return response;
  }

  async clearSession(): Promise<void> {
    this.token = '';
    await Promise.all([
      setSetting('auth_token', ''), setSetting('refresh_token', ''), setSetting('user_email', ''),
    ]);
  }

  async logout(): Promise<void> {
    const refresh = await getSetting('refresh_token');
    const access = this.token;
    await this.clearSession();
    if (refresh) {
      void fetch(`${this.backendUrl}/api/v1/auth/logout`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        signal: AbortSignal.timeout(2500),
        body: JSON.stringify({ refresh_token: refresh, access_token: access }),
      }).catch(() => {});
    }
  }

  async checkHealth(): Promise<{ ok: boolean; status: string; version?: string }> {
    try {
      const res = await fetch(`${this.backendUrl}/health`);
      if (!res.ok) return { ok: false, status: `HTTP ${res.status}` };
      const data = await res.json();
      return { ok: true, status: data.status, version: data.version };
    } catch (err: any) {
      return { ok: false, status: err.message || 'Offline' };
    }
  }

  async checkReady(): Promise<{ ready: boolean; missing?: string[] }> {
    try {
      const res = await fetch(`${this.backendUrl}/ready`);
      const data = await res.json();
      return { ready: data.status === 'ready', missing: data.missing };
    } catch {
      return { ready: false, missing: ['Server Unreachable'] };
    }
  }

  async login(email: string, password: string): Promise<string> {
    const res = await fetch(`${this.backendUrl}/api/v1/auth/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Authentication failed' }));
      throw new Error(err.detail || 'Authentication failed');
    }
    const data = await res.json();
    if (!data.access_token) throw new Error('Authentication did not return a session');
    this.token = data.access_token;
    await setSetting('auth_token', data.access_token);
    await setSetting('refresh_token', data.refresh_token || '');
    return data.access_token;
  }

  async refreshToken(refreshToken: string): Promise<string> {
    const res = await fetch(`${this.backendUrl}/api/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) {
      throw new Error('Failed to refresh token');
    }
    const data = await res.json();
    if (!data.access_token || !data.refresh_token) throw new Error('Invalid refresh response');
    this.token = data.access_token;
    await setSetting('auth_token', data.access_token);
    await setSetting('refresh_token', data.refresh_token);
    return data.access_token;
  }

  async ingestMessage(payload: {
    conversation_id: string;
    text: string;
    role?: 'user';
    is_extension_context?: boolean;
    event_id?: string;
    source_site?: 'chatgpt.com' | 'claude.ai' | 'gemini.google.com' | 'manual';
  }): Promise<IngestResponse> {
    const res = await this.request('/api/v1/messages/ingest', {
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({
        conversation_id: payload.conversation_id,
        text: payload.text,
        role: payload.role || 'user',
        is_extension_context: Boolean(payload.is_extension_context),
        event_id: payload.event_id,
        source_site: payload.source_site,
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(err.detail || `Ingest failed with status ${res.status}`);
    }
    return res.json();
  }

  async queryMemories(query: string, maxGeneral = 3): Promise<QueryMemoriesResponse> {
    const res = await this.request('/api/v1/memories/query', {
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({
        query,
        max_general: maxGeneral,
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(err.detail || `Memory query failed with status ${res.status}`);
    }
    return res.json();
  }

  async getAllMemories(): Promise<BackendMemoryDoc[]> {
    const res = await this.request('/api/v1/memories', {
      method: 'GET',
      headers: this.getHeaders(),
    });
    if (!res.ok) {
      throw new Error(`Failed to load memories: HTTP ${res.status}`);
    }
    return res.json();
  }

  async blockMemory(memoryId: string): Promise<boolean> {
    const res = await this.request(`/api/v1/memories/${encodeURIComponent(memoryId)}/block`, {
      method: 'POST',
      headers: this.getHeaders(),
    });
    return res.ok;
  }

  async updateMemory(memoryId: string, text: string): Promise<BackendMemoryDoc> {
    const res = await this.request(`/api/v1/memories/${encodeURIComponent(memoryId)}`, {
      method: 'PUT', headers: this.getHeaders(), body: JSON.stringify({ text }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(err.detail || `Memory update failed: HTTP ${res.status}`);
    }
    return res.json();
  }

  async deleteMemory(memoryId: string): Promise<boolean> {
    const res = await this.request(`/api/v1/memories/${encodeURIComponent(memoryId)}`, {
      method: 'DELETE',
      headers: this.getHeaders(),
    });
    return res.ok;
  }

  async getPreferences(): Promise<BackendPreferenceDoc[]> {
    const res = await this.request('/api/v1/preferences', {
      method: 'GET',
      headers: this.getHeaders(),
    });
    if (!res.ok) {
      throw new Error(`Failed to load preferences: HTTP ${res.status}`);
    }
    return res.json();
  }

  async updatePreference(preferenceId: string, newText: string): Promise<any> {
    const res = await this.request(`/api/v1/preferences/${encodeURIComponent(preferenceId)}`, {
      method: 'PUT',
      headers: this.getHeaders(),
      body: JSON.stringify({ preference_text: newText }),
    });
    if (!res.ok) {
      throw new Error(`Failed to update preference: HTTP ${res.status}`);
    }
    return res.json();
  }

  async removePreferenceEvidence(preferenceId: string, observationId: string): Promise<boolean> {
    const res = await this.request(`/api/v1/preferences/${encodeURIComponent(preferenceId)}/observations/${encodeURIComponent(observationId)}`, {
      method: 'DELETE', headers: this.getHeaders(),
    });
    if (!res.ok) throw new Error(`Could not remove evidence: HTTP ${res.status}`);
    return (await res.json()).ok === true;
  }

  async blockPreference(preferenceId: string): Promise<boolean> {
    const res = await this.request(`/api/v1/preferences/${encodeURIComponent(preferenceId)}/block`, {
      method: 'POST', headers: this.getHeaders(),
    });
    if (!res.ok) throw new Error(`Could not block preference: HTTP ${res.status}`);
    return (await res.json()).ok === true;
  }

  async forgetPreference(preferenceId: string): Promise<boolean> {
    const res = await this.request(`/api/v1/preferences/${encodeURIComponent(preferenceId)}`, {
      method: 'DELETE', headers: this.getHeaders(),
    });
    if (!res.ok) throw new Error(`Could not forget preference: HTTP ${res.status}`);
    return (await res.json()).ok === true;
  }
}
