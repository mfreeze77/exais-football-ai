import { api, upload, localURL, queryString } from "./api.js";
import { $, $$, el, icon, button, iconButton, duration, filmTime, friendly, dateLabel, fillSelect, toast, choose, debounce, mountIcons } from "./ui.js";
import { ReviewController } from "./review.js";

const state = {
  games: [], players: [], tags: [], playlists: [], clips: [], total: 0,
  clipMap: new Map(), jobs: new Map(), uploads: [], view: "library", playlistId: null,
  playlist: null, health: null, loading: false, requestToken: 0, abort: null,
  uploading: false, pollTimer: null, pollRunning: false, pickerToken: 0,
  pickerSelection: [], pickerItems: [], pickerTotal: 0, playlistMutation: false,
};

const review = new ReviewController({
  catalog: () => state,
  onSaved: clip => {
    state.clipMap.set(clip.id, clip);
    Promise.allSettled([loadClips(), loadStats(), loadTags()]).then(reportBackgroundFailures);
  },
  onExport: (job, clip) => {
    state.clipMap.set(clip.id, clip);
    state.jobs.set(job.id, job);
    renderJobs();
    schedulePolling();
  },
});

function reportBackgroundFailures(results) {
  const failure = results.find(item => item.status === "rejected");
  if (failure) toast(failure.reason.message, "error");
}

function showError(error) {
  $("#global-error-text").textContent = error.message || String(error);
  $("#global-error").hidden = false;
}

async function loadStats() {
  const stats = await api("/stats");
  $("#stat-clips").textContent = String(stats.clips);
  $("#nav-count").textContent = String(stats.clips);
  $("#stat-time").textContent = filmTime(stats.total_seconds);
  $("#stat-games").textContent = String(stats.games);
}

async function loadTags() {
  state.tags = await api("/tags");
  fillSelect($("#filter-tag"), state.tags, "All tags", $("#filter-tag").value, item => `${item.name} (${item.count})`);
}

