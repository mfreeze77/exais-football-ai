import { api } from "./api.js";
import { $, $$, el, button, fillSelect, choose, toast } from "./ui.js";

/** No user text becomes HTML. Each editor has its own draft and revision guard. */
export class TaxonomyManager {
  constructor(onChanged) {
    this.onChanged = onChanged;
    this.dialog = $("#taxonomy-dialog");
    this.state = null;
    this.kind = "tags";
    this.id = null;
    this.action = "edit";
    this.pending = false;
    this.sequence = 0;
    this.baseline = "";
    $("#manage-tags").addEventListener("click", () => this.open());
    $("#taxonomy-close").addEventListener("click", () => this.close());
    this.dialog.addEventListener("cancel", event => { event.preventDefault(); this.close(); });
    $("#taxonomy-reload").addEventListener("click", async () => { if (await this.allowLeave()) await this.load(); });
    $("#taxonomy-new").addEventListener("click", () => this.select(this.kind, null));
    $$("[data-tax-kind]").forEach(node => node.addEventListener("click", () => this.select(node.dataset.taxKind, null)));
    $("#taxonomy-search").addEventListener("input", () => this.renderList());
  }

  fingerprint() {
    const form = $("#taxonomy-form");
    if (!form) return "";
    return JSON.stringify($$("input,textarea,select", form).map(node => [node.name, node.type === "checkbox" ? node.checked : node.value]));
  }

  hasUnsavedWork() { return this.dialog.open && (this.pending || this.fingerprint() !== this.baseline); }

  async allowLeave() {
    if (this.pending) return false;
    if (this.fingerprint() === this.baseline) return true;
    return await choose("Keep your terminology draft?", "The changes in this editor have not been saved.", [
      { label: "Keep editing", value: "keep" }, { label: "Discard draft", value: "discard" },
    ]) === "discard";
  }

  async open() {
    if (this.dialog.open) return;
    this.dialog.showModal();
    await this.load();
  }

  async close() {
    if (!(await this.allowLeave())) return;
    ++this.sequence;
    this.dialog.close();
    $("#manage-tags").focus();
  }

  status(message, error = false) {
    const node = $("#taxonomy-status");
    node.textContent = message;
    node.classList.toggle("field-error", error);
  }

  setPending(value) {
    this.pending = value;
    this.dialog.setAttribute("aria-busy", String(value));
    $$("button,input,select,textarea", this.dialog).forEach(node => { node.disabled = value; });
  }

  async load() {
    const sequence = ++this.sequence;
    this.setPending(true);
    this.status("Loading terminology…");
    try {
      const state = await api("/taxonomy");
      if (sequence !== this.sequence || !this.dialog.open) return;
      this.state = state;
      this.action = "edit";
      if (!state[this.kind].some(item => item.id === this.id)) this.id = null;
      this.render();
      this.status(`Terminology revision ${state.revision}. Free-text tags remain available.`);
    } catch (error) { this.status(`${error.message} Use Reload to try again.`, true); }
    finally { if (sequence === this.sequence) this.setPending(false); }
  }

  async select(kind, id, action = "edit") {
    if (!(await this.allowLeave()) || !this.state) return;
    this.kind = kind;
    this.id = id;
    this.action = action;
    this.render();
  }

  render() {
    $$("[data-tax-kind]").forEach(node => node.setAttribute("aria-pressed", String(node.dataset.taxKind === this.kind)));
    $("#taxonomy-new").textContent = `New ${this.kind === "tags" ? "tag" : this.kind === "groups" ? "group" : "preset"}`;
    this.renderList();
    this.renderEditor();
  }

