import type { APIRoute } from 'astro';
import fs from 'node:fs';
import path from 'node:path';
import { root } from '../../lib/data';
export const GET: APIRoute = ({ params }) => {
  const file = params.file || '';
  if (!/^[a-zA-Z0-9-]+\.(png|gif)$/.test(file)) return new Response('Not found', {status:404});
  try {
    const bytes = fs.readFileSync(path.join(root, 'media', file));
    return new Response(new Uint8Array(bytes),{headers:{'Content-Type':file.endsWith('.gif')?'image/gif':'image/png','Cache-Control':'public, max-age=1800'}});
  } catch { return new Response('No cached image available',{status:404}); }
};