async function loadCatalogs() {
  const [games, players, playlists] = await Promise.all([api("/games"), api("/players"), api("/playlists")]);
  state.games = games;
  state.players = players;
  state.playlists = playlists;
  fillSelect($("#filter-game"), games, "All games");
  fillSelect($("#filter-player"), players, "All players", $("#filter-player").value, item => `${item.jersey ? `#${item.jersey} ` : ""}${item.name}`);
  fillSelect($("#import-game"), games, "No game assigned");
  renderCatalogs();
  renderPlaylistNav();
}

async function initialize() {
  $("#global-error").hidden = true;
  const results = await Promise.allSettled([api("/health"), loadStats(), loadCatalogs(), loadTags(), loadJobs()]);
  if (results[0].status === "fulfilled") {
    state.health = results[0].value;
    $("#connection-status").textContent = `Connected · ${state.health.version || "Ready"}`;
    $("#connection-status").closest(".local-badge").querySelector(".status-dot").classList.remove("disconnected");
    const ffprobe = state.health.capabilities?.ffprobe;
    const ffmpeg = state.health.capabilities?.ffmpeg;
    if (ffprobe && !ffprobe.available) showError(new Error(ffprobe.reason || "Video import requires ffprobe. Check the setup instructions."));
    else if (ffmpeg && !ffmpeg.available) showError(new Error(ffmpeg.reason || "FFmpeg is unavailable; video exports will need local setup."));
  } else {
    $("#connection-status").textContent = "Connection unavailable";
    $("#connection-status").closest(".local-badge").querySelector(".status-dot").classList.add("disconnected");
  }
  const failure = results.find(item => item.status === "rejected");
  if (failure) showError(failure.reason);
  await loadClips();
  schedulePolling();
}

function filterValues() {
  return {
    q: $("#clip-search").value.trim(),
    game_id: $("#filter-game").value,
    player_id: $("#filter-player").value,
    phase: $("#filter-phase").value,
    review_status: state.view === "review" ? "review" : $("#filter-status").value,
    tag: $("#filter-tag").value,
    favorite: state.view === "favorites" ? true : "",
    archived: state.view === "archived",
  };
}

function hasFilters() {
  const values = filterValues();
  return Boolean(values.q || values.game_id || values.player_id || values.phase || values.review_status || values.tag);
}

async function loadClips({ append = false } = {}) {
  const token = ++state.requestToken;
  state.abort?.abort();
  state.abort = new AbortController();
  state.loading = true;
  $("#clip-grid").setAttribute("aria-busy", "true");
  $("#load-more").disabled = true;
  if (!state.clips.length && !append) renderSkeletons();
  try {
    if (state.view === "playlist" && state.playlistId) {
      const playlist = await api(`/playlists/${encodeURIComponent(state.playlistId)}`, { signal: state.abort.signal });
      if (token !== state.requestToken) return;
      state.playlist = playlist;
      state.clips = playlist.clips || [];
      state.total = state.clips.length;
      $("#page-title").textContent = playlist.title;
      $("#collection-heading").replaceChildren(document.createTextNode("Session clips "), el("span", "count-badge", String(state.total)));
      $("#playlist-description").textContent = playlist.description || "Drag-free ordering: use the arrows below each clip to set the session sequence.";
      $("#playlist-description").hidden = false;
      $("#play-playlist").disabled = !state.clips.length;
    } else {
      const response = await api(`/clips?${queryString({ ...filterValues(), limit: 60, offset: append ? state.clips.length : 0 })}`, { signal: state.abort.signal });
      if (token !== state.requestToken) return;
      state.clips = append ? [...state.clips, ...response.items] : response.items;
      state.total = response.total;
      $("#result-count").textContent = String(state.total);
    }
    state.clips.forEach(clip => state.clipMap.set(clip.id, clip));
    renderClips();
    updateFilterCount();
  } catch (error) {
    if (error.name === "AbortError" || token !== state.requestToken) return;
    showError(error);
    if (!state.clips.length) {
      $("#clip-grid").replaceChildren();
      renderEmpty({ error: true });
    }
  } finally {
    if (token === state.requestToken) {
      state.loading = false;
      $("#clip-grid").setAttribute("aria-busy", "false");
      $("#load-more").disabled = false;
    }
  }
}

function renderSkeletons() {
  const grid = $("#clip-grid");
  grid.replaceChildren();
  for (let index = 0; index < 3; index++) { const skeleton = el("div", "loading-placeholder"); skeleton.setAttribute("aria-hidden", "true"); grid.append(skeleton); }
  $("#library-empty").hidden = true;
}

function makeClipCard(clip, index) {
  const card = el("article", "clip-card");
  const open = el("button", "clip-open");
  open.type = "button";
  open.setAttribute("aria-label", `Review ${clip.title}`);
  open.addEventListener("click", () => review.open(clip.id, state.clips.map(item => item.id)));
  const thumb = el("div", "clip-thumb");
  const image = el("img");
  image.alt = "";
  image.loading = "lazy";
  image.src = localURL(clip.thumbnail_url) || `/api/clips/${encodeURIComponent(clip.id)}/thumbnail`;
  image.addEventListener("error", () => { image.remove(); thumb.prepend(icon("film")); }, { once: true });
  thumb.append(image, el("span", `phase-badge ${clip.phase}`, friendly(clip.phase)), el("span", "clip-duration", duration(clip.duration)));
  const body = el("div", "clip-card-body");
  const title = el("h3", "", clip.title);
  title.title = clip.title;
  const game = state.games.find(item => item.id === clip.game_id);
  body.append(title, el("p", "clip-game", game ? `${game.title}${game.date ? ` · ${dateLabel(game.date)}` : ""}` : `Unassigned game · ${dateLabel(clip.created_at)}`));
  const tags = el("div", "clip-tags");
  const clipTags = clip.tags || [];
  clipTags.slice(0, 2).forEach(tag => { const node = el("span", "tag", tag); node.title = tag; tags.append(node); });
  if (clipTags.length > 2) tags.append(el("span", "tag", `+${clipTags.length - 2}`));
  if (!clipTags.length) tags.append(el("span", "tag tag-empty", "Add your coaching tags"));
  body.append(tags);
  open.append(thumb, body);
  const favorite = iconButton("star", clip.favorite ? `Remove ${clip.title} from favorites` : `Favorite ${clip.title}`, async () => {
    favorite.disabled = true;
    try {
      const saved = await api(`/clips/${encodeURIComponent(clip.id)}`, { method: "PATCH", body: { version: clip.version, favorite: !clip.favorite } });
      state.clipMap.set(saved.id, saved);
      await Promise.all([loadClips(), loadStats()]);
    } catch (error) { toast(error.status === 409 ? "This clip changed. The library has been refreshed; try again." : error.message, "error"); if (error.status === 409) await loadClips(); }
    finally { favorite.disabled = false; }
  });
  favorite.classList.add("card-favorite");
  favorite.classList.toggle("is-favorite", clip.favorite);
  favorite.setAttribute("aria-pressed", String(clip.favorite));
  const footer = el("div", "clip-card-footer");
  footer.append(el("span", `review-badge ${clip.review_status}`, clip.archived ? "Archived" : friendly(clip.review_status)), el("span", "clip-info", `${clip.width} × ${clip.height}`));
  card.append(open, favorite, footer);
  if (state.view === "playlist") {
    const controls = el("div", "playlist-card-controls");
    controls.append(el("span", "muted", `PLAY ${String(index + 1).padStart(2, "0")}`));
    const up = iconButton("up", `Move ${clip.title} earlier`, () => movePlaylistClip(index, -1));
    const down = iconButton("down", `Move ${clip.title} later`, () => movePlaylistClip(index, 1));
    up.disabled = index === 0 || state.playlistMutation;
    down.disabled = index === state.clips.length - 1 || state.playlistMutation;
    const remove = iconButton("close", `Remove ${clip.title} from this playlist`, () => removePlaylistClip(clip.id));
    remove.disabled = state.playlistMutation;
    controls.append(up, down, remove);
    card.append(controls);
  }
  return card;
}

function renderClips() {
  const grid = $("#clip-grid");
  grid.replaceChildren();
  state.clips.forEach((clip, index) => grid.append(makeClipCard(clip, index)));
  $("#library-empty").hidden = state.clips.length > 0;
  if (!state.clips.length) renderEmpty();
  $("#load-more-row").hidden = state.view === "playlist" || state.clips.length >= state.total;
  $("#loaded-summary").textContent = `${state.clips.length} of ${state.total} clips`;
}

function renderEmpty({ error = false } = {}) {
  let title = "Your next film session starts here";
  let description = "Import the clips you already have. Set the useful range, add a coaching note, and build your library.";
  let label = "Import your first clips";
  let action = () => $("#import-files").click();
  if (error) { title = "Your library is temporarily unavailable"; description = "Check the message above and reconnect to your local application."; label = "Try again"; action = initialize; }
  else if (state.view === "playlist") { title = "Make room for the plays that matter"; description = "Add clips from your library, then put them in the order you want to review."; label = "Add clips"; action = openPlaylistPicker; }
  else if (hasFilters()) { title = "No clips match these filters"; description = "Try another title, game, player, or tag. Your other clips are still in the library."; label = "Clear filters"; action = clearFilters; }
  else if (state.view === "favorites") { title = "Keep your best examples close"; description = "Select the star on any clip to find it here for your next film session."; label = "Browse all clips"; action = () => setView("library"); }
  else if (state.view === "archived") { title = "Your archive is empty"; description = "Archived clips stay in the workspace and can be brought back from their review details."; label = "Browse all clips"; action = () => setView("library"); }
  $("#empty-title").textContent = title;
  $("#empty-description").textContent = description;
  $("#empty-action").replaceChildren(icon(error ? "refresh" : "plus"), document.createTextNode(label));
  $("#empty-action").onclick = action;
  $("#library-empty").hidden = false;
}

async function setView(view, playlistId = null) {
  state.view = view;
  state.playlistId = playlistId;
  state.playlist = null;
  state.clips = [];
  const views = {
    library: ["Film library", "Every clip, coaching note, and detail. Ready when you are.", "All clips"],
    favorites: ["Your favorites", "The examples worth coming back to. Keep them close.", "Favorite clips"],
    review: ["In review", "Clips marked In review, ready for another focused pass.", "Clips in review"],
    archived: ["Clip archive", "Past film, still preserved. Open a clip to bring it back to your library.", "Archived clips"],
    playlist: [state.playlists.find(item => item.id === playlistId)?.title || "Playlist", "One focus. A collection of plays. Your next film session.", "Session clips"],
  };
  const [title, description, collection] = views[view];
  $("#page-title").replaceChildren(document.createTextNode(title), el("span", "title-dot", "."));
  $("#page-description").textContent = description;
  $("#breadcrumb-current").textContent = view === "playlist" ? "Playlist" : title;
  const count = el("span", "count-badge", "0");
  count.id = "result-count";
  $("#collection-heading").replaceChildren(document.createTextNode(`${collection} `), count);
  $$(".main-nav .nav-item").forEach(item => {
    const active = item.dataset.view === view;
    item.classList.toggle("active", active);
    if (active) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
  });
  $("#filter-bar").hidden = view === "playlist";
  $("#extra-filters").hidden = true;
  $("#more-filters").setAttribute("aria-expanded", "false");
  $("#filter-status").disabled = view === "review";
  $("#playlist-description").hidden = view !== "playlist";
  $("#play-playlist").hidden = view !== "playlist";
  $("#add-to-playlist").hidden = view !== "playlist";
  $("#library-stats").hidden = view === "playlist";
  renderPlaylistNav();
  await loadClips();
}

function updateFilterCount() {
  const values = filterValues();
  const count = [values.phase, values.review_status, values.tag].filter(Boolean).length;
  $("#active-filter-count").textContent = String(count);
  $("#active-filter-count").hidden = !count;
}

function clearFilters() {
  ["clip-search", "filter-game", "filter-player", "filter-phase", "filter-status", "filter-tag"].forEach(id => { $(`#${id}`).value = ""; });
  if (state.view === "review") setView("library"); else loadClips();
}

function renderPlaylistNav() {
  const nav = $("#playlist-nav");
  nav.replaceChildren();
  if (!state.playlists.length) nav.append(el("p", "sidebar-hint", "Group clips for a film session."));
  state.playlists.forEach(playlist => {
    const node = button("", () => setView("playlist", playlist.id), "nav-item", "playlist");
    node.append(el("span", "", playlist.title));
    node.title = playlist.title;
    const active = state.view === "playlist" && state.playlistId === playlist.id;
    node.classList.toggle("active", active);
    if (active) node.setAttribute("aria-current", "page");
    nav.append(node);
  });
}

function renderCatalogs() {
  const games = $("#games-list");
  games.replaceChildren();
  if (!state.games.length) games.append(el("p", "muted", "Add a game to keep your clips together."));
  state.games.forEach(game => {
    const row = el("div", "catalog-row");
    const content = el("div");
    content.append(el("p", "catalog-title", game.title), el("p", "muted", [game.opponent, dateLabel(game.date), game.season].filter(Boolean).join(" · ")));
    row.append(icon("film"), content);
    games.append(row);
  });
  const players = $("#players-list");
  players.replaceChildren();
  if (!state.players.length) players.append(el("p", "muted", "Add the players you want to review."));
  state.players.forEach(player => {
    const row = el("div", "catalog-row");
    const content = el("div");
    content.append(el("p", "catalog-title", player.name), el("p", "muted", player.position || "Position not set"));
    row.append(el("span", "jersey-badge", player.jersey || "—"), content);
    players.append(row);
  });
}

async function submitCatalogForm(event, path, success) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const submit = $('button[type="submit"]', form);
  const error = $(".form-error", form);
  const payload = Object.fromEntries(new FormData(form));
  submit.disabled = true;
  error.hidden = true;
  try {
    await api(path, { method: "POST", body: payload });
    // A slow request must not erase a new draft typed while it was in flight.
    if (JSON.stringify(Object.fromEntries(new FormData(form))) === JSON.stringify(payload)) form.reset();
    await Promise.all([loadCatalogs(), loadStats()]);
    toast(success);
  } catch (failure) { error.textContent = failure.message; error.hidden = false; }
  finally { submit.disabled = false; }
}

