import { createReadStream, existsSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, join, resolve } from 'node:path';

const contentTypes: Record<string, string> = {
  '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.svg': 'image/svg+xml',
};

export function servePreview(directory: string, port: number): void {
  const root = resolve(directory);
  const server = createServer((request, response) => {
    const pathname = decodeURIComponent((request.url ?? '/').split('?')[0]);
    const candidate = resolve(root, `.${pathname === '/' ? '/index.html' : pathname}`);
    if (!candidate.startsWith(`${root}/`) || !existsSync(candidate) || !statSync(candidate).isFile()) {
      response.writeHead(404).end('Not found');
      return;
    }
    response.setHeader('Content-Type', contentTypes[extname(candidate)] ?? 'application/octet-stream');
    createReadStream(candidate).pipe(response);
  });
  server.listen(port, '127.0.0.1', () => {
    process.stdout.write(`Preview: http://127.0.0.1:${port}\nServing ${join(root, 'index.html')}\n`);
  });
}
