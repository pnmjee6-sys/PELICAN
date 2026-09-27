/**
 * Context Injection and Extraction Logic
 *
 * Formats memory context to be inserted into the LLM prompt composer.
 * Ensures:
 * - Clear visual framing with start and end markers
 * - Clean replacement if "Use Memory" is clicked multiple times (no duplicate blocks)
 * - Safe detection by isExtensionInjectedContext so injected context is never captured as user evidence
 */

export const INJECTION_START_MARKER = '[Context Passport: Memory]';
export const INJECTION_END_MARKER = '[/Context Passport]';

// Regex that matches any existing injected Context Passport block
const INJECTION_BLOCK_REGEX = /\[Context Passport:[^\]]*\][\s\S]*?\[\/Context Passport\]/gi;
// Fallback and comment format regexes
const COMMENT_BLOCK_REGEX = /<!--\s*CONTEXT_PASSPORT_START[\s\S]*?CONTEXT_PASSPORT_END\s*-->/gi;
const FALLBACK_BLOCK_REGEX = /<!-- Context-Passport[\s\S]*?-->/gi;

export interface MemoryItem {
  id?: string;
  text: string;
  classification?: string;
}

export interface PreferenceItem {
  id?: string;
  preference_text: string;
  status?: string;
}

/**
 * Checks whether text contains extension-injected memory context.
 */
export function isExtensionInjectedContext(text: string): boolean {
  if (!text) return false;
  return /\[Context Passport:[^\]]*\][\s\S]*?\[\/Context Passport\]/i.test(text) ||
    /<!--\s*CONTEXT_PASSPORT_START[\s\S]*?CONTEXT_PASSPORT_END\s*-->/i.test(text) ||
    /<!-- Context-Passport[\s\S]*?-->/i.test(text);
}

/**
 * Strips any extension-injected memory block from the text, returning only original draft.
 */
export function stripExtensionInjectedContext(text: string): string {
  if (!text) return '';
  let cleaned = text.replace(INJECTION_BLOCK_REGEX, '');
  cleaned = cleaned.replace(COMMENT_BLOCK_REGEX, '');
  cleaned = cleaned.replace(FALLBACK_BLOCK_REGEX, '');
  return cleaned.trim();
}


/**
 * Formats memories and preferences into a structured context block.
 */
export function formatMemoryBlock(
  generalMemories: MemoryItem[] = [],
  sensitiveMemories: MemoryItem[] = [],
  preferences: PreferenceItem[] = []
): string {
  const parts: string[] = [];

  const allMemories = [...generalMemories, ...sensitiveMemories];
  if (allMemories.length > 0) {
    parts.push('Relevant Memories:');
    for (const mem of allMemories) {
      const clean = mem.text.trim();
      if (clean) {
        parts.push(`- ${clean}`);
      }
    }
  }

  if (preferences.length > 0) {
    if (parts.length > 0) parts.push('');
    parts.push('User Response Preference:');
    for (const pref of preferences) {
      const clean = pref.preference_text.trim();
      if (clean) {
        parts.push(`- ${clean}`);
      }
    }
  }

  if (parts.length === 0) {
    return '';
  }

  return `${INJECTION_START_MARKER}\n${parts.join('\n')}\n${INJECTION_END_MARKER}\n\n`;
}

/**
 * Replaces any existing injected block with the new block, or prepends it to the draft.
 * Guaranteed idempotent on repeated clicks.
 */
export function applyMemoryBlockToDraft(currentDraft: string, newBlock: string): string {
  const cleanDraftWithoutOldBlock = stripExtensionInjectedContext(currentDraft);

  if (!newBlock || newBlock.trim() === '') {
    return cleanDraftWithoutOldBlock;
  }

  // Prepend block cleanly before the user's draft prompt
  if (cleanDraftWithoutOldBlock.length > 0) {
    return `${newBlock.trim()}\n\n${cleanDraftWithoutOldBlock}`;
  }
  return newBlock.trim();
}