function startImports(files) {
  const maxSize = state.health?.max_upload_bytes;
  const gameId = $("#import-game").value;
  const items = [...files].filter(file => file instanceof File);
  if (!items.length) return;
  items.forEach(file => {
    const tooLarge = maxSize && file.size > maxSize;
    state.uploads.push({ id: crypto.randomUUID(), file, filename: file.name, gameId, progress: 0, status: tooLarge ? "failed" : "queued", error: tooLarge ? `File exceeds the ${Math.round(maxSize / 1024 / 1024)} MB import limit.` : null });
  });
  $("#upload-panel").hidden = false;
  renderUploads();
  runUploads();
}

function renderUploads() {
  const list = $("#upload-list");
  list.replaceChildren();
  state.uploads.forEach(item => {
    const row = el("div", `upload-row ${item.status === "duplicate" ? "complete" : item.status}`);
    row.dataset.uploadId = item.id;
    const content = el("div");
    content.append(el("p", "upload-name", item.filename));
    const progress = el("progress");
    progress.max = 100;
    progress.value = item.progress;
    progress.setAttribute("aria-label", `Import progress for ${item.filename}`);
    content.append(progress);
    const status = el("span", "upload-state");
    status.textContent = uploadStatus(item);
    row.append(icon(item.status === "complete" || item.status === "duplicate" ? "check" : "film"), content, status);
    if (item.status === "failed" && item.file) {
      const retry = button("Retry", () => { item.status = "queued"; item.error = null; item.progress = 0; renderUploads(); runUploads(); }, "button button-quiet button-small");
      status.append(document.createTextNode(" "), retry);
    }
    list.append(row);
  });
  updateUploadSummary();
}

