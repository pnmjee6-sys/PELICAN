import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { ChatGPTAdapter } from '../src/adapters/chatgpt.ts';
import { ClaudeAdapter } from '../src/adapters/claude.ts';
import { GeminiAdapter } from '../src/adapters/gemini.ts';
import { SiteCaptureController } from '../src/core/capture.ts';
import type { CaptureIngestPayload } from '../src/core/capture.ts';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function loadFixture(filename: string, url: string): JSDOM {
  const fixturePath = path.join(__dirname, 'fixtures', filename);
  const html = fs.readFileSync(fixturePath, 'utf8');
  const dom = new JSDOM(html, {
    url,
    runScripts: 'dangerously',
  });

  // Assign DOM environment to global scope for site adapter and observers
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    MutationObserver: dom.window.MutationObserver,
    KeyboardEvent: dom.window.KeyboardEvent,
    MouseEvent: dom.window.MouseEvent,
    Event: dom.window.Event,
    InputEvent: dom.window.InputEvent || dom.window.Event,
    HTMLElement: dom.window.HTMLElement,
    HTMLTextAreaElement: dom.window.HTMLTextAreaElement,
    HTMLInputElement: dom.window.HTMLInputElement,
    HTMLButtonElement: dom.window.HTMLButtonElement,
    Element: dom.window.Element,
    Node: dom.window.Node,
  });


  return dom;
}