  renderList() {
    const list = $("#taxonomy-list");
    list.replaceChildren();
    if (!this.state) return;
    const query = $("#taxonomy-search").value.toLocaleLowerCase();
    const items = this.state[this.kind].filter(item =>
      [item.name, item.description, ...(item.aliases || []).map(alias => alias.name)].join(" ").toLocaleLowerCase().includes(query));
    if (!items.length) list.append(el("p", "muted", "No matching items. Create one or keep using free-text tags."));
    items.forEach(item => {
      const row = button(item.name, () => this.select(this.kind, item.id), "taxonomy-item");
      row.setAttribute("aria-pressed", String(this.id === item.id));
      row.dataset.itemId = item.id;
      if (this.kind === "tags") {
        const group = this.state.groups.find(g => g.id === item.group_id)?.name || "Ungrouped";
        row.append(el("small", "muted", `${group} · ${item.count} active · ${item.archived_count} archived`));
      } else if (this.kind === "presets") row.append(el("small", "muted", `${item.tag_ids.length} tags`));
      list.append(row);
    });
  }

  field(form, name, caption, value = "", type = "input", max = 80) {
    const label = el("label", "", caption);
    const input = el(type);
    input.id = `tax-${name}`;
    input.name = name;
    input.value = value;
    if (type !== "select") input.maxLength = max;
    if (name === "name") input.required = true;
    label.append(input);
    form.append(label);
    return input;
  }

  submitButton(form, text) {
    const node = el("button", "button button-primary", text);
    node.type = "submit";
    form.append(node);
  }

  renderEditor() {
    const container = $("#taxonomy-editor");
    container.replaceChildren();
    this.plan = null;
    const item = this.state[this.kind].find(value => value.id === this.id);
    if (!item) { this.id = null; this.action = "edit"; }
    const singular = this.kind === "groups" ? "group" : this.kind === "presets" ? "preset" : "tag";
    container.append(el("h3", "", item ? `${this.action === "rename" ? "Rename or map" : this.action === "alias" ? "Add alias to" : "Edit"} ${item.name}` : `Create ${singular}`));
    const form = el("form", "form-stack");
    form.id = "taxonomy-form";
    container.append(form);
    if (this.action === "rename") {
      this.field(form, "name", "New name or existing target tag", "", "input", 256);
      form.append(el("p", "muted", "An existing target maps both tags into one. The target group and description win; old names stay searchable."));
      this.submitButton(form, "Preview change");
      form.addEventListener("submit", event => { event.preventDefault(); this.previewChange(item); });
      const preview = el("section", "taxonomy-preview");
      preview.id = "taxonomy-preview";
      preview.hidden = true;
      container.append(preview);
      form.addEventListener("input", () => { this.plan = null; preview.hidden = true; });
    } else if (this.action === "alias") {
      this.field(form, "name", "Alternative spelling", "", "input", 256);
      form.append(el("p", "muted", "An alias points directly to this tag. A spelling already used by another tag requires a previewed map."));
      this.submitButton(form, "Save alias");
      form.addEventListener("submit", event => {
        event.preventDefault();
        this.mutate(`/tags/${item.id}/aliases`, { name: $("#tax-name").value }, "POST", item.id);
      });
    } else {
      if (this.kind !== "tags" || !item) this.field(form, "name", `${singular[0].toUpperCase()}${singular.slice(1)} name`, item?.name || "");
      if (this.kind === "tags") {
        const groups = this.field(form, "group", "Group (optional)", "", "select");
        fillSelect(groups, this.state.groups, "Ungrouped", item?.group_id);
      }
      this.field(form, "description", "Description (optional)", item?.description || "", "textarea", 2000);
      if (this.kind === "presets") {
        const fieldset = el("fieldset", "taxonomy-checklist");
        fieldset.append(el("legend", "", "Tags in this preset (choose 1–40)"));
        if (!this.state.tags.length) fieldset.append(el("p", "muted", "Create a tag first, or save free-text tags on a clip."));
        this.state.tags.forEach(tag => {
          const label = el("label");
          const input = el("input");
          input.type = "checkbox";
          input.name = "tag_ids";
          input.value = tag.id;
          input.checked = Boolean(item?.tag_ids.includes(tag.id));
          const group = this.state.groups.find(g => g.id === tag.group_id)?.name || "Ungrouped";
          label.append(input, el("span", "", `${tag.name} · ${group}`));
          fieldset.append(label);
        });
        form.append(fieldset, el("p", "muted", "Presets add tags to a review draft. They never replace its existing labels or change the clip's Phase field."));
      }
      this.submitButton(form, item ? "Save details" : `Create ${singular}`);
      form.addEventListener("submit", event => {
        event.preventDefault();
        const body = { description: $("#tax-description").value };
        if ($("#tax-name")) body.name = $("#tax-name").value;
        if (this.kind === "tags") body.group_id = $("#tax-group").value || null;
        if (this.kind === "presets") {
          body.tag_ids = $$('input[name="tag_ids"]:checked', form).map(node => node.value);
          if (!body.tag_ids.length || body.tag_ids.length > 40) { this.status("Choose between 1 and 40 tags.", true); return; }
        }
        this.mutate(`/${this.kind}${item ? `/${item.id}` : ""}`, body, item ? "PUT" : "POST", item?.id);
      });
    }
    if (item) {
      const actions = el("div", "button-row taxonomy-actions");
      if (this.action !== "edit") actions.append(button("Back to tag details", () => this.select("tags", item.id)));
      else if (this.kind === "tags") {
        actions.append(button("Rename or map", () => this.select("tags", item.id, "rename")), button("Add alias", () => this.select("tags", item.id, "alias")));
        const aliases = el("div", "taxonomy-aliases");
        aliases.append(el("h4", "", "Alternative names"));
        if (!item.aliases.length) aliases.append(el("p", "muted", "No aliases yet."));
        item.aliases.forEach(alias => {
          const row = el("div", "taxonomy-alias");
          row.append(el("span", "", alias.name));
          if (alias.protected) row.append(el("small", "muted", "Historical name · retained"));
          else row.append(button("Remove alias", () => this.removeAlias(item, alias)));
          aliases.append(row);
        });
        container.append(aliases);
      } else actions.append(button(`Delete ${singular}`, () => this.deleteItem(item, singular)));
      container.append(actions);
    }
    this.baseline = this.fingerprint();
    form.addEventListener("input", () => this.status(this.fingerprint() !== this.baseline ? "Unsaved terminology draft" : `Terminology revision ${this.state.revision}`));
  }

