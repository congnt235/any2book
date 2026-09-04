import { spawnSync } from 'node:child_process';
import { checkbox, confirm } from '@inquirer/prompts';

export type DependencyId = 'uv' | 'pandoc' | 'epubcheck' | 'calibre';
export type SourceNeed = 'text' | 'documents' | 'pdf' | 'epub' | 'mobi';

export interface DependencyDefinition {
  id: DependencyId;
  label: string;
  command: string;
  required: boolean;
  purpose: string;
}

export interface ExecutableInstallCommand {
  dependency: DependencyId;
  command: string;
  args: string[];
  shell?: boolean;
}

export interface ManualInstallStep {
  dependency: DependencyId;
  manual: string;
}

export type InstallCommand = ExecutableInstallCommand | ManualInstallStep;

export type WindowsManager = 'winget' | 'choco';

export const dependencies: DependencyDefinition[] = [
  { id: 'uv', label: 'uv + managed Python', command: 'uv', required: true, purpose: 'Python runtime and packages' },
  { id: 'pandoc', label: 'Pandoc', command: 'pandoc', required: true, purpose: 'Structured conversion and EPUB rendering' },
  { id: 'epubcheck', label: 'EPUBCheck', command: 'epubcheck', required: false, purpose: 'Official EPUB3 validation' },
  { id: 'calibre', label: 'Calibre', command: 'ebook-convert', required: false, purpose: 'MOBI conversion and compatibility checks' },
];

export function commandExists(command: string): boolean {
  const finder = process.platform === 'win32' ? 'where' : 'which';
  return spawnSync(finder, [command], { stdio: 'ignore' }).status === 0;
}

export function missingDependencies(): DependencyDefinition[] {
  return dependencies.filter((dependency) => !commandExists(dependency.command));
}

export async function selectSourceNeeds(): Promise<SourceNeed[]> {
  return checkbox<SourceNeed>({
    message: 'Which sources do you want to convert?',
    choices: [
      { name: 'Plain text and Markdown', value: 'text', checked: true },
      { name: 'Word documents and local HTML', value: 'documents', checked: true },
      { name: 'Text-based PDF', value: 'pdf', checked: true },
      { name: 'Existing EPUB books', value: 'epub', checked: false },
      { name: 'MOBI books', value: 'mobi', checked: false },
    ],
    required: true,
  });
}

export function dependenciesForSources(sources: SourceNeed[]): DependencyId[] {
  const required = new Set<DependencyId>(['uv', 'epubcheck']);
  if (sources.some((source) => ['text', 'documents', 'pdf'].includes(source))) {
    required.add('pandoc');
  }
  if (sources.includes('mobi')) required.add('calibre');
  return [...required];
}

export function resolveSourceNeeds(ids: string[], all: boolean): SourceNeed[] {
  const known: SourceNeed[] = ['text', 'documents', 'pdf', 'epub', 'mobi'];
  if (all) return known;
  const unique = [...new Set(ids)];
  const unknown = unique.filter((id) => !known.includes(id as SourceNeed));
  if (unknown.length) {
    throw new Error(`Unknown source types: ${unknown.join(', ')}. Choose text, documents, pdf, epub, or mobi.`);
  }
  return unique as SourceNeed[];
}

function requireManager(candidates: string[], platform: string): string {
  const manager = candidates.find(commandExists);
  if (!manager) throw new Error(`No supported package manager found for ${platform}: ${candidates.join(', ')}`);
  return manager;
}

export function resolveDependencies(ids: string[], all: boolean): DependencyId[] {
  if (all) return missingDependencies().map((dependency) => dependency.id);
  const unique = [...new Set(ids)];
  const known = new Set(dependencies.map((dependency) => dependency.id));
  const unknown = unique.filter((id) => !known.has(id as DependencyId));
  if (unknown.length) {
    throw new Error(`Unknown dependencies: ${unknown.join(', ')}. Choose uv, pandoc, epubcheck, or calibre.`);
  }
  return unique as DependencyId[];
}

