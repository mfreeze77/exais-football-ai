/** One CSV record, so imported tags containing commas/quotes remain editable. */
export function formatTags(tags) {
  return tags.map(tag => /[,"\r\n]/.test(tag) ? `"${tag.replaceAll('"', '""')}"` : tag).join(", ");
}

export function parseTags(value) {
  const tags = [];
  let field = "", quoted = false, ended = false;
  const finish = () => { if (field.trim()) tags.push(field.trim()); field = ""; ended = false; };
  for (let index = 0; index < value.length; index++) {
    const char = value[index];
    if (quoted) {
      if (char !== '"') field += char;
      else if (value[index + 1] === '"') { field += '"'; index++; }
      else { quoted = false; ended = true; }
    } else if (char === ",") finish();
    else if (ended && !/\s/.test(char)) throw new Error("After a quoted tag, use a comma to start the next tag.");
    else if (char === '"' && !field.trim()) { field = ""; quoted = true; }
    else if (!ended) field += char;
  }
  if (quoted) throw new Error("Close the quoted tag. Use two quotes for a literal quote inside it.");
  finish();
  return [...new Set(tags)];
}