  async mutate(route, body, method, selectedId = null) {
    if (this.pending) return;
    this.setPending(true);
    this.status("Saving terminology…");
    try {
      const result = await api(`/taxonomy${route}`, { method, ...(body ? { body: { expected_revision: this.state.revision, ...body } } : {}) });
      this.state = result.taxonomy || result;
      this.id = selectedId;
      this.action = "edit";
      this.render();
      this.status(`Saved · terminology revision ${this.state.revision}. Film and existing notes are preserved.`);
      try { await this.onChanged(result); } catch (error) { toast(`Terminology saved; library refresh failed: ${error.message}`, "error"); }
    } catch (error) { this.status(`${error.message} Your draft is retained. Reload to reconcile a conflict.`, true); }
    finally { this.setPending(false); }
  }

  async deleteItem(item, singular) {
    if (!(await this.allowLeave())) return;
    const message = singular === "preset" ? "Only the shortcut is deleted. Tags already applied to clips stay in place." : "Tags in this group become ungrouped. No tags, film, or review history are deleted.";
    if (await choose(`Delete ${item.name}?`, message, [{ label: "Keep it", value: "keep" }, { label: `Delete ${singular}`, value: "delete" }]) !== "delete") return;
    await this.mutate(`/${this.kind}/${item.id}?expected_revision=${this.state.revision}`, null, "DELETE");
  }