export function buildWindowsInstallPlan(
  ids: DependencyId[], managers: WindowsManager[],
): InstallCommand[] {
  const hasWinget = managers.includes('winget');
  const hasChocolatey = managers.includes('choco');
  const wingetIds: Record<DependencyId, string | null> = {
    uv: 'astral-sh.uv', pandoc: 'JohnMacFarlane.Pandoc', calibre: 'calibre.calibre', epubcheck: null,
  };
  const chocolateyIds: Record<DependencyId, string> = {
    uv: 'uv', pandoc: 'pandoc', calibre: 'calibre', epubcheck: 'epubcheck',
  };
  const manualInstructions: Record<DependencyId, string> = {
    uv: 'Install uv manually from https://docs.astral.sh/uv/getting-started/installation/.',
    pandoc: 'Install Pandoc manually from https://pandoc.org/installing.html.',
    calibre: 'Install Calibre manually from https://calibre-ebook.com/download_windows.',
    epubcheck: 'Install EPUBCheck manually from https://www.w3.org/developers/tools/epubcheck/.',
  };
  const plan: InstallCommand[] = [];
  for (const id of ids) {
    const wingetId = wingetIds[id];
    if (hasWinget && wingetId) {
      plan.push({
        dependency: id,
        command: 'winget',
        args: ['install', '--exact', '--id', wingetId, '--accept-package-agreements', '--accept-source-agreements'],
      });
    } else if (hasChocolatey) {
      plan.push({ dependency: id, command: 'choco', args: ['install', chocolateyIds[id], '-y'] });
    } else if (dependencies.find((dependency) => dependency.id === id)?.required) {
      throw new Error(`No supported Windows installer is available for required dependency: ${id}`);
    } else {
      plan.push({
        dependency: id,
        manual: manualInstructions[id],
      });
    }
  }
  return plan;
}

export function buildInstallPlan(
  selected: DependencyId[],
  platform: NodeJS.Platform = process.platform,
  linuxManager?: string,
): InstallCommand[] {
  const ids = selected.filter((id) => !commandExists(dependencies.find((item) => item.id === id)!.command));
  if (!ids.length) return [];
  if (platform === 'darwin') {
    if (!commandExists('brew')) throw new Error('Homebrew is required for automatic dependency installation on macOS: https://brew.sh');
    return ids.map((id) => id === 'calibre'
      ? { dependency: id, command: 'brew', args: ['install', '--cask', 'calibre'] }
      : { dependency: id, command: 'brew', args: ['install', id] });
  }
  if (platform === 'win32') {
    const managers = (['winget', 'choco'] as WindowsManager[]).filter(commandExists);
    return buildWindowsInstallPlan(ids, managers);
  }
  if (platform === 'linux') {
    const manager = linuxManager ?? requireManager(['apt-get', 'dnf', 'pacman', 'zypper'], 'Linux');
    const plan: InstallCommand[] = [];
    for (const id of ids) {
      if (id === 'uv') {
        if (!commandExists('curl')) throw new Error('curl is required to install uv automatically on Linux.');
        plan.push({ dependency: id, command: 'sh', args: ['-c', 'curl -LsSf https://astral.sh/uv/install.sh | sh'] });
        continue;
      }
      const packageName = id === 'calibre' ? 'calibre' : id;
      if (manager === 'apt-get') plan.push({ dependency: id, command: 'sudo', args: ['apt-get', 'install', '-y', packageName] });
      else if (manager === 'dnf') plan.push({ dependency: id, command: 'sudo', args: ['dnf', 'install', '-y', packageName] });
      else if (manager === 'pacman') plan.push({ dependency: id, command: 'sudo', args: ['pacman', '-S', '--needed', '--noconfirm', packageName] });
      else plan.push({ dependency: id, command: 'sudo', args: ['zypper', '--non-interactive', 'install', packageName] });
    }
    return plan;
  }
  throw new Error(`Automatic dependency installation is not supported on ${platform}.`);
}

export async function executeInstallPlan(
  plan: InstallCommand[],
  options: { assumeYes: boolean },
): Promise<void> {
  if (!plan.length) return;
  process.stdout.write('\nInstallation plan:\n');
  for (const step of plan) {
    if ('manual' in step) process.stdout.write(`  ${step.dependency}: ${step.manual}\n`);
    else process.stdout.write(`  ${step.command} ${step.args.join(' ')}\n`);
  }
  const commands = plan.filter((step): step is ExecutableInstallCommand => 'command' in step);
  if (!commands.length) return;
  const approved = options.assumeYes || await confirm({
    message: 'Run the automatic system installation commands now?',
    default: false,
  });
  if (!approved) {
    process.stdout.write('Dependency installation skipped.\n');
    return;
  }
  for (const step of commands) {
    process.stdout.write(`\nInstalling ${step.dependency}...\n`);
    const result = spawnSync(step.command, step.args, { stdio: 'inherit', shell: step.shell ?? false });
    if (result.status !== 0) throw new Error(`Failed to install ${step.dependency} with ${step.command}.`);
  }
}
