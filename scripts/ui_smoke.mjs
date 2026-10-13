#!/usr/bin/env node
/**
 * Optional real-browser workflow test. Requires Python app dependencies, FFmpeg,
 * ffprobe, and Playwright with the selected browser installed. Never connects to
 * an existing workspace: creates its own server, temporary data, and test video.
 *
 * npm install --no-save --package-lock=false playwright
 * npx playwright install chromium
 * node scripts/ui_smoke.mjs
 *
 * UI_BROWSER=firefox, UI_BROWSER_EXECUTABLE, PLAYWRIGHT_MODULE_PATH, PYTHON,
 * and KEEP_UI_ARTIFACTS=1 are optional. Dependencies are not app requirements.
 */
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import { createServer } from "node:net";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
const candidates = [
  process.env.PLAYWRIGHT_MODULE_PATH,
  "playwright",
  process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES && path.join(process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES, "playwright"),
].filter(Boolean);
let playwright;
for (const candidate of candidates) {
  try { playwright = require(candidate); break; } catch (error) { if (error.code !== "MODULE_NOT_FOUND") throw error; }
}
if (!playwright) {
  console.error("UI smoke not run: install the optional Playwright package and its browser (see docs/UI_GUIDE.md).");
  process.exit(2);
}
const browserName = process.env.UI_BROWSER || "chromium";
assert.ok(["chromium", "firefox", "webkit"].includes(browserName), "UI_BROWSER must be chromium, firefox, or webkit");
const python = process.env.PYTHON || (existsSync(path.join(root, ".venv/bin/python")) ? path.join(root, ".venv/bin/python") : "python");
let browser, server, workspace, page;
let serverLog = "";
const failures = [];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function until(check, message, timeout = 15000) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    try { if (await check()) return; } catch (error) { last = error; }
    await sleep(100);
  }
  throw new Error(`${message}${last ? `: ${last.message}` : ""}`);
}

function run(executable, args) {
  const result = spawnSync(executable, args, { cwd: root, encoding: "utf8", timeout: 60000, maxBuffer: 2 * 1024 * 1024 });
  if (result.error || result.status !== 0) throw new Error(`${executable} failed: ${result.error?.message || result.stderr}`);
  return result.stdout;
}

async function freePort() {
  const socket = createServer();
  socket.listen(0, "127.0.0.1");
  await once(socket, "listening");
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  return port;
}

async function visible(selector) { await page.locator(selector).waitFor({ state: "visible" }); }
async function hidden(selector) { await page.locator(selector).waitFor({ state: "hidden" }); }
async function saved() { await until(async () => (await page.locator("#draft-status").textContent()) === "All changes saved", "Review did not save"); }
async function closeReview() { await page.locator("#review-close").click(); await hidden("#review-dialog"); }
async function shot(name) { if (process.env.KEEP_UI_ARTIFACTS === "1") await page.screenshot({ path: path.join(workspace, `${name}.png`), fullPage: true }); }
function passed(message) { console.log(`PASS ${message}`); }

