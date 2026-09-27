import type { UserMessageEvent, ObserverOptions } from './types.ts';

type Pending = { text: string; timer: ReturnType<typeof setTimeout> };

/** Observe rendered user turns without treating a changing DOM node as several messages. */
export function observeRenderedUserMessages(
  getNodes: () => Element[],
  getConversationId: () => string,
  callback: (message: UserMessageEvent) => void,
  options?: ObserverOptions,
): () => void {
  const debounceMs = options?.debounceMs ?? 600;
  const seen = new WeakSet<Element>();
  const pending = new Map<Element, Pending>();
  let conversationId = getConversationId();

  const cancelPending = () => {
    for (const item of pending.values()) clearTimeout(item.timer);
    pending.clear();
  };
  const seedExisting = () => {
    for (const node of getNodes()) seen.add(node);
  };
  seedExisting();

  const scan = () => {
    const nextId = getConversationId();
    if (nextId !== conversationId) {
      // A new-chat placeholder acquiring its real URL is the same conversation.
      const placeholderBecameReal = conversationId.includes('-new-') && !nextId.includes('-new-');
      conversationId = nextId;
      if (!placeholderBecameReal) {
        cancelPending();
      }
    }

    for (const node of getNodes()) {
      if (seen.has(node)) continue;
      const text = (node.textContent || '').trim();
      if (!text) continue;
      const prior = pending.get(node);
      if (prior?.text === text) continue;
      if (prior) clearTimeout(prior.timer);

      const timer = setTimeout(() => {
        pending.delete(node);
        if (!node.isConnected || seen.has(node)) return;
        const finalText = (node.textContent || '').trim();
        if (finalText !== text) {
          scan();
          return;
        }
        seen.add(node);
        callback({ text: finalText, conversationId: getConversationId() });
      }, debounceMs);
      pending.set(node, { text, timer });
    }
  };

  const observer = new MutationObserver(scan);
  if (typeof document !== 'undefined' && document.body) {
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  const onLocationChange = () => {
    scan();
  };
  if (typeof window !== 'undefined') {
    window.addEventListener('popstate', onLocationChange);
  }

  // Intercept pushState & replaceState for SPA route changes
  const origPushState = typeof window !== 'undefined' && window.history?.pushState ? window.history.pushState : null;
  const origReplaceState = typeof window !== 'undefined' && window.history?.replaceState ? window.history.replaceState : null;

  if (origPushState && window.history) {
    window.history.pushState = function (...args) {
      const res = origPushState.apply(this, args);
      onLocationChange();
      return res;
    };
  }
  if (origReplaceState && window.history) {
    window.history.replaceState = function (...args) {
      const res = origReplaceState.apply(this, args);
      onLocationChange();
      return res;
    };
  }

  return () => {
    observer.disconnect();
    if (typeof window !== 'undefined') {
      window.removeEventListener('popstate', onLocationChange);
      if (origPushState && window.history) {
        window.history.pushState = origPushState;
      }
      if (origReplaceState && window.history) {
        window.history.replaceState = origReplaceState;
      }
    }
    cancelPending();
  };
}
