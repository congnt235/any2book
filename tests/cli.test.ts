import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

describe('CLI metadata', () => {
  it('reports the version from package.json', () => {
    const metadata = JSON.parse(readFileSync(resolve('package.json'), 'utf8')) as {
      version: string;
    };
    const result = spawnSync(
      process.execPath,
      ['--import', 'tsx', resolve('src/cli.ts'), '--version'],
      { encoding: 'utf8' },
    );

    expect(result.status).toBe(0);
    expect(result.stderr).toBe('');
    expect(result.stdout.trim()).toBe(metadata.version);
  });
});
