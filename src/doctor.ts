import { spawnSync } from 'node:child_process';

export interface DependencyStatus {
  name: string;
  required: boolean;
  available: boolean;
  version?: string;
  purpose: string;
}

function probe(name: string, command: string, args: string[], required: boolean, purpose: string): DependencyStatus {
  const result = spawnSync(command, args, { encoding: 'utf8' });
  const text = `${result.stdout ?? ''}${result.stderr ?? ''}`.trim().split('\n')[0];
  return { name, required, available: result.status === 0, version: result.status === 0 ? text : undefined, purpose };
}

function pythonStatus(uvAvailable: boolean): DependencyStatus {
  const commands = process.platform === 'win32' ? ['python', 'py'] : ['python3', 'python'];
  for (const command of commands) {
    const status = probe('Python', command, ['--version'], true, 'Document processing');
    if (status.available) return status;
  }
  return {
    name: 'Python',
    required: true,
    available: uvAvailable,
    version: uvAvailable ? 'managed by uv; downloaded on first conversion' : undefined,
    purpose: 'Document processing',
  };
}

export function checkDependencies(): DependencyStatus[] {
  const uv = probe('uv', 'uv', ['--version'], true, 'Python environment and managed runtime');
  return [
    probe('Node.js', 'node', ['--version'], true, 'CLI orchestration'),
    pythonStatus(uv.available),
    uv,
    probe('Pandoc', 'pandoc', ['--version'], true, 'Structured document conversion'),
    probe('Calibre', 'ebook-convert', ['--version'], false, 'MOBI conversion and compatibility tests'),
    probe('EPUBCheck', 'epubcheck', ['--version'], false, 'EPUB3 conformance validation'),
    probe('Claude Code', 'claude', ['--version'], false, 'Optional authenticated AI correction'),
    probe('Codex CLI', 'codex', ['--version'], false, 'Optional authenticated AI correction'),
  ];
}
