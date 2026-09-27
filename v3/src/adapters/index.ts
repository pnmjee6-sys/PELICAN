/**
 * Site Adapters Registry
 */

import { ChatGPTAdapter } from './chatgpt.ts';
import { ClaudeAdapter } from './claude.ts';
import { GeminiAdapter } from './gemini.ts';
import type { SiteAdapter } from './types.ts';

export type * from './types.ts';
export * from './chatgpt.ts';
export * from './claude.ts';
export * from './gemini.ts';

export function getMatchingAdapter(url: string = window.location.href): SiteAdapter | null {
  const adapters: SiteAdapter[] = [
    new ChatGPTAdapter(),
    new ClaudeAdapter(),
    new GeminiAdapter(),
  ];

  for (const adapter of adapters) {
    if (adapter.matches(url)) {
      return adapter;
    }
  }
  return null;
}
