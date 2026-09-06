#!/usr/bin/env node
import { existsSync, readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Command } from 'commander';
import { ZodError } from 'zod';
import { convertInput, inspectInput } from './backend.js';
import { finalizeConfig, loadConfig } from './config.js';
import {
  buildInstallPlan,
  dependenciesForSources,
  executeInstallPlan,
  resolveSourceNeeds,
  selectSourceNeeds,
  type SourceNeed,
} from './dependencies.js';
import { checkDependencies } from './doctor.js';
import {
  agentTargets,
  installGlobalCli,
  installSkills,
  installedSkillPath,
  resolveAgents,
  selectAgents,
  selectUsage,
  uninstallSkills,
  type UsageMode,
} from './installer.js';
import { servePreview } from './preview.js';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const defaults = resolve(root, 'configs/default.yaml');
const packageMetadata = JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf8')) as {
  version: string;
};
const program = new Command();
program.name('any2book')
  .description('Convert personal knowledge sources to faithful EPUB3 books')
  .version(packageMetadata.version);

const collectValue = (value: string, values: string[]): string[] => [...values, value];
const printInstallResults = (results: Array<{ agent: string; path: string; status: string }>): void => {
  for (const result of results) console.log(`${result.status.padEnd(30)} ${result.agent.padEnd(10)} ${result.path}`);
};

program.command('install')
  .aliases(['init', 'install-skill'])
  .description('Set up Any2Book based on how you plan to use it')
  .option('--usage <mode>', 'cli, agent, or both')
  .option('-a, --agent <id>', 'agents, claude, codex, pi, gemini, opencode, cursor', collectValue, [])
  .option('--all', 'install the skill for every supported agent', false)
  .option('-s, --source <type>', 'text, documents, pdf, epub, or mobi (repeatable)', collectValue, [])
  .option('--all-sources', 'support every available source type', false)
  .option('--skip-cli', 'advanced: do not install the CLI globally', false)
  .option('--skip-deps', 'do not install system dependencies', false)
  .option('-f, --force', 'overwrite an existing Any2Book skill', false)
  .option('-y, --yes', 'accept defaults and system installation commands', false)
  .action(async (options: {
    usage?: string; agent: string[]; all: boolean; source: string[]; allSources: boolean;
    skipCli: boolean; skipDeps: boolean; force: boolean; yes: boolean;
  }) => {
    let usage: UsageMode = (options.usage as UsageMode | undefined) ?? 'both';
    if (options.usage && !['cli', 'agent', 'both'].includes(options.usage)) {
      throw new Error('Unknown usage mode. Choose cli, agent, or both.');
    }
    if (!options.usage && process.stdin.isTTY && !options.yes) usage = await selectUsage();

    if (!options.skipCli) console.log(`CLI runtime: ${installGlobalCli()}`);

    if (usage !== 'cli') {
      let selected = resolveAgents(options.agent, options.all);
      if (!selected.length) {
        if (!process.stdin.isTTY) throw new Error('Non-interactive agent setup requires --agent <id> or --all.');
        selected = await selectAgents('install');
      }
      printInstallResults(await installSkills(selected, { force: options.force, assumeYes: options.yes }));
      console.log('Restart the selected agent so it can discover the skill.');
    }

    if (!options.skipDeps) {
      let sources: SourceNeed[] = resolveSourceNeeds(options.source, options.allSources);
      if (!sources.length) {
        sources = process.stdin.isTTY && !options.yes
          ? await selectSourceNeeds()
          : ['text', 'documents', 'pdf'];
      }
      console.log(`Source support: ${sources.join(', ')}`);
      await executeInstallPlan(buildInstallPlan(dependenciesForSources(sources)), { assumeYes: options.yes });
    }

    console.log('\nDependency status:');
    for (const item of checkDependencies()) {
      console.log(`${item.available ? '✓' : item.required ? '✗' : '!'} ${item.name}: ${item.version ?? 'not installed'}`);
    }
  });

program.command('uninstall-skill')
  .description('Remove skills previously installed by Any2Book')
  .option('-a, --agent <id>', 'agent id (repeatable)', collectValue, [])
  .option('--all', 'remove from every supported agent', false)
  .action(async (options: { agent: string[]; all: boolean }) => {
    let selected = resolveAgents(options.agent, options.all);
    if (!selected.length) {
      if (!process.stdin.isTTY) throw new Error('Non-interactive uninstall requires --agent <id> or --all.');
      selected = await selectAgents('uninstall');
    }
    printInstallResults(uninstallSkills(selected));
  });

