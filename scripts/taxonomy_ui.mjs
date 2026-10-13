/** Additional real-browser EFA-017 acceptance; uses only the smoke test's isolated film. */
import assert from "node:assert/strict";

export async function qualifyTaxonomy({ page, request, first, second, until, shot }) {
  const manager = page.locator("#taxonomy-dialog");
  const state = () => request("/taxonomy");
  const idOf = async (kind, name) => {
    const item = (await state())[kind].find(value => value.name === name);
    assert.ok(item, `${kind} missing: ${name}`);
    return item.id;
  };
  const settled = async prefix => until(async () =>
    (await page.locator("#taxonomy-status").textContent()).startsWith(prefix) &&
    (await manager.getAttribute("aria-busy")) === "false", `Terminology did not reach ${prefix}`);
  const selectKind = kind => page.locator(`[data-tax-kind="${kind}"]`).click();
  const pick = async (kind, name) => {
    await selectKind(kind);
    await page.locator(`#taxonomy-list [data-item-id="${await idOf(kind, name)}"]`).click();
  };
  const submit = async () => {
    await page.locator('#taxonomy-form button[type="submit"]').click();
    await settled("Saved");
  };
  const open = async () => { await page.locator("#manage-tags").click(); await settled("Terminology revision"); };
  const close = async () => { await page.locator("#taxonomy-close").click(); await manager.waitFor({ state: "hidden" }); };
  const settledLayout = async width => {
    await until(async () => manager.evaluate((dialog, targetWidth) => {
      const bounds = dialog.getBoundingClientRect();
      const editor = document.querySelector("#taxonomy-editor").getBoundingClientRect();
      const columns = getComputedStyle(document.querySelector(".taxonomy-layout")).gridTemplateColumns.split(" ").length;
      return innerWidth === targetWidth && bounds.width >= Math.min(1000, targetWidth - 60) &&
        bounds.right <= targetWidth + 1 && editor.width >= Math.min(400, targetWidth - 100) &&
        columns === (targetWidth <= 700 ? 1 : 2);
    }, width), `Terminology layout did not settle at ${width}px`, 5000);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  };
  await page.setViewportSize({ width: 1440, height: 1000 });
  await open();
  await selectKind("groups");
  await page.locator("#tax-name").fill("Coach vocabulary");
  await page.locator("#tax-description").fill("Optional local terminology");
  await submit();
  await pick("groups", "Coach vocabulary");
  await page.locator("#tax-name").fill("Our technique");
  await submit();
  const groupId = await idOf("groups", "Our technique");
  const quotedTag = 'cut, "block"';
  for (const name of ["pursuit", quotedTag, '<img src=x onerror="window.__taxonomyUnsafe=true">']) {
    await selectKind("tags");
    await page.locator("#tax-name").fill(name);
    await page.locator("#tax-group").selectOption(groupId);
    await submit();
  }
  await pick("tags", "pursuit");
  await manager.getByRole("button", { name: "Add alias", exact: true }).click();
  await page.locator("#tax-name").fill("scrape");
  await submit();
  assert.equal(await manager.locator("img").count(), 0);
  assert.equal(await page.evaluate(() => window.__taxonomyUnsafe), undefined);
  await selectKind("presets");
  await page.locator("#tax-name").fill("Linebacker focus");
  for (const name of ["pursuit", quotedTag]) await page.locator(`#taxonomy-form input[value="${await idOf("tags", name)}"]`).check();
  await submit();
  const presetId = await idOf("presets", "Linebacker focus");
  await pick("presets", "Linebacker focus");
  await page.locator("#tax-description").fill("Read, react, pursue");
  await submit();
  // Generic form labels stack vertically; preset checkboxes must remain beside
  // their wrapped text on both desktop and small viewports.
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await settledLayout(width);
    await page.waitForFunction(() => [...document.querySelectorAll(".taxonomy-checklist label")].every(label => {
      const box = label.querySelector("input").getBoundingClientRect();
      const text = label.querySelector("span").getBoundingClientRect();
      return getComputedStyle(label).flexDirection === "row" && box.width <= 24 &&
        text.left >= box.right && text.right <= label.getBoundingClientRect().right + 1;
    }), null, { timeout: 3000 });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await settledLayout(1440);
  await shot("05-taxonomy-presets");
  await close();

  const original = await request(`/clips/${first.id}`);
  await page.getByRole("button", { name: `Review ${original.title}`, exact: true }).click();
  await page.locator("#review-content").waitFor({ state: "visible" });
  await page.locator("#edit-description").fill("Keep this unsaved coaching detail");
  await page.locator("#notes-tab").click();
  await page.locator("#note-body").fill("Keep this note while adding a preset");
  await page.locator("#details-tab").click();
  await page.locator("#review-preset").selectOption(presetId);
  await page.locator("#review-add-preset").click();
  assert.equal(await page.locator("#edit-description").inputValue(), "Keep this unsaved coaching detail");
  assert.equal(await page.locator("#note-body").inputValue(), "Keep this note while adding a preset");
  const parseDraft = async () => page.evaluate(async () => {
    const { parseTags } = await import("/static/tag-input.js");
    return parseTags(document.querySelector("#edit-tags").value);
  });
  assert.deepEqual(new Set(await parseDraft()), new Set([...original.tags, "pursuit", quotedTag]));
  assert.equal((await request(`/clips/${first.id}`)).version, original.version, "Adding a draft must not immediately write");
  await page.locator("#save-clip").click();
  await until(async () => (await page.locator("#draft-status").textContent()) === "All changes saved", "Preset draft did not save");
  const applied = await request(`/clips/${first.id}`);
  assert.deepEqual(new Set(applied.tags), new Set([...original.tags, "pursuit", quotedTag]));
  assert.equal(applied.phase, original.phase);
  assert.equal(applied.source_sha256, first.source_sha256);
  await page.locator("#review-close").click();
  await page.locator("#review-dialog").waitFor({ state: "hidden" });
  const secondNow = await request(`/clips/${second.id}`);
  await request(`/clips/${second.id}`, { method: "PATCH", body: JSON.stringify({ version: secondNow.version, tags: ["unknown coach term", "pursuit"] }) });
  console.log("PASS EFA-017 group/tag/preset creation, optional grouped labels, safe text, quoted tags, additive draft and note preservation");

  await open();
  await pick("tags", "pursuit");
  await manager.getByRole("button", { name: "Rename or map", exact: true }).click();
  await page.locator("#tax-name").fill("pursuit angle");
  await page.locator('#taxonomy-form button[type="submit"]').click();
  await settled("Preview ready");
  assert.ok((await page.locator("#taxonomy-preview").textContent()).includes("2 clips"));
  assert.equal((await request(`/clips/${first.id}`)).version, applied.version);
  // Simulate another tab editing a previewed clip. Applying an old preview must fail.
  const secondBefore = await request(`/clips/${second.id}`);
  await request(`/clips/${second.id}`, { method: "PATCH", body: JSON.stringify({ version: secondBefore.version, description: "Changed after preview" }) });
  await page.locator("#taxonomy-apply").click();
  await until(async () => (await page.locator("#taxonomy-status").textContent()).includes("Preview again"), "Stale preview did not show a conflict");
  assert.equal(await page.locator("#tax-name").inputValue(), "pursuit angle");
  assert.ok((await request(`/clips/${first.id}`)).tags.includes("pursuit"));
  await page.locator('#taxonomy-form button[type="submit"]').click();
  await settled("Preview ready");
  await page.setViewportSize({ width: 820, height: 1180 });
  await settledLayout(820);
  await shot("06-taxonomy-preview");
  assert.ok(await manager.evaluate(node => node.scrollWidth <= node.clientWidth + 1), "Taxonomy preview overflows on tablet");
  await page.locator("#taxonomy-apply").click();
  await settled("Change applied");
  assert.equal((await request("/clips?tag=scrape")).total, 2);
  assert.equal((await request("/clips?tag=pursuit")).total, 2);
  assert.equal((await state()).tags.find(t => t.name === "pursuit angle").count, 2);
  assert.equal((await state()).presets.find(p => p.id === presetId).tag_ids.length, 2);
  console.log("PASS EFA-017 reviewed rename, stale-preview rejection, retained aliases, affected counts, and stable preset identities");

  await pick("tags", "unknown coach term");
  await manager.getByRole("button", { name: "Rename or map", exact: true }).click();
  await page.locator("#tax-name").fill("pursuit angle");
  await page.locator('#taxonomy-form button[type="submit"]').click();
  await settled("Preview ready");
  assert.ok((await page.locator("#taxonomy-preview").textContent()).includes("Map into pursuit angle"));
  await page.locator("#taxonomy-apply").click();
  await settled("Change applied");
  assert.deepEqual((await request(`/clips/${second.id}`)).tags, ["pursuit angle"]);
  assert.equal((await request("/clips?tag=unknown%20coach%20term")).total, 2);
  const beforeDelete = await request(`/clips/${first.id}`);
  await pick("presets", "Linebacker focus");
  await manager.getByRole("button", { name: "Delete preset", exact: true }).click();
  await page.locator("#choice-dialog").getByRole("button", { name: "Delete preset", exact: true }).click();
  await settled("Saved");
  assert.equal((await state()).presets.length, 0);
  assert.deepEqual(await request(`/clips/${first.id}`), beforeDelete);
  console.log("PASS EFA-017 explicit unfamiliar-tag mapping, duplicate removal, and preset deletion without relabeling film");

  await pick("groups", "Our technique");
  await page.locator("#tax-description").fill("Retain this draft on conflict");
  const fresh = await state();
  await request("/taxonomy/groups", { method: "POST", body: JSON.stringify({ expected_revision: fresh.revision, name: "Another tab" }) });
  await page.locator('#taxonomy-form button[type="submit"]').click();
  await until(async () => (await page.locator("#taxonomy-status").textContent()).includes("Terminology changed"), "Missing terminology version conflict");
  assert.equal(await page.locator("#tax-description").inputValue(), "Retain this draft on conflict");
  await page.locator("#taxonomy-close").click();
  await page.locator("#choice-dialog").getByRole("button", { name: "Keep editing", exact: true }).click();
  assert.equal(await page.locator("#tax-description").inputValue(), "Retain this draft on conflict");
  await page.locator("#taxonomy-reload").click();
  await page.locator("#choice-dialog").getByRole("button", { name: "Discard draft", exact: true }).click();
  await settled("Terminology revision");
  await page.setViewportSize({ width: 390, height: 844 });
  await settledLayout(390);
  await shot("07-taxonomy-small-screen");
  assert.ok(await manager.evaluate(node => node.scrollWidth <= node.clientWidth + 1), "Taxonomy editor overflows on small screens");
  await close();
  await until(async () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "Page overflows after terminology close", 3000);
  console.log("PASS EFA-017 conflict/draft recovery, keyboard-accessible controls, and responsive terminology manager");
}
