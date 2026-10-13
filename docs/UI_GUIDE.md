# Using the EXAIS film room

The browser film room organizes the clips you already have. Import files, set the
useful portion of each play, add football labels and coaching notes, and return
to the same review later. The application serves its own JavaScript and CSS;
there is no frontend build step, hosted media dependency, or model setup needed
for the core workflow.

Start the application using the setup in the repository README, then open the
loopback address it prints. The sidebar connection indicator and any setup error
describe the current local application. The first screen intentionally starts
empty until you import your own footage.

## Import and organize clips

1. Open **Games & players** to create a game or a roster entry. A game requires a
   title and opponent. A player requires a name; jersey and position are optional.
   These entries are available to all clips in this workspace.
2. Choose the **Game for these imports**, or leave the clips unassigned. This
   target is independent of the library's current game filter.
3. Drop one or several existing video files onto the import area, or use
   **Choose clips**. Imports run one at a time with a separate progress bar and
   result for each file. Keep the browser tab open until the queue finishes.
4. Each successful import appears in the library with its original duration,
   thumbnail when available, phase, tags, and review status. Identical source
   bytes return the existing clip; importing them again does not replace its
   saved review.

Upload progress measures bytes sent to the local server. A completed transfer
can remain at **Validating & indexing** while the server inspects the video.
Failed imports display their error and a retry button. The configured upload
size limit is checked when the server reports it. A failed file stays available
for retry until the import panel is dismissed or the tab closes.

Search matches titles, original filenames, coaching summaries, tags, and saved
notes. Filter by game or player directly; **Filters** also exposes phase, review
status, and a single tag. **Favorites**, **To review**, and **Archive** are saved
views: To review selects clips explicitly marked **In review**. The archive is
separate from the active clip counts. Large result sets load in groups of 60.

Games and players can be created in this milestone. Editing or deleting catalog
entries is not yet exposed in the UI. Tags are comma separated, with a maximum
of 40 tags of 64 characters each; the server normalizes whitespace and case.

## Review a play and tighten the range

Open a clip card to enter the review dialog. The video uses your original file;
browser support for its particular container and codecs determines whether it
plays directly. H.264/AAC MP4 is the application’s export format. If a source
cannot play in the current browser, the film room displays a message and still
allows metadata editing and a saved-selection export.

Use **IN** and **OUT** to set the useful range in seconds, or pause at a moment
and choose **Mark current**. The in mark must be before the out mark and both must
be within the original source duration. **Use full clip** resets the selection.
The highlighted bar shows the range and current playback position.

**Loop selection** repeatedly plays between the current draft marks. Starting
playback outside that range moves to the in mark. While paused, you can seek
outside the selection to adjust a mark or inspect context. Slow playback is
available at 0.25×, 0.5×, and 0.75×, with 1×, 1.5×, and 2× also available. Frame
buttons move by one nominal frame interval using the source's reported frame
rate; browser decoding and variable-frame-rate media limit frame exactness.

The review's **Details** panel stores the title, game, phase, review status, tags,
players, coaching summary, favorite, and archive flag. The player checklist
records your own labels. It does not derive roster identities from model tracks.

**Save changes** persists these fields and the selected range as a new revision.
The source file is preserved. Trimming a saved selection does not replace the
source or change the meaning of existing note timestamps.

### Drafts and concurrent changes

The header shows **Unsaved changes**, **Saving**, or **All changes saved**. Closing
the review or moving to another clip with a draft offers **Keep editing**,
**Discard draft**, and **Save all**. Save all includes a nonempty coaching-note
draft. Edits typed during an in-flight save remain in the form; the UI does not
navigate away if those newer changes still need saving.

If another tab changes a clip first, the server rejects the outdated revision.
The conflict message retains your draft. **Load latest version** asks before
replacing that draft with the current saved clip. This milestone does not merge
competing edits automatically.

Drafts live in the current browser page. A normal reload or close asks the browser
to warn when review, playlist, catalog, or import work is pending. Browser crash
recovery and durable offline drafts are not implemented. Save work you want to
keep before closing the application.

## Coaching notes

In **Notes**, write a coaching observation and choose **Add note**. With
**Attach current position** checked, the note stores the video's current position
in original-source seconds. Uncheck it for a note about the whole clip.

Saved notes appear in timestamp order, followed by untimed notes. Selecting a
timestamp pauses and seeks to that source moment, including moments outside the
currently selected range. The trash button deletes that individual saved note.
Notes are separate from metadata revisions; restoring a clip revision does not
restore deleted notes or erase existing ones. Notes currently support adding and
deleting, with no edit-note control.

## Favorites, archives, and playlists

Use a card's star or the review's Favorite checkbox to keep useful examples in
**Favorites**. Archive a clip through review details to remove it from the active
library. Open it in **Archive**, clear the checkbox, and save to restore it. This
preserves source footage and associated review records.

Create a playlist from the sidebar's plus button, then choose **Add clips**.
Checkbox selections survive searches and loading additional results. **Save
playlist** stores the selected ordered list. Use each playlist card's arrows to
move a play earlier or later and its remove button to remove only that playlist
reference. Clips already in a playlist remain there if later archived.

