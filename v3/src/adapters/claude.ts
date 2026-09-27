/**
 * Claude Site Adapter (claude.ai)
 */

import type { SiteAdapter, UserMessageEvent, ObserverOptions } from './types.ts';
import { observeRenderedUserMessages } from './messageObserver.ts';
import { readComposer, writeComposer } from './composer.ts';

export class ClaudeAdapter implements SiteAdapter {
  readonly name = 'claude' as const;
  readonly displayName = 'Claude';

  private newChatSessionId: string | null = null;

  matches(url: string): boolean {
    return /claude\.ai/.test(url);
  }

  getConversationId(): string {
    const match = window.location.pathname.match(/\/chat\/([a-zA-Z0-9_-]+)/);
    if (match && match[1]) {
      this.newChatSessionId = null;
      return match[1];
    }
    // Generate a distinct session ID per new conversation instance
    if (!this.newChatSessionId) {
      this.newChatSessionId = `claude-new-${Math.random().toString(36).slice(2, 10)}`;
    }
    return this.newChatSessionId;
  }

  resetNewChatSession(): void {
    this.newChatSessionId = `claude-new-${Math.random().toString(36).slice(2, 10)}`;
  }

  getComposer(): HTMLElement | null {
    return (
      document.querySelector('div[contenteditable="true"].ProseMirror') ||
      document.querySelector('fieldset div[contenteditable="true"]') ||
      document.querySelector('div[contenteditable="true"]') ||
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

    const parentContainer = composer.closest('fieldset') || composer.parentElement;
    if (!parentContainer) return false;

    if (parentContainer.querySelector('.context-passport-action-container')) {
      return true;
    }

    const container = document.createElement('div');
    container.className = 'context-passport-action-container';
    container.style.display = 'inline-flex';
    container.style.alignItems = 'center';
    container.style.margin = '4px 6px';
    container.appendChild(button);

    const sendBtn =
      parentContainer.querySelector('button[aria-label*="Send"]') ||
      parentContainer.querySelector('button[type="submit"]');

    if (sendBtn && sendBtn.parentElement) {
      sendBtn.parentElement.insertBefore(container, sendBtn);
    } else {
      parentContainer.appendChild(container);
    }
    return true;
  }

  observeUserMessages(callback: (msg: UserMessageEvent) => void, options?: ObserverOptions): () => void {
    return observeRenderedUserMessages(
      () => {
        const nodes = Array.from(document.querySelectorAll('[data-testid="user-message"], .font-user-message'))
          .filter((node) => !node.closest('[data-testid="assistant-message"], .font-claude-message, [data-is-streaming="true"]'));
        return nodes.filter((node) => !nodes.some((other) => other !== node && other.contains(node)));
      },
      () => this.getConversationId(),
      callback,
      options,
    );
  }


}
