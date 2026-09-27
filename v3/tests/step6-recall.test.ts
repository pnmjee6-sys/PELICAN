import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { ChatGPTAdapter } from '../src/adapters/chatgpt.ts';
import { ClaudeAdapter } from '../src/adapters/claude.ts';
import { GeminiAdapter } from '../src/adapters/gemini.ts';
import { screenRecall } from '../src/core/recall.ts';
import {
  handleUseMemoryAction,
  promptSensitiveApproval,
  showComposerFallbackUI,
  showErrorToast,
} from '../src/core/recallController.ts';
import { SiteCaptureController } from '../src/core/capture.ts';
import type { CaptureIngestPayload } from '../src/core/capture.ts';
import { formatMemoryBlock } from '../src/core/injection.ts';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function loadFixture(filename: string, url: string): JSDOM {
  const fixturePath = path.join(__dirname, 'fixtures', filename);
  const html = fs.readFileSync(fixturePath, 'utf8');
  const dom = new JSDOM(html, { url, runScripts: 'dangerously' });

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

  try {
    Object.defineProperty(globalThis.navigator, 'clipboard', {
      value: {
        writeText: async (text: string) => { (dom.window as any).__clipboard = text; },
        readText: async () => ((dom.window as any).__clipboard || ''),
      },
      configurable: true,
      writable: true,
    });
  } catch {}

  return dom;
}


function createMockButton(doc: Document): HTMLButtonElement {
  const btn = doc.createElement('button');
  btn.type = 'button';
  btn.className = 'context-passport-btn';
  btn.innerHTML = '<span>Use Memory</span>';
  doc.body.appendChild(btn);
  return btn;
}

// =========================================================================
// Step 6 Recall, Consent & Composer Insertion Test Suite
// =========================================================================

