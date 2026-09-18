// Publish one snapshot's manifest and derived tables; nested market/history folders are not bundled.
import { cpSync, existsSync, rmSync, lstatSync, mkdirSync, readdirSync } from 'node:fs';
import { resolve, join } from 'node:path';

const src = resolve(process.argv[2] ?? process.env.BORDA_DATA_DIR ?? '../data');
const dst = resolve('static/data');
if (src === dst || src.startsWith(`${dst}/`)) {
 console.error('The source snapshot must be outside static/data.');
 process.exit(1);
}
if (!existsSync(join(src, 'manifest.json')) || !existsSync(join(src, 'catalog.parquet'))) {
 console.error(`No catalog snapshot at ${src} – run \`uv run borda run\` first or set BORDA_DATA_DIR.`);
 process.exit(1);
}
const snapshotFiles = readdirSync(src, { withFileTypes: true }).filter(entry => entry.isFile() && (entry.name === 'manifest.json' || entry.name.endsWith('.parquet')));
if (existsSync(dst) || (() => { try { return lstatSync(dst).isSymbolicLink(); } catch { return false; } })()) rmSync(dst, { recursive: true, force: true });
mkdirSync(dst, { recursive: true });
for (const file of snapshotFiles) cpSync(join(src, file.name), join(dst, file.name));
console.log(`Synced Borda snapshot ${src} -> ${dst} (${snapshotFiles.length} files)`);