// =========================================================================
// 1. ChatGPT Adapter Suite
// =========================================================================
test('ChatGPT Adapter — Step 5 Comprehensive Capture Suite', async (t) => {
  await t.test('capture toggle defaults to OFF and dynamically enables/disables', async () => {
    const dom = loadFixture('chatgpt.html', 'https://chatgpt.com/c/chatgpt-saved-convo-1');
    const adapter = new ChatGPTAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: false,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    // Capture is OFF: typing and sending message produces 0 captured events
    adapter.setComposerDraft('Hello ChatGPT while capture is OFF');
    const sendBtn = document.querySelector('button[data-testid="send-button"]') as HTMLButtonElement;
    sendBtn.click();

    const thread = document.querySelector('#chatgpt-messages')!;
    const newMsg1 = document.createElement('div');
    newMsg1.setAttribute('data-message-author-role', 'user');
    newMsg1.innerHTML = '<p>Hello ChatGPT while capture is OFF</p>';
    thread.appendChild(newMsg1);

    await wait(100);
    assert.equal(captured.length, 0, 'No events should be captured when capture is OFF');

    // Dynamically enable capture
    controller.setEnabled(true);
    assert.equal(controller.isEnabled(), true);

    // Now send a message with capture ON
    adapter.setComposerDraft('Hello ChatGPT with capture ON');
    sendBtn.click();

    const newMsg2 = document.createElement('div');
    newMsg2.setAttribute('data-message-author-role', 'user');
    newMsg2.innerHTML = '<p>Hello ChatGPT with capture ON</p>';
    thread.appendChild(newMsg2);

    await wait(100);
    assert.equal(captured.length, 1, 'Exactly one event captured when capture is enabled');
    assert.equal(captured[0].text, 'Hello ChatGPT with capture ON');

    // Dynamically disable capture again
    controller.setEnabled(false);
    adapter.setComposerDraft('Another turn with capture toggled back OFF');
    sendBtn.click();

    const newMsg3 = document.createElement('div');
    newMsg3.setAttribute('data-message-author-role', 'user');
    newMsg3.innerHTML = '<p>Another turn with capture toggled back OFF</p>';
    thread.appendChild(newMsg3);

    await wait(100);
    assert.equal(captured.length, 1, 'No new events captured after disabling');

    controller.destroy();
    dom.window.close();
  });

  await t.test('one new user turn sends exactly one eligible event with metadata', async () => {
    const dom = loadFixture('chatgpt.html', 'https://chatgpt.com/c/chatgpt-saved-convo-1');
    const adapter = new ChatGPTAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    adapter.setComposerDraft('I prefer concise bullet points without pleasantries.');
    const sendBtn = document.querySelector('button[data-testid="send-button"]') as HTMLButtonElement;
    sendBtn.click();

    const thread = document.querySelector('#chatgpt-messages')!;
    const userMsg = document.createElement('div');
    userMsg.setAttribute('data-message-author-role', 'user');
    userMsg.setAttribute('data-message-id', 'msg-turn-1');
    userMsg.innerHTML = '<div class="whitespace-pre-wrap"><p>I prefer concise bullet points without pleasantries.</p></div>';
    thread.appendChild(userMsg);

    await wait(100);
    assert.equal(captured.length, 1);
    const event = captured[0];
    assert.equal(event.text, 'I prefer concise bullet points without pleasantries.');
    assert.equal(event.role, 'user');
    assert.equal(event.is_extension_context, false);
    assert.equal(event.source_site, 'chatgpt.com');
    assert.equal(event.conversation_id, 'chatgpt-saved-convo-1');
    assert.ok(event.event_id && event.event_id.length > 0);

    controller.destroy();
    dom.window.close();
  });

  await t.test('first turn after SPA navigation preserves new conversation ID', async () => {
    const dom = loadFixture('chatgpt.html', 'https://chatgpt.com/');
    const adapter = new ChatGPTAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    adapter.setComposerDraft('Starting a brand new thread on architecture');
    const sendBtn = document.querySelector('button[data-testid="send-button"]') as HTMLButtonElement;
    sendBtn.click();

    // Client-side SPA navigation to real conversation path
    dom.window.history.pushState({}, '', '/c/chatgpt-new-convo-999');

    const thread = document.querySelector('#chatgpt-messages')!;
    const userMsg = document.createElement('div');
    userMsg.setAttribute('data-message-author-role', 'user');
    userMsg.innerHTML = '<p>Starting a brand new thread on architecture</p>';
    thread.appendChild(userMsg);

    await wait(100);
    assert.equal(captured.length, 1);
    assert.equal(captured[0].conversation_id, 'chatgpt-new-convo-999');
    assert.equal(captured[0].text, 'Starting a brand new thread on architecture');

    controller.destroy();
    dom.window.close();
  });

  await t.test('refreshed page seeds historical messages and captures only the new turn', async () => {
    const dom = loadFixture('chatgpt.html', 'https://chatgpt.com/c/chatgpt-saved-convo-1');
    const adapter = new ChatGPTAdapter();
    const captured: CaptureIngestPayload[] = [];

    // Controller starts after page load where DOM already contains history
    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    await wait(100);
    assert.equal(captured.length, 0, 'Pre-existing history on load must never be captured');

    // New turn authored by user
    adapter.setComposerDraft('Fresh prompt after refreshing the browser window');
    const composer = adapter.getComposer()!;
    composer.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));

    const thread = document.querySelector('#chatgpt-messages')!;
    const userMsg = document.createElement('div');
    userMsg.setAttribute('data-message-author-role', 'user');
    userMsg.innerHTML = '<p>Fresh prompt after refreshing the browser window</p>';
    thread.appendChild(userMsg);

    await wait(100);
    assert.equal(captured.length, 1);
    assert.equal(captured[0].text, 'Fresh prompt after refreshing the browser window');

    controller.destroy();
    dom.window.close();
  });

  await t.test('nested message elements produce exactly one turn', async () => {
    const dom = loadFixture('chatgpt.html', 'https://chatgpt.com/c/chatgpt-saved-convo-1');
    const adapter = new ChatGPTAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    adapter.setComposerDraft('Testing nested markup in ChatGPT');
    const sendBtn = document.querySelector('button[data-testid="send-button"]') as HTMLButtonElement;
    sendBtn.click();

    const thread = document.querySelector('#chatgpt-messages')!;
    const outerUserNode = document.createElement('div');
    outerUserNode.setAttribute('data-message-author-role', 'user');
    outerUserNode.innerHTML = `
      <div data-message-author-role="user" class="inner-container">
        <div class="whitespace-pre-wrap">
          <p>Testing nested markup in ChatGPT</p>
        </div>
      </div>
    `;
    thread.appendChild(outerUserNode);

    await wait(100);
    assert.equal(captured.length, 1, 'Nested user message containers must yield exactly one turn');
    assert.equal(captured[0].text, 'Testing nested markup in ChatGPT');

    controller.destroy();
    dom.window.close();
  });

  await t.test('assistant replies are never captured as user memories', async () => {
    const dom = loadFixture('chatgpt.html', 'https://chatgpt.com/c/chatgpt-saved-convo-1');
    const adapter = new ChatGPTAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    const thread = document.querySelector('#chatgpt-messages')!;
    const assistantMsg = document.createElement('div');
    assistantMsg.setAttribute('data-message-author-role', 'assistant');
    assistantMsg.innerHTML = '<div class="markdown prose"><p>Here is an assistant reply explaining Python.</p></div>';
    thread.appendChild(assistantMsg);

    await wait(100);
    assert.equal(captured.length, 0, 'Assistant messages must be completely ignored');

    controller.destroy();
    dom.window.close();
  });

  await t.test('old history loaded dynamically is not captured without a user send', async () => {
    const dom = loadFixture('chatgpt.html', 'https://chatgpt.com/c/chatgpt-saved-convo-1');
    const adapter = new ChatGPTAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    // Simulating user scrolling up to load earlier history (no send armed!)
    const thread = document.querySelector('#chatgpt-messages')!;
    const oldHistoryMsg = document.createElement('div');
    oldHistoryMsg.setAttribute('data-message-author-role', 'user');
    oldHistoryMsg.innerHTML = '<p>Old message loaded from infinite scroll history</p>';
    thread.prepend(oldHistoryMsg);

    await wait(100);
    assert.equal(captured.length, 0, 'Dynamic history loading must not trigger capture without matching send');

    controller.destroy();
    dom.window.close();
  });

  await t.test('repeated DOM updates and streaming settle to exactly one captured turn', async () => {
    const dom = loadFixture('chatgpt.html', 'https://chatgpt.com/c/chatgpt-saved-convo-1');
    const adapter = new ChatGPTAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 50,
      onCapture: (p) => { captured.push(p); },
    });

    adapter.setComposerDraft('Streaming prompt that mutates rapidly in DOM');
    const sendBtn = document.querySelector('button[data-testid="send-button"]') as HTMLButtonElement;
    sendBtn.click();

    const thread = document.querySelector('#chatgpt-messages')!;
    const mutatingNode = document.createElement('div');
    mutatingNode.setAttribute('data-message-author-role', 'user');
    mutatingNode.textContent = 'Streaming';
    thread.appendChild(mutatingNode);

    // Rapid successive DOM updates
    await wait(15);
    mutatingNode.textContent = 'Streaming prompt';
    await wait(15);
    mutatingNode.textContent = 'Streaming prompt that mutates rapidly in DOM';

    // Wait for debounce timer to fire once after settling
    await wait(120);
    assert.equal(captured.length, 1, 'Debounce must ensure exactly one event fires once settled');
    assert.equal(captured[0].text, 'Streaming prompt that mutates rapidly in DOM');

    controller.destroy();
    dom.window.close();
  });

  await t.test('injected memory block is stripped from captured turn and draft', async () => {
    const dom = loadFixture('chatgpt.html', 'https://chatgpt.com/c/chatgpt-saved-convo-1');
    const adapter = new ChatGPTAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    const draftWithMemory = `<!-- CONTEXT_PASSPORT_START -->
[Relevant Memory]
Prefers concise code examples with types
<!-- CONTEXT_PASSPORT_END -->
Please explain Python generators with a clear code snippet.`;

    adapter.setComposerDraft(draftWithMemory);
    const sendBtn = document.querySelector('button[data-testid="send-button"]') as HTMLButtonElement;
    sendBtn.click();

    const thread = document.querySelector('#chatgpt-messages')!;
    const userMsg = document.createElement('div');
    userMsg.setAttribute('data-message-author-role', 'user');
    userMsg.textContent = draftWithMemory;
    thread.appendChild(userMsg);


    await wait(100);
    assert.equal(captured.length, 1);
    assert.equal(captured[0].text, 'Please explain Python generators with a clear code snippet.');
    assert.equal(captured[0].text.includes('CONTEXT_PASSPORT'), false);
    assert.equal(captured[0].text.includes('Prefers concise code examples'), false);
    assert.equal(captured[0].is_extension_context, false);

    controller.destroy();
    dom.window.close();
  });
});