function uploadStatus(item) {
  if (item.status === "queued") return "Waiting";
  if (item.status === "uploading") return item.progress >= 100 ? "Validating & indexing…" : `Uploading ${item.progress}%`;
  if (item.status === "duplicate") return "Already in library";
  if (item.status === "complete") return "Imported";
  return item.error || "Import failed";
}

function updateUploadSummary() {
  const done = state.uploads.filter(item => ["complete", "duplicate"].includes(item.status)).length;
  const failed = state.uploads.filter(item => item.status === "failed").length;
  const pending = state.uploads.filter(item => ["queued", "uploading"].includes(item.status)).length;
  $("#upload-summary").textContent = `${done} of ${state.uploads.length} ready${failed ? ` · ${failed} failed` : ""}${pending ? " · Keep this tab open until imports finish." : ""}`;
  $("#dismiss-uploads").disabled = pending > 0;
}

async function runUploads() {
  if (state.uploading) return;
  state.uploading = true;
  try {
    let item;
    while ((item = state.uploads.find(entry => entry.status === "queued"))) {
      item.status = "uploading";
      renderUploads();
      const form = new FormData();
      form.append("file", item.file);
      if (item.gameId) form.append("game_id", item.gameId);
      try {
        const response = await upload("/imports", form, progress => {
          item.progress = progress;
          const row = $$(".upload-row").find(node => node.dataset.uploadId === item.id);
          if (row) { $("progress", row).value = progress; $(".upload-state", row).textContent = uploadStatus(item); }
        });
        item.status = response.duplicate ? "duplicate" : "complete";
        item.progress = 100;
        item.file = null;
        state.clipMap.set(response.clip.id, response.clip);
      } catch (error) { item.status = "failed"; item.error = error.message; }
      renderUploads();
      await Promise.allSettled([loadClips(), loadStats(), loadTags()]);
    }
  } finally { state.uploading = false; updateUploadSummary(); }
}

