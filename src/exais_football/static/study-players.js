import { api, localURL } from './api.js';
import { el, duration, choose } from './ui.js';
import { input, select, check, row, section, post, clamp, media, action, details } from './tools-common.js';

export class ComparisonPlayer {
  constructor(owner, host, pair) { this.owner=owner;this.host=host;this.pair=pair;this.frame=0;this.closed=false; }
  async open(){
    this.closed=false;this.host.replaceChildren();const p=this.pair;
    const block=section(p.title,p.stale?'Clip metadata changed since this comparison was saved. It still uses the saved cuts; save settings to rebind current cuts.':'Common timeline uses saved source anchors; no video is rewritten.');
    this.left=media(p.left_snapshot,'study-comparison-left');this.right=media(p.right_snapshot,'study-comparison-right');
    this.left.controls=false;this.right.controls=false;this.right.muted=true;
    const stages=el('div','tools-comparison');
    for(const [side,video] of [['left',this.left],['right',this.right]]){
      const card=el('div');const stage=el('div','tools-comparison-stage');stage.append(video);
      const zoom=input(`${side} zoom`,'study-'+side+'-zoom',p[side+'_zoom'],'range');zoom.control.min='1';zoom.control.max='4';zoom.control.step='.05';
      zoom.control.addEventListener('input',()=>video.style.transform=`scale(${zoom.control.value})`);
      video.style.transform=`scale(${zoom.control.value})`;this[side+'Zoom']=zoom.control;
      card.append(el('h4','',p[side+'_snapshot'].title),stage,zoom.wrapper);stages.append(card);
    }
    this.timeline=input('Common elapsed time relative to anchors','study-comparison-seek',p.elapsed_start,'range');
    this.timeline.control.min=String(p.elapsed_start);this.timeline.control.max=String(p.elapsed_end);this.timeline.control.step='.005';
    this.timeline.control.addEventListener('input',()=>this.seek(Number(this.timeline.control.value)));
    this.clock=el('p','tools-clock');this.clock.id='study-comparison-clock';
    this.play=action(this.owner,'Play both',()=>this.toggle(),'study-comparison-play',true);
    const speed=select('Both playback speeds','study-comparison-speed',['.25','.5','1','1.5','2'],'1');
    speed.control.addEventListener('change',()=>{this.left.playbackRate=Number(speed.control.value);this.right.playbackRate=Number(speed.control.value);});
    this.left.addEventListener('loadedmetadata',()=>this.seek(p.elapsed_start),{once:true});this.right.addEventListener('loadedmetadata',()=>this.seek(p.elapsed_start),{once:true});
    for(const video of [this.left,this.right]){
      video.addEventListener('error',()=>{this.pause();this.owner.setStatus('One comparison source cannot play. Use a compatible MP4 or the external handoff.',true);});
      video.addEventListener('ended',()=>this.pause());
    }
    const leftAnchor=input('Left source anchor','study-comparison-left-anchor',p.left_anchor,'number');
    const rightAnchor=input('Right source anchor','study-comparison-right-anchor',p.right_anchor,'number');
    block.append(stages,row(this.play,speed.wrapper,action(this.owner,'Return to anchors',()=>this.seek(clamp(0,p.elapsed_start,p.elapsed_end)),'study-comparison-zero')),this.timeline.wrapper,this.clock,
      row(leftAnchor.wrapper,rightAnchor.wrapper),action(this.owner,'Save anchors and zoom',async()=>{
        const updated=await api(`/study/comparisons/${p.id}`,{method:'PUT',body:{title:p.title,version:p.version,left_clip_id:p.left_clip_id,right_clip_id:p.right_clip_id,left_anchor:Number(leftAnchor.control.value),right_anchor:Number(rightAnchor.control.value),left_zoom:Number(this.leftZoom.value),right_zoom:Number(this.rightZoom.value)}});
        this.dispose();this.pair=updated;await this.open();this.owner.setStatus('Comparison settings saved.');
      },'study-comparison-save-settings'),action(this.owner,'Prepare external Kinovea comparison',async()=>{
        const result=await post(`/study/comparisons/${p.id}/kinovea/launch`);block.append(details('External launch and synchronization manifest',result));
      },'study-comparison-native'));
    this.host.append(block);this.watch();
  }
  seek(u){
    const p=this.pair;u=clamp(u,p.elapsed_start,p.elapsed_end);
    if(this.left.readyState)this.left.currentTime=clamp(p.left_anchor+u,p.left_snapshot.start_seconds,p.left_snapshot.end_seconds);
    if(this.right.readyState)this.right.currentTime=clamp(p.right_anchor+u,p.right_snapshot.start_seconds,p.right_snapshot.end_seconds);
  }
  pause(){this.left?.pause();this.right?.pause();if(this.play)this.play.textContent='Play both';}
  async toggle(){
    if(!this.left.paused){this.pause();return;}
    const p=this.pair;const u=this.left.currentTime-p.left_anchor;
    if(u>=p.elapsed_end-.04)this.seek(p.elapsed_start);
    try{await Promise.all([this.left.play(),this.right.play()]);this.play.textContent='Pause both';}
    catch(error){this.pause();throw error;}
  }
  watch(){
    if(this.closed)return;const p=this.pair;
    const u=clamp(this.left.currentTime-p.left_anchor,p.elapsed_start,p.elapsed_end);
    const target=p.right_anchor+u;
    if(!this.left.paused){
      if(u>=p.elapsed_end-.015){this.pause();this.seek(p.elapsed_end);}
      else if(this.right.readyState>=2&&Math.abs(this.right.currentTime-target)>.08)this.right.currentTime=target;
    }
    this.timeline.control.value=String(u);
    this.clock.textContent=`Left ${duration(this.left.currentTime,true)} · Right ${duration(this.right.currentTime,true)} · drift ${Math.abs(this.right.currentTime-target).toFixed(3)} s`;
    this.frame=requestAnimationFrame(()=>this.watch());
  }
  dispose(){this.closed=true;cancelAnimationFrame(this.frame);this.pause();for(const v of [this.left,this.right])if(v){v.removeAttribute('src');v.load();}}
}

