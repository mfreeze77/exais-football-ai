#!/usr/bin/env node
/** Real-browser regression for the recovered backlog; generated film and a fresh workspace only. */
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const playwright = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const python = process.env.PYTHON || (existsSync(path.join(root, '.venv/bin/python')) ? path.join(root, '.venv/bin/python') : 'python');
const browserName = process.env.UI_BROWSER || 'firefox';
assert.ok(['chromium', 'firefox', 'webkit'].includes(browserName));
let browser, server, workspace, page;
let log = '';
const errors = [];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, label, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await check()) return; await sleep(100); }
  throw new Error(label + (page ? ': ' + await page.locator('#study-tools-status').textContent().catch(() => '') : ''));
}
async function screenshot(name) {
  if (!page || !workspace) return;
  const target = process.env.UI_ARTIFACT_DIR || workspace;
  await mkdir(target, { recursive: true });
  await page.screenshot({ path: path.join(target, name + '.png'), fullPage: true });
}
try {
  browser = await playwright[browserName].launch({ headless: true });
  workspace = await mkdtemp(path.join(tmpdir(), 'exais-backlog-ui-'));
  const clips = [];
  for (let i = 0; i < 2; i++) {
    const target = path.join(workspace, `backlog-${i}.mp4`);
    const generated = spawnSync('ffmpeg', ['-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
      `testsrc2=size=320x180:rate=30`, '-f', 'lavfi', '-i', `sine=frequency=${440 + i * 220}:sample_rate=48000`,
      '-t', '4', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-threads', '1', '-c:a', 'aac', '-shortest', target],
      { encoding: 'utf8', timeout: 60000 });
    assert.equal(generated.status, 0, generated.stderr); clips.push(target);
  }
  const socket = createServer(); socket.listen(0, '127.0.0.1'); await once(socket, 'listening');
  const port = socket.address().port; await new Promise(resolve => socket.close(resolve));
  const origin = `http://127.0.0.1:${port}`;
  server = spawn(python, ['-m', 'exais_football.cli', 'serve', '--data-dir', path.join(workspace, 'film'), '--port', String(port)],
    { cwd: root, env: { ...process.env, PYTHONPATH: path.join(root, 'src') }, stdio: ['ignore', 'pipe', 'pipe'] });
  server.on('error', error => { log += error.message; });
  for (const stream of [server.stdout, server.stderr]) stream.on('data', chunk => { log = (log + chunk).slice(-16000); });
  await until(async () => { try { return (await fetch(origin + '/api/health', { signal: AbortSignal.timeout(1000) })).ok; } catch { return false; } }, 'Server startup');
  const request = async (route, body = undefined) => {
    const response = await fetch(origin + '/api' + route, { method: body === undefined ? 'GET' : 'POST',
      headers: { 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal: AbortSignal.timeout(30000) });
    const data = await response.json(); assert.ok(response.ok, route + ': ' + JSON.stringify(data)); return data;
  };
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  page.setDefaultTimeout(15000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error' && /content.security.policy|refused.to.(load|execute|apply)/i.test(message.text())) errors.push(message.text()); });
  await page.goto(origin); await page.locator('#import-files').setInputFiles(clips);
  await until(async () => (await request('/clips')).total === 2, 'Import');
  const [first, second] = (await request('/clips')).items;
  const note = await request(`/clips/${first.id}/notes`, { body: 'Preserve this coaching note', at_seconds: 1 });
  await page.locator('#open-study-tools').click();
  const dialog = page.locator('#study-tools-dialog'); await dialog.waitFor({ state: 'visible' });
  const select = async clip => dialog.getByRole('checkbox', { name: `Select ${clip.title}`, exact: true }).check();
  await select(first); await select(second);
  await page.locator('#study-bulk-mode').selectOption('add'); await page.locator('#study-bulk-tags').fill('backlog-browser-review');
  await page.locator('#study-bulk-preview-button').click(); await page.locator('#study-bulk-apply').waitFor();
  assert.ok(!(await request(`/clips/${first.id}`)).tags.includes('backlog-browser-review'), 'Preview mutated film');
  await page.locator('#study-bulk-apply').click(); await page.locator('#study-batch-undo').waitFor();
  for (const clip of [first, second]) assert.ok((await request(`/clips/${clip.id}`)).tags.includes('backlog-browser-review'));
  await page.locator('#study-batch-undo').click();
  await until(async () => !(await request(`/clips/${first.id}`)).tags.includes('backlog-browser-review'), 'Undo');
  assert.deepEqual(await request(`/clips/${first.id}/notes`), [note]);
  await select(first); await page.locator('#study-cut-title').fill('Independent browser cut');
  await page.locator('#study-cut-start').fill('1'); await page.locator('#study-cut-end').fill('2');
  await page.locator('#study-create-cut').click();
  await until(async () => (await request('/clips')).total === 3, 'Independent cut');
  const cut = (await request('/clips')).items.find(c => c.title === 'Independent browser cut');
  assert.equal(cut.source_sha256, first.source_sha256); assert.notEqual(cut.id, first.id);
  assert.deepEqual(await request(`/clips/${cut.id}/notes`), []);
  await select(second); await page.locator('#study-nav-compare').click();
  await page.locator('#study-left-anchor').fill('1'); await page.locator('#study-right-anchor').fill('1');
  await page.locator('#study-save-comparison').click(); await page.locator('#study-comparison-left').waitFor();
  await until(async () => page.locator('#study-comparison-left').evaluate(v => v.readyState >= 2), 'Comparison decode');
  await page.locator('#study-comparison-zero').click();
  await until(async () => page.locator('#study-comparison-right').evaluate(v => Math.abs(v.currentTime - 1) < .08), 'Comparison anchor');
  await screenshot('08-backlog-comparison');
  await page.locator('#study-nav-library').click(); await page.locator('#study-clear-selection').click(); await select(first);
  await page.locator('#study-nav-board').click(); await page.locator('#study-build-frame-index').click();
  await until(async () => page.locator('.tools-decoded-frame').isVisible(), 'Exact decoded frame');
  const index = await request(`/study/clips/${first.id}/frame-index`); assert.equal(index.status, 'ready'); assert.equal(index.count, 120);
  await page.locator('#study-board-next').click();
  await page.locator('#study-drawing-tool').selectOption('arrow');
  await dialog.getByText('Keyboard placement', { exact: true }).click();
  await page.locator('#study-drawing-add-points').click(); await page.locator('#study-save-drawings').click();
  await until(async () => (await request(`/study/clips/${first.id}/drawings`)).drawings.length === 1, 'Saved drawing');
  await page.locator('#study-show-timeline').click();
  await until(async () => await page.locator('#study-timeline-strip img').count() === 9, 'Timeline thumbnails');
  await screenshot('09-backlog-drawing');
  await page.locator('#study-nav-cv').click(); await page.locator('#study-cv-environment-check').waitFor();
  const environment = await request('/cv/environment'); assert.ok(!environment.fingerprint, 'Unconfigured model unexpectedly ready');
  assert.equal((await request('/work')).filter(j => j.kind.startsWith('cv_')).length, 0, 'Opening CV ran a model');
  await page.locator('#study-nav-collections').click(); await page.locator('#study-collection-title').fill('Browser saved collection');
  await page.locator('#study-save-search').click();
  await until(async () => (await request('/study/searches')).length === 1, 'Saved search');
  const [search] = await request('/study/searches');
  await page.locator('#study-preview-search-' + search.id).click(); await page.locator('#study-freeze-search').click();
  await until(async () => (await request('/playlists')).length === 1, 'Frozen playlist');
  await page.locator('#study-start-session').click(); await page.locator('#study-session-watch-again').click();
  await until(async () => { const [s] = await request('/study/sessions'); return s && (await request(`/study/sessions/${s.id}`)).progress.watch_again.length === 1; }, 'Session watch-again');
  await page.locator('#study-nav-recovery').click(); await page.locator('#study-create-backup').click();
  await until(async () => { const jobs = (await request('/work')).filter(j => j.kind === 'backup'); if (jobs.some(j => ['failed', 'interrupted'].includes(j.status))) throw new Error(JSON.stringify(jobs)); return jobs.some(j => j.status === 'complete'); }, 'Verified backup', 60000);
  await page.locator('#study-refresh-work').click();
  for (const width of [1440, 820, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await until(async () => dialog.evaluate(d => d.scrollWidth <= d.clientWidth + 1), 'Responsive tool dialog');
    await screenshot('10-backlog-recovery-' + width);
  }
  await page.locator('#study-tools-close').click(); await dialog.waitFor({ state: 'hidden' });
  for (const clip of [first, second]) assert.equal((await request(`/clips/${clip.id}`)).source_sha256, clip.source_sha256);
  assert.deepEqual(errors, [], 'Browser runtime or CSP errors');
  console.log('BACKLOG UI PASSED: bulk preview/apply/undo, notes, source-sharing cuts, comparisons, decoded frames, drawings, timeline, no implicit model run, saved collections, sessions, verified backup, responsive layout');
} catch (error) {
  console.error(error.stack || error); console.error(log);
  try { await screenshot('backlog-failure'); } catch {}
  process.exitCode = 1;
} finally {
  await browser?.close();
  if (server && server.exitCode === null) { server.kill('SIGTERM'); await Promise.race([once(server, 'exit'), sleep(5000)]); if (server.exitCode === null) { server.kill('SIGKILL'); await Promise.race([once(server, 'exit'), sleep(1000)]); } }
  if (workspace) await rm(workspace, { recursive: true, force: true });
}
