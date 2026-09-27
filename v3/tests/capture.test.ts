import test from 'node:test';
import assert from 'node:assert/strict';
import { CaptureGate } from '../src/core/capture.ts';
import { isExtensionInjectedContext, formatMemoryBlock, stripExtensionInjectedContext } from '../src/core/injection.ts';
import { containsSecret, screenText } from '../src/core/screener.ts';
import { screenRecall } from '../src/core/recall.ts';

test('capture requires a matching send and only consumes it once', () => {
  const gate = new CaptureGate();
  assert.equal(gate.take('Old message loaded from history'), null);
  assert.equal(gate.arm('Please explain step by step', 'event-1', 1000), true);
  assert.equal(gate.take('Please explain', 1100), null);
  assert.equal(gate.take('Please explain step by step', 1200), 'event-1');
  assert.equal(gate.take('Please explain step by step', 1300), null);
});

test('capture refuses recognizable secrets and clears pending sends', () => {
  const gate = new CaptureGate();
  assert.equal(gate.arm('password: MySecret1234', 'secret', 1000), false);
  assert.equal(gate.take('password: MySecret1234', 1100), null);
  gate.arm('A normal message', 'event-2', 1200);
  gate.clear();
  assert.equal(gate.take('A normal message', 1300), null);
});

test('an inserted memory block is never included in captured user text', () => {
  const block = formatMemoryBlock([{ text: 'Prefers Python' }]);
  assert.equal(stripExtensionInjectedContext(`${block}Please explain Python`), 'Please explain Python');
  assert.equal(isExtensionInjectedContext(`${block}Please explain Python`), true);
  const gate = new CaptureGate();
  assert.equal(gate.arm(`${block}Please explain Python`, 'user-words-only', 1000), true);
  assert.equal(gate.take(`${block}Please explain Python`, 1100), 'user-words-only');
  assert.equal(isExtensionInjectedContext('Please summarize the phrase relevant memories: in this article'), false);
});

test('client screening covers unquoted credentials and personal details', () => {
  assert.equal(containsSecret('password: MySecret1234'), true);
  assert.equal(containsSecret('api_key=abcd1234EFGH5678'), true);
  assert.equal(screenText('My email is me@example.com').classification, 'sensitive');
  assert.equal(screenText('My home address is 123 Main Street').classification, 'sensitive');
  assert.equal(screenText('I am Muslim').classification, 'sensitive');
});

test('recall never auto-injects a mislabelled sensitive or secret memory', () => {
  const result = screenRecall({
    general_memories: [
      { text: 'I prefer Python examples', classification: 'general' },
      { text: 'I have a peanut allergy', classification: 'general' },
      { text: 'password: MySecret1234', classification: 'general' },
      { text: 'Unrecognized opaque credential', classification: 'secret' },
    ],
    sensitive_memories: [{ text: 'My personal email is me@example.com', classification: 'sensitive' }],
    preferences: [{ preference_text: 'Short answers' }, { preference_text: 'My personal email is me@example.com' }],
  });
  assert.deepEqual(result.general.map((m) => m.text), ['I prefer Python examples']);
  assert.equal(result.sensitive.length, 2);
  assert.deepEqual(result.preferences.map((p) => p.preference_text), ['Short answers']);
});
