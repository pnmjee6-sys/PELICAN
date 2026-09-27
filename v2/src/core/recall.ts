import type { MemoryItem, PreferenceItem } from './injection.ts';
import { containsSecret, containsSensitive } from './screener.ts';

export interface RecallCandidates {
  general_memories?: MemoryItem[];
  sensitive_memories?: MemoryItem[];
  preferences?: PreferenceItem[];
}

/** The extension checks backend labels again before preparing a third-party prompt. */
export function screenRecall(response: RecallCandidates) {
  const general: MemoryItem[] = [];
  const sensitive: MemoryItem[] = [];
  for (const memory of [...(response.general_memories || []), ...(response.sensitive_memories || [])]) {
    if (!memory.text || containsSecret(memory.text)) continue;
    if (memory.classification === 'sensitive' ||
        (response.sensitive_memories || []).includes(memory) || containsSensitive(memory.text)) {
      sensitive.push(memory);
    } else {
      general.push(memory);
    }
  }
  const preferences = (response.preferences || []).filter((pref) =>
    Boolean(pref.preference_text) && !containsSecret(pref.preference_text) && !containsSensitive(pref.preference_text));
  return { general, sensitive, preferences };
}
