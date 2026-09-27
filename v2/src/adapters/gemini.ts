/**
 * Gemini Site Adapter (gemini.google.com)
 */

import type { SiteAdapter, UserMessageEvent } from './types.ts';
import { observeRenderedUserMessages } from './messageObserver.ts';
import { readComposer, writeComposer } from './composer.ts';

export class GeminiAdapter implements SiteAdapter {
  readonly name = 'gemini' as const;
  readonly displayName = 'Gemini';

  private newChatSessionId: string | null = null;

  matches(url: string): boolean {
    return /gemini\.google\.com/.test(url);
  }

  getConversationId(): string {
    const match = window.location.pathname.match(/\/app\/([a-zA-Z0-9_-]+)/);
    if (match && match[1]) {
      this.newChatSessionId = null;
      return match[1];
    }
    // Generate a distinct session ID per new conversation instance
    if (!this.newChatSessionId) {
      this.newChatSessionId = `gemini-new-${Math.random().toString(36).slice(2, 10)}`;
    }
    return this.newChatSessionId;
  }

  resetNewChatSession(): void {
    this.newChatSessionId = `gemini-new-${Math.random().toString(36).slice(2, 10)}`;
  }

  getComposer(): HTMLElement | null {
    return (
      document.querySelector('rich-textarea .ql-editor') ||
      document.querySelector('div[contenteditable="true"].ql-editor') ||
      document.querySelector('rich-textarea div[contenteditable="true"]') ||
      document.querySelector('textarea')
    );
  }

  getComposerDraft(): string {
    return readComposer(this.getComposer());
  }

  setComposerDraft(text: string): void {
    writeComposer(this.getComposer(), text);
  }

  attachUseMemoryButton(button: HTMLElement): boolean {
    const composer = this.getComposer();
    if (!composer) return false;

    const parentBar =
      composer.closest('.input-area-container') ||
      composer.closest('form') ||
      composer.parentElement;
    if (!parentBar) return false;

    if (parentBar.querySelector('.context-passport-action-container')) {
      return true;
    }

    const container = document.createElement('div');
    container.className = 'context-passport-action-container';
    container.style.display = 'inline-flex';
    container.style.alignItems = 'center';
    container.style.margin = '4px 6px';
    container.appendChild(button);

    const sendBtn =
      parentBar.querySelector('button.send-button') ||
      parentBar.querySelector('button[aria-label*="Send message"]') ||
      parentBar.querySelector('button[mat-icon-button]');

    if (sendBtn && sendBtn.parentElement) {
      sendBtn.parentElement.insertBefore(container, sendBtn);
    } else {
      parentBar.appendChild(container);
    }
    return true;
  }

  observeUserMessages(callback: (msg: UserMessageEvent) => void): () => void {
    return observeRenderedUserMessages(
      () => {
        const primary = Array.from(document.querySelectorAll('user-query'));
        const nodes = primary.length ? primary : Array.from(document.querySelectorAll('div[data-sender="user"], .user-query-container, .user-query-text'));
        return nodes.filter((node) => !node.closest('model-response, .model-response-container'))
          .filter((node) => !nodes.some((other) => other !== node && other.contains(node)));
      },
      () => this.getConversationId(),
      callback,
    );
  }

}
