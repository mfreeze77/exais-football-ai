/** Dependency-free tests for source-preserving tag CSV editing. */
import assert from "node:assert/strict";
import { parseTags, formatTags } from "../src/exais_football/static/tag-input.js";
const examples = [[], ["a"], ["a", "b"], ['cut, "block"', "straße", "<script>", "line\nbreak"], ['quote"inside'], [",", '"']];
for (const tags of examples) assert.deepEqual(parseTags(formatTags(tags)), tags);
assert.deepEqual(parseTags(" a , a , b , "), ["a", "b"]);
assert.throws(() => parseTags('"unclosed'));
assert.throws(() => parseTags('"closed"bad'));
console.log(`PASS tag input: ${examples.length} exact round trips, duplicate/whitespace handling, malformed quote rejection`);
