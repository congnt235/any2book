import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { installSkills, resolveAgents, uninstallSkills } from '../src/installer.js';

let home: string | undefined;
afterEach(() => {
  delete process.env.ANY2BOOK_HOME;
  if (home) rmSync(home, { recursive: true, force: true });
});

describe('skill installer', () => {
  it('installs and safely uninstalls an owned skill', async () => {
    home = mkdtempSync(join(tmpdir(), 'any2book-home-'));
    process.env.ANY2BOOK_HOME = home;
    const [agent] = resolveAgents(['claude'], false);
    const [installed] = await installSkills([agent], { force: false, assumeYes: true });
    expect(installed.status).toBe('installed');
    expect(existsSync(join(installed.path, 'SKILL.md'))).toBe(true);
    expect(readFileSync(join(installed.path, 'SKILL.md'), 'utf8')).toContain('any2book convert');
    expect(uninstallSkills([agent])[0].status).toBe('removed');
    expect(existsSync(installed.path)).toBe(false);
  });
});
