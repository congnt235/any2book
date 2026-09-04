import { readFileSync } from 'node:fs';
import { basename, extname, resolve } from 'node:path';
import { parse as parseYaml } from 'yaml';
import { z } from 'zod';

const nullableString = z.string().min(1).nullable().default(null);

export const configSchema = z.object({
  version: z.literal(1).default(1),
  book: z.object({
    title: nullableString,
    authors: z.array(z.string()).default([]),
    language: z.string().min(2).default('vi'),
    identifier: nullableString,
    publisher: nullableString,
    description: nullableString,
    cover: nullableString,
  }),
  conversion: z.object({
    mode: z.literal('faithful').default('faithful'),
    tableOfContents: z.enum(['auto', 'none']).default('auto'),
    splitLevel: z.number().int().min(1).max(6).default(1),
    preserveImages: z.boolean().default(true),
    preserveFootnotes: z.boolean().default(true),
    includeSourceMetadata: z.boolean().default(true),
  }),
  output: z.object({
    directory: z.string().default('./dist'),
    filename: nullableString,
    preview: z.boolean().default(true),
    report: z.boolean().default(true),
  }),
  ai: z.object({
    provider: z.enum(['off', 'claude', 'codex']).default('off'),
    minimumConfidence: z.number().min(0.5).max(1).default(0.9),
    maxCorrections: z.number().int().min(1).max(200).default(80),
    timeoutSeconds: z.number().int().min(30).max(3600).default(600),
    batchPages: z.number().int().min(1).max(100).default(10),
    jobDirectory: nullableString,
    resume: z.boolean().default(false),
  }),
});

export type Any2BookConfig = z.infer<typeof configSchema>;

function mergeObjects(base: unknown, override: unknown): unknown {
  if (typeof base !== 'object' || base === null || Array.isArray(base)) return override;
  if (typeof override !== 'object' || override === null || Array.isArray(override)) return override;
  const result: Record<string, unknown> = { ...(base as Record<string, unknown>) };
  for (const [key, value] of Object.entries(override)) {
    result[key] = key in result ? mergeObjects(result[key], value) : value;
  }
  return result;
}

export function loadConfig(defaultPath: string, userPath?: string): Any2BookConfig {
  const defaults: unknown = parseYaml(readFileSync(defaultPath, 'utf8'));
  const user: unknown = userPath ? parseYaml(readFileSync(resolve(userPath), 'utf8')) : {};
  return configSchema.parse(mergeObjects(defaults, user));
}

export function finalizeConfig(
  config: Any2BookConfig,
  input: string,
  options: {
    output?: string;
    title?: string;
    author?: string[];
    language?: string;
    ai?: 'off' | 'claude' | 'codex';
    aiMinimumConfidence?: number;
    aiMaxCorrections?: number;
    aiBatchPages?: number;
    jobDir?: string;
    resume?: boolean;
  },
): Any2BookConfig {
  const stem = basename(input, extname(input));
  const outputPath = options.output ? resolve(options.output) : undefined;
  const outputDirectory = outputPath ? resolve(outputPath, '..') : resolve(config.output.directory);
  return configSchema.parse({
    ...config,
    book: {
      ...config.book,
      title: options.title ?? config.book.title ?? stem,
      authors: options.author?.length ? options.author : config.book.authors,
      language: options.language ?? config.book.language,
    },
    output: {
      ...config.output,
      directory: outputDirectory,
      filename: outputPath ? basename(outputPath) : (config.output.filename ?? `${stem}.epub`),
    },
    ai: {
      ...config.ai,
      provider: options.ai ?? config.ai.provider,
      minimumConfidence: options.aiMinimumConfidence ?? config.ai.minimumConfidence,
      maxCorrections: options.aiMaxCorrections ?? config.ai.maxCorrections,
      batchPages: options.aiBatchPages ?? config.ai.batchPages,
      jobDirectory: options.jobDir
        ? resolve(options.jobDir)
        : (config.ai.jobDirectory ? resolve(config.ai.jobDirectory) : resolve(outputDirectory, '.any2book-job')),
      resume: options.resume ?? config.ai.resume,
    },
  });
}
