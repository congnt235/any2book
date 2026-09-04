import { describe, expect, it } from 'vitest';
import { resolve } from 'node:path';
import { finalizeConfig, loadConfig } from '../src/config.js';

describe('configuration', () => {
  it('infers title and EPUB filename', () => {
    const config = loadConfig(resolve('configs/default.yaml'));
    const result = finalizeConfig(config, 'fixtures/txt/sample.txt', {});
    expect(result.book.title).toBe('sample');
    expect(result.output.filename).toBe('sample.epub');
    expect(result.conversion.mode).toBe('faithful');
    expect(result.ai.provider).toBe('off');
  });

  it('enables an authenticated CLI AI provider explicitly', () => {
    const config = loadConfig(resolve('configs/default.yaml'));
    const result = finalizeConfig(config, 'fixtures/pdf/sample.pdf', { ai: 'claude' });
    expect(result.ai.provider).toBe('claude');
    expect(result.ai.minimumConfidence).toBe(0.9);
    expect(result.ai.batchPages).toBe(10);
    expect(result.ai.jobDirectory).toContain('.any2book-job');
  });
});
