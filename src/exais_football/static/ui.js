export const $ = (selector, parent = document) => parent.querySelector(selector);
export const $$ = (selector, parent = document) => [...parent.querySelectorAll(selector)];

const iconPaths = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  star: '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z"/>',
  review: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h5m-5 4 2 2 5-5"/>',
  archive: '<rect x="3" y="3" width="18" height="5" rx="1"/><path d="M5 8v12h14V8m-10 4h6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  users: '<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3m1-16a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 5v1"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 4v3"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  filter: '<path d="M4 6h16M7 12h10m-7 6h4"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 6a8 8 0 0 1 13.3 2M4.6 16A8 8 0 0 0 18 18"/>',
  film: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4m-4 6h4m10-6h4m-4 6h4m-11-4 4 2-4 2z"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  play: '<path d="m8 4 12 8-12 8z"/>',
  pause: '<path d="M8 5v14m8-14v14"/>',
  'frame-back': '<path d="M5 5v14m14-14L8 12l11 7Z"/>',
  'frame-next': '<path d="M19 5v14M5 5l11 7-11 7Z"/>',
  loop: '<path d="m16 3 4 4-4 4M4 10V7h16M8 21l-4-4 4-4m12 1v3H4"/>',
  left: '<path d="m14 5-7 7 7 7"/>',
  right: '<path d="m10 5 7 7-7 7"/>',
  up: '<path d="m5 14 7-7 7 7"/>',
  down: '<path d="m5 10 7 7 7-7"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  external: '<path d="M14 3h7v7m0-7L10 14m1-11H3v18h18v-8"/>',
  playlist: '<path d="M3 5h16M3 10h12M3 15h8m4-1 6 4-6 4Z"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
  warning: '<path d="m12 3 10 18H2Zm0 6v5m0 3v1"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
};

export function icon(name) {
  const span = document.createElement("span");
  span.className = "icon";
  span.setAttribute("aria-hidden", "true");
  // Only trusted, locally defined icon paths enter this markup.
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${iconPaths[name] || iconPaths.film}</svg>`;
  return span;
}

export function mountIcons(parent = document) {
  $$('[data-icon]', parent).forEach(node => node.replaceWith(icon(node.dataset.icon)));
}

export function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = text;
  return node;
}

export function button(text, onClick, className = "button button-secondary", iconName) {
  const node = el("button", className);
  node.type = "button";
  if (iconName) node.append(icon(iconName));
  node.append(document.createTextNode(text));
  node.addEventListener("click", onClick);
  return node;
}

export function iconButton(name, label, onClick) {
  const node = button("", onClick, "icon-button", name);
  node.title = label;
  node.setAttribute("aria-label", label);
  return node;
}

export function duration(seconds = 0, precise = false) {
  const value = Math.max(0, Number(seconds) || 0);
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor(value / 60) % 60;
  const secs = Math.floor(value) % 60;
  let result = `${hours ? `${hours}:` : ""}${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  if (precise) result += `.${String(Math.floor((value * 1000) % 1000)).padStart(3, "0")}`;
  return result;
}

export function filmTime(seconds = 0) {
  const value = Math.max(0, Number(seconds) || 0);
  if (value >= 3600) return `${Math.floor(value / 3600)}h ${Math.floor(value / 60) % 60}m`;
  if (value >= 60) return `${Math.floor(value / 60)}m ${Math.floor(value) % 60}s`;
  return `${Math.floor(value)}s`;
}

export function friendly(value) {
  return ({ offense: "Offense", defense: "Defense", special_teams: "Special teams", other: "Other", new: "New", review: "In review", approved: "Approved", needs_work: "Needs work", queued: "Queued", running: "Processing", complete: "Ready", failed: "Failed" })[value] || value;
}

export function dateLabel(value) {
  if (!value) return "";
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function fillSelect(node, records, firstLabel, value = node.value, labeler = item => item.title || item.name) {
  node.replaceChildren(new Option(firstLabel, ""));
  records.forEach(item => node.add(new Option(labeler(item), item.id ?? item.name)));
  node.value = value || "";
}

export function toast(message, kind = "success") {
  const node = el("div", `toast toast-${kind}`);
  node.setAttribute("role", kind === "error" ? "alert" : "status");
  node.append(icon(kind === "error" ? "warning" : "check"), el("span", "", message));
  node.append(iconButton("close", "Dismiss notification", () => node.remove()));
  const region = $("#toast-region");
  region.append(node);
  if (typeof region.showPopover === "function") region.showPopover();
  setTimeout(() => node.remove(), kind === "error" ? 14000 : 6500);
}

let choiceQueue = Promise.resolve();
export function choose(title, message, options) {
  const show = () => new Promise(resolve => {
    const dialog = $("#choice-dialog");
    $("#choice-title").textContent = title;
    $("#choice-body").textContent = message;
    const actions = $("#choice-actions");
    actions.replaceChildren();
    const finish = value => { dialog.removeEventListener("cancel", cancel); dialog.close(); resolve(value); };
    const cancel = event => { event.preventDefault(); finish(null); };
    dialog.addEventListener("cancel", cancel);
    options.forEach(option => actions.append(button(option.label, () => finish(option.value), `button ${option.primary ? "button-primary" : "button-secondary"}`)));
    dialog.showModal();
  });
  const result = choiceQueue.then(show);
  choiceQueue = result.catch(() => {});
  return result;
}

export function isEditing(event) {
  return Boolean(event.target.closest('input,textarea,select,[contenteditable="true"],button,a,summary'));
}

export function debounce(fn, wait = 220) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), wait); };
}
