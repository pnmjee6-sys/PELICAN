import { stripExtensionInjectedContext } from './injection.ts';
import { containsSecret } from './screener.ts';

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
