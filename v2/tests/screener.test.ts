import test from 'node:test';
import assert from 'node:assert/strict';
import { screenText, containsSecret } from '../src/core/screener.ts';

test('screener classifies API keys and tokens as secret', () => {
  const secretSamples = [
    'My key is AIzaSyD-7890123456789012345678901234567',
    'Here is sk-ant-api03-abcdefghijklmnopqrstuvwxyz123456',
    'OpenAI key: sk-abcdefghijklmnopqrstuvwxyz1234567890',
    'Supabase service role: sbp_1234567890abcdefghijklmnopqrstuvwxyz',
    'GitHub PAT: ghp_123456789012345678901234567890123456',
    '-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...',
    'Authorization: Bearer my-super-secret-production-token-12345',
    'Database config: password = "SuperSecretPassword123!"',
  ];

  for (const sample of secretSamples) {
    assert.equal(containsSecret(sample), true, `Failed to detect secret in: ${sample}`);
    const res = screenText(sample);
    assert.equal(res.classification, 'secret', `Failed classification for: ${sample}`);
  }
});

test('screener classifies allergies and medical info as sensitive', () => {
  const sensitiveSamples = [
    'I have a severe food allergy to peanuts and tree nuts.',
    'I was recently diagnosed with celiac disease.',
    'My social security number is required for tax forms.',
  ];

  for (const sample of sensitiveSamples) {
    const res = screenText(sample);
    assert.equal(res.classification, 'sensitive', `Expected sensitive for: ${sample}`);
  }
});

test('screener classifies normal working preferences as general', () => {
  const generalSamples = [
    'I prefer using TypeScript with strict mode enabled.',
    'Please explain step by step with clear code examples.',
    'I am building a web app using FastAPI and MongoDB.',
  ];

  for (const sample of generalSamples) {
    const res = screenText(sample);
    assert.equal(res.classification, 'general', `Expected general for: ${sample}`);
    assert.equal(containsSecret(sample), false);
  }
});