test('Step 6 — Recall, Consent & Composer Insertion Suite', async (t) => {
  await t.test('compact private-memory card never blocks the page or stacks', async () => {
    const dom = loadFixture('chatgpt.html', 'https://chatgpt.com/c/consent');
    const input = document.createElement('input');
    document.body.appendChild(input);
    input.focus();
    const first = promptSensitiveApproval([{text:'My email is me@example.com',classification:'sensitive'}]);
    const card = document.getElementById('cp-sensitive-consent')!;
    assert.ok(card);
    assert.equal(document.querySelector('.cp-modal-backdrop'), null);
    assert.equal(document.activeElement, input);
    assert.match(card.textContent || '', /Private memory available/);
    assert.doesNotMatch(card.textContent || '', /me@example.com/);
    const second = promptSensitiveApproval([{text:'Severe peanut allergy',classification:'sensitive'}]);
    assert.equal(await first, false);
    assert.equal(document.querySelectorAll('#cp-sensitive-consent').length, 1);
    (document.querySelector('#cp-allow-btn') as HTMLButtonElement).click();
    assert.equal(await second, true);
    assert.equal(document.querySelector('#cp-sensitive-consent'), null);
    dom.window.close();
  });
  await t.test('1. Up-to-three general selection selects at most 3 top general memories', () => {
    const rawBackendResults = {
      general_memories: [
        { text: 'Prefers TypeScript', classification: 'general' },
        { text: 'Prefers Vite for bundling', classification: 'general' },
        { text: 'Prefers Vitest over Jest', classification: 'general' },
        { text: 'Prefers ESLint flat config', classification: 'general' },
        { text: 'Prefers Tailwind CSS', classification: 'general' },
      ],
      preferences: [{ preference_text: 'Concise answers' }],
    };

    const screened = screenRecall(rawBackendResults);
    assert.equal(screened.general.length, 3, 'Must select at most 3 general memories');
    assert.deepEqual(
      screened.general.map((m) => m.text),
      ['Prefers TypeScript', 'Prefers Vite for bundling', 'Prefers Vitest over Jest']
    );
    assert.equal(screened.preferences.length, 1);
  });

  await t.test('2. Secret & sensitive fail-closed screening drops credentials and demotes sensitive items', () => {
    const rawBackendResults = {
      general_memories: [
        { text: 'Prefers Python 3.11', classification: 'general' },
        // Mislabelled secret
        { text: 'api_key=sk-ant-api03-abcdef1234567890abcdef', classification: 'general' },
        // Mislabelled password
        { text: 'password: MySecretPassword123', classification: 'general' },
        // Mislabelled sensitive items
        { text: 'I have a severe peanut allergy and asthma', classification: 'general' },
        { text: 'My personal phone number is 555-0199', classification: 'general' },
      ],
      sensitive_memories: [
        { text: 'Diagnosed with hypertension', classification: 'sensitive' },
      ],
      preferences: [
        { preference_text: 'Prefers step-by-step code walkthroughs' },
        // Secret in preference
        { preference_text: 'Authorization: Bearer my-secret-token' },
      ],
    };

    const screened = screenRecall(rawBackendResults);

    // Secrets must be dropped completely
    const allTexts = [...screened.general, ...screened.sensitive].map((m) => m.text);
    assert.equal(allTexts.some((t) => t.includes('sk-ant-') || t.includes('MySecretPassword')), false);
    assert.equal(screened.preferences.some((p) => p.preference_text.includes('Bearer my-secret-token')), false);

    // Sensitive items must be in sensitive candidates, never in general
    assert.deepEqual(
      screened.general.map((m) => m.text),
      ['Prefers Python 3.11']
    );
    assert.equal(screened.sensitive.length, 3);
    assert.ok(screened.sensitive.some((m) => m.text.includes('peanut allergy')));
    assert.ok(screened.sensitive.some((m) => m.text.includes('hypertension')));
  });

  await t.test('3. Secret in user draft cancels recall immediately with warning', async () => {
    const dom = loadFixture('chatgpt.html', 'https://chatgpt.com/c/c-1');
    const adapter = new ChatGPTAdapter();
    const button = createMockButton(document);

    adapter.setComposerDraft('Here is my secret key api_key=sk-proj-abcdef1234567890 please check it');

    let backendCalled = false;
    const mockApiClient = {
      queryMemories: async () => {
        backendCalled = true;
        return { general_memories: [{ text: 'Prefers Python' }] };
      },
    };

    const result = await handleUseMemoryAction({
      adapter,
      apiClient: mockApiClient,
      button,
    });

    assert.equal(result.success, false);
    assert.equal(result.reason, 'secret_detected');
    assert.equal(backendCalled, false, 'Backend query must NOT be invoked when draft contains a secret');
    assert.equal(button.querySelector('span')?.textContent, 'Secret in prompt!');

    dom.window.close();
  });

  await t.test('4. Sensitive modal denial ("Don\'t use") excludes sensitive memories while preparing prompt', async () => {
    const dom = loadFixture('chatgpt.html', 'https://chatgpt.com/c/c-1');
    const adapter = new ChatGPTAdapter();
    const button = createMockButton(document);

    adapter.setComposerDraft('What should I cook for dinner tonight?');

    const mockApiClient = {
      queryMemories: async () => ({
        general_memories: [{ text: 'Prefers Mediterranean cuisine', classification: 'general' }],
        sensitive_memories: [{ text: 'Severe allergy to shellfish and shrimp', classification: 'sensitive' }],
      }),
    };

    // User chooses "Don't use"
    const onSensitivePrompt = async (items: any[]) => {
      assert.equal(items.length, 1);
      assert.ok(items[0].text.includes('shellfish'));
      return false; // User denies
    };

    const result = await handleUseMemoryAction({
      adapter,
      apiClient: mockApiClient,
      button,
      onSensitivePrompt,
    });

    assert.equal(result.success, true);
    assert.equal(result.approvedSensitiveCount, 0);
    assert.equal(result.generalCount, 1);

    const draft = adapter.getComposerDraft();
    assert.ok(draft.includes('Mediterranean cuisine'), 'General memory must be attached');
    assert.ok(!draft.includes('shellfish'), 'Denied sensitive memory must NOT be attached');
    assert.ok(draft.includes('What should I cook for dinner tonight?'), 'User draft preserved');

    dom.window.close();
  });

  await t.test('5. Sensitive modal allowance ("Allow once") includes sensitive memories', async () => {
    const dom = loadFixture('claude.html', 'https://claude.ai/chat/c-2');
    const adapter = new ClaudeAdapter();
    const button = createMockButton(document);

    adapter.setComposerDraft('Recommend a restaurant menu in Rome');

    const mockApiClient = {
      queryMemories: async () => ({
        general_memories: [{ text: 'Prefers vegetarian options', classification: 'general' }],
        sensitive_memories: [{ text: 'Celiac disease (must be 100% gluten-free)', classification: 'sensitive' }],
      }),
    };

    // User chooses "Allow once"
    const onSensitivePrompt = async () => true;

    const result = await handleUseMemoryAction({
      adapter,
      apiClient: mockApiClient,
      button,
      onSensitivePrompt,
    });

    assert.equal(result.success, true);
    assert.equal(result.approvedSensitiveCount, 1);
    assert.equal(result.generalCount, 1);

    const draft = adapter.getComposerDraft();
    assert.ok(draft.includes('vegetarian options'));
    assert.ok(draft.includes('Celiac disease (must be 100% gluten-free)'));
    assert.ok(draft.includes('Recommend a restaurant menu in Rome'));

    dom.window.close();
  });

  await t.test('6. Repeated clicks cleanly replace previous memory block without duplication', async () => {
    const dom = loadFixture('chatgpt.html', 'https://chatgpt.com/c/c-3');
    const adapter = new ChatGPTAdapter();
    const button = createMockButton(document);

    adapter.setComposerDraft('Explain concurrency models in Go vs Rust.');

    let recallVersion = 1;
    const mockApiClient = {
      queryMemories: async () => {
        if (recallVersion === 1) {
          return { general_memories: [{ text: 'Prefers concise Go code' }] };
        } else if (recallVersion === 2) {
          return { general_memories: [{ text: 'Prefers Rust type safety examples' }] };
        } else {
          return { general_memories: [] }; // No memories
        }
      },
    };

    // First click: attaches version 1
    await handleUseMemoryAction({ adapter, apiClient: mockApiClient, button });
    const draft1 = adapter.getComposerDraft();
    assert.ok(draft1.includes('Prefers concise Go code'));
    assert.equal(draft1.split('[Context Passport: Memory]').length, 2, 'Exactly one start marker');

    // Second click: cleanly replaces with version 2
    recallVersion = 2;
    await handleUseMemoryAction({ adapter, apiClient: mockApiClient, button });
    const draft2 = adapter.getComposerDraft();
    assert.ok(draft2.includes('Prefers Rust type safety examples'));
    assert.ok(!draft2.includes('Prefers concise Go code'), 'Old memory block must be replaced');
    assert.equal(draft2.split('[Context Passport: Memory]').length, 2, 'No duplicated memory blocks');
    assert.ok(draft2.endsWith('Explain concurrency models in Go vs Rust.'));

    // Third click (no memories found): cleanly strips block leaving user draft
    recallVersion = 3;
    await handleUseMemoryAction({ adapter, apiClient: mockApiClient, button });
    const draft3 = adapter.getComposerDraft();
    assert.equal(draft3.trim(), 'Explain concurrency models in Go vs Rust.');
    assert.ok(!draft3.includes('Context Passport'));

    dom.window.close();
  });

  await t.test('7. User draft edits during in-flight recall are preserved ("Draft changed—retry")', async () => {
    const dom = loadFixture('gemini.html', 'https://gemini.google.com/app/c-4');
    const adapter = new GeminiAdapter();
    const button = createMockButton(document);

    adapter.setComposerDraft('Initial draft before query');

    const mockApiClient = {
      queryMemories: async () => {
        // User alters draft while backend query is resolving
        adapter.setComposerDraft('Updated draft typed while query was in-flight!');
        return { general_memories: [{ text: 'Prefers Go' }] };
      },
    };

    const result = await handleUseMemoryAction({ adapter, apiClient: mockApiClient, button });
    assert.equal(result.success, false);
    assert.equal(result.reason, 'draft_changed');
    assert.equal(adapter.getComposerDraft(), 'Updated draft typed while query was in-flight!');
    assert.equal(button.querySelector('span')?.textContent, 'Draft changed—retry');

    dom.window.close();
  });

  await t.test('8. Inserted memory block is never captured as a new memory on submit', async () => {
    const dom = loadFixture('chatgpt.html', 'https://chatgpt.com/c/c-5');
    const adapter = new ChatGPTAdapter();
    const captured: CaptureIngestPayload[] = [];

    const controller = new SiteCaptureController({
      adapter,
      initialEnabled: true,
      debounceMs: 30,
      onCapture: (p) => { captured.push(p); },
    });

    const memoryBlock = formatMemoryBlock([{ text: 'Prefers async Python' }]);
    const userPrompt = 'How do I build a FastAPI background worker?';
    adapter.setComposerDraft(`${memoryBlock}${userPrompt}`);

    // Send the prompt containing the attached memory block
    const sendBtn = document.querySelector('button[data-testid="send-button"]') as HTMLButtonElement;
    sendBtn.click();

    // Rendered message in DOM contains full prompt text
    const thread = document.querySelector('#chatgpt-messages')!;
    const userNode = document.createElement('div');
    userNode.setAttribute('data-message-author-role', 'user');
    userNode.textContent = `${memoryBlock}${userPrompt}`;
    thread.appendChild(userNode);

    await wait(80);
    assert.equal(captured.length, 1);
    assert.equal(captured[0].text, userPrompt, 'Injected memory block must be stripped before capture');
    assert.ok(!captured[0].text.includes('Prefers async Python'));
    assert.equal(captured[0].is_extension_context, false);

    controller.destroy();
    dom.window.close();
  });

  await t.test('9. Missing or uncontrollable composer triggers manual copy/paste fallback UI', async () => {
    const dom = loadFixture('chatgpt.html', 'https://chatgpt.com/c/c-6');
    const button = createMockButton(document);

    // Mock an adapter where getComposer returns null (site changed DOM)
    const brokenAdapter: any = {
      name: 'chatgpt',
      displayName: 'ChatGPT',
      getComposer: () => null,
      getComposerDraft: () => '',
      setComposerDraft: () => false,
    };

    const mockApiClient = {
      queryMemories: async () => ({
        general_memories: [{ text: 'Prefers Docker and Kubernetes', classification: 'general' }],
      }),
    };

    let fallbackTriggered = false;
    let fallbackText = '';
    const onFallback = (text: string) => {
      fallbackTriggered = true;
      fallbackText = text;
      showComposerFallbackUI(text);
    };

    const result = await handleUseMemoryAction({
      adapter: brokenAdapter,
      apiClient: mockApiClient,
      button,
      onFallback,
    });

    assert.equal(result.success, true);
    assert.equal(result.fallbackTriggered, true);
    assert.equal(fallbackTriggered, true);
    assert.ok(fallbackText.includes('Prefers Docker and Kubernetes'));

    // Check that fallback UI modal was rendered into document.body
    const fallbackModal = document.getElementById('cp-composer-fallback-backdrop');
    assert.ok(fallbackModal !== null, 'Fallback modal must be rendered in DOM');
    const textarea = fallbackModal?.querySelector('.cp-fallback-textarea') as HTMLTextAreaElement;
    assert.ok(textarea !== null);
    assert.ok(textarea.value.includes('Prefers Docker and Kubernetes'));

    // Test copy button inside fallback modal
    const copyBtn = fallbackModal?.querySelector('#cp-fallback-copy-btn') as HTMLButtonElement;
    assert.ok(copyBtn !== null);
    copyBtn.click();
    await wait(20);
    assert.equal(copyBtn.textContent, 'Copied!');

    fallbackModal?.remove();
    dom.window.close();
  });

  await t.test('10. Backend failure UI preserves user draft and displays error state', async () => {
    const dom = loadFixture('claude.html', 'https://claude.ai/chat/c-7');
    const adapter = new ClaudeAdapter();
    const button = createMockButton(document);

    adapter.setComposerDraft('Important research prompt that must never be lost');

    const failingApiClient = {
      queryMemories: async () => {
        throw new Error('500 Internal Server Error: Atlas index unreachable');
      },
    };

    let errorCaught: Error | null = null;
    const onError = (err: Error) => {
      errorCaught = err;
      showErrorToast(err.message);
    };

    const result = await handleUseMemoryAction({
      adapter,
      apiClient: failingApiClient,
      button,
      onError,
    });

    assert.equal(result.success, false);
    assert.equal(result.reason, 'backend_error');
    assert.ok(errorCaught !== null);
    assert.equal(adapter.getComposerDraft(), 'Important research prompt that must never be lost', 'Draft must be 100% preserved on error');
    assert.equal(button.querySelector('span')?.textContent, 'Backend error');
    assert.ok(button.classList.contains('cp-btn-error'));

    const toast = document.getElementById('cp-error-toast');
    assert.ok(toast !== null, 'Error toast must be displayed');
    assert.ok(toast?.textContent?.includes('Atlas index unreachable'));

    toast?.remove();
    dom.window.close();
  });
});
