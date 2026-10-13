import { api, upload, localURL } from "./api.js";
import { $, $$, el, button, icon, iconButton, duration, friendly, dateLabel, fillSelect, toast, choose, isEditing } from "./ui.js";

const editableIds = ["edit-title", "edit-game", "edit-phase", "edit-status", "edit-tags", "edit-description", "edit-favorite", "edit-archived", "trim-start", "trim-end"];

export class ReviewController {
  constructor({ catalog, onSaved, onExport }) {
    this.catalog = catalog;
    this.onSaved = onSaved;
    this.onExport = onExport;
    this.dialog = $("#review-dialog");
    this.video = $("#review-video");
    this.clip = null;
    this.notes = [];
    this.session = [];
    this.token = 0;
    this.saving = false;
    this.loading = false;
    this.frameRequest = 0;
    this.advancedLoaded = false;
    this.bind();
  }

  bind() {
    $("#review-close").addEventListener("click", () => this.requestClose());
    this.dialog.addEventListener("cancel", event => { event.preventDefault(); this.requestClose(); });
    this.dialog.addEventListener("close", () => { this.video.pause(); cancelAnimationFrame(this.frameRequest); });
    $("#save-clip").addEventListener("click", () => this.saveAll());
    $("#export-clip").addEventListener("click", () => this.export());
    $("#clip-details-form").addEventListener("submit", event => { event.preventDefault(); this.saveAll(); });
    editableIds.forEach(id => $(`#${id}`).addEventListener("input", () => this.updateDraftState()));
    $("#edit-players").addEventListener("change", () => this.updateDraftState());
    $("#note-body").addEventListener("input", () => this.updateDraftState());
    $("#note-form").addEventListener("submit", event => { event.preventDefault(); this.addNote(); });
    $("#note-timed").addEventListener("change", () => this.updateClock());
    $("#mark-in").addEventListener("click", () => this.mark("start"));
    $("#mark-out").addEventListener("click", () => this.mark("end"));
    $("#reset-trim").addEventListener("click", () => {
      if (!this.clip) return;
      $("#trim-start").value = "0";
      $("#trim-end").value = String(this.clip.source_duration);
      this.updateDraftState();
    });
    $("#frame-back").addEventListener("click", () => this.step(-1));
    $("#frame-next").addEventListener("click", () => this.step(1));
    $("#toggle-play").addEventListener("click", () => this.togglePlay());
    $("#playback-speed").addEventListener("change", event => { this.video.playbackRate = Number(event.target.value); });
    this.video.addEventListener("loadedmetadata", () => {
      if (!this.clip) return;
      this.video.currentTime = Math.min(Number(this.clip.start_seconds), this.video.duration || 0);
      this.video.playbackRate = Number($("#playback-speed").value);
      this.updateClock();
    });
    this.video.addEventListener("timeupdate", () => this.updateClock());
    this.video.addEventListener("play", () => {
      if (this.validRange() && $("#loop-selection").checked) {
        const [start, end] = this.range();
        if (this.video.currentTime < start || this.video.currentTime >= end) this.video.currentTime = start;
      }
      this.updatePlayButton();
      this.watchLoop();
    });
    this.video.addEventListener("pause", () => { this.updatePlayButton(); cancelAnimationFrame(this.frameRequest); });
    this.video.addEventListener("ended", () => {
      if ($("#loop-selection").checked && this.validRange()) {
        this.video.currentTime = this.range()[0];
        this.video.play().catch(() => {});
      }
    });
    this.video.addEventListener("error", () => {
      if (this.clip && this.video.getAttribute("src")) $("#video-error").hidden = false;
    });
    $$(".review-tab").forEach(tab => {
      tab.addEventListener("click", () => this.setTab(tab.dataset.tab));
      tab.addEventListener("keydown", event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const tabs = $$(".review-tab");
        let index = tabs.indexOf(tab);
        if (event.key === "ArrowLeft") index = (index + tabs.length - 1) % tabs.length;
        if (event.key === "ArrowRight") index = (index + 1) % tabs.length;
        if (event.key === "Home") index = 0;
        if (event.key === "End") index = tabs.length - 1;
        this.setTab(tabs[index].dataset.tab);
        tabs[index].focus();
      });
    });
    $("#previous-clip").addEventListener("click", () => this.next(-1));
    $("#next-clip").addEventListener("click", () => this.next(1));
    $("#reload-clip").addEventListener("click", async () => {
      const answer = await choose("Load the latest saved version?", "This replaces your unsaved details and note draft with the saved clip. You can keep editing instead.", [{ label: "Keep editing", value: "keep" }, { label: "Load latest", value: "reload", primary: true }]);
      if (answer === "reload" && this.clip) await this.load(this.clip.id);
    });
    $("#launch-kinovea").addEventListener("click", () => this.launchKinovea());
    $("#upload-kva").addEventListener("click", () => $("#kva-file").click());
    $("#kva-file").addEventListener("change", event => this.attachKva(event.target));
    $("#import-analysis").addEventListener("click", () => $("#analysis-file").click());
    $("#analysis-file").addEventListener("change", event => this.importAnalysis(event.target));
    $("#load-history").addEventListener("click", () => this.loadHistory());
    this.dialog.addEventListener("keydown", event => this.keyboard(event));
  }

  async open(id, session = []) {
    if (this.saving) return;
    if (this.dialog.open && !(await this.allowLeave())) return;
    this.session = [...session];
    if (!this.dialog.open) this.dialog.showModal();
    this.setTab("details");
    await this.load(id);
  }

  async load(id) {
    const token = ++this.token;
    this.loading = true;
    this.clip = null;
    this.video.pause();
    this.video.removeAttribute("src");
    this.video.load();
    $("#review-loading").hidden = false;
    $("#review-content").hidden = true;
    $("#review-title").textContent = "Opening clip…";
    $("#review-context").textContent = "";
    $("#draft-status").textContent = "Loading…";
    $("#save-clip").disabled = true;
    $("#export-clip").disabled = true;
    $("#review-conflict").hidden = true;
    try {
      const [clipResult, notesResult] = await Promise.allSettled([api(`/clips/${encodeURIComponent(id)}`), api(`/clips/${encodeURIComponent(id)}/notes`)]);
      if (token !== this.token || !this.dialog.open) return;
      if (clipResult.status === "rejected") throw clipResult.reason;
      this.clip = clipResult.value;
      this.notes = notesResult.status === "fulfilled" ? notesResult.value : [];
      if (notesResult.status === "rejected") toast(`Clip opened, but notes could not load: ${notesResult.reason.message}`, "error");
      this.advancedLoaded = false;
      this.populate();
      this.loading = false;
      $("#review-loading").hidden = true;
      $("#review-content").hidden = false;
      this.updateDraftState();
    } catch (error) {
      if (token !== this.token) return;
      this.loading = false;
      toast(error.message, "error");
      this.dialog.close();
    }
  }

  populate() {
    const clip = this.clip;
    if (!clip) return;
    const catalog = this.catalog();
    const game = catalog.games.find(item => item.id === clip.game_id);
    $("#review-title").textContent = clip.title;
    $("#review-context").textContent = `${game?.title || "No game assigned"} · ${friendly(clip.phase)} · ${duration(clip.source_duration)} source`;
    $("#edit-title").value = clip.title;
    fillSelect($("#edit-game"), catalog.games, "No game assigned", clip.game_id);
    $("#edit-phase").value = clip.phase;
    $("#edit-status").value = clip.review_status;
    $("#edit-tags").value = (clip.tags || []).join(", ");
    $("#edit-description").value = clip.description || "";
    $("#edit-favorite").checked = clip.favorite;
    $("#edit-archived").checked = clip.archived;
    $("#trim-start").value = String(clip.start_seconds);
    $("#trim-end").value = String(clip.end_seconds);
    $("#trim-start").max = String(clip.source_duration);
    $("#trim-end").max = String(clip.source_duration);
    const players = $("#edit-players");
    players.replaceChildren();
    if (!catalog.players.length) players.append(el("p", "muted", "Add players in Games & players to tag them here."));
    catalog.players.forEach(player => {
      const label = el("label");
      const checkbox = el("input");
      checkbox.type = "checkbox";
      checkbox.value = player.id;
      checkbox.checked = (clip.player_ids || []).includes(player.id);
      label.append(checkbox, document.createTextNode(`${player.jersey ? `#${player.jersey} ` : ""}${player.name}${player.position ? ` · ${player.position}` : ""}`));
      players.append(label);
    });
    $("#note-body").value = "";
    $("#history-list").replaceChildren();
    $("#analysis-list").replaceChildren();
    $("#video-error").hidden = true;
    this.video.src = localURL(clip.source_url) || `/api/clips/${encodeURIComponent(clip.id)}/source`;
    this.video.load();
    this.baseline = JSON.stringify(this.draft());
    this.renderNotes();
    const details = $("#source-detail-list");
    details.replaceChildren();
    [
      ["Original file", clip.original_filename],
      ["Duration", duration(clip.source_duration, true)],
      ["Video", `${clip.width} × ${clip.height} · ${Number(clip.fps || 0).toFixed(2)} fps`],
      ["Source audio", clip.has_audio ? "Present" : "No audio track"],
      ["Imported", dateLabel(clip.created_at)],
      ["Source SHA-256", clip.source_sha256],
    ].forEach(([label, value]) => details.append(el("dt", "", label), el("dd", "", String(value ?? "—"))));
    this.updateSession();
    if (!$("#advanced-panel").hidden) this.loadAdvanced();
  }

  draft() {
    return {
      title: $("#edit-title").value.trim(),
      game_id: $("#edit-game").value || null,
      phase: $("#edit-phase").value,
      review_status: $("#edit-status").value,
      description: $("#edit-description").value,
      tags: [...new Set($("#edit-tags").value.split(",").map(value => value.trim()).filter(Boolean))],
      player_ids: $$('#edit-players input:checked').map(input => input.value).sort(),
      favorite: $("#edit-favorite").checked,
      archived: $("#edit-archived").checked,
      start_seconds: Number($("#trim-start").value),
      end_seconds: Number($("#trim-end").value),
    };
  }

  hasMetadataChanges() { return Boolean(this.clip && (!this.validRange() || this.baseline !== JSON.stringify(this.draft()))); }
  hasUnsavedWork() { return Boolean(this.dialog.open && this.clip && (this.hasMetadataChanges() || $("#note-body").value.trim())); }
  range() { return [Number($("#trim-start").value), Number($("#trim-end").value)]; }
  validRange() {
    if (!this.clip || $("#trim-start").value === "" || $("#trim-end").value === "") return false;
    const [start, end] = this.range();
    return Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end > start && end <= this.clip.source_duration + 0.000001;
  }

  updateDraftState() {
    if (!this.clip || this.loading) return;
    const dirty = this.hasUnsavedWork();
    const valid = this.validRange();
    const [start, end] = this.range();
    $("#draft-status").textContent = this.saving ? "Saving…" : dirty ? "Unsaved changes" : "All changes saved";
    $("#draft-status").classList.toggle("dirty", dirty);
    $("#review-version").textContent = `Saved version ${this.clip.version}`;
    $("#save-clip").disabled = this.saving || !dirty;
    $("#export-clip").disabled = this.saving || !valid;
    const exportLabel = this.hasMetadataChanges() ? "Save & export" : "Export selection";
    $("#export-clip").replaceChildren(icon("download"), document.createTextNode(exportLabel));
    $("#selected-duration").textContent = valid ? `${duration(end - start, true)} selected` : "Check in / out marks";
    $("#trim-error").hidden = valid;
    $("#trim-error").textContent = valid ? "" : `Use an in point before the out point, between 0 and ${this.clip.source_duration} seconds.`;
    const clamp = value => Math.min(100, Math.max(0, value / this.clip.source_duration * 100));
    $("#selection-fill").style.left = `${clamp(start)}%`;
    $("#selection-fill").style.width = `${Math.max(0, clamp(end) - clamp(start))}%`;
    this.updateClock();
  }

  async saveMetadata() {
    if (!this.clip || !this.hasMetadataChanges()) return true;
    if (!$("#clip-details-form").reportValidity() || !this.validRange()) {
      this.setTab("details");
      if (!this.validRange()) $("#trim-start").focus();
      return false;
    }
    const id = this.clip.id;
    const token = this.token;
    const draft = this.draft();
    const baselineAfterSave = JSON.stringify(draft);
    try {
      const saved = await api(`/clips/${encodeURIComponent(id)}`, { method: "PATCH", body: { ...draft, version: this.clip.version } });
      if (token !== this.token) return false;
      this.clip = saved;
      this.baseline = baselineAfterSave;
      $("#review-title").textContent = saved.title;
      $("#review-conflict").hidden = true;
      this.onSaved(saved);
      return true;
    } catch (error) {
      if (error.status === 409) $("#review-conflict").hidden = false;
      else toast(error.message, "error");
      return false;
    }
  }

  async saveAll({ quiet = false } = {}) {
    if (!this.clip || this.saving || this.loading) return false;
    this.saving = true;
    this.updateDraftState();
    try {
      if (!(await this.saveMetadata())) return false;
      if ($("#note-body").value.trim() && !(await this.addNote({ quiet: true, partOfSave: true }))) return false;
      if (!quiet) toast("Review saved. Your original footage is preserved.");
      return true;
    } finally { this.saving = false; this.updateDraftState(); }
  }

  async allowLeave() {
    if (this.saving) return false;
    if (!this.hasUnsavedWork()) return true;
    this.video.pause();
    const answer = await choose("Keep your review changes?", "Your trim, details, and any note draft have not all been saved.", [{ label: "Keep editing", value: "keep" }, { label: "Discard draft", value: "discard" }, { label: "Save all", value: "save", primary: true }]);
    if (answer === "save") return (await this.saveAll({ quiet: true })) && !this.hasUnsavedWork();
    return answer === "discard";
  }

  async requestClose() {
    if (!(await this.allowLeave())) return;
    ++this.token;
    this.video.pause();
    this.dialog.close();
    this.clip = null;
  }

  mark(which) {
    if (!this.clip || !Number.isFinite(this.video.currentTime)) return;
    const value = Math.min(this.clip.source_duration, Math.max(0, this.video.currentTime));
    $(`#trim-${which}`).value = String(Math.round(value * 1000) / 1000);
    this.updateDraftState();
  }

  step(direction) {
    if (!this.clip || !Number.isFinite(this.video.duration)) return;
    this.video.pause();
    const fps = Number(this.clip.fps) || 30;
    this.video.currentTime = Math.max(0, Math.min(this.video.duration, this.video.currentTime + direction / fps));
    this.updateClock();
  }

  togglePlay() {
    if (!this.clip) return;
    if (this.video.paused) this.video.play().catch(() => toast("This source cannot play in the current browser. Try an MP4 export.", "error"));
    else this.video.pause();
  }

  updatePlayButton() {
    $("#toggle-play").replaceChildren(icon(this.video.paused ? "play" : "pause"), document.createTextNode(this.video.paused ? "Play" : "Pause"));
  }

  watchLoop() {
    cancelAnimationFrame(this.frameRequest);
    const tick = () => {
      if (!this.dialog.open || this.video.paused || !this.clip) return;
      if (!this.video.seeking && $("#loop-selection").checked && this.validRange()) {
        const [start, end] = this.range();
        if (this.video.currentTime >= end) this.video.currentTime = start;
      }
      this.updateClock();
      this.frameRequest = requestAnimationFrame(tick);
    };
    this.frameRequest = requestAnimationFrame(tick);
  }

  updateClock() {
    const current = this.video.currentTime || 0;
    $("#video-clock").replaceChildren(document.createTextNode(`${duration(current, true)} `), el("span", "", `/ ${duration(this.clip?.source_duration || 0, true)}`));
    $("#note-time").textContent = $("#note-timed").checked ? duration(current, true) : "Untimed";
    $("#selection-playhead").style.left = `${Math.max(0, Math.min(100, current / (this.clip?.source_duration || 1) * 100))}%`;
  }

  setTab(name) {
    ["details", "notes", "advanced"].forEach(value => {
      const tab = $(`#${value}-tab`);
      tab.classList.toggle("active", value === name);
      tab.setAttribute("aria-selected", String(value === name));
      tab.tabIndex = value === name ? 0 : -1;
      $(`#${value}-panel`).hidden = value !== name;
    });
    if (name === "advanced" && this.clip && !this.advancedLoaded) this.loadAdvanced();
  }

  keyboard(event) {
    if (event.defaultPrevented || this.loading || !this.clip || $("#choice-dialog").open) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); this.saveAll(); return; }
    if (isEditing(event) || event.ctrlKey || event.metaKey || event.altKey) return;
    const key = event.key.toLowerCase();
    if ([" ", "arrowleft", "arrowright", "i", "o", "l", "n"].includes(key)) event.preventDefault();
    if (key === " ") this.togglePlay();
    if (key === "arrowleft") this.step(-1);
    if (key === "arrowright") this.step(1);
    if (key === "i") this.mark("start");
    if (key === "o") this.mark("end");
    if (key === "l") $("#loop-selection").checked = !$("#loop-selection").checked;
    if (key === "n") { this.video.pause(); this.setTab("notes"); $("#note-body").focus(); }
  }

  async addNote({ quiet = false, partOfSave = false } = {}) {
    if (!this.clip || (this.saving && !partOfSave) || this.noteSaving) return false;
    const body = $("#note-body").value.trim();
    if (!body) { $("#note-body").focus(); return false; }
    const id = this.clip.id;
    const token = this.token;
    const at = $("#note-timed").checked ? Math.min(this.clip.source_duration, Math.max(0, this.video.currentTime || 0)) : null;
    this.noteSaving = true;
    $("#add-note").disabled = true;
    try {
      const note = await api(`/clips/${encodeURIComponent(id)}/notes`, { method: "POST", body: { body, at_seconds: at } });
      if (token !== this.token) return false;
      this.notes.push(note);
      if ($("#note-body").value.trim() === body) $("#note-body").value = "";
      this.renderNotes();
      if (!quiet) toast("Coaching note saved.");
      return true;
    } catch (error) { toast(error.message, "error"); return false; }
    finally { this.noteSaving = false; $("#add-note").disabled = false; this.updateDraftState(); }
  }

  renderNotes() {
    $("#notes-count").textContent = String(this.notes.length);
    const list = $("#note-list");
    list.replaceChildren();
    if (!this.notes.length) list.append(el("p", "muted", "No notes yet. Pause at a teaching moment and add your first one."));
    [...this.notes].sort((a, b) => (a.at_seconds ?? Infinity) - (b.at_seconds ?? Infinity)).forEach(note => {
      const card = el("article", "note-card");
      const header = el("div", "note-card-header");
      if (note.at_seconds !== null && note.at_seconds !== undefined) header.append(button(duration(note.at_seconds, true), () => { this.video.pause(); this.video.currentTime = note.at_seconds; }, "note-time-button"));
      else header.append(el("span", "muted", "Clip note"));
      header.append(iconButton("trash", "Delete this note", async event => {
        const id = this.clip?.id;
        const token = this.token;
        const control = event.currentTarget;
        control.disabled = true;
        try {
          await api(`/clips/${encodeURIComponent(id)}/notes/${encodeURIComponent(note.id)}`, { method: "DELETE" });
          if (token !== this.token) return;
          this.notes = this.notes.filter(item => item.id !== note.id);
          this.renderNotes();
        } catch (error) { toast(error.message, "error"); control.disabled = false; }
      }));
      card.append(header, el("p", "", note.body));
      list.append(card);
    });
  }

  async export() {
    if (!this.clip || this.saving || !this.validRange()) return;
    if (this.hasUnsavedWork() && !(await this.saveAll({ quiet: true }))) return;
    if (this.hasUnsavedWork()) { toast("New edits were made while saving. Save the current draft before exporting.", "error"); return; }
    const clip = this.clip;
    $("#export-clip").disabled = true;
    try {
      const job = await api(`/clips/${encodeURIComponent(clip.id)}/exports`, { method: "POST", body: {} });
      this.onExport(job, clip);
      toast(`Export queued for “${clip.title}”, saved version ${job.clip_version}.`);
    } catch (error) { toast(error.message, "error"); }
    finally { this.updateDraftState(); }
  }

  updateSession() {
    const index = this.session.indexOf(this.clip?.id);
    $("#session-bar").hidden = this.session.length < 2 || index < 0;
    $("#session-position").textContent = `Clip ${index + 1} of ${this.session.length}`;
    $("#previous-clip").disabled = index <= 0;
    $("#next-clip").disabled = index >= this.session.length - 1;
  }

  async next(direction) {
    if (!this.clip || this.loading || this.saving) return;
    const index = this.session.indexOf(this.clip.id) + direction;
    if (index < 0 || index >= this.session.length || !(await this.allowLeave())) return;
    await this.load(this.session[index]);
  }

  async loadAdvanced() {
    if (!this.clip) return;
    const id = this.clip.id;
    const token = this.token;
    this.advancedLoaded = true;
    const results = await Promise.allSettled([api(`/clips/${encodeURIComponent(id)}/kinovea`), api(`/clips/${encodeURIComponent(id)}/analysis`)]);
    if (token !== this.token) return;
    if (results[0].status === "fulfilled") {
      const handoff = results[0].value;
      $("#kinovea-status").textContent = handoff.available ? "Configured on this Windows computer." : handoff.reason || "Desktop launch is unavailable on this computer. You can still attach a KVA file.";
      $("#launch-kinovea").disabled = !handoff.available;
      const url = localURL(handoff.annotations_url || `/api/clips/${encodeURIComponent(id)}/kinovea/annotations`);
      $("#download-kva").hidden = !handoff.has_annotations || !url;
      if (url) { $("#download-kva").href = url; $("#download-kva").setAttribute("download", ""); }
    } else {
      $("#kinovea-status").textContent = results[0].reason.message;
      $("#launch-kinovea").disabled = true;
      $("#download-kva").hidden = true;
    }
    if (results[1].status === "fulfilled") this.renderAnalysis(results[1].value);
    else $("#analysis-list").replaceChildren(el("p", "field-error", results[1].reason.message));
  }

  async launchKinovea() {
    if (!this.clip || this.saving) return;
    if (this.hasUnsavedWork() && !(await this.saveAll({ quiet: true }))) return;
    if (this.hasUnsavedWork()) { toast("New edits were made while saving. Save the current draft before opening Kinovea.", "error"); return; }
    const id = this.clip.id;
    $("#launch-kinovea").disabled = true;
    try { await api(`/clips/${encodeURIComponent(id)}/kinovea/launch`, { method: "POST", body: {} }); toast("Kinovea launch requested on this computer."); }
    catch (error) { toast(error.message, "error"); }
    finally { if (id === this.clip?.id) await this.loadAdvanced(); }
  }

  async attachKva(input) {
    const file = input.files[0];
    input.value = "";
    if (!file || !this.clip) return;
    const id = this.clip.id;
    const form = new FormData();
    form.append("file", file);
    $("#upload-kva").disabled = true;
    try { await upload(`/clips/${encodeURIComponent(id)}/kinovea/annotations`, form); toast("Kinovea annotations attached to this clip."); if (id === this.clip?.id) await this.loadAdvanced(); }
    catch (error) { toast(error.message, "error"); }
    finally { $("#upload-kva").disabled = false; }
  }

  async importAnalysis(input) {
    const file = input.files[0];
    input.value = "";
    if (!file || !this.clip) return;
    const id = this.clip.id;
    if (file.size > 2 * 1024 * 1024) { toast("Choose an analysis JSON file smaller than 2 MB.", "error"); return; }
    $("#import-analysis").disabled = true;
    try {
      let payload;
      try { payload = JSON.parse(await file.text()); } catch { throw new Error("The selected file is not valid JSON."); }
      await api(`/clips/${encodeURIComponent(id)}/analysis`, { method: "POST", body: payload });
      toast("Suggestions imported for human review. Your clip labels are unchanged.");
      if (id === this.clip?.id) await this.loadAdvanced();
    } catch (error) { toast(error.message, "error"); }
    finally { $("#import-analysis").disabled = false; }
  }

  renderAnalysis(records) {
    const list = $("#analysis-list");
    list.replaceChildren();
    if (!records.length) list.append(el("p", "muted", "No imported suggestions for this source."));
    records.forEach(record => {
      const card = el("article", "analysis-card");
      const positions = Object.entries(record.positions || record.payload?.positions || {});
      card.append(el("h4", "", `${positions.length} position suggestions`));
      card.append(el("p", "", `Revision: ${record.upstream_revision || record.payload?.upstream_revision || "Not supplied"}`));
      positions.forEach(([track, position]) => card.append(el("p", "", `Track ${track} → ${position}`)));
      if (record.notes || record.payload?.notes) card.append(el("p", "", record.notes || record.payload.notes));
      const label = el("label", "form-stack", "Human review");
      const status = el("select");
      status.append(new Option("Unreviewed", "unreviewed"), new Option("Accepted", "accepted"), new Option("Rejected", "rejected"));
      status.value = record.status || "unreviewed";
      const id = this.clip.id;
      status.addEventListener("change", async () => {
        status.disabled = true;
        try {
          await api(`/clips/${encodeURIComponent(id)}/analysis/${encodeURIComponent(record.id)}`, { method: "PATCH", body: { status: status.value } });
          record.status = status.value;
          toast("Human review recorded.");
        } catch (error) { status.value = record.status; toast(error.message, "error"); }
        finally { status.disabled = false; }
      });
      label.append(status);
      card.append(label);
      list.append(card);
    });
  }

  async loadHistory() {
    if (!this.clip) return;
    const id = this.clip.id;
    const token = this.token;
    $("#load-history").disabled = true;
    try {
      const history = await api(`/clips/${encodeURIComponent(id)}/history`);
      if (token !== this.token) return;
      const list = $("#history-list");
      list.replaceChildren();
      history.forEach(revision => {
        const card = el("article", "history-card");
        card.append(el("h4", "", `Version ${revision.version}${revision.version === this.clip.version ? " · Current" : ""}`));
        card.append(el("p", "", revision.snapshot?.title || this.clip.title));
        card.append(el("p", "", dateLabel(revision.created_at)));
        if (revision.version !== this.clip.version) card.append(button("Restore this version", () => this.restore(revision.version), "button button-secondary button-small"));
        list.append(card);
      });
    } catch (error) { toast(error.message, "error"); }
    finally { $("#load-history").disabled = false; }
  }

  async restore(version) {
    if (!this.clip || this.saving || !(await this.allowLeave())) return;
    const answer = await choose(`Restore version ${version}?`, "The saved title, trim, tags, and other clip details will become a new revision. Source footage and coaching notes stay attached.", [{ label: "Cancel", value: "cancel" }, { label: "Restore version", value: "restore", primary: true }]);
    if (answer !== "restore") return;
    const id = this.clip.id;
    this.saving = true;
    try {
      const clip = await api(`/clips/${encodeURIComponent(id)}/restore`, { method: "POST", body: { version, current_version: this.clip.version } });
      this.onSaved(clip);
      await this.load(id);
      toast(`Version ${version} restored as a new revision.`);
    } catch (error) { if (error.status === 409) $("#review-conflict").hidden = false; toast(error.message, "error"); }
    finally { this.saving = false; this.updateDraftState(); }
  }
}
