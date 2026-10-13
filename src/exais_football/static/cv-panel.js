import { api, upload, localURL } from './api.js';
import { el, duration } from './ui.js';
import { input, select, check, row, section, details, downloadLink, post, clamp, media, action } from './tools-common.js';
import { frameAt } from './drawing-board.js';

function reviewedLabel(track,decisions,t,unreviewed,names,roster){
  if(track.suppressed)return null;let label=unreviewed?`${track.position} ?`:null;let player=null;
  for(const d of decisions){const p=d.payload;if(d.stale||d.track_id!==track.track_id||d.kind==='continuity'||!(p.start_seconds<=t&&t<p.end_seconds))continue;
    if(d.kind==='position')label=p.value==='suppressed'?null:p.value;else if(d.kind==='identity')player=p.player_id;
  }if(label&&names&&player)label+=` · ${roster.get(player)??'identity unavailable'}`;return label;
}
export class CVPanel {
  constructor(owner,pane,clip){this.owner=owner;this.pane=pane;this.clip=clip;this.closed=false;this.points=[];this.calPlan=null;this.run=null;this.projection=null;this.frame=0;this.timer=null;this.origin=0;this.changed=false;}
  dirty(){return this.changed;}
  async open(){
    this.index=await api(`/study/clips/${this.clip.id}/frame-index`);
    this.environment=await api('/cv/environment');
    const setup=section('Optional analysis environment','This is a local, explicitly configured toolkit. Starting the film room does not download or run a model. Raw suggestions are not player identities or coaching conclusions.');
    this.envDetails=details('Current environment status',this.environment);setup.append(this.envDetails,
      action(this.owner,'Check configured environment',async()=>{
        this.environment=await post('/cv/environment/check');this.envDetails.replaceWith(this.envDetails=details('Verified environment manifest',this.environment));this.owner.setStatus('Environment checked. Analysis still requires a deliberate run.');
      },'study-cv-environment-check'),el('p','muted','Setup: exais-football cv-configure --help. Acquire weights separately, record their license/source/hash, and use the pinned external checkout.'));this.pane.append(setup);
    await this.calibration();await this.analysis();await this.reviewRuns();this.paint();
  }
  async calibration(){
    this.calibrations=await api(`/cv/clips/${this.clip.id}/calibrations`);const latest=this.calibrations[0];
    const block=section('Calibrate the field','Pick at least four widely distributed, identifiable field points. Pixel coordinates refer to the native source; field X is yards between goal lines (0–100), Y is yards across the field (0–53⅓). Review the projected grid before saving.');
    this.calFrame=input('Calibration source frame (zero-based)','study-cal-frame',latest?.payload.frame??0,'number');this.calFrame.control.step='1';
    const stage=el('div','tools-board-stage');this.calImage=el('img','tools-cal-image');this.calImage.alt='Selected source frame for field calibration';
    this.calCanvas=el('canvas','tools-cal-canvas');this.calCanvas.width=Math.min(1280,this.clip.width);this.calCanvas.height=Math.round(this.calCanvas.width*this.clip.height/this.clip.width);this.calCanvas.setAttribute('aria-label','Click field landmarks; equivalent pixel and yard coordinate inputs are below');
    stage.append(this.calImage,this.calCanvas);this.pointList=el('div');this.pointList.id='study-cal-points';this.calPreview=el('div');this.calPreview.id='study-cal-preview';
    this.points=structuredClone(latest?.payload.points??[]);
    this.calCanvas.addEventListener('pointerdown',event=>{
      if(this.points.length>=64)return;const r=this.calCanvas.getBoundingClientRect();this.readPoints();this.points.push({px:Math.round((event.clientX-r.left)/r.width*this.clip.width),py:Math.round((event.clientY-r.top)/r.height*this.clip.height),fx:0,fy:0});this.invalidateCalibration();this.renderPoints();
    });
    this.calFrame.control.addEventListener('input',()=>this.invalidateCalibration());
    block.append(row(this.calFrame.wrapper,action(this.owner,'Load indexed calibration frame',async()=>{
      this.index=await post(`/study/clips/${this.clip.id}/frame-index`);await this.loadCalFrame();
    },'study-cal-load-frame')),stage,action(this.owner,'Add coordinate row',()=>{
      this.readPoints();this.points.push({px:0,py:0,fx:0,fy:0});this.invalidateCalibration();this.renderPoints();
    },'study-cal-add-point'),this.pointList,
      action(this.owner,'Preview field grid',async()=>{
        this.readPoints();const body=this.calBody();const plan=await post(`/cv/clips/${this.clip.id}/calibrations/preview`,body);this.calPlan={body,plan};this.calPreview.replaceChildren(el('p','',`Reprojection RMS: ${plan.reprojection_rms_pixels.toFixed(4)} pixels. Inspect the grid before saving.`),action(this.owner,'Save reviewed calibration',async()=>{
          if(!this.calPlan||JSON.stringify(this.calBody())!==JSON.stringify(this.calPlan.body))throw new Error('Calibration changed. Preview the grid again.');
          const saved=await post(`/cv/clips/${this.clip.id}/calibrations`,{...this.calPlan.body,preview_token:this.calPlan.plan.preview_token});
          this.calibrations.unshift(saved);this.changed=false;this.calPlan=null;this.refreshCalSelect();this.calPreview.replaceChildren(el('p','',`Calibration revision ${saved.revision} saved.`));this.owner.setStatus('Reviewed calibration saved.');
        },'study-cal-save',true));this.drawCalibration();
      },'study-cal-preview-button',true),this.calPreview);
    this.pane.append(block);this.renderPoints();if(this.index.status==='ready')await this.loadCalFrame();
  }
  invalidateCalibration(){this.changed=true;this.calPlan=null;this.calPreview.replaceChildren(el('p','muted','Preview the changed calibration before saving.'));this.drawCalibration();}
  readPoints(){if(!this.pointControls)return;this.points=this.pointControls.map(controls=>Object.fromEntries(Object.entries(controls).map(([key,c])=>[key,Number(c.value)])));}
  renderPoints(){this.pointControls=[];this.pointList.replaceChildren();this.points.forEach((point,i)=>{
    const controls={};const line=row(el('strong','',String(i+1)));for(const key of ['px','py','fx','fy']){const value=input(`${key} · point ${i+1}`,`study-cal-${i}-${key}`,point[key],'number');controls[key]=value.control;value.control.addEventListener('input',()=>{this.readPoints();this.invalidateCalibration();});line.append(value.wrapper);}
    line.append(action(this.owner,'Remove point',()=>{this.readPoints();this.points.splice(i,1);this.invalidateCalibration();this.renderPoints();}));this.pointControls.push(controls);this.pointList.append(line);
  });this.drawCalibration();}
  calBody(){this.readPoints();return{source_sha256:this.clip.source_sha256,expected_revision:this.calibrations[0]?.revision??0,frame:Number(this.calFrame.control.value),points:this.points};}
  async loadCalFrame(){const i=Number(this.calFrame.control.value);if(!Number.isInteger(i)||i<0||i>=this.index.count)throw new Error('Select a frame within the source index.');
    const image=new Image();image.src=localURL(`/api/study/clips/${this.clip.id}/frames/${i}`);await image.decode();if(this.closed)return;this.calImage.src=image.src;this.drawCalibration();}
  drawCalibration(){if(!this.calCanvas)return;const c=this.calCanvas,ctx=c.getContext('2d'),sx=c.width/this.clip.width,sy=c.height/this.clip.height;ctx.clearRect(0,0,c.width,c.height);ctx.strokeStyle='#7cffc4';ctx.lineWidth=1.5;
    for(const [a,b] of this.calPlan?.plan.grid??[]){ctx.beginPath();ctx.moveTo(a[0]*sx,a[1]*sy);ctx.lineTo(b[0]*sx,b[1]*sy);ctx.stroke();}
    this.points.forEach((p,i)=>{if(!Number.isFinite(p.px)||!Number.isFinite(p.py))return;ctx.fillStyle='yellow';ctx.beginPath();ctx.arc(p.px*sx,p.py*sy,5,0,Math.PI*2);ctx.fill();ctx.fillStyle='white';ctx.font='bold 16px sans-serif';ctx.fillText(String(i+1),p.px*sx+8,p.py*sy-8);});
  }
  refreshCalSelect(){if(!this.calSelect)return;const old=this.calSelect.control.value;this.calSelect.control.replaceChildren(new Option('Choose reviewed calibration',''));this.calibrations.forEach(c=>this.calSelect.control.add(new Option(`Revision ${c.revision} · frame ${c.payload.frame}`,c.id)));this.calSelect.control.value=this.calibrations[0]?.id??old;}
  async analysis(){
    const block=section('Prepare a single-play analysis','This profile processes the full source, not just a saved trim. Retain pre-snap context, use one camera view, and mark the snap manually on an indexed frame.');
    this.calSelect=select('Saved calibration','study-cv-calibration',[]);this.refreshCalSelect();
    this.snap=input('Snap source frame (zero-based)','study-cv-snap',20,'number');this.snap.control.step='1';
    this.direction=select('Offense direction','study-cv-direction',['unknown','left','right'],'unknown');this.color=input('Offense visual team description','study-cv-team-color');
    this.context=check('I reviewed pre-snap setup and confirm this source contains one continuous camera view','study-cv-context');
    this.cuts=input('Camera-cut source times, when present','study-cv-cuts');
    const preflight=el('div');preflight.id='study-cv-preflight';
    const intent=()=>({source_sha256:this.clip.source_sha256,calibration_id:this.calSelect.control.value||null,snap_frame:Number(this.snap.control.value),context_start:0,context_end:this.clip.source_duration,context_reviewed:this.context.control.checked,offense_direction:this.direction.control.value,offense_color:this.color.control.value,camera_cuts:this.cuts.control.value.trim()?this.cuts.control.value.split(',').map(Number):[]});
    block.append(row(this.calSelect.wrapper,this.snap.wrapper),row(this.direction.wrapper,this.color.wrapper),this.context.wrapper,this.cuts.wrapper,
      action(this.owner,'Check this play',async()=>{const result=await post(`/cv/clips/${this.clip.id}/preflight`,intent());preflight.replaceChildren(details('Play readiness',result));this.owner.setStatus(result.status==='ready'?'Play inputs are ready for an explicit run.':result.reasons.join('; '),result.status!=='ready');},'study-cv-preflight-button'),
      action(this.owner,'Run optional local analysis',async()=>{
        if(!this.environment.fingerprint)throw new Error('Run the configured environment check first.');
        const job=await post(`/cv/clips/${this.clip.id}/analysis`,{...intent(),environment_fingerprint:this.environment.fingerprint});
        this.owner.setStatus(`Analysis queued (${job.id}). Raw output stays separate from human review.`);await this.pollJobs();
      },'study-cv-run',true),preflight);
    this.jobHost=el('div');this.jobHost.id='study-cv-jobs';block.append(this.jobHost);this.pane.append(block);
    await this.pollJobs();
  }
  async pollJobs(){
    clearTimeout(this.timer);if(this.closed)return;const jobs=(await api('/work')).filter(j=>j.clip_id===this.clip.id&&j.kind.startsWith('cv_'));
    if(this.closed)return;this.jobHost.replaceChildren();jobs.slice(0,10).forEach(j=>{
      const line=row(el('span','',`${j.kind}: ${j.status} · ${j.error?.message??j.stage}`));
      if(['queued','running'].includes(j.status))line.append(action(this.owner,'Cancel analysis task',async()=>{await post(`/work/${j.id}/cancel`);await this.pollJobs();}));
      if(['failed','cancelled','interrupted'].includes(j.status))line.append(action(this.owner,'Retry frozen analysis task',async()=>{await post(`/work/${j.id}/retry`);await this.pollJobs();}));
      if(j.result?.download_url)line.append(downloadLink(j.result,'Download overlay'));this.jobHost.append(line);
    });if(jobs.some(j=>['queued','running'].includes(j.status)))this.timer=setTimeout(()=>this.pollJobs().catch(e=>this.owner.setStatus(e.message,true)),1500);
  }
  async reviewRuns(){
    const block=section('Review source-bound analysis','A track ID is anonymous and local to one analysis run. Position corrections and roster identity require separate decisions with evidence.');
    const importFile=input('Import a complete verified analysis ZIP','study-cv-import','','file');importFile.control.accept='.zip';
    block.append(importFile.wrapper,action(this.owner,'Import analysis package',async()=>{
      const file=importFile.control.files[0];if(!file)throw new Error('Choose a complete analysis package.');const form=new FormData();form.append('file',file);
      await upload(`/cv/clips/${this.clip.id}/runs`,form);await this.refreshRuns();
    },'study-cv-import-button'));
    this.runSelect=select('Preserved analysis run','study-cv-run-select',[]);this.runSelect.control.addEventListener('change',()=>this.loadRun().catch(e=>this.owner.setStatus(e.message,true)));
    block.append(row(this.runSelect.wrapper,action(this.owner,'Refresh analysis runs',()=>this.refreshRuns(),'study-cv-refresh-runs')));
    this.video=media({...this.clip,start_seconds:0},'study-cv-video');this.overlayCanvas=el('canvas','tools-cv-overlay');this.overlayCanvas.width=Math.min(1280,this.clip.width);this.overlayCanvas.height=Math.round(this.overlayCanvas.width*this.clip.height/this.clip.width);
    const stage=el('div','tools-board-stage');stage.append(this.video,this.overlayCanvas);block.append(stage);
    this.unreviewed=check('Show unreviewed position suggestions (?)','study-cv-show-unreviewed');this.identities=check('Show manually linked roster names','study-cv-show-identities');
    this.playback=select('Video version','study-cv-video-version',[{value:'source',label:'Clean original'}],'source');this.playback.control.addEventListener('change',()=>this.switchVideo().catch(e=>this.owner.setStatus(e.message,true)));
    block.append(row(this.unreviewed.wrapper,this.identities.wrapper,this.playback.wrapper));
    this.trackSelect=select('Track to review','study-cv-track',[]);this.position=select('Reviewed position','study-cv-position',['DL','LB','CB','S','OL','QB','RB','WR','unknown','suppressed'],'unknown');
    this.player=select('Manual roster identity','study-cv-player',[{value:'unknown',label:'Unknown / revoke identity'},...this.owner.catalog().players.map(p=>({value:p.id,label:p.name}))],'unknown');
    this.start=input('Decision from source seconds','study-cv-decision-start',0,'number');this.end=input('Decision until source seconds','study-cv-decision-end',this.clip.source_duration,'number');this.evidence=input('Review evidence / reason (required)','study-cv-evidence');this.evidence.control.maxLength=1000;
    const decide=async kind=>{
      if(!this.run)throw new Error('Select a preserved analysis run.');
      const body={expected_revision:this.projection.review_version,start_seconds:Number(this.start.control.value),end_seconds:Number(this.end.control.value),evidence:this.evidence.control.value};
      if(kind==='position')body.value=this.position.control.value;else body.player_id=this.player.control.value==='unknown'?null:this.player.control.value;
      await post(`/cv/runs/${this.run.id}/tracks/${encodeURIComponent(this.trackSelect.control.value)}/${kind}`,body);await this.loadRun();this.owner.setStatus('Human decision recorded separately; raw model output is unchanged.');
    };
    block.append(row(this.trackSelect.wrapper,this.position.wrapper,this.player.wrapper),row(this.start.wrapper,this.end.wrapper),this.evidence.wrapper,
      row(action(this.owner,'Record position correction',()=>decide('position'),'study-cv-save-position',true),action(this.owner,'Record manual identity',()=>decide('identity'),'study-cv-save-identity')));
    const continuity=section('Review tracking continuity','Continuity changes create a new projection. Earlier position/identity decisions become visibly stale and are not reused without review.');
    this.continuity=select('Continuity action','study-cv-continuity-action',['suppress','restore','split','link','unlink'],'suppress');this.split=input('Split at source frame','study-cv-split-frame',30,'number');this.other=input('Other raw track ID (link/unlink)','study-cv-other-track');
    continuity.append(row(this.continuity.wrapper,this.split.wrapper,this.other.wrapper),action(this.owner,'Record continuity decision',async()=>{
      if(!this.run)throw new Error('Select an analysis run.');const choice=this.continuity.control.value;
      await post(`/cv/runs/${this.run.id}/tracks/${encodeURIComponent(this.trackSelect.control.value)}/continuity`,{expected_revision:this.projection.review_version,action:choice,at_frame:choice==='split'?Number(this.split.control.value):null,other_track_id:['link','unlink'].includes(choice)?this.other.control.value:null,evidence:this.evidence.control.value});
      await this.loadRun();this.owner.setStatus('Continuity projection updated; review stale labels before exporting.');
    },'study-cv-save-continuity'));block.append(continuity);
    this.qualityHost=el('div');this.decisionHost=el('div');block.append(this.qualityHost,this.decisionHost);
    const from=input('Overlay export in (source seconds)','study-overlay-start',this.clip.start_seconds,'number');const until=input('Overlay export out (source seconds)','study-overlay-end',this.clip.end_seconds,'number');
    block.append(row(from.wrapper,until.wrapper),action(this.owner,'Render reviewed overlay MP4',async()=>{
      if(!this.run)throw new Error('Select an analysis run.');
      await post(`/cv/runs/${this.run.id}/overlays`,{expected_review_revision:this.projection.review_version,include_unreviewed:this.unreviewed.control.checked,include_identities:this.identities.control.checked,start_seconds:Number(from.control.value),end_seconds:Number(until.control.value)});
      this.owner.setStatus('Overlay queued with frozen review decisions and source audio.');await this.pollJobs();
    },'study-cv-render',true));this.pane.append(block);await this.refreshRuns();
    const identitySection=section('Find specifically reviewed player appearances','This view uses current per-track identity decisions, not manual whole-clip player tags. Revoked and stale identities are excluded.');
    const identityPlayer=select('Roster player to find','study-identity-filter',[{value:'',label:'Choose a player'},...this.owner.catalog().players.map(p=>({value:p.id,label:p.name}))],'');let identityOffset=0;const identityResults=el('div');
    const findIdentities=async()=>{if(!identityPlayer.control.value)throw new Error('Choose a roster player.');const found=await api(`/cv/players/${identityPlayer.control.value}/identities?limit=25&offset=${identityOffset}`);identityResults.replaceChildren(...found.items.map(item=>{const card=section(item.clip.title,`Reviewed track identity · run ${item.run_id}`);card.append(details('Effective source intervals and evidence',item.intervals),action(this.owner,'Review matching clip',async()=>{await this.owner.close();if(!this.owner.dialog.open)await this.owner.review.open(item.clip.id); }));return card;}));if(!found.items.length)identityResults.append(el('p','muted','No effective reviewed identities on this page.'));if(found.next_offset!==null)identityResults.append(action(this.owner,'Next identity page',async()=>{identityOffset=found.next_offset;await findIdentities();}));};
    identitySection.append(identityPlayer.wrapper,action(this.owner,'Find reviewed identities',async()=>{identityOffset=0;await findIdentities();},'study-find-identities'),identityResults);this.pane.append(identitySection);
  }
  async refreshRuns(){const runs=await api(`/cv/clips/${this.clip.id}/runs`);const old=this.runSelect.control.value;
    this.runSelect.control.replaceChildren(new Option('Choose a run',''));runs.forEach(r=>this.runSelect.control.add(new Option(`${r.created_at} · ${r.manifest.model.backend}`,r.id)));this.runs=runs;this.runSelect.control.value=runs.find(r=>r.id===old)?.id??runs[0]?.id??'';await this.loadRun();}
  async loadRun(){
    const nonce=this.runNonce=(this.runNonce??0)+1; const id=this.runSelect.control.value;if(!id){this.run=null;this.projection=null;return;}const projection=await api(`/cv/runs/${id}/projection`);if(this.runSelect.control.value!==id||this.closed)return;
    this.run=this.runs.find(r=>r.id===id);this.projection=projection;const old=this.trackSelect.control.value;
    this.trackSelect.control.replaceChildren();projection.tracks.forEach(t=>this.trackSelect.control.add(new Option(`${t.track_id} · model ${t.position}${t.suppressed?' · suppressed':''}`,t.track_id)));this.trackSelect.control.value=projection.tracks.find(t=>t.track_id===old)?.track_id??projection.tracks[0]?.track_id??'';
    this.frames=new Map();for(const track of projection.tracks)for(const f of track.frames){if(!this.frames.has(f.i))this.frames.set(f.i,[]);this.frames.get(f.i).push({track,box:f.box});}
    const quality=await api(`/cv/runs/${id}/quality`);if(this.runNonce!==nonce||this.closed)return;this.qualityHost.replaceChildren(details('Tracking quality flags requiring review',quality));this.decisionHost.replaceChildren(details('Human decisions and staleness',projection.decisions),details('Immutable run provenance',this.run.manifest));
    const overlays=(await api(`/artifacts?clip_id=${this.clip.id}`)).filter(a=>a.kind==='cv_overlay'&&a.manifest.run_id===id&&a.download_url);if(this.runNonce!==nonce||this.closed)return;this.overlays=overlays;
    this.playback.control.replaceChildren(new Option('Clean original','source'));overlays.forEach(a=>this.playback.control.add(new Option(`Overlay revision ${a.manifest.review_revision}${a.stale?' · older decisions':''}`,a.id)));this.playback.control.value='source';await this.switchVideo();
  }
  async switchVideo(){
    const nonce=this.videoNonce=(this.videoNonce??0)+1; const selected=this.overlays?.find(a=>a.id===this.playback.control.value);const sourceTime=this.video.currentTime+this.origin;const playing=!this.video.paused;this.video.pause();
    this.origin=selected?.manifest.start_seconds??0;this.burned=Boolean(selected);const url=localURL(selected?.download_url??this.clip.source_url);
    this.video.addEventListener('loadedmetadata',()=>{if(this.videoNonce!==nonce||this.closed)return;this.video.currentTime=clamp(sourceTime-this.origin,0,this.video.duration);if(playing)this.video.play().catch(e=>this.owner.setStatus(e.message,true));},{once:true});this.video.src=url;
  }
  paint(){
    if(this.closed)return;const c=this.overlayCanvas;if(c){const ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);
      if(this.projection&&!this.burned){const t=this.video.currentTime+this.origin,i=frameAt(this.projection.timestamps,t);const roster=new Map(this.owner.catalog().players.map(p=>[p.id,p.name]));
        for(const {track,box} of this.frames.get(i)??[]){const label=reviewedLabel(track,this.projection.decisions,t,this.unreviewed.control.checked,this.identities.control.checked,roster);if(!label)continue;const [x1,y1,x2,y2]=box.map((x,j)=>x*(j%2?c.height/this.clip.height:c.width/this.clip.width));ctx.strokeStyle=label.includes('?')?'#ffd466':'#82f1b0';ctx.lineWidth=2;ctx.strokeRect(x1,y1,x2-x1,y2-y1);ctx.font='bold 15px sans-serif';ctx.fillStyle='black';ctx.fillRect(x1,Math.max(0,y1-21),ctx.measureText(label).width+6,20);ctx.fillStyle='white';ctx.fillText(label,x1+3,Math.max(15,y1-6));}
      }
    }this.frame=requestAnimationFrame(()=>this.paint());
  }
  dispose(){this.closed=true;cancelAnimationFrame(this.frame);clearTimeout(this.timer);if(this.video){this.video.pause();this.video.removeAttribute('src');this.video.load();}}
}
