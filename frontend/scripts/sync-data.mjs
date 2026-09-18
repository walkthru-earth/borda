// Copy the pipeline's Parquet output into static/data so `vite build` ships it.
// (In dev a symlink is enough: ln -s ../../data static/data)
import { cpSync, existsSync, rmSync, lstatSync } from 'node:fs';
import { resolve } from 'node:path';

const src = resolve(process.argv[2] ?? '../data');
const dst = resolve('static/data');
if (!existsSync(src)) {
	console.error(`no data dir at ${src} – run \`uv run egmarket run\` first`);
	process.exit(1);
}
if (existsSync(dst) || (() => { try { return lstatSync(dst).isSymbolicLink(); } catch { return false; } })()) rmSync(dst, { recursive: true, force: true });
cpSync(src, dst, { recursive: true, dereference: true });
console.log(`synced ${src} -> ${dst}`);
