import { mkdirSync, rmSync } from 'node:fs';
import { basename, extname, resolve } from 'node:path';
import { convertInput } from './backend.js';
import { finalizeConfig, loadConfig } from './config.js';

const target = resolve('.any2book/quality');
rmSync(target, { recursive: true, force: true });
mkdirSync(target, { recursive: true });
const inputs = [
  'fixtures/txt/sample.txt',
  'fixtures/markdown/sample.md',
  'fixtures/html/sample.html',
  'fixtures/docx/sample.docx',
  'fixtures/pdf/sample.pdf',
  'fixtures/epub/sample.epub',
];
for (const relativeInput of inputs) {
  const input = resolve(relativeInput);
  const format = basename(relativeInput, extname(relativeInput));
  const parent = relativeInput.split('/').at(-2) ?? format;
  const config = finalizeConfig(loadConfig(resolve('configs/default.yaml')), input, {
    output: resolve(target, `${parent}-${format}.epub`),
    title: `Any2Book ${parent} fixture`,
    language: 'en',
  });
  config.output.preview = parent === 'txt';
  const result = convertInput(input, config);
  process.stdout.write(`${result.adapter}: ${result.output}\n`);
}