try {
  // Check browser availability before starting a server or generating fixtures.
  browser = await playwright[browserName].launch({ headless: true, timeout: 25000, ...(process.env.UI_BROWSER_EXECUTABLE ? { executablePath: process.env.UI_BROWSER_EXECUTABLE } : {}) });
  workspace = await mkdtemp(path.join(tmpdir(), "exais-ui-smoke-"));
  const clips = [path.join(workspace, "synthetic-release.mp4"), path.join(workspace, "synthetic-coverage.mp4")];
  for (const [index, target] of clips.entries()) {
    run("ffmpeg", ["-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", index ? "color=c=0x173c2a:s=320x180:r=30" : "testsrc2=size=320x180:rate=30", "-f", "lavfi", "-i", `sine=frequency=${440 + index * 110}:sample_rate=48000`, "-t", "4", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-threads", "1", "-c:a", "aac", "-shortest", target]);
  }
  const port = await freePort();
  const origin = `http://127.0.0.1:${port}`;
  const appData = path.join(workspace, "data");
  server = spawn(python, ["-m", "exais_football.cli", "serve", "--data-dir", appData, "--port", String(port)], { cwd: root, env: { ...process.env, PYTHONPATH: path.join(root, "src") }, stdio: ["ignore", "pipe", "pipe"] });
  server.on("error", error => { serverLog += error.message; });
  for (const stream of [server.stdout, server.stderr]) stream.on("data", chunk => { serverLog = (serverLog + String(chunk)).slice(-12000); });
  await until(async () => (await fetch(`${origin}/api/health`, { signal: AbortSignal.timeout(1000) })).ok, "Temporary app did not start", 30000);
  const request = async (route, options = {}) => {
    const response = await fetch(`${origin}/api${route}`, { ...options, headers: { "Content-Type": "application/json", ...options.headers }, signal: AbortSignal.timeout(15000) });
    const body = await response.json();
    assert.ok(response.ok, `${route}: ${JSON.stringify(body)}`);
    return body;
  };
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce", acceptDownloads: true });
  page = await context.newPage();
  page.setDefaultTimeout(12000);
  page.on("pageerror", error => failures.push(error.message));
  page.on("console", message => { if (message.type() === "error" && /content.security.policy|refused.to.(load|execute|apply)/i.test(message.text())) failures.push(message.text()); });
  await page.goto(origin);
  await visible("#library-empty");
  assert.equal(await page.locator("#stat-clips").textContent(), "0");
  await shot("01-empty-library");
  passed("usable empty library, local assets, and initial connection");

  await page.locator("#manage-library").click();
  await page.locator('#game-form input[name="title"]').fill("Week 1 · Synthetic test");
  await page.locator('#game-form input[name="opponent"]').fill("Practice squad");
  await page.locator('#game-form button[type="submit"]').click();
  await page.locator("#games-list").getByText("Week 1 · Synthetic test", { exact: true }).waitFor();
  await page.locator('#player-form input[name="name"]').fill("Test receiver");
  await page.locator('#player-form input[name="jersey"]').fill("18");
  await page.locator('#player-form input[name="position"]').fill("WR");
  await page.locator('#player-form button[type="submit"]').click();
  await page.locator("#players-list").getByText("Test receiver", { exact: true }).waitFor();
  await page.locator('[data-close="manage-dialog"]').click();
  const [game] = await request("/games");
  const [player] = await request("/players");
  await page.locator("#import-game").selectOption(game.id);
  await page.locator("#import-files").setInputFiles(clips);
  await until(async () => (await page.locator("#upload-summary").textContent()).startsWith("2 of 2 ready"), "Batch import did not finish", 30000);
  await until(async () => (await page.locator(".clip-card").count()) === 2, "Imported cards did not appear");
  const imported = await request("/clips");
  const first = imported.items.find(clip => clip.original_filename === "synthetic-release.mp4");
  const second = imported.items.find(clip => clip.original_filename === "synthetic-coverage.mp4");
  assert.ok(first && second);
  assert.equal(first.game_id, game.id);
  await page.locator("#import-files").setInputFiles(clips[0]);
  await until(async () => (await page.locator("#upload-summary").textContent()).startsWith("3 of 3 ready"), "Duplicate import did not finish");
  assert.equal((await request("/clips")).total, 2);
  assert.ok((await page.locator("#upload-list").textContent()).includes("Already in library"));
  passed("game/player creation, batch upload progress, explicit game assignment, duplicate detection");

  const title = '<img src=x onerror="window.__unsafe=true"> Release';
  await page.getByRole("button", { name: `Review ${first.title}`, exact: true }).click();
  await visible("#review-content");
  await page.waitForFunction(() => document.querySelector("#review-video").readyState >= 2);
  await page.locator("#edit-title").fill(title);
  await page.locator("#edit-phase").selectOption("offense");
  await page.locator("#edit-status").selectOption("review");
  await page.locator("#edit-tags").fill("release, first step");
  await page.locator(`#edit-players input[value="${player.id}"]`).check();
  await page.locator("#edit-favorite").check();
  await page.locator("#trim-start").fill("0.5");
  await page.locator("#trim-end").fill("2.5");
  await page.locator("#save-clip").click();
  await saved();
  const edited = await request(`/clips/${first.id}`);
  assert.equal(edited.title, title);
  assert.equal(edited.start_seconds, 0.5);
  assert.equal(edited.end_seconds, 2.5);
  assert.deepEqual(edited.player_ids, [player.id]);
  assert.equal(edited.source_sha256, first.source_sha256);
  assert.equal(await page.evaluate(() => window.__unsafe), undefined);
  assert.equal(await page.locator("#review-title img").count(), 0);

  // Playback is real media, not a mocked HTMLMediaElement.
  await page.locator("#playback-speed").selectOption("0.5");
  await page.locator("#review-video").evaluate(video => { video.muted = true; video.currentTime = 2.45; });
  await page.locator("#toggle-play").click();
  await until(async () => page.locator("#review-video").evaluate(video => !video.paused && video.currentTime >= 0.5 && video.currentTime < 1.2), "Selection failed to loop during real playback");
  await page.locator("#review-video").evaluate(video => video.pause());
  const beforeFrame = await page.locator("#review-video").evaluate(video => video.currentTime);
  await page.locator("#frame-next").click();
  await until(async () => (await page.locator("#review-video").evaluate(video => video.currentTime)) > beforeFrame, "Frame step failed");
  passed("non-destructive trim, football labels, safe text rendering, slow playback, loop, frame step");

  await page.locator("#review-video").evaluate(video => { video.currentTime = 1.25; });
  await page.locator("#notes-tab").click();
  await page.locator("#note-body").fill("Explode through the first step.");
  await page.locator("#add-note").click();
  await page.locator(".note-card").waitFor();
  const notes = await request(`/clips/${first.id}/notes`);
  assert.equal(notes.length, 1);
  assert.ok(Math.abs(notes[0].at_seconds - 1.25) < 0.05);
  await page.locator(".note-time-button").click();
  assert.ok(Math.abs((await page.locator("#review-video").evaluate(video => video.currentTime)) - 1.25) < 0.05);
  await page.locator("#details-tab").click();
  await page.locator("#edit-title").fill("Unsaved title");
  await page.locator("#review-close").click();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  assert.equal(await page.locator("#edit-title").inputValue(), "Unsaved title");
  await page.locator("#review-close").click();
  await page.getByRole("button", { name: "Discard draft", exact: true }).click();
  await hidden("#review-dialog");
  assert.equal((await request(`/clips/${first.id}`)).title, title);
  passed("source-relative note persistence and jump, close guard retains/discards drafts deliberately");

  await page.getByRole("button", { name: `Review ${title}`, exact: true }).click();
  await visible("#review-content");
  await page.locator("#edit-description").fill("Draft in this browser");
  await request(`/clips/${first.id}`, { method: "PATCH", body: JSON.stringify({ version: edited.version, description: "Changed in another tab" }) });
  await page.locator("#save-clip").click();
  await visible("#review-conflict");
  assert.equal(await page.locator("#edit-description").inputValue(), "Draft in this browser");
  await page.locator("#reload-clip").click();
  await page.getByRole("button", { name: "Load latest", exact: true }).click();
  await until(async () => (await page.locator("#edit-description").inputValue()) === "Changed in another tab", "Latest revision failed to load");
  await saved();
  passed("optimistic version conflict preserves draft and explicit reload resolves it");

  await page.locator("#export-clip").click();
  await until(async () => (await request("/jobs")).length === 1, "Export was not queued");
  await closeReview();
  await page.locator("#open-jobs").click();
  const link = page.getByRole("link", { name: "Download MP4", exact: true });
  await link.waitFor({ timeout: 30000 });
  const downloadPromise = page.waitForEvent("download");
  await link.click();
  const download = await downloadPromise;
  const exportedPath = path.join(workspace, "exported-selection.mp4");
  await download.saveAs(exportedPath);
  const probe = JSON.parse(run("ffprobe", ["-v", "error", "-show_streams", "-show_format", "-of", "json", exportedPath]));
  assert.ok(Math.abs(Number(probe.format.duration) - 2) < 0.15);
  assert.ok(probe.streams.some(stream => stream.codec_type === "audio"));
  assert.equal((await request(`/clips/${first.id}`)).source_sha256, first.source_sha256);
  await page.locator('[data-close="jobs-dialog"]').click();
  passed("pending export polling, browser download, FFprobe duration/audio, preserved source hash");

  await page.locator('[data-view="favorites"]').click();
  await until(async () => (await page.locator(".clip-card").count()) === 1, "Favorite filter failed");
  await page.locator('[data-view="library"]').click();
  await page.locator("#clip-search").fill("Explode through the first step");
  await until(async () => (await page.locator(".clip-card").count()) === 1, "Timed note search failed");
  await page.locator("#clip-search").fill("");
  await until(async () => (await page.locator(".clip-card").count()) === 2, "Search reset failed");
  await page.locator("#new-playlist").click();
  await page.locator('#playlist-form input[name="title"]').fill("Release session");
  await page.locator('#playlist-form button[type="submit"]').click();
  await hidden("#playlist-dialog");
  await page.locator("#add-to-playlist").click();
  await page.locator(".picker-row").first().waitFor();
  await page.locator(".picker-row").filter({ hasText: title }).locator("input").check();
  await page.locator("#picker-search").fill("synthetic-coverage");
  await until(async () => (await page.locator(".picker-row").count()) === 1, "Playlist search failed");
  await page.locator(".picker-row input").check();
  assert.equal(await page.locator("#picker-count").textContent(), "2 selected");
  await page.locator("#save-playlist-clips").click();
  await hidden("#playlist-picker");
  await until(async () => (await page.locator(".clip-card").count()) === 2, "Playlist did not show both selected clips");
  const [playlist] = await request("/playlists");
  assert.deepEqual((await request(`/playlists/${playlist.id}`)).clip_ids, [first.id, second.id]);
  await page.getByRole("button", { name: `Move ${second.title} earlier`, exact: true }).click();
  await until(async () => (await request(`/playlists/${playlist.id}`)).clip_ids[0] === second.id, "Playlist reordering failed");
  await page.locator("#play-playlist").click();
  await visible("#review-content");
  await until(async () => (await page.locator("#review-title").textContent()) === second.title, "Session opened wrong first clip");
  await page.locator("#next-clip").click();
  await until(async () => (await page.locator("#review-title").textContent()) === title, "Session next clip failed");
  await closeReview();
  passed("favorites, note search, playlist selection across search, ordering, sequential review");

  await page.locator('[data-view="library"]').click();
  await page.getByRole("button", { name: `Review ${title}`, exact: true }).click();
  await visible("#review-content");
  await page.locator("#edit-archived").check();
  await page.locator("#save-clip").click();
  await saved();
  await closeReview();
  await until(async () => (await page.locator(".clip-card").count()) === 1, "Archive did not hide clip from active library");
  await page.locator('[data-view="archived"]').click();
  await page.getByRole("button", { name: `Review ${title}`, exact: true }).click();
  await visible("#review-content");
  await page.locator("#edit-archived").uncheck();
  await page.locator("#save-clip").click();
  await saved();
  await closeReview();
  await page.locator('[data-view="library"]').click();
  await until(async () => (await page.locator(".clip-card").count()) === 2, "Restore from archive failed");
  await shot("02-library");
  await page.setViewportSize({ width: 820, height: 1180 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "Tablet page overflows horizontally");
  await page.getByRole("button", { name: `Review ${title}`, exact: true }).click();
  await visible("#review-content");
  await shot("03-tablet-review");
  assert.ok(await page.locator("#review-dialog").evaluate(dialog => dialog.scrollWidth <= dialog.clientWidth + 1), "Tablet review overflows horizontally");
  await closeReview();
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(await page.locator("#new-playlist").isVisible(), "Small-screen playlist creation inaccessible");
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "Small-screen page overflows horizontally");
  await shot("04-small-screen");
  passed("archive/restore, tablet layout, small-screen playlist access");
  assert.deepEqual(failures, [], "Browser runtime or CSP errors");
  console.log(`UI SMOKE PASSED (${browserName})`);
} catch (error) {
  console.error(`UI SMOKE FAILED (${browserName}): ${error.stack || error.message}`);
  if (page && workspace) {
    try { await page.screenshot({ path: path.join(workspace, "failure.png"), fullPage: true }); } catch {}
  }
  if (serverLog) console.error(`Temporary server output:\n${serverLog}`);
  process.exitCode = 1;
} finally {
  await browser?.close();
  if (server && server.exitCode === null) {
    server.kill("SIGTERM");
    await Promise.race([once(server, "exit"), sleep(3000)]);
    if (server.exitCode === null) server.kill("SIGKILL");
  }
  if (workspace) {
    if (process.env.KEEP_UI_ARTIFACTS === "1") console.log(`Temporary generated-film artifacts: ${workspace}`);
    else await rm(workspace, { recursive: true, force: true });
  }
}
