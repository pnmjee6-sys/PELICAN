/**
 * ChatGPT Site Adapter (chatgpt.com)
 */

import type { SiteAdapter, UserMessageEvent } from './types.ts';
import { observeRenderedUserMessages } from './messageObserver.ts';
import { readComposer, writeComposer } from './composer.ts';

export class ChatGPTAdapter implements SiteAdapter {
  readonly name = 'chatgpt' as const;
  readonly displayName = 'ChatGPT';

  private newChatSessionId: string | null = null;

  matches(url: string): boolean {
    return /chatgpt\.com|chat\.openai\.com/.test(url);
  }

  getConversationId(): string {
    const match = window.location.pathname.match(/\/c\/([a-zA-Z0-9_-]+)/);
    if (match && match[1]) {
      this.newChatSessionId = null;
      return match[1];
    }
    // Generate a distinct session ID per new conversation instance
    if (!this.newChatSessionId) {
      this.newChatSessionId = `chatgpt-new-${Math.random().toString(36).slice(2, 10)}`;
    }
    return this.newChatSessionId;
  }

  resetNewChatSession(): void {
    this.newChatSessionId = `chatgpt-new-${Math.random().toString(36).slice(2, 10)}`;
  }

  getComposer(): HTMLElement | null {
    return (
      document.querySelector('#prompt-textarea') ||
      document.querySelector('textarea[data-id="root"]') ||
      document.querySelector('form textarea') ||
      document.querySelector('div[contenteditable="true"]')
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

    const form = composer.closest('form') || composer.parentElement;
    if (!form) return false;

    // Check if already attached inside this form
    if (form.querySelector('.context-passport-action-container')) {
      return true;
    }

    const container = document.createElement('div');
    container.className = 'context-passport-action-container';
    container.style.display = 'inline-flex';
    container.style.alignItems = 'center';
    container.style.margin = '4px 6px';
    container.appendChild(button);

    const sendBtn =
      form.querySelector('button[data-testid="send-button"]') ||
      form.querySelector('button[aria-label="Send prompt"]') ||
      form.querySelector('button[type="submit"]');

    if (sendBtn && sendBtn.parentElement) {
      sendBtn.parentElement.insertBefore(container, sendBtn);
    } else {
      form.appendChild(container);
    }
    return true;
  }

  observeUserMessages(callback: (msg: UserMessageEvent) => void): () => void {
    return observeRenderedUserMessages(
      () => Array.from(document.querySelectorAll('[data-message-author-role="user"]'))
        .filter((node) => !node.parentElement?.closest('[data-message-author-role="user"]')),
      () => this.getConversationId(),
      callback,
    );
  }

}
