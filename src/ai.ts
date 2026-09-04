import { spawnSync } from 'node:child_process';
import { confirm, select } from '@inquirer/prompts';

export type AiProvider = 'off' | 'claude' | 'codex';
export type AiProviderOption = AiProvider | 'auto';

function available(command: string): boolean {
  const finder = process.platform === 'win32' ? 'where' : 'which';
  return spawnSync(finder, [command], { stdio: 'ignore' }).status === 0;
}

export function availableAiProviders(): Exclude<AiProvider, 'off'>[] {
  const providers: Exclude<AiProvider, 'off'>[] = [];
  if (available('claude')) providers.push('claude');
  if (available('codex')) providers.push('codex');
  return providers;
}

export async function prepareAiProvider(
  requested: string,
  options: { nonInteractive: boolean; approved: boolean },
): Promise<AiProvider> {
  if (!['off', 'auto', 'claude', 'codex'].includes(requested)) {
    throw new Error('Unknown AI provider. Choose off, auto, claude, or codex.');
  }
  if (requested === 'off') return 'off';
  const installed = availableAiProviders();
  if (!installed.length) throw new Error('No supported AI CLI found. Install and sign in to Claude Code or Codex.');

  let provider: Exclude<AiProvider, 'off'>;
  if (requested === 'auto') {
    if (installed.length === 1) provider = installed[0];
    else if (options.nonInteractive || !process.stdin.isTTY) provider = installed[0];
    else provider = await select({
      message: 'Which signed-in AI CLI should review extraction errors?',
      choices: installed.map((item) => ({ name: item === 'claude' ? 'Claude Code' : 'Codex CLI', value: item })),
    });
  } else {
    provider = requested as Exclude<AiProvider, 'off'>;
    if (!installed.includes(provider)) throw new Error(`${provider} CLI is not installed or not on PATH.`);
  }

  if (!options.approved) {
    if (options.nonInteractive || !process.stdin.isTTY) {
      throw new Error('AI review uploads extracted text. Pass --yes-ai to approve non-interactively.');
    }
    const approved = await confirm({
      message: `Send extracted low-level document text to ${provider === 'claude' ? 'Claude Code' : 'Codex CLI'} for conservative correction?`,
      default: false,
    });
    if (!approved) throw new Error('AI review cancelled.');
  }
  return provider;
}
