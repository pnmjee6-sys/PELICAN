/**
 * Extension Storage Adapter
 *
 * Manages configuration, per-site capture toggles (defaulting to OFF),
 * and authentication credentials using chrome.storage.local with in-memory fallbacks.
 */

export interface StorageSchema {
  capture_chatgpt: boolean;
  capture_claude: boolean;
  capture_gemini: boolean;
  backend_url: string;
  auth_token: string;
  refresh_token?: string;
  user_email: string;
}

declare const __PELICAN_BACKEND_URL__: string;
export const DEFAULT_BACKEND_URL = typeof __PELICAN_BACKEND_URL__ !== 'undefined'
  ? __PELICAN_BACKEND_URL__ : 'http://127.0.0.1:8000';

const DEFAULT_SETTINGS: StorageSchema = {
  // Capture is OFF by default across all sites
  capture_chatgpt: false,
  capture_claude: false,
  capture_gemini: false,
  backend_url: DEFAULT_BACKEND_URL,
  auth_token: '',
  refresh_token: '',
  user_email: '',
};

// In-memory cache / fallback for non-extension test environments
const memoryStore: Record<string, any> = { ...DEFAULT_SETTINGS };

function packagedBackendUrl(): string {
  const url = typeof chrome !== 'undefined' && chrome.runtime?.getManifest?.().homepage_url;
  return typeof url === 'string' && /^https:\/\//.test(url) ? url.replace(/\/+$/, '') : DEFAULT_BACKEND_URL;
}

function hasChromeStorage(): boolean {
  return typeof chrome !== 'undefined' && !!chrome.storage && !!chrome.storage.local;
}

export async function getSetting<K extends keyof StorageSchema>(key: K): Promise<StorageSchema[K]> {
  if (hasChromeStorage()) {
    return new Promise((resolve) => {
      chrome.storage.local.get([key], (result) => {
        if (result && result[key] !== undefined) {
          resolve(result[key]);
        } else {
          resolve((key === 'backend_url' ? packagedBackendUrl() : DEFAULT_SETTINGS[key]) as StorageSchema[K]);
        }
      });
    });
  }
  return memoryStore[key] !== undefined ? memoryStore[key] : DEFAULT_SETTINGS[key];
}

export async function setSetting<K extends keyof StorageSchema>(key: K, value: StorageSchema[K]): Promise<void> {
  memoryStore[key] = value;
  if (hasChromeStorage()) {
    return new Promise((resolve) => {
      chrome.storage.local.set({ [key]: value }, () => resolve());
    });
  }
}

export async function getAllSettings(): Promise<StorageSchema> {
  if (hasChromeStorage()) {
    return new Promise((resolve) => {
      chrome.storage.local.get(null, (result) => {
        resolve({
          ...DEFAULT_SETTINGS,
          backend_url: packagedBackendUrl(),
          ...result,
        });
      });
    });
  }
  return { ...DEFAULT_SETTINGS, ...memoryStore };
}

export async function isSiteCaptureEnabled(site: 'chatgpt' | 'claude' | 'gemini'): Promise<boolean> {
  const key = `capture_${site}` as keyof StorageSchema;
  const val = await getSetting(key);
  return Boolean(val);
}

export async function setSiteCaptureEnabled(site: 'chatgpt' | 'claude' | 'gemini', enabled: boolean): Promise<void> {
  const key = `capture_${site}` as keyof StorageSchema;
  await setSetting(key, enabled);
}