program.command('skill-status').description('Show supported agent skill locations').action(() => {
  for (const agent of agentTargets()) {
    const path = installedSkillPath(agent);
    console.log(`${agent.detected ? 'detected' : 'not detected'}  ${agent.id.padEnd(10)} ${path}${existsSync(path) ? ' [installed]' : ''}`);
  }
});

program.command('doctor').description('Check conversion dependencies').action(() => {
  const statuses = checkDependencies();
  for (const item of statuses) {
    const icon = item.available ? '✓' : item.required ? '✗' : '!';
    console.log(`${icon} ${item.name.padEnd(10)} ${item.version ?? 'not installed'} — ${item.purpose}`);
  }
  if (statuses.some((item) => item.required && !item.available)) process.exitCode = 1;
});

program.command('inspect').argument('<input>').description('Inspect an input and select its adapter').action((input: string) => {
  console.log(JSON.stringify(inspectInput(input), null, 2));
});

program.command('convert')
  .argument('<input>')
  .option('-o, --output <path>')
  .option('-c, --config <path>')
  .option('--title <title>')
  .option('--author <author>', 'repeatable author', (value: string, values: string[]) => [...values, value], [])
  .option('--language <code>')
  .option('--ai <provider>', 'off, auto, claude, or codex')
  .option('--ai-minimum-confidence <number>', 'minimum confidence for applying a patch', Number)
  .option('--ai-max-corrections <number>', 'maximum corrections requested from each AI batch', Number)
  .option('--ai-batch-pages <number>', 'pages per checkpointed AI request', Number)
  .option('--job-dir <path>', 'persistent AI checkpoint directory')
  .option('--resume', 'resume completed AI batches from --job-dir', false)
  .option('--yes-ai', 'approve sending extracted text to the selected AI CLI', false)
  .option('--non-interactive', 'never prompt; infer missing metadata', false)
  .option('--keep-workdir', 'keep intermediate files for debugging', false)
  .description('Convert a supported file to EPUB3')
  .action(async (input: string, options: {
    output?: string; config?: string; title?: string; author: string[]; language?: string;
    ai?: string; aiMinimumConfidence?: number; aiMaxCorrections?: number;
    aiBatchPages?: number; jobDir?: string; resume: boolean;
    yesAi: boolean; nonInteractive: boolean; keepWorkdir: boolean;
  }) => {
    if (!existsSync(input)) throw new Error(`Input does not exist: ${input}`);
    const baseConfig = loadConfig(defaults, options.config);
    const requestedAi = options.ai ?? baseConfig.ai.provider;
    if (options.resume && requestedAi === 'off') {
      throw new Error('--resume requires --ai claude, --ai codex, or --ai auto.');
    }
    if (requestedAi !== 'off' && inspectInput(input).format !== 'pdf') {
      throw new Error('AI correction currently supports PDF inputs only.');
    }
    if (requestedAi !== 'off') {
      console.warn('AI text correction is disabled: PDF conversion preserves source content.');
    }
    const ai = 'off' as const;
    const config = finalizeConfig(baseConfig, input, { ...options, ai });
    const result = convertInput(input, config, options.keepWorkdir);
    console.log(`EPUB: ${result.output}`);
    if (result.preview) console.log(`Preview: ${result.preview}`);
    if (result.readerHtml) console.log(`Reader HTML: ${result.readerHtml}`);
    if (result.aiReview) console.log(`AI review: ${result.aiReview}`);
    if (result.report) console.log(`Report: ${result.report}`);
    console.log(`Adapter: ${result.adapter}; warnings: ${result.warnings.length}`);
    for (const warning of result.warnings) console.warn(`[${warning.code}] ${warning.message}`);
  });

program.command('preview').argument('<directory>').option('-p, --port <port>', 'port', '4173')
  .description('Serve a generated HTML preview')
  .action((directory: string, options: { port: string }) => servePreview(directory, Number(options.port)));

program.parseAsync().catch((error: unknown) => {
  if (error instanceof ZodError) console.error(error.issues.map((issue) => `${issue.path.join('.')}: ${issue.message}`).join('\n'));
  else console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