  async removeAlias(item, alias) {
    if (!(await this.allowLeave())) return;
    if (await choose(`Remove ${alias.name}?`, "This spelling will no longer resolve as an alias. Existing clip labels remain canonical and unchanged.", [{ label: "Keep alias", value: "keep" }, { label: "Remove alias", value: "remove" }]) !== "remove") return;
    await this.mutate(`/tags/${item.id}/aliases/remove`, { name: alias.name }, "POST", item.id);
  }

  async previewChange(item) {
    if (this.pending || !$("#taxonomy-form").reportValidity()) return;
    this.setPending(true);
    this.plan = null;
    $("#taxonomy-preview").hidden = true;
    this.status("Checking affected clips and presets…");
    const request = { tag_id: item.id, new_name: $("#tax-name").value };
    try {
      this.plan = await api("/taxonomy/changes/preview", { method: "POST", body: request });
      this.changeRequest = { ...request, new_name: this.plan.requested_name, preview_token: this.plan.preview_token };
      const box = $("#taxonomy-preview");
      box.replaceChildren();
      box.hidden = false;
      box.append(el("h4", "", `${this.plan.operation === "map" ? "Map into" : "Rename to"} ${this.plan.target.name}`));
      box.append(el("p", "muted", `${this.plan.affected_clips.length} clips will receive a new metadata revision; ${this.plan.affected_presets.length} presets will use the canonical tag. Afterward: ${this.plan.active_count_after} active and ${this.plan.archived_count_after} archived clips.`));
      if (this.plan.operation === "map") {
        const group = this.state.groups.find(g => g.id === this.plan.target.group_id)?.name || "Ungrouped";
        box.append(el("p", "muted", `The target keeps its group (${group}) and description (${this.plan.target.description || "empty"}). Duplicate tags and preset entries are combined. The audit record retains both definitions.`));
      }
      box.append(el("p", "muted", "Original video, old revision snapshots, coaching notes, and frozen export jobs do not change. The old name remains a protected alias."));
      const list = el("ul", "taxonomy-affected");
      this.plan.affected_clips.forEach(clip => {
        const row = el("li");
        row.append(el("strong", "", `${clip.title} · v${clip.version}${clip.archived ? " · archived" : ""}`), el("p", "muted", `${JSON.stringify(clip.before)} → ${JSON.stringify(clip.after)}`));
        list.append(row);
      });
      this.plan.affected_presets.forEach(preset => list.append(el("li", "muted", `Preset: ${preset.name}`)));
      box.append(list);
      const accept = button("Apply reviewed change", () => this.applyPreview(), "button button-primary");
      accept.id = "taxonomy-apply";
      box.append(accept);
      this.status("Preview ready. Check every affected clip before applying.");
    } catch (error) { this.plan = null; this.status(error.message, true); }
    finally { this.setPending(false); }
  }

  async applyPreview() {
    if (!this.plan || this.pending) return;
    const request = this.changeRequest;
    this.setPending(true);
    this.status("Applying the reviewed change atomically…");
    try {
      const result = await api("/taxonomy/changes/apply", { method: "POST", body: request });
      this.state = result.taxonomy;
      this.id = result.result.target.id;
      this.action = "edit";
      this.render();
      this.status(`Change applied · ${result.result.affected_clips.length} clip revisions saved. Old names remain searchable.`);
      try { await this.onChanged(result); } catch (error) { toast(`Change saved; library refresh failed: ${error.message}`, "error"); }
    } catch (error) {
      this.plan = null;
      $("#taxonomy-preview").hidden = true;
      const confirmedRejection = ["taxonomy_preview_stale", "taxonomy_storage_error", "taxonomy_not_found", "unchanged_tag_name"].includes(error.code);
      this.status(`${error.message} ${confirmedRejection ? "No change was applied by this request. Preview again, or reload terminology." : "The result could not be confirmed. Reload before retrying."}`, true);
    } finally { this.setPending(false); }
  }
}