// =========================================================================
// 2. Claude Adapter Suite
// =========================================================================
test('Claude Adapter — Step 5 Comprehensive Capture Suite', async (t) => {
  await t.test('capture toggle defaults to OFF and dynamically enables', async () => {
    const dom = loadFixture('claude.html', 'https://claude.ai/chat/claude-saved-convo-1');
    const adapter = new ClaudeAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: false,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    adapter.setComposerDraft('Hello Claude while capture is OFF');
    const sendBtn = document.querySelector('button[aria-label="Send Message"]') as HTMLButtonElement;
    sendBtn.click();

    const thread = document.querySelector('#claude-messages')!;
    const msg = document.createElement('div');
    msg.setAttribute('data-testid', 'user-message');
    msg.innerHTML = '<div class="font-user-message"><p>Hello Claude while capture is OFF</p></div>';
    thread.appendChild(msg);

    await wait(100);
    assert.equal(captured.length, 0);

    controller.setEnabled(true);
    adapter.setComposerDraft('Hello Claude with capture ON');
    sendBtn.click();

    const msg2 = document.createElement('div');
    msg2.setAttribute('data-testid', 'user-message');
    msg2.innerHTML = '<div class="font-user-message"><p>Hello Claude with capture ON</p></div>';
    thread.appendChild(msg2);

    await wait(100);
    assert.equal(captured.length, 1);
    assert.equal(captured[0].text, 'Hello Claude with capture ON');
    assert.equal(captured[0].source_site, 'claude.ai');

    controller.destroy();
    dom.window.close();
  });

  await t.test('one new user turn on Claude with contenteditable ProseMirror', async () => {
    const dom = loadFixture('claude.html', 'https://claude.ai/chat/claude-saved-convo-1');
    const adapter = new ClaudeAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    adapter.setComposerDraft('Explain async generators in TypeScript');
    const composer = adapter.getComposer()!;
    composer.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));

    const thread = document.querySelector('#claude-messages')!;
    const userMsg = document.createElement('div');
    userMsg.setAttribute('data-testid', 'user-message');
    userMsg.innerHTML = '<div class="font-user-message"><p>Explain async generators in TypeScript</p></div>';
    thread.appendChild(userMsg);

    await wait(100);
    assert.equal(captured.length, 1);
    assert.equal(captured[0].text, 'Explain async generators in TypeScript');
    assert.equal(captured[0].source_site, 'claude.ai');
    assert.equal(captured[0].conversation_id, 'claude-saved-convo-1');

    controller.destroy();
    dom.window.close();
  });

  await t.test('first turn after SPA navigation on Claude', async () => {
    const dom = loadFixture('claude.html', 'https://claude.ai/');
    const adapter = new ClaudeAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    adapter.setComposerDraft('Claude first prompt on new chat');
    const sendBtn = document.querySelector('button[aria-label="Send Message"]') as HTMLButtonElement;
    sendBtn.click();

    dom.window.history.pushState({}, '', '/chat/claude-new-chat-888');

    const thread = document.querySelector('#claude-messages')!;
    const userMsg = document.createElement('div');
    userMsg.setAttribute('data-testid', 'user-message');
    userMsg.innerHTML = '<div class="font-user-message"><p>Claude first prompt on new chat</p></div>';
    thread.appendChild(userMsg);

    await wait(100);
    assert.equal(captured.length, 1);
    assert.equal(captured[0].conversation_id, 'claude-new-chat-888');

    controller.destroy();
    dom.window.close();
  });

  await t.test('Claude assistant replies are completely excluded', async () => {
    const dom = loadFixture('claude.html', 'https://claude.ai/chat/claude-saved-convo-1');
    const adapter = new ClaudeAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    const thread = document.querySelector('#claude-messages')!;
    const assistantMsg = document.createElement('div');
    assistantMsg.setAttribute('data-testid', 'assistant-message');
    assistantMsg.innerHTML = '<div class="font-claude-message"><p>Here is Claude responding to the user.</p></div>';
    thread.appendChild(assistantMsg);

    await wait(100);
    assert.equal(captured.length, 0);

    controller.destroy();
    dom.window.close();
  });

  await t.test('Claude nested message elements produce exactly one turn', async () => {
    const dom = loadFixture('claude.html', 'https://claude.ai/chat/claude-saved-convo-1');
    const adapter = new ClaudeAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    adapter.setComposerDraft('Nested Claude test');
    const sendBtn = document.querySelector('button[aria-label="Send Message"]') as HTMLButtonElement;
    sendBtn.click();

    const thread = document.querySelector('#claude-messages')!;
    const outerUserNode = document.createElement('div');
    outerUserNode.setAttribute('data-testid', 'user-message');
    outerUserNode.innerHTML = '<div class="font-user-message"><p>Nested Claude test</p></div>';
    thread.appendChild(outerUserNode);

    await wait(100);
    assert.equal(captured.length, 1);

    controller.destroy();
    dom.window.close();
  });

  await t.test('refreshed page on Claude seeds historical messages and captures only new turn', async () => {
    const dom = loadFixture('claude.html', 'https://claude.ai/chat/claude-saved-convo-1');
    const adapter = new ClaudeAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    await wait(100);
    assert.equal(captured.length, 0, 'Claude historical messages must be ignored on load');

    adapter.setComposerDraft('New Claude message after reload');
    const sendBtn = document.querySelector('button[aria-label="Send Message"]') as HTMLButtonElement;
    sendBtn.click();

    const thread = document.querySelector('#claude-messages')!;
    const userMsg = document.createElement('div');
    userMsg.setAttribute('data-testid', 'user-message');
    userMsg.innerHTML = '<div class="font-user-message"><p>New Claude message after reload</p></div>';
    thread.appendChild(userMsg);

    await wait(100);
    assert.equal(captured.length, 1);
    assert.equal(captured[0].text, 'New Claude message after reload');

    controller.destroy();
    dom.window.close();
  });

  await t.test('old history on Claude loaded dynamically is not captured without a send', async () => {
    const dom = loadFixture('claude.html', 'https://claude.ai/chat/claude-saved-convo-1');
    const adapter = new ClaudeAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    const thread = document.querySelector('#claude-messages')!;
    const oldHistoryMsg = document.createElement('div');
    oldHistoryMsg.setAttribute('data-testid', 'user-message');
    oldHistoryMsg.innerHTML = '<div class="font-user-message"><p>Earlier scrolled turn</p></div>';
    thread.prepend(oldHistoryMsg);

    await wait(100);
    assert.equal(captured.length, 0);

    controller.destroy();
    dom.window.close();
  });

  await t.test('repeated DOM updates on Claude settle to exactly one turn', async () => {
    const dom = loadFixture('claude.html', 'https://claude.ai/chat/claude-saved-convo-1');
    const adapter = new ClaudeAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 50,
      onCapture: (p) => { captured.push(p); },
    });

    adapter.setComposerDraft('Claude streaming prompt');
    const sendBtn = document.querySelector('button[aria-label="Send Message"]') as HTMLButtonElement;
    sendBtn.click();

    const thread = document.querySelector('#claude-messages')!;
    const userMsg = document.createElement('div');
    userMsg.setAttribute('data-testid', 'user-message');
    const inner = document.createElement('div');
    inner.className = 'font-user-message';
    inner.textContent = 'Claude';
    userMsg.appendChild(inner);
    thread.appendChild(userMsg);

    await wait(15);
    inner.textContent = 'Claude streaming';
    await wait(15);
    inner.textContent = 'Claude streaming prompt';

    await wait(120);
    assert.equal(captured.length, 1);
    assert.equal(captured[0].text, 'Claude streaming prompt');

    controller.destroy();
    dom.window.close();
  });

  await t.test('Claude injected memory block is cleanly stripped', async () => {
    const dom = loadFixture('claude.html', 'https://claude.ai/chat/claude-saved-convo-1');
    const adapter = new ClaudeAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    const draftWithMemory = `<!-- CONTEXT_PASSPORT_START -->
[Relevant Memory]
Prefers concise code explanations
<!-- CONTEXT_PASSPORT_END -->
Please explain functional programming in Scala.`;

    adapter.setComposerDraft(draftWithMemory);
    const sendBtn = document.querySelector('button[aria-label="Send Message"]') as HTMLButtonElement;
    sendBtn.click();

    const thread = document.querySelector('#claude-messages')!;
    const userMsg = document.createElement('div');
    userMsg.setAttribute('data-testid', 'user-message');
    const inner = document.createElement('div');
    inner.className = 'font-user-message';
    inner.textContent = draftWithMemory;
    userMsg.appendChild(inner);
    thread.appendChild(userMsg);

    await wait(100);
    assert.equal(captured.length, 1);
    assert.equal(captured[0].text, 'Please explain functional programming in Scala.');
    assert.equal(captured[0].text.includes('CONTEXT_PASSPORT'), false);

    controller.destroy();
    dom.window.close();
  });

});