async function createPlaylist(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form.reportValidity()) return;
  const submit = $('button[type="submit"]', form);
  const error = $(".form-error", form);
  const payload = Object.fromEntries(new FormData(form));
  submit.disabled = true;
  error.hidden = true;
  try {
    const playlist = await api("/playlists", { method: "POST", body: payload });
    state.playlists.push(playlist);
    if (JSON.stringify(Object.fromEntries(new FormData(form))) === JSON.stringify(payload)) {
      form.reset();
      $("#playlist-dialog").close();
    }
    await setView("playlist", playlist.id);
    toast("Playlist created. Add the clips for this session.");
  } catch (failure) { error.textContent = failure.message; error.hidden = false; }
  finally { submit.disabled = false; }
}

async function openPlaylistPicker() {
  if (!state.playlist) return;
  state.pickerSelection = [...state.playlist.clip_ids];
  state.pickerBaseline = JSON.stringify(state.pickerSelection);
  state.pickerPlaylistId = state.playlist.id;
  state.pickerItems = [];
  $("#picker-search").value = "";
  $("#playlist-picker-title").textContent = `Clips for ${state.playlist.title}`;
  $("#playlist-picker").showModal();
  await loadPicker();
}

async function loadPicker({ append = false } = {}) {
  const token = ++state.pickerToken;
  $("#picker-more").disabled = true;
  if (!append) $("#picker-list").replaceChildren(el("p", "muted", "Loading your clips…"));
  try {
    const result = await api(`/clips?${queryString({ q: $("#picker-search").value.trim(), archived: false, limit: 60, offset: append ? state.pickerItems.length : 0 })}`);
    if (token !== state.pickerToken || !$("#playlist-picker").open) return;
    state.pickerItems = append ? [...state.pickerItems, ...result.items] : result.items;
    state.pickerTotal = result.total;
    renderPicker();
  } catch (error) { if (token === state.pickerToken) $("#picker-list").replaceChildren(el("p", "field-error", error.message)); }
  finally { if (token === state.pickerToken) $("#picker-more").disabled = false; }
}

