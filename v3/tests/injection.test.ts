import test from 'node:test';
import assert from 'node:assert/strict';
import {
  formatMemoryBlock,
  isExtensionInjectedContext,
  stripExtensionInjectedContext,
  applyMemoryBlockToDraft,
  INJECTION_START_MARKER,
  INJECTION_END_MARKER,
} from '../src/core/injection.ts';

test('formatMemoryBlock creates structured context block with markers', () => {
  const general = [{ text: 'User works with TypeScript and Tailwind CSS.' }];
  const sensitive = [{ text: 'User has severe peanut allergy.' }];
  const prefs = [{ preference_text: 'Prefers step-by-step explanations.' }];

  const block = formatMemoryBlock(general, sensitive, prefs);
  assert.ok(block.includes(INJECTION_START_MARKER));
  assert.ok(block.includes(INJECTION_END_MARKER));
  assert.ok(block.includes('User works with TypeScript'));
  assert.ok(block.includes('User has severe peanut allergy'));
  assert.ok(block.includes('Prefers step-by-step explanations'));
});

test('isExtensionInjectedContext detects injected markers', () => {
  assert.equal(isExtensionInjectedContext('[Context Passport: Memory]\nRelevant Memories:\n...\n[/Context Passport]'), true);
  assert.equal(isExtensionInjectedContext('<!-- Context-Passport memory block -->'), true);
  assert.equal(isExtensionInjectedContext('Some ordinary user message'), false);
});

test('stripExtensionInjectedContext removes injected block and preserves user draft', () => {
  const originalDraft = 'How do I center a div?';
  const block = formatMemoryBlock([{ text: 'Fact 1' }]);
  const injected = `${block}\n\n${originalDraft}`;

  const stripped = stripExtensionInjectedContext(injected);
  assert.equal(stripped, originalDraft);
});

test('applyMemoryBlockToDraft is idempotent on repeated clicks (no duplicated blocks)', () => {
  const userDraft = 'Please explain how async generators work.';

  // Click 1: Use Memory
  const block1 = formatMemoryBlock([{ text: 'Likes Python 3.11' }]);
  const draftAfterClick1 = applyMemoryBlockToDraft(userDraft, block1);

  assert.ok(draftAfterClick1.includes('Likes Python 3.11'));
  assert.ok(draftAfterClick1.includes('Please explain how async generators work.'));
  // Verify start marker appears only once
  const occurrences1 = draftAfterClick1.split(INJECTION_START_MARKER).length - 1;
  assert.equal(occurrences1, 1);

  // Click 2: Use Memory clicked again (e.g. updated memory)
  const block2 = formatMemoryBlock([{ text: 'Likes Python 3.11' }, { text: 'Prefers pytest' }]);
  const draftAfterClick2 = applyMemoryBlockToDraft(draftAfterClick1, block2);

  const occurrences2 = draftAfterClick2.split(INJECTION_START_MARKER).length - 1;
  assert.equal(occurrences2, 1, 'Memory block was duplicated on repeated click!');
  assert.ok(draftAfterClick2.includes('Prefers pytest'));
  assert.ok(draftAfterClick2.includes('Please explain how async generators work.'));

  // Click 3: Use Memory clicked a third time
  const draftAfterClick3 = applyMemoryBlockToDraft(draftAfterClick2, block2);
  const occurrences3 = draftAfterClick3.split(INJECTION_START_MARKER).length - 1;
  assert.equal(occurrences3, 1, 'Memory block was duplicated on 3rd click!');
});
