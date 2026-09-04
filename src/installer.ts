import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { checkbox, confirm, select } from '@inquirer/prompts';

export const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const skillName = 'any2book';

export interface AgentTarget {
  id: string;
  label: string;
  command?: string;
  skillRoot: string;
  detected: boolean;
}

export type UsageMode = 'cli' | 'agent' | 'both';

function commandExists(command: string): boolean {
  const finder = process.platform === 'win32' ? 'where' : 'which';
  return spawnSync(finder, [command], { stdio: 'ignore' }).status === 0;
}

function target(id: string, label: string, relativeRoot: string, command?: string): AgentTarget {
  const skillRoot = join(process.env.ANY2BOOK_HOME ?? homedir(), relativeRoot);
  return {
    id,
    label,
    command,
    skillRoot,
    detected: Boolean((command && commandExists(command)) || existsSync(dirname(skillRoot))),
  };
}

export function agentTargets(): AgentTarget[] {
  return [
    target('agents', 'Agent Skills shared root (recommended)', '.agents/skills'),
    target('claude', 'Claude Code', '.claude/skills', 'claude'),
    target('codex', 'OpenAI Codex CLI', '.codex/skills', 'codex'),
    target('pi', 'Pi coding agent', '.pi/agent/skills', 'pi'),
    target('gemini', 'Gemini CLI', '.gemini/skills', 'gemini'),
    target('opencode', 'OpenCode', '.config/opencode/skills', 'opencode'),
    target('cursor', 'Cursor', '.cursor/skills', 'cursor'),
  ];
}

export function installedSkillPath(agent: AgentTarget): string {
  return join(agent.skillRoot, skillName);
}

export function globalCliInstalled(): boolean {
  const result = spawnSync('npm', ['root', '--global'], { encoding: 'utf8' });
  if (result.status !== 0) return false;
  return existsSync(join(result.stdout.trim(), 'any2book', 'package.json'));
}

export async function selectUsage(): Promise<UsageMode> {
  return select<UsageMode>({
    message: 'How do you want to use Any2Book?',
    choices: [
      { name: 'From an AI agent and the terminal (recommended)', value: 'both' },
      { name: 'From an AI agent', value: 'agent' },
      { name: 'Directly from the terminal', value: 'cli' },
    ],
    default: 'both',
  });
}

export function installGlobalCli(): 'installed' | 'already installed' {
  if (globalCliInstalled()) return 'already installed';
  const packageData = JSON.parse(readFileSync(join(packageRoot, 'package.json'), 'utf8')) as { version: string };
  const result = spawnSync('npm', ['install', '--global', `any2book@${packageData.version}`], {
    stdio: 'inherit',
  });
  if (result.status !== 0) {
    throw new Error('Failed to install the global CLI. Check npm global permissions and try again.');
  }
  return 'installed';
}

function installFiles(agent: AgentTarget): string {
  const destination = installedSkillPath(agent);
  mkdirSync(destination, { recursive: true });
  cpSync(join(packageRoot, 'SKILL.md'), join(destination, 'SKILL.md'));
  cpSync(join(packageRoot, 'references'), join(destination, 'references'), { recursive: true });
  writeFileSync(join(destination, '.any2book-install.json'), JSON.stringify({
    package: 'any2book',
    version: JSON.parse(readFileSync(join(packageRoot, 'package.json'), 'utf8')).version,
    agent: agent.id,
    installedAt: new Date().toISOString(),
  }, null, 2));
  return destination;
}

export async function selectAgents(action: 'install' | 'uninstall'): Promise<AgentTarget[]> {
  const targets = agentTargets();
  const eligible = action === 'install'
    ? targets
    : targets.filter((agent) => existsSync(installedSkillPath(agent)));
  if (!eligible.length) return [];
  const selected = await checkbox({
    message: action === 'install' ? 'Install the Any2Book skill for:' : 'Remove Any2Book from:',
    choices: eligible.map((agent) => ({
      name: `${agent.label}${agent.detected ? ' (detected)' : ''} — ${agent.skillRoot}`,
      value: agent.id,
      checked: action === 'install' && agent.detected,
    })),
    required: true,
  });
  return eligible.filter((agent) => selected.includes(agent.id));
}

export async function installSkills(
  selected: AgentTarget[], options: { force: boolean; assumeYes: boolean },
): Promise<Array<{ agent: string; path: string; status: string }>> {
  const results: Array<{ agent: string; path: string; status: string }> = [];
  for (const agent of selected) {
    const destination = installedSkillPath(agent);
    let overwrite = options.force;
    if (existsSync(destination) && !overwrite && !options.assumeYes && process.stdin.isTTY) {
      overwrite = await confirm({ message: `Overwrite ${destination}?`, default: false });
    }
    if (existsSync(destination) && !overwrite) {
      results.push({ agent: agent.id, path: destination, status: 'skipped' });
      continue;
    }
    if (existsSync(destination)) rmSync(destination, { recursive: true, force: true });
    results.push({ agent: agent.id, path: installFiles(agent), status: 'installed' });
  }
  return results;
}

export function uninstallSkills(selected: AgentTarget[]): Array<{ agent: string; path: string; status: string }> {
  return selected.map((agent) => {
    const destination = installedSkillPath(agent);
    const owned = existsSync(join(destination, '.any2book-install.json'));
    if (!owned) return { agent: agent.id, path: destination, status: 'skipped (not installer-owned)' };
    rmSync(destination, { recursive: true, force: true });
    return { agent: agent.id, path: destination, status: 'removed' };
  });
}

export function resolveAgents(ids: string[], all: boolean): AgentTarget[] {
  const targets = agentTargets();
  if (all) return targets;
  const unique = [...new Set(ids)];
  const unknown = unique.filter((id) => !targets.some((agent) => agent.id === id));
  if (unknown.length) throw new Error(`Unknown agent(s): ${unknown.join(', ')}. Use any2book install --help.`);
  return targets.filter((agent) => unique.includes(agent.id));
}
