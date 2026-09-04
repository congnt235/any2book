import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Any2BookConfig } from './config.js';

export interface ConversionResult {
  status: 'success' | 'failed';
  output?: string;
  report?: string;
  preview?: string;
  readerHtml?: string;
  aiReview?: string;
  quality?: Record<string, unknown>;
  adapter: string;
  warnings: Array<{ code: string; severity: string; message: string; location?: string }>;
}

export interface InspectionResult {
  path: string;
  format: string;
  adapter: string;
  size: number;
  supported: boolean;
  scanPdf?: boolean;
}

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');

function invoke(args: string[]): string {
  const result = spawnSync('uv', ['run', 'any2book-backend', ...args], {
    cwd: projectRoot,
    env: {
      ...process.env,
      UV_PROJECT_ENVIRONMENT: process.env.ANY2BOOK_PYTHON_ENV ?? join(homedir(), '.cache', 'any2book', 'venv'),
    },
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'inherit'],
  });
  if (result.status !== 0) {
    throw new Error(result.stdout.trim() || 'Any2Book backend failed; see the error above.');
  }
  return result.stdout.trim();
}

export function inspectInput(input: string): InspectionResult {
  return JSON.parse(invoke(['inspect', '--input', resolve(input)])) as InspectionResult;
}

export function convertInput(input: string, config: Any2BookConfig, keepWorkdir = false): ConversionResult {
  const workdir = mkdtempSync(join(tmpdir(), 'any2book-'));
  const configPath = join(workdir, 'config.json');
  writeFileSync(configPath, JSON.stringify(config, null, 2));
  try {
    const output = invoke([
      'convert', '--input', resolve(input), '--config', configPath, '--work-dir', workdir,
    ]);
    return JSON.parse(output) as ConversionResult;
  } finally {
    if (!keepWorkdir) rmSync(workdir, { recursive: true, force: true });
    else process.stderr.write(`Work directory: ${workdir}\n`);
  }
}