function renderPicker() {
  const list = $("#picker-list");
  list.replaceChildren();
  if (!state.pickerItems.length) list.append(el("p", "muted", "No clips found. Import clips first, or try another search."));
  state.pickerItems.forEach(clip => {
    const row = el("label", "picker-row");
    const checkbox = el("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.pickerSelection.includes(clip.id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.pickerSelection.push(clip.id);
      else state.pickerSelection = state.pickerSelection.filter(id => id !== clip.id);
      $("#picker-count").textContent = `${state.pickerSelection.length} selected`;
    });
    const info = el("span");
    info.append(el("strong", "", clip.title), el("small", "", `${friendly(clip.phase)} · ${duration(clip.duration)}`));
    row.append(checkbox, info);
    list.append(row);
  });
  $("#picker-count").textContent = `${state.pickerSelection.length} selected`;
  $("#picker-more").hidden = state.pickerItems.length >= state.pickerTotal;
}

async function savePicker() {
  if (state.pickerSaving) return;
  const id = state.pickerPlaylistId;
  const selection = [...state.pickerSelection];
  state.pickerSaving = true;
  $("#save-playlist-clips").disabled = true;
  try {
    await api(`/playlists/${encodeURIComponent(id)}/clips`, { method: "PUT", body: { clip_ids: selection } });
    state.pickerBaseline = JSON.stringify(selection);
    const unchanged = state.pickerBaseline === JSON.stringify(state.pickerSelection);
    if (unchanged) $("#playlist-picker").close();
    if (id === state.playlistId) await loadClips();
    toast(unchanged ? "Playlist saved. Use the arrows to adjust its order." : "Playlist saved. Your newer selection changes are still here; save again when ready.");
  } catch (error) { toast(error.message, "error"); }
  finally { state.pickerSaving = false; $("#save-playlist-clips").disabled = false; }
}

async function mutatePlaylist(ids) {
  if (state.playlistMutation || !state.playlist) return;
  const id = state.playlist.id;
  state.playlistMutation = true;
  renderClips();
  try { await api(`/playlists/${encodeURIComponent(id)}/clips`, { method: "PUT", body: { clip_ids: ids } }); if (state.playlistId === id) await loadClips(); }
  catch (error) { toast(error.message, "error"); }
  finally { state.playlistMutation = false; renderClips(); }
}

function movePlaylistClip(index, direction) {
  if (!state.playlist || state.playlistMutation) return;
  const ids = [...state.playlist.clip_ids];
  if (index + direction < 0 || index + direction >= ids.length) return;
  [ids[index], ids[index + direction]] = [ids[index + direction], ids[index]];
  mutatePlaylist(ids);
}

