/**
 * Context Passport Backend API Client
 *
 * Provides typed methods for interacting with the FastAPI backend.
 */

import type { MemoryItem, PreferenceItem } from './injection.ts';

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
}

export class ContextPassportApiClient {
  private backendUrl: string;
  private token: string;

  constructor(backendUrl = 'http://localhost:8000', token = '') {
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
    this.token = data.access_token;
    return data.access_token;
  }

  async ingestMessage(payload: {
    conversation_id: string;
    text: string;
    role?: 'user';
    is_extension_context?: boolean;
    event_id?: string;
  }): Promise<IngestResponse> {
    const res = await fetch(`${this.backendUrl}/api/v1/messages/ingest`, {
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({
        conversation_id: payload.conversation_id,
        text: payload.text,
        role: payload.role || 'user',
        is_extension_context: Boolean(payload.is_extension_context),
        event_id: payload.event_id,
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(err.detail || `Ingest failed with status ${res.status}`);
    }
    return res.json();
  }

  async queryMemories(query: string, maxGeneral = 3): Promise<QueryMemoriesResponse> {
    const res = await fetch(`${this.backendUrl}/api/v1/memories/query`, {
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
    const res = await fetch(`${this.backendUrl}/api/v1/memories`, {
      method: 'GET',
      headers: this.getHeaders(),
    });
    if (!res.ok) {
      throw new Error(`Failed to load memories: HTTP ${res.status}`);
    }
    return res.json();
  }

  async blockMemory(memoryId: string): Promise<boolean> {
    const res = await fetch(`${this.backendUrl}/api/v1/memories/${encodeURIComponent(memoryId)}/block`, {
      method: 'POST',
      headers: this.getHeaders(),
    });
    return res.ok;
  }

  async deleteMemory(memoryId: string): Promise<boolean> {
    const res = await fetch(`${this.backendUrl}/api/v1/memories/${encodeURIComponent(memoryId)}`, {
      method: 'DELETE',
      headers: this.getHeaders(),
    });
    return res.ok;
  }

  async getPreferences(): Promise<PreferenceItem[]> {
    const res = await fetch(`${this.backendUrl}/api/v1/preferences`, {
      method: 'GET',
      headers: this.getHeaders(),
    });
    if (!res.ok) {
      throw new Error(`Failed to load preferences: HTTP ${res.status}`);
    }
    return res.json();
  }

  async updatePreference(preferenceId: string, newText: string): Promise<any> {
    const res = await fetch(`${this.backendUrl}/api/v1/preferences/${encodeURIComponent(preferenceId)}`, {
      method: 'PUT',
      headers: this.getHeaders(),
      body: JSON.stringify({ preference_text: newText }),
    });
    if (!res.ok) {
      throw new Error(`Failed to update preference: HTTP ${res.status}`);
    }
    return res.json();
  }
}