// =========================================================================
// 3. Gemini Adapter Suite
// =========================================================================
test('Gemini Adapter — Step 5 Comprehensive Capture Suite', async (t) => {
  await t.test('capture toggle defaults to OFF and dynamically enables', async () => {
    const dom = loadFixture('gemini.html', 'https://gemini.google.com/app/gemini-saved-convo-1');
    const adapter = new GeminiAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: false,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    adapter.setComposerDraft('Hello Gemini while capture is OFF');
    const sendBtn = document.querySelector('button.send-button') as HTMLButtonElement;
    sendBtn.click();

    const thread = document.querySelector('#gemini-messages')!;
    const msg = document.createElement('user-query');
    msg.innerHTML = '<div class="user-query-container"><div class="user-query-text">Hello Gemini while capture is OFF</div></div>';
    thread.appendChild(msg);

    await wait(100);
    assert.equal(captured.length, 0);

    controller.setEnabled(true);
    adapter.setComposerDraft('Hello Gemini with capture ON');
    sendBtn.click();

    const msg2 = document.createElement('user-query');
    msg2.innerHTML = '<div class="user-query-container"><div class="user-query-text">Hello Gemini with capture ON</div></div>';
    thread.appendChild(msg2);

    await wait(100);
    assert.equal(captured.length, 1);
    assert.equal(captured[0].text, 'Hello Gemini with capture ON');
    assert.equal(captured[0].source_site, 'gemini.google.com');

    controller.destroy();
    dom.window.close();
  });

  await t.test('Gemini nested message custom elements produce exactly one turn', async () => {
    const dom = loadFixture('gemini.html', 'https://gemini.google.com/app/gemini-saved-convo-1');
    const adapter = new GeminiAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    adapter.setComposerDraft('How does Paxos achieve consensus?');
    const sendBtn = document.querySelector('button.send-button') as HTMLButtonElement;
    sendBtn.click();

    const thread = document.querySelector('#gemini-messages')!;
    const nestedQuery = document.createElement('user-query');
    nestedQuery.id = 'gemini-turn-paxos';
    nestedQuery.innerHTML = `
      <div class="user-query-container">
        <div class="user-query-text">How does Paxos achieve consensus?</div>
      </div>
    `;
    thread.appendChild(nestedQuery);

    await wait(100);
    assert.equal(captured.length, 1, 'Gemini nested user-query custom element must produce exactly one turn');
    assert.equal(captured[0].text, 'How does Paxos achieve consensus?');
    assert.equal(captured[0].conversation_id, 'gemini-saved-convo-1');
    assert.equal(captured[0].source_site, 'gemini.google.com');

    controller.destroy();
    dom.window.close();
  });

  await t.test('first turn after SPA navigation on Gemini', async () => {
    const dom = loadFixture('gemini.html', 'https://gemini.google.com/app');
    const adapter = new GeminiAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    adapter.setComposerDraft('Gemini first prompt after SPA navigation');
    const sendBtn = document.querySelector('button.send-button') as HTMLButtonElement;
    sendBtn.click();

    dom.window.history.pushState({}, '', '/app/gemini-new-chat-777');

    const thread = document.querySelector('#gemini-messages')!;
    const msg = document.createElement('user-query');
    msg.innerHTML = '<div class="user-query-container"><div class="user-query-text">Gemini first prompt after SPA navigation</div></div>';
    thread.appendChild(msg);

    await wait(100);
    assert.equal(captured.length, 1);
    assert.equal(captured[0].conversation_id, 'gemini-new-chat-777');

    controller.destroy();
    dom.window.close();
  });

  await t.test('Gemini model responses are completely excluded', async () => {
    const dom = loadFixture('gemini.html', 'https://gemini.google.com/app/gemini-saved-convo-1');
    const adapter = new GeminiAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    const thread = document.querySelector('#gemini-messages')!;
    const modelResponse = document.createElement('model-response');
    modelResponse.innerHTML = `
      <div class="model-response-container">
        <div class="response-container">
          Gemini model response answering question.
        </div>
      </div>
    `;
    thread.appendChild(modelResponse);

    await wait(100);
    assert.equal(captured.length, 0, 'Gemini model responses must never be captured');

    controller.destroy();
    dom.window.close();
  });

  await t.test('Gemini injected memory block is cleanly stripped', async () => {
    const dom = loadFixture('gemini.html', 'https://gemini.google.com/app/gemini-saved-convo-1');
    const adapter = new GeminiAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    const draftWithMemory = `<!-- CONTEXT_PASSPORT_START -->
[Relevant Memory]
Prefers Go and Rust
<!-- CONTEXT_PASSPORT_END -->
Compare Go channels with Rust channels.`;

    adapter.setComposerDraft(draftWithMemory);
    const sendBtn = document.querySelector('button.send-button') as HTMLButtonElement;
    sendBtn.click();

    const thread = document.querySelector('#gemini-messages')!;
    const userMsg = document.createElement('user-query');
    const container = document.createElement('div');
    container.className = 'user-query-container';
    const textNode = document.createElement('div');
    textNode.className = 'user-query-text';
    textNode.textContent = draftWithMemory;
    container.appendChild(textNode);
    userMsg.appendChild(container);
    thread.appendChild(userMsg);


    await wait(100);
    assert.equal(captured.length, 1);
    assert.equal(captured[0].text, 'Compare Go channels with Rust channels.');
    assert.equal(captured[0].text.includes('CONTEXT_PASSPORT'), false);

    controller.destroy();
    dom.window.close();
  });

  await t.test('refreshed page on Gemini seeds historical messages and captures only new turn', async () => {
    const dom = loadFixture('gemini.html', 'https://gemini.google.com/app/gemini-saved-convo-1');
    const adapter = new GeminiAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    await wait(100);
    assert.equal(captured.length, 0, 'Gemini historical query must never be captured on load');

    adapter.setComposerDraft('New Gemini query after reload');
    const sendBtn = document.querySelector('button.send-button') as HTMLButtonElement;
    sendBtn.click();

    const thread = document.querySelector('#gemini-messages')!;
    const userMsg = document.createElement('user-query');
    userMsg.innerHTML = '<div class="user-query-container"><div class="user-query-text">New Gemini query after reload</div></div>';
    thread.appendChild(userMsg);

    await wait(100);
    assert.equal(captured.length, 1);
    assert.equal(captured[0].text, 'New Gemini query after reload');

    controller.destroy();
    dom.window.close();
  });

  await t.test('old history on Gemini loaded dynamically is not captured without a send', async () => {
    const dom = loadFixture('gemini.html', 'https://gemini.google.com/app/gemini-saved-convo-1');
    const adapter = new GeminiAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 40,
      onCapture: (p) => { captured.push(p); },
    });

    const thread = document.querySelector('#gemini-messages')!;
    const oldHistoryMsg = document.createElement('user-query');
    oldHistoryMsg.innerHTML = '<div class="user-query-container"><div class="user-query-text">Previous conversation message</div></div>';
    thread.prepend(oldHistoryMsg);

    await wait(100);
    assert.equal(captured.length, 0);

    controller.destroy();
    dom.window.close();
  });

  await t.test('repeated DOM updates on Gemini settle to exactly one turn', async () => {
    const dom = loadFixture('gemini.html', 'https://gemini.google.com/app/gemini-saved-convo-1');
    const adapter = new GeminiAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 50,
      onCapture: (p) => { captured.push(p); },
    });

    adapter.setComposerDraft('Gemini mutating turn');
    const sendBtn = document.querySelector('button.send-button') as HTMLButtonElement;
    sendBtn.click();

    const thread = document.querySelector('#gemini-messages')!;
    const userMsg = document.createElement('user-query');
    const container = document.createElement('div');
    container.className = 'user-query-container';
    const textNode = document.createElement('div');
    textNode.className = 'user-query-text';
    textNode.textContent = 'Gemini';
    container.appendChild(textNode);
    userMsg.appendChild(container);
    thread.appendChild(userMsg);

    await wait(15);
    textNode.textContent = 'Gemini mutating';
    await wait(15);
    textNode.textContent = 'Gemini mutating turn';

    await wait(120);
    assert.equal(captured.length, 1);
    assert.equal(captured[0].text, 'Gemini mutating turn');

    controller.destroy();
    dom.window.close();
  });
});
