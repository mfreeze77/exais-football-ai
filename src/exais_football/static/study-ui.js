import { api, queryString, localURL } from './api.js';
import { $, el, button, duration, choose } from './ui.js';
import { parseTags } from './tag-input.js';
import { input, select, check, row, section, details, downloadLink, post, table, clamp, media, discardDraft, action } from './tools-common.js';
import { DrawingBoard } from './drawing-board.js';
import { CVPanel } from './cv-panel.js';
import { ComparisonPlayer, SessionPlayer } from './study-players.js';

export class StudyTools {
  constructor({ catalog, filters, onChanged, review }) {
    this.catalog = catalog; this.filters = filters; this.onChanged = onChanged; this.review = review;
    this.selected = new Map(); this.generation = 0; this.dirty = () => false; this.dispose = () => {};
    this.dialog = el('dialog', 'tools-dialog'); this.dialog.id = 'study-tools-dialog';
    this.dialog.setAttribute('aria-labelledby', 'study-tools-heading');
    const heading = el('h2', '', 'Film-study tools'); heading.id = 'study-tools-heading';
    this.status = el('p', 'tools-status'); this.status.id = 'study-tools-status'; this.status.setAttribute('role', 'status'); this.status.setAttribute('aria-live', 'polite');
    const close = button('Close tools', () => this.close(), 'button button-secondary'); close.id = 'study-tools-close';
    const header = el('header', 'tools-header'); header.append(heading, close);
    this.nav = el('nav', 'tools-nav'); this.nav.setAttribute('aria-label', 'Film-study tools');
    for (const [key, title] of [['library','Select & edit'],['collections','Collections & sessions'],['compare','Compare'],['board','Draw & inspect'],['kva','Kinovea review'],['cv','Computer vision'],['recovery','Files & recovery']]) {
      this.nav.append(action(this, title, () => this.show(key), `study-nav-${key}`));
    }
    this.content = el('div', 'tools-content'); this.content.id = 'study-tools-content';
    this.dialog.append(header, this.status, this.nav, this.content); document.body.append(this.dialog);
    this.dialog.addEventListener('cancel', event => { event.preventDefault(); this.close(); });
    this.dialog.addEventListener('close', () => { this.dispose(); this.generation++; this.focus?.focus(); });
    window.addEventListener('beforeunload', event => { if (this.dialog.open && this.dirty()) { event.preventDefault(); event.returnValue = ''; } });
  }
  setStatus(text, error = false) { this.status.textContent = text; this.status.classList.toggle('tools-error', error); }
  async close() {
    if (!(await discardDraft(this.dirty()))) return;
    this.dirty = () => false; this.dialog.close();
  }
  async open(clipId = null) {
    this.focus = document.activeElement;
    if (!this.dialog.open) this.dialog.showModal();
    try {
      if (clipId) { const clip = await api(`/clips/${clipId}`); this.selected.set(clip.id, clip); }
      await this.show('library');
    } catch (error) { this.setStatus(error.message, true); }
  }
  async show(page) {
    if (!(await discardDraft(this.dirty()))) return;
    this.dispose(); this.dispose = () => {}; this.dirty = () => false;
    const generation = ++this.generation; this.content.replaceChildren();
    this.page = page; this.setStatus('Loading tools…');
    this.nav.querySelectorAll('button').forEach(b => b.setAttribute('aria-current', b.id === `study-nav-${page}` ? 'page' : 'false'));
    const pane = el('div'); this.content.append(pane);
    this.pane = pane;
    if (page === 'library') await this.library(pane);
    else if (page === 'collections') await this.collections(pane);
    else if (page === 'compare') await this.comparisons(pane);
    else if (page === 'board') {
      const clip = await this.oneClip(); if (generation !== this.generation) return; const board = new DrawingBoard(this, pane, clip);
      await board.open(); if (generation !== this.generation || !pane.isConnected) { board.dispose(); return; } this.dispose = () => board.dispose(); this.dirty = () => board.dirty();
    } else if (page === 'kva') await this.kva(pane);
    else if (page === 'cv') { const clip = await this.oneClip(); if (generation !== this.generation) return; const cv = new CVPanel(this, pane, clip); await cv.open(); if (generation !== this.generation || !pane.isConnected) { cv.dispose(); return; } this.dispose = () => cv.dispose(); this.dirty = () => cv.dirty(); }
    else await this.recovery(pane);
    if (generation === this.generation) this.setStatus(`${this.selected.size} clips selected. Originals stay preserved.`);
  }
  async oneClip() {
    if (this.selected.size !== 1) throw new Error('Select exactly one clip in Select & edit for this tool.');
    return api(`/clips/${[...this.selected.keys()][0]}`);
  }
  selection() { return [...this.selected.values()].map(c => ({ clip_id: c.id, version: c.version })); }
  async refreshSelected() {
    const clips = await Promise.all([...this.selected.keys()].map(id => api(`/clips/${id}`)));
    this.selected = new Map(clips.map(c => [c.id, c]));
    this.setStatus('Selected clip versions refreshed. Build a new preview before changing them.');
  }
  async library(pane) {
    const picker = section('Choose the clips to work on', 'Selections persist across search pages. Bulk edits affect only these selected versions.');
    const q = input('Find clips', 'study-search', '', 'search'); const archived = check('Include archived view', 'study-search-archived');
    const count = el('p'); count.id = 'study-selection-count';
    const list = el('div'); let offset = 0; let total = 0; let generation = 0;
    const refreshCount = () => { count.textContent = `${this.selected.size} clips selected`; };
    const load = async () => {
      const current = ++generation;
      const result = await api(`/clips?${queryString({q:q.control.value, archived:archived.control.checked, limit:60, offset})}`);
      if (current !== generation || !picker.isConnected) return;
      total = result.total; list.replaceChildren(table(['Select','Clip','Range','Review'], result.items.map(clip => {
        const selected = check(`Select ${clip.title}`, '', this.selected.has(clip.id));
        selected.control.setAttribute('aria-label', `Select ${clip.title}`); selected.wrapper.lastChild.textContent = '';
        selected.control.addEventListener('change', () => {
          if (selected.control.checked && this.selected.size >= 500) { selected.control.checked = false; this.setStatus('Select at most 500 clips.', true); return; }
          if (selected.control.checked) this.selected.set(clip.id, clip); else this.selected.delete(clip.id);
          refreshCount();
        });
        const inspect = action(this, 'Open review', async () => { await this.close(); if (!this.dialog.open) await this.review.open(clip.id); });
        return [selected.wrapper, clip.title, `${duration(clip.start_seconds,true)}–${duration(clip.end_seconds,true)}`, inspect];
      })));
      refreshCount(); pager.textContent = `${total ? offset+1 : 0}–${Math.min(total,offset+60)} of ${total}`;
      back.disabled = offset === 0; next.disabled = offset+60 >= total;
    };
    const back = action(this, 'Previous page', async () => { offset = Math.max(0,offset-60); await load(); }, 'study-previous-page');
    const next = action(this, 'Next page', async () => { if (offset+60<total) { offset+=60; await load(); } }, 'study-next-page');
    const pager = el('span', 'muted');
    picker.append(row(q.wrapper,archived.wrapper,action(this,'Search', async () => {offset=0;await load();},'study-search-submit')),
      row(count,action(this,'Clear selection',()=>{this.selected.clear();return load();},'study-clear-selection'),
          action(this,'Refresh selected versions',()=>this.refreshSelected(),'study-refresh-selection')),list,row(back,pager,next));
    pane.append(picker);
    await load();
    const bulk = section('Apply consistent metadata', 'Choose only fields to change. Preview lists before/after values, then applies them as one transaction.');
    const tags = input('Tags (quoted comma-separated)', 'study-bulk-tags');
    const mode = select('Tag action','study-bulk-mode',['unchanged','add','remove','replace'],'unchanged');
    const phase = select('Phase','study-bulk-phase',['unchanged','offense','defense','special_teams','other'],'unchanged');
    const status = select('Review status','study-bulk-status',['unchanged','new','review','approved','needs_work'],'unchanged');
    const favorite = select('Favorite','study-bulk-favorite',['unchanged','yes','no'],'unchanged');
    const archive = select('Archive','study-bulk-archive',['unchanged','yes','no'],'unchanged');
    const game = select('Game','study-bulk-game',[{value:'unchanged',label:'Do not change'}, {value:'none',label:'Clear game'},
      ...this.catalog().games.map(g=>({value:g.id,label:g.title}))],'unchanged');
    const changePlayers = check('Replace player assignments with selected roster entries','study-bulk-change-players');
    const players = select('Players','study-bulk-players',this.catalog().players.map(p=>({value:p.id,label:p.name}))); players.control.multiple=true;
    const preview = el('div'); preview.id='study-bulk-preview';
    const body = () => {
      const changes={}; if(mode.control.value!=='unchanged') changes.tags={mode:mode.control.value,values:parseTags(tags.control.value)};
      if(phase.control.value!=='unchanged') changes.phase=phase.control.value;
      if(status.control.value!=='unchanged') changes.review_status=status.control.value;
      for(const [field,control] of [['favorite',favorite.control],['archived',archive.control]]) if(control.value!=='unchanged') changes[field]=control.value==='yes';
      if(game.control.value!=='unchanged') changes.game_id=game.control.value==='none'?null:game.control.value;
      if(changePlayers.control.checked) changes.player_ids=[...players.control.selectedOptions].map(o=>o.value);
      if(!this.selected.size) throw new Error('Select one or more clips first.');
      if(!Object.keys(changes).length) throw new Error('Choose at least one field to change.');
      return {clips:this.selection(),changes};
    };
    bulk.addEventListener('input',()=>preview.replaceChildren(el('p','muted','Build a new preview after changing the form.')));
    bulk.addEventListener('change',()=>preview.replaceChildren(el('p','muted','Build a new preview after changing the form.')));
    bulk.append(row(mode.wrapper,tags.wrapper),row(phase.wrapper,status.wrapper,game.wrapper),row(favorite.wrapper,archive.wrapper),changePlayers.wrapper,players.wrapper,
      action(this,'Preview selected edits',async()=>{
        const proposed=body(); const plan=await post('/study/batches/preview',proposed);
        preview.replaceChildren(table(['Clip','Before','After'],plan.clips.map(c=>[c.title??c.clip_id,JSON.stringify(c.before),JSON.stringify(c.after)])));
        preview.append(action(this,`Apply to ${proposed.clips.length} selected clips`,async()=>{
          const receipt=await post('/study/batches',{...proposed,preview_token:plan.preview_token});
          this.selected.clear(); await this.onChanged(); preview.replaceChildren(el('p','','Bulk edit committed.'),
            action(this,'Undo this batch',async()=>{await post(`/study/batches/${receipt.id}/undo`);await this.onChanged();preview.replaceChildren(el('p','','Batch undone.'));await load();},'study-batch-undo'));
          await load();this.setStatus('Only the previewed selected clips were changed.');
        },'study-bulk-apply',true));
      },'study-bulk-preview-button',true),preview);
    pane.append(bulk);
    const cuts=section('Create another cut of a selected source','Select exactly one clip. A new cut shares its original media, but has independent metadata, notes, and revisions.');
    const title=input('New cut title','study-cut-title','Alternate review cut');const start=input('In (source seconds)','study-cut-start',0,'number'); const end=input('Out (source seconds)','study-cut-end',1,'number');const inherit=check('Copy existing labels into the new independent cut','study-cut-inherit');
    cuts.append(row(title.wrapper,start.wrapper,end.wrapper),inherit.wrapper,action(this,'Create independent cut',async()=>{
      const clip=await this.oneClip(); const result=await post(`/study/clips/${clip.id}/views`,{version:clip.version,title:title.control.value,start_seconds:Number(start.control.value),end_seconds:Number(end.control.value),inherit_labels:inherit.control.checked});
      await this.onChanged();await load();this.setStatus(`Created ${result.title}; original bytes are unchanged.`);
    },'study-create-cut',true));pane.append(cuts);
    const history=section('Recent bulk operations');
    const batches=await api('/study/batches');
    history.append(table(['Operation','State','Action'],batches.map(b=>[b.id,b.undone?'Undone':'Applied',b.undone?'—':action(this,'Undo batch',async()=>{await post(`/study/batches/${b.id}/undo`);await this.onChanged();await this.show('library');})])));pane.append(history);
  }
  async collections(pane) {
    const title=input('Collection title','study-collection-title','My study collection');
    const create=section('Save the current library filters','A saved collection updates dynamically. A playlist snapshot freezes the reviewed list and order.');
    const filters=Object.fromEntries(Object.entries(this.filters()).filter(([,v])=>v!==''&&v!==undefined));
    create.append(title.wrapper,details('Current library filters',filters),action(this,'Save current filters',async()=>{
      await post('/study/searches',{title:title.control.value,filters});await this.show('collections');
    },'study-save-search',true));pane.append(create);
    const searches=await api('/study/searches');const list=section('Saved collections');
    for(const search of searches){
      const entry=section(search.title);const result=await api(`/study/searches/${search.id}/results?limit=60`);
      entry.append(el('p','',`${result.total} matching clips now`),details('Saved filter',search.filters));
      entry.append(action(this,'Preview playlist snapshot',async()=>{
        const plan=await post(`/study/searches/${search.id}/snapshot-preview`);const preview=el('div');
        preview.append(details('Frozen order and clip versions',plan),action(this,'Create frozen playlist',async()=>{
          await post(`/study/searches/${search.id}/playlist`,{preview_token:plan.preview_token});await this.onChanged();await this.show('collections');
        },'study-freeze-search',true));entry.append(preview);
      },'study-preview-search-'+search.id));
      entry.append(action(this,'Replace with current filters',async()=>{
        if(await choose('Update collection?','This changes only its dynamic filter, not existing playlists.',[{label:'Cancel',value:false},{label:'Update',value:true,primary:true}])){
          await api(`/study/searches/${search.id}`,{method:'PUT',body:{title:search.title,filter_version:1,filters,version:search.version}});await this.show('collections');
        }
      }));
      entry.append(action(this,'Delete saved collection',async()=>{
        if(await choose('Delete collection?','Existing playlists and clips remain.',[{label:'Cancel',value:false},{label:'Delete collection',value:true}])){
          await api(`/study/searches/${search.id}?version=${search.version}`,{method:'DELETE'});await this.show('collections');
        }
      }));list.append(entry);
    }pane.append(list);
    const sessions=section('Resumable film sessions','Starting a session freezes the playlist’s current cuts. Save position, replay, and watch-again flags independently of coaching notes.');
    const playlists=await api('/playlists');const choice=select('Playlist','study-session-playlist',playlists.map(p=>({value:p.id,label:p.title})),playlists[0]?.id??'');
    const host=el('div');
    const open=async session=>{this.dispose();this.dispose=()=>{};const player=new SessionPlayer(this,host,session);await player.open();if(!host.isConnected||this.pane!==pane){player.dispose();return;}this.dispose=()=>player.dispose();};
    sessions.append(choice.wrapper,action(this,'Start frozen session',async()=>{const session=await post('/study/sessions',{playlist_id:choice.control.value});await open(session);},'study-start-session',true));
    const saved=await api('/study/sessions');
    saved.forEach(s=>sessions.append(action(this,`Resume ${s.title}`,async()=>open(await api(`/study/sessions/${s.id}`)),`study-resume-${s.id}`)));
    sessions.append(host);pane.append(sessions);
  }
  async comparisons(pane){
    const create=section('Compare two clips','Select two clips in Select & edit. Each anchor is a source timestamp for the same event, such as the snap.');
    const title=input('Comparison title','study-comparison-title','Snap comparison');const left=input('Left anchor (seconds)','study-left-anchor',0,'number');const right=input('Right anchor (seconds)','study-right-anchor',0,'number');
    const host=el('div');const open=async pair=>{this.dispose();const player=new ComparisonPlayer(this,host,pair);await player.open();if(!host.isConnected||this.pane!==pane){player.dispose();return;}this.dispose=()=>player.dispose();};
    create.append(row(title.wrapper,left.wrapper,right.wrapper),action(this,'Save and open comparison',async()=>{
      if(this.selected.size!==2)throw new Error('Select exactly two clips first.');const ids=[...this.selected.keys()];
      const pair=await post('/study/comparisons',{title:title.control.value,left_clip_id:ids[0],right_clip_id:ids[1],left_anchor:Number(left.control.value),right_anchor:Number(right.control.value)});await open(pair);
    },'study-save-comparison',true));pane.append(create);
    const saved=await api('/study/comparisons');saved.forEach(pair=>pane.append(action(this,`Open ${pair.title}`,()=>open(pair),`study-comparison-${pair.id}`)));
    pane.append(host);
  }
  async kva(pane){
    const clip=await this.oneClip();const block=section(`Review Kinovea changes: ${clip.title}`,'Upload a KVA file using the clip review’s Advanced panel first. No proposed field changes apply until you review this comparison. Unsupported XML stays preserved.');
    const confirmed=check('I confirm the uploaded KVA describes this preserved source video','study-kva-confirm');const host=el('div');
    block.append(confirmed.wrapper,action(this,'Preview KVA fields',async()=>{
      const plan=await post(`/study/clips/${clip.id}/kva/preview`,{source_confirmed:confirmed.control.checked});const choices={};
      host.replaceChildren(table(['Field','Baseline','Current catalog','Incoming KVA','Use'],plan.fields.map(f=>{
        const decision=select(`Choose ${f.key}`,'',[{value:'catalog',label:'Keep catalog'},{value:'kva',label:'Use KVA'}],f.recommended);choices[f.key]=decision.control;
        return [f.key+(f.conflict?' · conflict':''),JSON.stringify(f.baseline),JSON.stringify(f.current),JSON.stringify(f.incoming),decision.wrapper];
      })),details('Source binding and fields preserved without interpretation',plan.incoming));
      const apply=async decision=>{
        await post(`/study/clips/${clip.id}/kva/apply`,{source_confirmed:confirmed.control.checked,preview_token:plan.preview_token,decision,choices:Object.fromEntries(Object.entries(choices).map(([k,c])=>[k,c.value]))});
        await this.onChanged();host.replaceChildren(el('p','',decision==='accepted'?'Reviewed changes applied. Raw XML remains intact.':'Import rejected. Catalog metadata is unchanged.'));
      };
      host.append(row(action(this,'Apply reviewed choices',()=>apply('accepted'),'study-kva-apply',true),action(this,'Reject incoming changes',()=>apply('rejected'),'study-kva-reject')));
    },'study-kva-preview',true),host);pane.append(block);
    pane.append(details('Prior import decisions',await api(`/study/clips/${clip.id}/kva/history`)));
  }
  async recovery(pane){
    const operations=section('Verified files & recovery','Full backup includes the catalog and required original/review/analysis files. Restore is create-only into a new folder using the command-line recovery tool.');
    const result=el('div');
    const queue=async(path,body={})=>{const job=await post(path,body);this.setStatus(`Queued ${job.kind}. Follow its state below.`);await this.show('recovery');};
    operations.append(row(action(this,'Create full verified backup',()=>queue('/backups'),'study-create-backup',true),action(this,'Verify library integrity',()=>queue('/integrity'),'study-verify-integrity')),
      el('p','muted','Restore safely: exais-football restore BACKUP.zip NEW_FOLDER previews recovery. After review, repeat with --execute --expected-sha256 HASH_FROM_PREVIEW. Existing destinations are refused.'));
    const mediaChoice=check('Include trimmed MP4 files (otherwise metadata only)','study-package-media');const sidecars=check('Include selected KVA sidecars','study-package-sidecars',true);
    operations.append(mediaChoice.wrapper,sidecars.wrapper,action(this,`Package ${this.selected.size} selected clips`,()=>queue('/packages',{clips:this.selection(),include_media:mediaChoice.control.checked,include_sidecars:sidecars.control.checked}),'study-create-package'),result);pane.append(operations);
    const jobs=await api('/work');const tasks=section('Local work queue','Interrupted and failed attempts remain visible; retry creates a new attempt with the same frozen intent.');
    tasks.append(action(this,'Refresh tasks and files',()=>this.show('recovery'),'study-refresh-work'));
    tasks.append(table(['Kind','Status','Stage / failure','Action'],jobs.map(job=>{
      const actions=el('div','tools-row');if(['queued','running'].includes(job.status))actions.append(action(this,'Cancel task',async()=>{await post(`/work/${job.id}/cancel`);await this.show('recovery');}));
      if(['failed','interrupted','cancelled'].includes(job.status))actions.append(action(this,'Retry frozen attempt',async()=>{await post(`/work/${job.id}/retry`);await this.show('recovery');}));
      if(job.result?.download_url)actions.append(downloadLink(job.result));
      if(job.result)actions.append(details('Task result',job.result));
      return [job.kind,job.status,job.error?.message??job.stage,actions];
    })));pane.append(tasks);
    const files=section('Published artifacts');const artifacts=await api('/artifacts');
    files.append(table(['Type','Created','Status','Download / evidence'],artifacts.map(r=>[r.kind,r.created_at,r.stale?'Older review decisions':'Current publication',row(downloadLink(r),details('Provenance',r.manifest))])));pane.append(files);
    const storage=await api('/storage');const cleanup=section('Manage derived storage','Only expendable thumbnails, decoded-frame images, and retained trim exports can be selected. Original film, notes, KVA, model results and backups are protected.');
    const chosen=new Set();cleanup.append(details('Storage totals and policy',storage.totals),el('p','',`${(storage.free_bytes/1024**3).toFixed(2)} GiB free`));
    cleanup.append(table(['Select','Derived file','Bytes','Eligibility'],storage.candidates.slice(0,1000).map(c=>{
      const value=check(`Select ${c.path}`,'');value.control.disabled=!c.eligible;value.control.addEventListener('change',()=>value.control.checked?chosen.add(c.id):chosen.delete(c.id));return[value.wrapper,c.path,c.bytes,c.excluded_reason??'Eligible'];
    })));
    const preview=el('div');cleanup.append(action(this,'Preview selected cleanup',async()=>{
      const ids=[...chosen];const plan=await post('/storage/preview',{ids});preview.replaceChildren(details('Files to remove',plan),action(this,'Remove only these derived files',async()=>{
        const outcome=await post('/storage/cleanup',{ids,preview_token:plan.preview_token});preview.replaceChildren(details('Actual cleanup result',outcome));this.setStatus('Cleanup finished; originals were not candidates.');
      },'study-confirm-cleanup'));
    },'study-preview-cleanup'),preview);pane.append(cleanup);
  }
}
