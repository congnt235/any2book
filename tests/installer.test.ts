import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
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

  it('does not treat --yes as permission to overwrite an existing skill', async () => {
    home = mkdtempSync(join(tmpdir(), 'any2book-home-'));
    process.env.ANY2BOOK_HOME = home;
    const [agent] = resolveAgents(['claude'], false);
    const destination = join(agent.skillRoot, 'any2book');
    const sentinel = join(destination, 'sentinel.txt');
    mkdirSync(destination, { recursive: true });
    writeFileSync(sentinel, 'keep');

    const [result] = await installSkills([agent], { force: false, assumeYes: true });

    expect(result.status).toBe('skipped');
    expect(readFileSync(sentinel, 'utf8')).toBe('keep');
  });
});
