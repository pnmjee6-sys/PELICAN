import { stripExtensionInjectedContext } from './injection.ts';
import { containsSecret, screenText } from './screener.ts';
import type { SiteAdapter, SiteName } from '../adapters/types.ts';

const normalize = (text: string) => text.replace(/\s+/g, ' ').trim();

interface PendingSend {
  text: string;
  eventId: string;
  createdAt: number;
}

/** Only a user send action can authorize capture of a matching rendered turn. */
export class CaptureGate {
  private pending: PendingSend[] = [];

  arm(draft: string, eventId: string, now = Date.now()): boolean {
    const text = normalize(stripExtensionInjectedContext(draft));
    if (!text || containsSecret(text)) return false;
    this.pending = this.pending.filter((item) => now - item.createdAt < 30_000);
    if (this.pending.some((item) => item.text === text && now - item.createdAt < 750)) return true;
    this.pending.push({ text, eventId, createdAt: now });
    return true;
  }

  take(renderedText: string, now = Date.now()): string | null {
    const text = normalize(stripExtensionInjectedContext(renderedText));
    this.pending = this.pending.filter((item) => now - item.createdAt < 30_000);
    const index = this.pending.findIndex((item) => item.text === text);
    if (index < 0) return null;
    return this.pending.splice(index, 1)[0].eventId;
  }

  clear(): void {
    this.pending = [];
  }
}

export type SourceSite = 'chatgpt.com' | 'claude.ai' | 'gemini.google.com' | 'manual';

export interface CaptureIngestPayload {
  conversation_id: string;
  text: string;
  role: 'user';
  is_extension_context: false;
  event_id: string;
  source_site: SourceSite;
}

export interface SiteCaptureOptions {
  adapter: SiteAdapter;
  initialEnabled?: boolean;
  debounceMs?: number;
  onCapture: (payload: CaptureIngestPayload) => Promise<void> | void;
}

const SITE_ORIGIN_MAP: Record<SiteName, SourceSite> = {
  chatgpt: 'chatgpt.com',
  claude: 'claude.ai',
  gemini: 'gemini.google.com',
};


export class SiteCaptureController {
  readonly adapter: SiteAdapter;
  private enabled: boolean;
  private readonly onCapture: (payload: CaptureIngestPayload) => Promise<void> | void;
  readonly gate: CaptureGate;
  private readonly debounceMs?: number;
  private unsubscribeObserver: (() => void) | null = null;
  private isDestroyed = false;

  private readonly handleSubmit: (e: Event) => void;
  private readonly handleKeyDown: (e: KeyboardEvent) => void;
  private readonly handleClick: (e: MouseEvent) => void;

  constructor(options: SiteCaptureOptions) {
    this.adapter = options.adapter;
    this.enabled = options.initialEnabled ?? false;
    this.onCapture = options.onCapture;
    this.debounceMs = options.debounceMs;
    this.gate = new CaptureGate();

    this.handleSubmit = (event: Event) => {
      const composer = this.adapter.getComposer();
      if (composer && event.target instanceof Element && event.target.contains(composer)) {
        this.recordSend();
      }
    };

    this.handleKeyDown = (event: KeyboardEvent) => {
      const composer = this.adapter.getComposer();
      if (
        composer &&
        event.key === 'Enter' &&
        !event.shiftKey &&
        !event.isComposing &&
        event.target instanceof Node &&
        (composer === event.target || composer.contains(event.target))
      ) {
        this.recordSend();
      }
    };

    this.handleClick = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const button = target.closest('button');
      if (!button || button.classList.contains('context-passport-btn')) return;

      const label = `${button.getAttribute('aria-label') || ''} ${button.getAttribute('data-testid') || ''} ${button.textContent || ''} ${button.querySelector('[aria-label]')?.getAttribute('aria-label') || ''}`;
      const isSendButton =
        button.matches('button[type="submit"], button.send-button, [data-testid="send-button"], button[aria-label*="Send" i], button[aria-label*="send" i]') ||
        /\b(send|submit|run prompt)\b/i.test(label);

      if (isSendButton) {
        this.recordSend();
      }
    };

    if (typeof document !== 'undefined') {
      document.addEventListener('submit', this.handleSubmit, true);
      document.addEventListener('keydown', this.handleKeyDown, true);
      document.addEventListener('click', this.handleClick, true);
    }

    this.unsubscribeObserver = this.adapter.observeUserMessages(async (msg) => {
      if (!this.enabled || this.isDestroyed) return;

      const cleanText = stripExtensionInjectedContext(msg.text).trim();
      if (!cleanText) return;

      // Screen for credentials - skip entirely if recognized secret
      const screenResult = screenText(msg.text);
      if (screenResult.classification === 'secret' || containsSecret(cleanText)) {
        return;
      }

      // Check if authorized by user send action
      const eventId = this.gate.take(msg.text);
      if (!eventId) return;

      const sourceSite = SITE_ORIGIN_MAP[this.adapter.name] || 'chatgpt.com';
      await this.onCapture({
        conversation_id: msg.conversationId,
        text: cleanText,
        role: 'user',
        is_extension_context: false,
        event_id: eventId,
        source_site: sourceSite,
      });
    }, this.debounceMs ? { debounceMs: this.debounceMs } : undefined);
  }

  isEnabled(): boolean {
    return this.enabled;
  }

  setEnabled(enabled: boolean): void {
    this.enabled = enabled;
    if (!enabled) {
      this.gate.clear();
    }
  }

  recordSend(): boolean {
    if (!this.enabled || this.isDestroyed) return false;
    const draft = this.adapter.getComposerDraft();
    const eventId = typeof crypto !== 'undefined' && crypto.randomUUID
      ? crypto.randomUUID()
      : `event-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
    return this.gate.arm(draft, eventId);
  }

  destroy(): void {
    this.isDestroyed = true;
    if (typeof document !== 'undefined') {
      document.removeEventListener('submit', this.handleSubmit, true);
      document.removeEventListener('keydown', this.handleKeyDown, true);
      document.removeEventListener('click', this.handleClick, true);
    }
    if (this.unsubscribeObserver) {
      this.unsubscribeObserver();
      this.unsubscribeObserver = null;
    }
    this.gate.clear();
  }
}
