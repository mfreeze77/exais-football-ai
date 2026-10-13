import { api, localURL } from './api.js';
import { el, button, choose } from './ui.js';

export function input(label, id, value = '', type = 'text') {
  const wrapper = el('label', 'tools-field', label);
  const control = el('input'); control.type = type; control.id = id;
  control.value = String(value); if (type === 'number') control.step = '0.001';
  wrapper.append(control); return { wrapper, control };
}
export function select(label, id, values, current = '') {
  const wrapper = el('label', 'tools-field', label); const control = el('select'); control.id = id;
  values.forEach(item => control.add(new Option(item.label ?? String(item), item.value ?? String(item))));
  control.value = String(current); wrapper.append(control); return { wrapper, control };
}
export function check(label, id, checked = false) {
  const wrapper = el('label', 'tools-check'); const control = el('input'); control.type = 'checkbox';
  control.id = id; control.checked = checked; wrapper.append(control, el('span', '', label));
  return { wrapper, control };
}
export function row(...nodes) { const node = el('div', 'tools-row'); node.append(...nodes); return node; }
export function section(title, description = '') {
  const node = el('section', 'tools-section'); node.append(el('h3', '', title));
  if (description) node.append(el('p', 'muted', description)); return node;
}
export function details(title, data) {
  const node = el('details', 'tools-evidence'); node.append(el('summary', '', title), el('pre', '', JSON.stringify(data, null, 2))); return node;
}
export function downloadLink(record, label = 'Open verified artifact') {
  const link = el('a', 'button button-secondary', label); const url = localURL(record.download_url);
  if (url) { link.href = url; link.setAttribute('download', ''); }
  else { link.removeAttribute('href'); link.textContent = 'File not retained'; }
  return link;
}
export const post = (path, body = {}) => api(path, { method: 'POST', body });
export function table(headers, rows) {
  const wrap = el('div', 'tools-table-scroll'); const tab = el('table', 'tools-table');
  const head = el('thead'); const tr = el('tr');
  headers.forEach(h => { const th = el('th', '', h); th.scope = 'col'; tr.append(th); }); head.append(tr);
  const body = el('tbody'); rows.forEach(cells => { const tr = el('tr'); cells.forEach(value => {
    const td = el('td'); td.append(value instanceof Node ? value : document.createTextNode(String(value ?? ''))); tr.append(td);
  }); body.append(tr); }); tab.append(head, body); wrap.append(tab); return wrap;
}
export function clamp(t, lo, hi) { return Math.max(lo, Math.min(hi, Number(t) || 0)); }
export function media(clip, id) {
  const video = el('video', 'tools-video'); video.id = id; video.controls = true; video.playsInline = true;
  video.preload = 'metadata'; video.src = localURL(clip.source_url);
  video.addEventListener('loadedmetadata', () => { video.currentTime = clip.start_seconds; }, { once: true });
  return video;
}
export async function discardDraft(dirty) {
  if (!dirty) return true;
  return await choose('Keep your unfinished work?', 'This tool has unsaved edits. Stay to save them, or explicitly discard this tool draft.',
    [{ label: 'Keep editing', value: false, primary: true }, { label: 'Discard tool draft', value: true }]) === true;
}
export function isTextEditing(event) {
  return Boolean(event.target.closest('input,textarea,select,[contenteditable="true"]'));
}
export function action(owner, label, fn, id, primary = false) {
  const node = button(label, async () => {
    node.disabled = true; owner.setStatus('Working…');
    try { await fn(); }
    catch (error) { owner.setStatus(error.message || String(error), true); }
    finally { node.disabled = false; }
  }, `button ${primary ? 'button-primary' : 'button-secondary'}`);
  if (id) node.id = id; return node;
}
