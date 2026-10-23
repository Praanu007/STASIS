// Runs every test/*.test.ts under Node: each file is bundled with esbuild, as
// the page is, then imported; it exports `tests`, a list of [name, function],
// and a failed check throws.
import { build } from 'esbuild';
import { mkdtemp, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const dir = fileURLToPath(new URL('.', import.meta.url));
const files = (await readdir(dir)).filter((f) => f.endsWith('.test.ts')).sort();
const out = await mkdtemp(join(tmpdir(), 'stasis-dashboard-test-'));
let passed = 0, failed = 0;
try {
  for (const f of files) {
    const js = join(out, f.replace(/\.ts$/, '.mjs'));
    await build({ entryPoints: [join(dir, f)], bundle: true, platform: 'node', format: 'esm', outfile: js, logLevel: 'warning' });
    const { tests } = await import(pathToFileURL(js).href);
    for (const [name, fn] of tests) {
      const t0 = performance.now();
      try {
        await fn();
        passed++;
        console.log(`ok    ${f.replace(/\.test\.ts$/, '')}: ${name} (${Math.round(performance.now() - t0)} ms)`);
      } catch (e) {
        failed++;
        console.log(`FAIL  ${f.replace(/\.test\.ts$/, '')}: ${name}\n      ${e instanceof Error ? e.message : e}`);
      }
    }
  }
} finally {
  await rm(out, { recursive: true, force: true });
}
console.log(`\n${passed} passed, ${failed} failed`);
if (failed || !passed) process.exit(1);
