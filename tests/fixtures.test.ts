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

  it('keeps sidecar artifacts isolated for books in the same output directory', () => {
    const base = loadConfig(resolve('configs/default.yaml'));
    const firstInput = resolve('fixtures/txt/sample.txt');
    const secondInput = resolve('fixtures/markdown/sample.md');
    const first = convertInput(firstInput, finalizeConfig(base, firstInput, {
      output: resolve(output, 'first.epub'), title: 'First book',
    }));
    const second = convertInput(secondInput, finalizeConfig(base, secondInput, {
      output: resolve(output, 'second.epub'), title: 'Second book',
    }));

    expect(first.readerHtml).not.toBe(second.readerHtml);
    expect(first.preview).not.toBe(second.preview);
    expect(first.report).not.toBe(second.report);
    expect(readFileSync(resolve(first.readerHtml!, 'book.json'), 'utf8')).toContain('First book');
    expect(readFileSync(resolve(second.readerHtml!, 'book.json'), 'utf8')).toContain('Second book');
    expect(existsSync(first.output!)).toBe(true);
    expect(existsSync(second.output!)).toBe(true);
  }, 30_000);
});
