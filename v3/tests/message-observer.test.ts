import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { ChatGPTAdapter } from '../src/adapters/chatgpt.ts';
import { GeminiAdapter } from '../src/adapters/gemini.ts';
import { CaptureGate } from '../src/core/capture.ts';

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function page(html: string, url: string): JSDOM {
  const dom = new JSDOM(html, { url });
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    MutationObserver: dom.window.MutationObserver,
  });
  return dom;
}

test('new chats receive distinct temporary conversation IDs', () => {
  const dom = page('<body></body>', 'https://chatgpt.com/');
  const adapter = new ChatGPTAdapter();
  const first = adapter.getConversationId();
  dom.window.history.pushState({}, '', '/c/saved-chat');
  assert.equal(adapter.getConversationId(), 'saved-chat');
  dom.window.history.pushState({}, '', '/');
  const second = adapter.getConversationId();
  assert.notEqual(first, second);
  dom.window.close();
});

test('capture ignores old history and waits for the completed sent message', async () => {
  const dom = page('<body><div id="messages"><div data-message-author-role="user">old at load</div></div></body>', 'https://chatgpt.com/c/one');
  const adapter = new ChatGPTAdapter();
  const gate = new CaptureGate();
  const captured: string[] = [];
  const stop = adapter.observeUserMessages((message) => {
    if (gate.take(message.text)) captured.push(message.text);
  });
  const list = document.querySelector('#messages')!;
  const partial = document.createElement('div');
  partial.setAttribute('data-message-author-role', 'user');
  partial.textContent = 'Please explain';
  gate.arm('Please explain in steps', 'one');
  list.appendChild(partial);
  await wait(100);
  partial.textContent = 'Please explain in steps';
  await wait(700);
  assert.deepEqual(captured, ['Please explain in steps']);

  dom.window.history.pushState({}, '', '/c/two');
  list.innerHTML = '<div data-message-author-role="user">old history from another chat</div>';
  await wait(700);
  assert.deepEqual(captured, ['Please explain in steps']);
  stop();
  dom.window.close();
});

test('Gemini nested message elements produce one turn', async () => {
  const dom = page('<body><div id="messages"></div></body>', 'https://gemini.google.com/app/a');
  const adapter = new GeminiAdapter();
  const messages: string[] = [];
  const stop = adapter.observeUserMessages((message) => messages.push(message.text));
  document.querySelector('#messages')!.innerHTML = '<user-query id="parent"><div class="user-query-text">Hello Gemini</div></user-query>';
  await wait(700);
  assert.deepEqual(messages, ['Hello Gemini']);
  stop();
  dom.window.close();
});

test('first sent turn survives a same-frame SPA route change', async () => {
  const dom = page('<body><div id="messages"></div></body>', 'https://chatgpt.com/c/old');
  const adapter = new ChatGPTAdapter();
  const gate = new CaptureGate();
  const captured: string[] = [];
  const stop = adapter.observeUserMessages((message) => {
    if (gate.take(message.text)) captured.push(message.text);
  });
  gate.arm('First sent message', 'first-event');
  dom.window.history.pushState({}, '', '/c/new');
  document.querySelector('#messages')!.innerHTML = '<div data-message-author-role="user">First sent message</div>';
  await wait(700);
  assert.deepEqual(captured, ['First sent message']);
  stop();
  dom.window.close();
});
