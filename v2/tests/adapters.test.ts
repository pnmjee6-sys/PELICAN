import test from 'node:test';
import assert from 'node:assert/strict';
import { ChatGPTAdapter } from '../src/adapters/chatgpt.ts';
import { ClaudeAdapter } from '../src/adapters/claude.ts';
import { GeminiAdapter } from '../src/adapters/gemini.ts';
import { getMatchingAdapter } from '../src/adapters/index.ts';

test('site adapters match respective platform URLs', () => {
  const chatgpt = new ChatGPTAdapter();
  assert.equal(chatgpt.matches('https://chatgpt.com/'), true);
  assert.equal(chatgpt.matches('https://chatgpt.com/c/ab12-34cd'), true);
  assert.equal(chatgpt.matches('https://claude.ai/chat/abc'), false);

  const claude = new ClaudeAdapter();
  assert.equal(claude.matches('https://claude.ai/'), true);
  assert.equal(claude.matches('https://claude.ai/chat/xyz-987'), true);
  assert.equal(claude.matches('https://gemini.google.com/app/xyz'), false);

  const gemini = new GeminiAdapter();
  assert.equal(gemini.matches('https://gemini.google.com/'), true);
  assert.equal(gemini.matches('https://gemini.google.com/app/conv-123'), true);
  assert.equal(gemini.matches('https://chatgpt.com/'), false);
});

test('getMatchingAdapter resolves active site adapter correctly', () => {
  const a1 = getMatchingAdapter('https://chatgpt.com/c/test-chat');
  assert.equal(a1?.name, 'chatgpt');

  const a2 = getMatchingAdapter('https://claude.ai/chat/test-chat');
  assert.equal(a2?.name, 'claude');

  const a3 = getMatchingAdapter('https://gemini.google.com/app/test-chat');
  assert.equal(a3?.name, 'gemini');

  const aNone = getMatchingAdapter('https://example.com/');
  assert.equal(aNone, null);
});
