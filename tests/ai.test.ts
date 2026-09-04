import { describe, expect, it } from 'vitest';
import { prepareAiProvider } from '../src/ai.js';

describe('AI provider selection', () => {
  it('keeps AI disabled without probing or consent', async () => {
    await expect(prepareAiProvider('off', { nonInteractive: true, approved: false }))
      .resolves.toBe('off');
  });

  it('rejects unknown providers', async () => {
    await expect(prepareAiProvider('api-key-provider', { nonInteractive: true, approved: true }))
      .rejects.toThrow('Unknown AI provider');
  });
});
