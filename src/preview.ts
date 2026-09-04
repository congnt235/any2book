import {
  closeSync, constants, createReadStream, fstatSync, openSync, realpathSync, statSync,
} from 'node:fs';
import { createServer } from 'node:http';
import * as path from 'node:path';

const contentTypes: Record<string, string> = {
  '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.svg': 'image/svg+xml',
};

type PathOperations = Pick<typeof path, 'isAbsolute' | 'relative' | 'sep'>;

export interface PreviewRoot {
  path: string;
  device: number;
  inode: number;
}

export function isPathWithin(
  root: string, candidate: string, operations: PathOperations = path,
): boolean {
  const relativePath = operations.relative(root, candidate);
  return relativePath !== ''
    && relativePath !== '..'
    && !relativePath.startsWith(`..${operations.sep}`)
    && !operations.isAbsolute(relativePath);
}

export function resolvePreviewRoot(directory: string): PreviewRoot {
  const rootPath = realpathSync(path.resolve(directory));
  const root = statSync(rootPath);
  if (!root.isDirectory()) throw new Error(`Preview root is not a directory: ${directory}`);
  return { path: rootPath, device: root.dev, inode: root.ino };
}

function isCurrentPreviewRoot(root: PreviewRoot): boolean {
  try {
    const currentPath = realpathSync(root.path);
    const current = statSync(currentPath);
    return currentPath === root.path
      && current.isDirectory()
      && current.dev === root.device
      && current.ino === root.inode;
  } catch {
    return false;
  }
}

export function resolvePreviewFile(root: PreviewRoot, pathname: string): string | null {
  try {
    if (!isCurrentPreviewRoot(root)) return null;
    const candidate = path.resolve(
      root.path, `.${pathname === '/' ? '/index.html' : pathname}`,
    );
    if (!isPathWithin(root.path, candidate)) return null;
    const realCandidate = realpathSync(candidate);
    return isPathWithin(root.path, realCandidate) && statSync(realCandidate).isFile()
      ? realCandidate
      : null;
  } catch {
    return null;
  }
}

export interface OpenPreviewFile {
  descriptor: number;
  path: string;
}

export function openPreviewFile(root: PreviewRoot, pathname: string): OpenPreviewFile | null {
  const candidate = resolvePreviewFile(root, pathname);
  if (!candidate) return null;
  let descriptor: number | null = null;
  try {
    const noFollow = process.platform === 'win32' ? 0 : constants.O_NOFOLLOW;
    descriptor = openSync(candidate, constants.O_RDONLY | noFollow);
    const opened = fstatSync(descriptor);
    const currentPath = realpathSync(candidate);
    const current = statSync(currentPath);
    if (
      !opened.isFile()
      || !isCurrentPreviewRoot(root)
      || !isPathWithin(root.path, currentPath)
      || opened.dev !== current.dev
      || opened.ino !== current.ino
    ) {
      closeSync(descriptor);
      return null;
    }
    return { descriptor, path: currentPath };
  } catch {
    if (descriptor !== null) closeSync(descriptor);
    return null;
  }
}

export function servePreview(directory: string, port: number): void {
  const root = resolvePreviewRoot(directory);
  const server = createServer((request, response) => {
    let candidate: OpenPreviewFile | null = null;
    try {
      const pathname = decodeURIComponent((request.url ?? '/').split('?')[0]);
      candidate = openPreviewFile(root, pathname);
    } catch {
      response.writeHead(400).end('Bad request');
      return;
    }
    if (!candidate) {
      response.writeHead(404).end('Not found');
      return;
    }
    response.setHeader(
      'Content-Type', contentTypes[path.extname(candidate.path)] ?? 'application/octet-stream',
    );
    const stream = createReadStream(candidate.path, {
      fd: candidate.descriptor,
      autoClose: true,
    });
    stream.on('error', () => {
      if (!response.headersSent) response.writeHead(500).end('Could not read preview file');
      else response.destroy();
    });
    stream.pipe(response);
  });
  server.listen(port, '127.0.0.1', () => {
    process.stdout.write(
      `Preview: http://127.0.0.1:${port}\nServing ${path.join(root.path, 'index.html')}\n`,
    );
  });
}