export class SessionPlayer {
  constructor(owner,host,session){this.owner=owner;this.host=host;this.record=session;this.chain=Promise.resolve();this.closed=false;this.lastSave=0;this.switching=false;}
  async open(){
    this.closed=false;this.host.replaceChildren();const block=section(this.record.snapshot.title,'Session order and cuts are frozen. Progress and watch-again flags do not modify notes or clip metadata.');
    this.heading=el('h4');this.note=el('p','muted');this.video=media(this.current(),'study-session-video');
    this.position=el('p');this.position.id='study-session-progress';
    this.video.addEventListener('loadedmetadata',()=>{const c=this.current();this.video.currentTime=clamp(this.record.progress.at_seconds,c.start_seconds,c.end_seconds);});
    this.video.addEventListener('pause',()=>{if(!this.switching&&!this.closed)this.persist().catch(e=>this.owner.setStatus(e.message,true));});
    this.video.addEventListener('timeupdate',()=>{
      if(this.closed||this.switching)return;
      const c=this.current();
      if(!this.video.paused&&this.video.currentTime>=c.end_seconds-.015){if(this.loop.control.checked){this.video.currentTime=c.start_seconds;this.persist('replay',this.record.progress.index,c.start_seconds).then(()=>this.refreshLabels()).catch(e=>this.owner.setStatus(e.message,true));return;}this.switching=true;this.video.pause();this.advance(1,true).catch(e=>{this.owner.setStatus(e.message,true);this.switching=false;});}
      else if(!this.video.paused&&performance.now()-this.lastSave>2000){this.lastSave=performance.now();this.persist().catch(e=>this.owner.setStatus(e.message,true));}
    });
    this.video.addEventListener('error',()=>this.owner.setStatus('This session source is unavailable or unsupported. Skip it without changing the frozen order.',true));
    this.loop=check('Loop the current cut','study-session-loop');this.notesHost=el('div');this.notesGeneration=0;
    this.watchAgain=action(this.owner,'Watch again',async()=>{const marked=this.record.progress.watch_again.includes(this.current().id);await this.persist(marked?'clear_watch_again':'watch_again');this.refreshLabels();},'study-session-watch-again');
    block.append(this.heading,this.note,this.video,this.loop.wrapper,this.notesHost,row(
      action(this.owner,'Previous clip',()=>this.advance(-1),'study-session-previous'),
      action(this.owner,'Replay this cut',async()=>{await this.persist('replay',this.record.progress.index,this.current().start_seconds);this.video.currentTime=this.current().start_seconds;this.refreshLabels();},'study-session-replay'),
      this.watchAgain,action(this.owner,'Next clip / skip',()=>this.advance(1),'study-session-next')),
      row(action(this.owner,'Save resume position',async()=>{await this.persist();this.owner.setStatus('Session position saved.');},'study-session-save',true),
          action(this.owner,'Reload session state',async()=>{this.video.pause();this.record=await api(`/study/sessions/${this.record.id}`);this.conflicted=false;this.loadCurrent();this.owner.setStatus('Reloaded stored session progress.');},'study-session-reload'),
          action(this.owner,'Reset session progress',async()=>{
            if(await choose('Reset progress?','Replay counts, watch-again flags and resume position reset. Coaching notes stay unchanged.',[{label:'Cancel',value:false},{label:'Reset progress',value:true,primary:true}])){await this.persist('reset');this.loadCurrent();}
          },'study-session-reset')),this.position);
    this.host.append(block);this.loadCurrent();
  }
  current(){return this.record.snapshot.clips[this.record.progress.index];}
  refreshLabels(){
    const c=this.current(),p=this.record.progress;
    this.heading.textContent=c.title;
    const availability=this.record.availability.find(a=>a.clip_id===c.id)?.status??'unknown';
    this.note.textContent=`${availability} · ${duration(c.start_seconds,true)}–${duration(c.end_seconds,true)} · Saved cut, not a live playlist`;
    this.position.textContent=`Clip ${p.index+1} of ${this.record.snapshot.clips.length} · ${p.replays[c.id]??0} replays · ${p.watch_again.length} watch-again clips${p.finished?' · Session complete':''}`;
    this.watchAgain.textContent=p.watch_again.includes(c.id)?'Remove watch-again flag':'Watch again';
  }
  loadCurrent(){this.switching=true;this.video.pause();this.video.src=localURL(this.current().source_url);this.refreshLabels();this.switching=false;const generation=++this.notesGeneration;api(`/clips/${this.current().id}/notes`).then(notes=>{if(!this.closed&&generation===this.notesGeneration)this.notesHost.replaceChildren(el('h4','','Saved coaching notes'),...notes.map(n=>el('p','muted',`${n.at_seconds===null?'General':duration(n.at_seconds,true)} · ${n.body}`)));}).catch(e=>this.owner.setStatus(e.message,true));}
  persist(actionName='position',index=this.record.progress.index,at=null){
    const clip=this.record.snapshot.clips[index];const seconds=at??clamp(this.video.currentTime,clip.start_seconds,clip.end_seconds);
    const run=()=>api(`/study/sessions/${this.record.id}`,{method:'PATCH',body:{version:this.record.version,index,at_seconds:seconds,action:actionName}}).then(record=>{this.record=record;return record;});
    const operation=this.chain.then(()=>{if(this.conflicted)throw new Error('Session changed elsewhere. Reload stored progress before saving.');return run();});this.chain=operation.catch(()=>{this.conflicted=true;this.video.pause();});return operation;
  }
  async advance(delta,autoplay=false){
    this.switching=true;this.video.pause();
    try{
      const i=this.record.progress.index+delta;
      if(i>=this.record.snapshot.clips.length){await this.persist('finish');this.refreshLabels();return;}
      if(i<0)return;
      await this.persist('position',i,this.record.snapshot.clips[i].start_seconds);this.loadCurrent();
      if(autoplay)await this.video.play();
    }finally{this.switching=false;}
  }
  dispose(){if(this.closed)return;this.closed=true;if(this.video){this.video.pause();this.persist().catch(e=>this.owner.setStatus(e.message,true));this.video.removeAttribute('src');this.video.load();}}
}
