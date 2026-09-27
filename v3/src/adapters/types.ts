/**
 * Site Adapter Type Definitions
 */

export type SiteName = 'chatgpt' | 'claude' | 'gemini';

export interface UserMessageEvent {
  text: string;
  conversationId: string;
}

export interface ObserverOptions {
  debounceMs?: number;
}

export interface SiteAdapter {
  name: SiteName;
  displayName: string;
  matches(url: string): boolean;
  getConversationId(): string;
  getComposer(): HTMLElement | null;
  getComposerDraft(): string;
  setComposerDraft(text: string): void;
  attachUseMemoryButton(button: HTMLElement): boolean;
  observeUserMessages(callback: (msg: UserMessageEvent) => void, options?: ObserverOptions): () => void;
}