**Start session** opens the first play. **Previous** and **Next** move through the
playlist's order with the same draft protection as ordinary review. Opening a
clip from a library view also lets you step through that view's loaded clips.
Session advancement is manual; continuous automatic playback and playlist
renaming/deletion are not implemented.

## Export an MP4

Choose **Export selection** in review. If there are unsaved changes, **Save &
export** first saves them. Export jobs use a frozen saved revision, so later
changes to the range or title do not rewrite an already queued export.

The sidebar's **Exports** opens the queue. Pending jobs are polled until they are
ready or fail. A completed job offers **Download MP4**; failed jobs retain a
readable error. The backend creates an accurately trimmed H.264 MP4 with AAC
audio when the source has an audio track. Your original remains unchanged. These
exports contain the selected video and audio; coaching-note text and imported
KVA drawings are not burned into them.

## Advanced review integrations

### Kinovea

The **Advanced** panel checks whether a separately installed and configured
Kinovea executable can launch on this Windows computer. **Open in Kinovea** makes
that explicit local handoff request. Other platforms display the reason the
native launcher is unavailable.

**Attach .kva** uploads a validated Kinovea annotation sidecar for the current
source. An attached file can be downloaded again. Native Kinovea edits do not
automatically synchronize with browser tags, note records, or saved trim marks.
Attach the sidecar explicitly after editing it externally. This milestone has
no browser telestration renderer or native side-by-side viewer. Kinovea remains
a separate application with its own license and setup.

### Imported football-cv suggestions

**Import analysis JSON** attaches a supported football-cv position-suggestion
bundle. Running the model remains a separate external setup step. The browser
does not start inference, install weights, or generate analysis from an import.

The bundle must identify the original source with its SHA-256 and retain the
upstream revision. The server validates the schema and rejects wrong-source
results. The UI shows position suggestions against per-clip track IDs and offers
**Unreviewed**, **Accepted**, and **Rejected** as human review states. Accepting a
bundle records that decision; it does not create roster identities or silently
replace human labels. Consult the integration documentation for the import
schema and external toolkit limitations.

### Earlier versions

**View saved versions** loads the clip's revision history. Restoring an earlier
version creates a new revision containing that version's saved editable details
and trim. The confirmation handles current drafts before the restore. Source
footage and coaching notes remain attached to the clip.

## Keyboard and accessibility

All actions have labeled controls, and review uses a modal dialog with keyboard
focus containment. The Details, Notes, and Advanced tabs support arrow keys,
Home, and End. The page includes a skip-to-library link, visible focus styles,
status messages, and a reduced-motion preference.

| Shortcut | Action |
| --- | --- |
| `/` outside a dialog | Focus library search |
| Space in the review player | Play or pause |
| Left / Right arrow | Move one nominal frame |
| I / O | Set in / out to current source position |
| L | Toggle selection looping |
| N | Pause and focus a new note |
| Ctrl+S / Command+S | Save details and a nonempty note draft |
| Escape | Close the review, with draft protection |

Player shortcuts are inactive while focus is in a text field, select, link, or
button, preserving native control behavior. Save remains available while typing.
The layout adapts to desktop and tablet widths, with a stacked review and
accessible playlist navigation on narrower screens. It does not require hover
or drag gestures for essential operations.

## Optional browser verification

The first candidate passed this complete Firefox workflow in [GitHub Actions](https://github.com/mfreeze77/exais-football-ai/actions/runs/38332745633) at commit `153e5073e918dc06d28b4615f2d7749ed9e5b62b`. See [qualification evidence](QUALIFICATION.md) for the eight observed workflow groups and remaining platform/model gates.

`scripts/ui_smoke.mjs` is an optional real-browser workflow test. It requires
Node, the Python application's dependencies, FFmpeg/ffprobe, and Playwright with
a browser. These are test tools, not frontend runtime dependencies. One setup
sequence from the repository root is:

```sh
npm install --no-save --package-lock=false playwright
npx playwright install chromium
node scripts/ui_smoke.mjs
```

To use Firefox instead:

```sh
npx playwright install firefox
UI_BROWSER=firefox node scripts/ui_smoke.mjs
```

Set `PLAYWRIGHT_MODULE_PATH` when the package is installed outside the repository,
or `UI_BROWSER_EXECUTABLE` to use an already provisioned compatible browser.
`PYTHON` selects the Python interpreter; otherwise the script uses the local
`.venv/bin/python` when available. `KEEP_UI_ARTIFACTS=1` retains synthetic fixtures
and screenshots in the temporary directory printed at completion.

The test launches its own server and workspace, creates two synthetic video clips
with audio, and exercises the empty state, catalogs, imports, duplicate handling,
safe text, metadata/trim saves, real playback and looping, notes, draft protection,
revision conflicts, export polling/download/audio, search, playlists, archives,
and responsive layout. It never connects to the user's existing workspace and
removes generated artifacts by default. A missing browser or runtime failure is
a failed/unrun qualification gate, not evidence that the UI works. Refer to the
repository's qualification report and CI result for the checks actually run.