function removePlaylistClip(id) {
  if (!state.playlist || state.playlistMutation) return;
  mutatePlaylist(state.playlist.clip_ids.filter(value => value !== id));
}

async function loadJobs() {
  const jobs = await api("/jobs");
  jobs.forEach(job => state.jobs.set(job.id, job));
  renderJobs();
}

function pendingJobs() { return [...state.jobs.values()].filter(job => ["queued", "running"].includes(job.status)); }

function renderJobs() {
  const jobs = [...state.jobs.values()].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  const pending = pendingJobs().length;
  $("#jobs-count").textContent = String(pending);
  $("#jobs-count").hidden = !pending;
  const list = $("#jobs-list");
  list.replaceChildren();
  if (!jobs.length) list.append(el("p", "muted", "No exports yet. Open a clip, set its range, and choose Export selection."));
  jobs.forEach(job => {
    const row = el("article", "job-row");
    row.append(icon(job.status === "failed" ? "warning" : "download"));
    const content = el("div", "job-body");
    const clip = state.clipMap.get(job.clip_id);
    content.append(el("strong", "", clip?.title || job.provenance?.title || "Exported clip"));
    content.append(el("p", "muted", `Saved version ${job.clip_version} · ${dateLabel(job.created_at)}`));
    const status = el("span", "job-status");
    if (["queued", "running"].includes(job.status)) status.append(el("span", "spinner"));
    status.append(document.createTextNode(friendly(job.status)));
    content.append(status);
    if (job.error) content.append(el("p", "job-error", typeof job.error === "string" ? job.error : job.error.message || "Export failed. Try again from the clip."));
    row.append(content);
    if (job.status === "complete") {
      const url = localURL(job.download_url || `/api/jobs/${encodeURIComponent(job.id)}/file`);
      if (url) {
        const link = el("a", "button button-primary button-small", "Download MP4");
        link.href = url;
        link.setAttribute("download", "");
        row.append(link);
      }
    }
    list.append(row);
  });
}

function schedulePolling(delay = 1400) {
  clearTimeout(state.pollTimer);
  if (pendingJobs().length) state.pollTimer = setTimeout(pollJobs, delay);
}

async function pollJobs() {
  if (state.pollRunning) return;
  state.pollRunning = true;
  let failed = false;
  try {
    const pending = pendingJobs();
    const results = await Promise.allSettled(pending.map(job => api(`/jobs/${encodeURIComponent(job.id)}`)));
    results.forEach((result, index) => {
      if (result.status === "rejected") { failed = true; return; }
      const previous = pending[index];
      const job = result.value;
      state.jobs.set(job.id, job);
      if (previous.status !== job.status && job.status === "complete") toast("Your MP4 export is ready. Download it from Exports.");
      if (previous.status !== job.status && job.status === "failed") toast("An export failed. Open Exports for the details.", "error");
    });
    renderJobs();
  } finally {
    state.pollRunning = false;
    schedulePolling(failed ? 6000 : 1400);
  }
}

