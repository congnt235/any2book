import { existsSync, readFileSync, rmSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterAll, describe, expect, it } from 'vitest';
import { convertInput } from '../src/backend.js';
import { finalizeConfig, loadConfig } from '../src/config.js';

const output = resolve('.any2book/test-output');
afterAll(() => rmSync(resolve('.any2book'), { recursive: true, force: true }));

describe('fixture conversion', () => {
  it('converts TXT to a validated EPUB package', () => {
    const input = resolve('fixtures/txt/sample.txt');
    const base = loadConfig(resolve('configs/default.yaml'));
    const config = finalizeConfig(base, input, { output: resolve(output, 'sample.epub') });
    const result = convertInput(input, config);
    expect(result.status).toBe('success');
    expect(existsSync(result.output!)).toBe(true);
    expect(result.adapter).toBe('txt-adapter');
    expect(result.preview).toBeTruthy();
    expect(readFileSync(result.preview!, 'utf8')).toContain('Knowledge as a Personal Book');
  }, 30_000);
});
