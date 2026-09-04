import {
  closeSync, mkdtempSync, mkdirSync, readFileSync, realpathSync, renameSync, rmSync,
  symlinkSync, writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join, posix, win32 } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  isPathWithin, openPreviewFile, resolvePreviewFile, resolvePreviewRoot,
} from '../src/preview.js';

describe('preview path containment', () => {
  it('accepts files inside a Windows preview root', () => {
    expect(isPathWithin('C:\\book', 'C:\\book\\index.html', win32)).toBe(true);
    expect(isPathWithin('C:\\book', 'C:\\other\\index.html', win32)).toBe(false);
  });

  it('rejects sibling paths with a shared POSIX prefix', () => {
    expect(isPathWithin('/books/one', '/books/one/index.html', posix)).toBe(true);
    expect(isPathWithin('/books/one', '/books/one-evil/index.html', posix)).toBe(false);
  });

  it('does not serve files through a directory symlink outside the preview root', () => {
    if (process.platform === 'win32') return;
    const root = mkdtempSync(join(tmpdir(), 'any2book-preview-'));
    const external = mkdtempSync(join(tmpdir(), 'any2book-external-'));
    try {
      mkdirSync(join(root, 'safe'));
      writeFileSync(join(root, 'safe', 'inside.txt'), 'inside');
      writeFileSync(join(external, 'secret.txt'), 'secret');
      symlinkSync(external, join(root, 'linked'), 'dir');
      const previewRoot = resolvePreviewRoot(root);

      expect(resolvePreviewFile(previewRoot, '/safe/inside.txt'))
        .toBe(realpathSync(join(root, 'safe', 'inside.txt')));
      expect(resolvePreviewFile(previewRoot, '/linked/secret.txt')).toBeNull();
    } finally {
      rmSync(root, { recursive: true, force: true });
      rmSync(external, { recursive: true, force: true });
    }
  });

  it('returns an already-opened file descriptor for streaming', () => {
    if (process.platform === 'win32') return;
    const root = mkdtempSync(join(tmpdir(), 'any2book-preview-'));
    const file = join(root, 'index.html');
    try {
      writeFileSync(file, 'validated content');
      const opened = openPreviewFile(resolvePreviewRoot(root), '/');
      expect(opened).not.toBeNull();
      if (!opened) return;
      try {
        rmSync(file);
        writeFileSync(file, 'replacement content');
        expect(readFileSync(opened.descriptor, 'utf8')).toBe('validated content');
      } finally {
        closeSync(opened.descriptor);
      }
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it('rejects a preview root replaced after startup', () => {
    if (process.platform === 'win32') return;
    const parent = mkdtempSync(join(tmpdir(), 'any2book-preview-parent-'));
    const root = join(parent, 'preview');
    const moved = join(parent, 'preview-original');
    const external = mkdtempSync(join(tmpdir(), 'any2book-external-'));
    try {
      mkdirSync(root);
      writeFileSync(join(root, 'index.html'), 'inside');
      writeFileSync(join(external, 'index.html'), 'outside');
      const previewRoot = resolvePreviewRoot(root);
      renameSync(root, moved);
      symlinkSync(external, root, 'dir');

      expect(openPreviewFile(previewRoot, '/')).toBeNull();
    } finally {
      rmSync(parent, { recursive: true, force: true });
      rmSync(external, { recursive: true, force: true });
    }
  });
});