function bind() {
  mountIcons();
  // The import target is explicit and independent of the current library filters.
  const target = el("label", "import-target", "Game for these imports");
  const select = el("select");
  select.id = "import-game";
  select.append(new Option("No game assigned", ""));
  target.append(select);
  $("#import-zone>div").append(target);
  ["#choose-files", "#top-import"].forEach(selector => $(selector).addEventListener("click", () => $("#import-files").click()));
  $("#import-files").addEventListener("change", event => { startImports(event.target.files); event.target.value = ""; });
  const zone = $("#import-zone");
  let dragDepth = 0;
  zone.addEventListener("dragenter", event => { event.preventDefault(); dragDepth++; zone.classList.add("drag-active"); });
  zone.addEventListener("dragover", event => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; });
  zone.addEventListener("dragleave", event => { event.preventDefault(); dragDepth--; if (dragDepth <= 0) zone.classList.remove("drag-active"); });
  zone.addEventListener("drop", event => { event.preventDefault(); dragDepth = 0; zone.classList.remove("drag-active"); startImports(event.dataTransfer.files); });
  window.addEventListener("dragover", event => { if ([...event.dataTransfer.types].includes("Files")) event.preventDefault(); });
  window.addEventListener("drop", event => { if ([...event.dataTransfer.types].includes("Files")) event.preventDefault(); });
  $("#dismiss-uploads").addEventListener("click", () => { if (!state.uploading) { state.uploads = []; $("#upload-panel").hidden = true; } });
  $$(".main-nav [data-view]").forEach(node => node.addEventListener("click", () => setView(node.dataset.view)));
  $("#clip-search").addEventListener("input", debounce(() => loadClips()));
  ["filter-game", "filter-player", "filter-phase", "filter-status", "filter-tag"].forEach(id => $(`#${id}`).addEventListener("change", () => loadClips()));
  $("#more-filters").addEventListener("click", () => {
    const panel = $("#extra-filters");
    panel.hidden = !panel.hidden;
    $("#more-filters").setAttribute("aria-expanded", String(!panel.hidden));
  });
  $("#clear-filters").addEventListener("click", clearFilters);
  $("#refresh-library").addEventListener("click", initialize);
  $("#retry-load").addEventListener("click", initialize);
  $("#load-more").addEventListener("click", () => loadClips({ append: true }));
  $("#manage-library").addEventListener("click", () => $("#manage-dialog").showModal());
  $("#game-form").addEventListener("submit", event => submitCatalogForm(event, "/games", "Game added to your catalog."));
  $("#player-form").addEventListener("submit", event => submitCatalogForm(event, "/players", "Player added to your catalog."));
  $("#new-playlist").addEventListener("click", () => { $("#playlist-dialog").showModal(); $('#playlist-form input[name="title"]').focus(); });
  $("#playlist-form").addEventListener("submit", createPlaylist);
  $("#add-to-playlist").addEventListener("click", openPlaylistPicker);
  $("#picker-search").addEventListener("input", debounce(() => loadPicker()));
  $("#picker-more").addEventListener("click", () => loadPicker({ append: true }));
  $("#save-playlist-clips").addEventListener("click", savePicker);
  $("#play-playlist").addEventListener("click", () => { if (state.clips.length) review.open(state.clips[0].id, state.clips.map(clip => clip.id)); });
  $("#open-jobs").addEventListener("click", async () => { $("#jobs-dialog").showModal(); try { await loadJobs(); schedulePolling(); } catch (error) { toast(error.message, "error"); } });
  $$('[data-close]').filter(node => node.dataset.close !== "playlist-picker").forEach(node => node.addEventListener("click", () => $(`#${node.dataset.close}`).close()));
  $("#playlist-picker").addEventListener("cancel", async event => {
    event.preventDefault();
    await closePicker();
  });
  $('[data-close="playlist-picker"]').addEventListener("click", closePicker);
  document.addEventListener("keydown", event => {
    if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey || $("dialog[open]") || event.target.closest("input,textarea,select,[contenteditable]")) return;
    event.preventDefault();
    $("#clip-search").focus();
  });
  window.addEventListener("beforeunload", event => {
    const formDraft = $$('#manage-dialog input, #manage-dialog textarea, #playlist-dialog input, #playlist-dialog textarea').some(input => input.value.trim());
    if (state.uploading || review.hasUnsavedWork() || formDraft || pickerDirty()) { event.preventDefault(); event.returnValue = ""; }
  });
}

function pickerDirty() {
  return $("#playlist-picker").open && JSON.stringify(state.pickerSelection) !== state.pickerBaseline;
}

async function closePicker() {
  if (state.pickerSaving) return;
  if (!pickerDirty()) { $("#playlist-picker").close(); return; }
  const answer = await choose("Save this playlist selection?", "You changed the clips in this session.", [{ label: "Keep editing", value: "keep" }, { label: "Discard selection", value: "discard" }, { label: "Save playlist", value: "save", primary: true }]);
  if (answer === "save") await savePicker();
  if (answer === "discard") $("#playlist-picker").close();
}

bind();
initialize();
