import { spawnSync } from 'node:child_process';
import { existsSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';

const mode = process.argv[2];
const target = resolve(process.argv[3] ?? 'dist');
if (!existsSync(target)) {
  process.stderr.write(`Quality target does not exist: ${target}\n`);
  process.exit(1);
}
const files = readdirSync(target, { recursive: true })
  .map(String).filter((file) => file.endsWith('.epub')).map((file) => resolve(target, file));

if (!files.length) {
  process.stderr.write(`No EPUB files in ${target}.\n`);
  process.exit(1);
}
const command = mode === 'calibre' ? 'ebook-convert' : 'epubcheck';
for (const file of files) {
  const args = mode === 'calibre' ? [file, `${file}.calibre.epub`] : [file];
  const result = spawnSync(command, args, { stdio: 'inherit' });
  if (result.error && (result.error as NodeJS.ErrnoException).code === 'ENOENT') {
    process.stderr.write(`${command} is not installed. Run any2book doctor.\n`);
    process.exit(1);
  }
  if (result.status !== 0) process.exit(result.status ?? 1);
}
