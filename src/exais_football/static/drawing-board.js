import { api, localURL } from './api.js';
import { el, duration, choose } from './ui.js';
import { input, select, row, section, post, clamp, media, action, downloadLink } from './tools-common.js';

export function frameAt(timestamps,time){
  let low=0,high=timestamps.length;
  while(low<high){const mid=(low+high)>>1;if(timestamps[mid]<=time+0.000001)low=mid+1;else high=mid;}
  return Math.max(0,low-1);
}
export function paintDrawing(ctx,drawing,width,height){
  const pts=drawing.points.map(p=>({x:p.x*width,y:p.y*height}));
  ctx.save();ctx.strokeStyle=ctx.fillStyle=drawing.color;ctx.lineWidth=drawing.width*width;ctx.lineCap='round';ctx.lineJoin='round';
  ctx.shadowColor='black';ctx.shadowBlur=2;ctx.beginPath();
  if(drawing.kind==='text'){ctx.font=`bold ${Math.max(14,width*.023)}px sans-serif`;ctx.fillText(drawing.text,pts[0].x,pts[0].y);}
  else if(drawing.kind==='circle'){const [a,b]=pts;ctx.ellipse((a.x+b.x)/2,(a.y+b.y)/2,Math.abs(b.x-a.x)/2,Math.abs(b.y-a.y)/2,0,0,Math.PI*2);ctx.stroke();}
  else{
    ctx.moveTo(pts[0].x,pts[0].y);for(const p of pts.slice(1))ctx.lineTo(p.x,p.y);ctx.stroke();
    if(drawing.kind==='arrow'){const [a,b]=pts;const angle=Math.atan2(b.y-a.y,b.x-a.x);const n=Math.max(12,width*.023);ctx.beginPath();ctx.moveTo(b.x,b.y);ctx.lineTo(b.x-n*Math.cos(angle-.45),b.y-n*Math.sin(angle-.45));ctx.lineTo(b.x-n*Math.cos(angle+.45),b.y-n*Math.sin(angle+.45));ctx.closePath();ctx.fill();}
  }ctx.restore();
}
export class DrawingBoard {
  constructor(owner,pane,clip){this.owner=owner;this.pane=pane;this.clip=clip;this.closed=false;this.undo=[];this.redo=[];this.active=null;this.decoded=null;this.imageToken=0;this.trimDirty=false;}
  dirty(){return this.trimDirty || JSON.stringify(this.drawings??[])!==this.baseline;}
  async open(){
    this.record=await api(`/study/clips/${this.clip.id}/drawings`);this.drawings=structuredClone(this.record.drawings);this.baseline=JSON.stringify(this.drawings);
    const block=section(`Draw and inspect: ${this.clip.title}`,'Annotations are source-relative and independent of clip labels. Exact mode displays an actual decoded source frame, not a frame-rate approximation.');
    this.video=media(this.clip,'study-board-video');this.image=el('img','tools-decoded-frame');this.image.hidden=true;this.image.alt='Exact decoded source frame';this.image.addEventListener("load",()=>this.fitCanvas?.());
    this.canvas=el('canvas','tools-drawing-canvas');this.canvas.id='study-drawing-canvas';this.canvas.setAttribute('aria-label','Drawing area; use the labeled point inputs for keyboard-only placement');
    this.canvas.width=Math.min(1280,this.clip.width);this.canvas.height=Math.round(this.canvas.width*this.clip.height/this.clip.width);
    const stage=el('div','tools-board-stage');stage.id='study-board-stage';stage.append(this.video,this.image,this.canvas);this.stage=stage; this.fitCanvas=()=>{const target=this.image.hidden?this.video:this.image;const a=target.getBoundingClientRect(), b=stage.getBoundingClientRect();const sourceWidth=target===this.video?(this.video.videoWidth||this.clip.width):(this.image.naturalWidth||this.clip.width);const sourceHeight=target===this.video?(this.video.videoHeight||this.clip.height):(this.image.naturalHeight||this.clip.height);const scale=Math.min(a.width/sourceWidth,a.height/sourceHeight);const w=sourceWidth*scale,h=sourceHeight*scale;this.canvas.style.inset="auto";this.canvas.style.left=`${a.left-b.left+(a.width-w)/2}px`;this.canvas.style.top=`${a.top-b.top+(a.height-h)/2}px`;this.canvas.style.width=`${w}px`;this.canvas.style.height=`${h}px`;}; this.resizeObserver=new ResizeObserver(this.fitCanvas); this.resizeObserver.observe(stage);this.resizeObserver.observe(this.video);this.resizeObserver.observe(this.image);
    this.mode=select('Tool','study-drawing-tool',['inspect','arrow','circle','text','freehand'],'inspect');
    this.color=select('Drawing color','study-drawing-color',['yellow','white','red','blue','green'],'yellow');
    this.text=input('Visible text label','study-drawing-text');this.text.control.maxLength=300;
    this.start=input('Visible from source seconds','study-drawing-start',this.clip.start_seconds,'number');this.end=input('Visible until source seconds','study-drawing-end',this.clip.end_seconds,'number');
    this.mode.control.addEventListener('change',()=>this.canvas.style.pointerEvents=this.mode.control.value==='inspect'?'none':'auto');this.canvas.style.pointerEvents='none';
    this.seek=input('Source time','study-board-seek',this.clip.start_seconds,'range');this.seek.control.min='0';this.seek.control.max=String(this.clip.source_duration);this.seek.control.step='.001';
    this.seek.control.addEventListener('input',()=>{this.leaveExact();this.video.currentTime=Number(this.seek.control.value);});
    this.clock=el('p','tools-clock');this.clock.id='study-board-clock';this.list=el('div');this.list.id='study-drawing-list';
    this.video.addEventListener('play',()=>this.leaveExact());this.video.addEventListener('loadedmetadata',()=>{this.fitCanvas();
      if(this.video.videoWidth!==this.clip.width||this.video.videoHeight!==this.clip.height)this.owner.setStatus('Display geometry differs from the stored source. Drawing/export on this profile requires review.',true);
    });
    this.index=await api(`/study/clips/${this.clip.id}/frame-index`);
    block.append(row(this.mode.wrapper,this.color.wrapper,this.text.wrapper),row(this.start.wrapper,this.end.wrapper),stage,
      row(action(this.owner,'Play / pause',async()=>{this.leaveExact();if(this.video.paused)await this.video.play();else this.video.pause();},'study-board-play'),
          action(this.owner,'Previous frame',()=>this.step(-1),'study-board-previous'),action(this.owner,'Next frame',()=>this.step(1),'study-board-next'),
          action(this.owner,'Build exact frame index',async()=>{this.index=await post(`/study/clips/${this.clip.id}/frame-index`);await this.exact(frameAt(this.index.timestamps,this.video.currentTime));},'study-build-frame-index'),
          action(this.owner,'Fullscreen drawing view',()=>stage.requestFullscreen(),'study-board-fullscreen')),
      this.seek.wrapper,this.clock);
    const trimIn=input('Snapped clip in (source seconds)','study-snapped-in',this.clip.start_seconds,'number');
    const trimOut=input('Snapped clip out (source seconds)','study-snapped-out',this.clip.end_seconds,'number');
    for(const control of [trimIn.control,trimOut.control])control.addEventListener('input',()=>this.trimDirty=true);
    const snap=side=>{if(this.index.status!=='ready')throw new Error('Build the exact frame index first.');const n=this.decoded??frameAt(this.index.timestamps,this.video.currentTime);if(side==='in')trimIn.control.value=this.index.timestamps[n];else trimOut.control.value=this.index.timestamps[n+1]??this.clip.source_duration;this.trimDirty=true;};
    block.append(row(trimIn.wrapper,trimOut.wrapper,action(this.owner,'Snap in to this frame',()=>snap('in'),'study-snap-in'),action(this.owner,'Snap out after this frame',()=>snap('out'),'study-snap-out')),
      action(this.owner,'Save snapped clip range',async()=>{this.clip=await api(`/clips/${this.clip.id}`,{method:'PATCH',body:{version:this.clip.version,start_seconds:Number(trimIn.control.value),end_seconds:Number(trimOut.control.value)}});this.trimDirty=false;this.owner.selected.set(this.clip.id,this.clip);await this.owner.onChanged();this.owner.setStatus('Snapped range saved as a new clip revision. Source film and drawings were preserved.');},'study-save-snapped-range'),
      action(this.owner,'Show timeline previews',async()=>{if(this.index.status!=='ready')throw new Error('Build the frame index first.');const result=await api(`/study/clips/${this.clip.id}/timeline?count=9`);this.strip.replaceChildren(...result.items.map(item=>{const node=action(this.owner,`${item.at_seconds.toFixed(3)} s`,()=>this.exact(item.frame));node.classList.add('tools-timeline-item');const image=el('img');image.alt=`Source frame ${item.frame}`;image.loading='lazy';image.src=localURL(item.image_url);node.prepend(image);return node;}));},'study-show-timeline'));
    this.strip=el('div','tools-timeline-strip');this.strip.id='study-timeline-strip';block.append(this.strip);
    const x1=input('Start X (0–1)','study-drawing-x1',.2,'number'),y1=input('Start Y (0–1)','study-drawing-y1',.3,'number');
    const x2=input('End X (0–1)','study-drawing-x2',.6,'number'),y2=input('End Y (0–1)','study-drawing-y2',.6,'number');
    const keyboard=el('details');keyboard.append(el('summary','','Keyboard placement'),row(x1.wrapper,y1.wrapper,x2.wrapper,y2.wrapper),action(this.owner,'Add drawing from points',()=>{
      const points=[{x:Number(x1.control.value),y:Number(y1.control.value)}];if(this.mode.control.value!=='text')points.push({x:Number(x2.control.value),y:Number(y2.control.value)});this.add(points);
    },'study-drawing-add-points'));block.append(keyboard);
    block.append(row(action(this.owner,'Undo drawing edit',()=>{if(this.undo.length){this.redo.push(structuredClone(this.drawings));this.drawings=this.undo.pop();this.renderList();}},'study-drawing-undo'),
      action(this.owner,'Redo drawing edit',()=>{if(this.redo.length){this.undo.push(structuredClone(this.drawings));this.drawings=this.redo.pop();this.renderList();}},'study-drawing-redo'),
      action(this.owner,'Save drawings',()=>this.save(),'study-save-drawings',true),action(this.owner,'Export annotated still',()=>this.snapshot(),'study-save-snapshot')),
      action(this.owner,'Reload saved drawings',async()=>{if(await choose('Reload drawings?','Discard only this unsaved drawing draft?', [{label:'Keep editing',value:false},{label:'Reload saved',value:true,primary:true}])){this.record=await api(`/study/clips/${this.clip.id}/drawings`);this.drawings=structuredClone(this.record.drawings);this.baseline=JSON.stringify(this.drawings);this.undo=[];this.redo=[];this.renderList();}},'study-reload-drawings'),this.list);
    const revision=input('Earlier saved drawing revision','study-drawing-restore-revision',1,'number');block.append(row(revision.wrapper,action(this.owner,'Restore as a new drawing revision',async()=>{
      if(this.dirty())throw new Error('Save or reload the current draft before restoring a revision.');
      this.record=await post(`/study/clips/${this.clip.id}/drawings/restore`,{version:this.record.version,restore_revision:Number(revision.control.value)});this.drawings=structuredClone(this.record.drawings);this.baseline=JSON.stringify(this.drawings);this.renderList();
    },'study-drawing-restore')));
    this.pane.append(block);this.renderList();this.bindPointer();this.paint();
  }
  point(event){const r=this.canvas.getBoundingClientRect();return{x:clamp((event.clientX-r.left)/r.width,0,1),y:clamp((event.clientY-r.top)/r.height,0,1)};}
  bindPointer(){
    this.canvas.addEventListener('pointerdown',event=>{if(this.mode.control.value==='inspect')return;this.video.pause();this.canvas.setPointerCapture(event.pointerId);this.active=[this.point(event)];event.preventDefault();});
    this.canvas.addEventListener('pointermove',event=>{if(!this.active)return;const p=this.point(event);if(this.mode.control.value==='freehand'){if(this.active.length<1000)this.active.push(p);}else if(this.mode.control.value!=='text')this.active=[this.active[0],p];});
    this.canvas.addEventListener('pointerup',event=>{if(!this.active)return;const points=this.active;this.active=null;if(this.mode.control.value!=='text'&&points.length===1)points.push(this.point(event));try{this.add(points);}catch(e){this.owner.setStatus(e.message,true);}});
    this.canvas.addEventListener('pointercancel',()=>{this.active=null;});
  }
  drawing(points){
    const kind=this.mode.control.value;if(kind==='inspect')throw new Error('Choose a drawing tool first.');
    const start=Number(this.start.control.value),end=Number(this.end.control.value);
    if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||start>=end||end>this.clip.source_duration)throw new Error('Choose a valid source-relative drawing interval.');
    if(points.some(p=>!Number.isFinite(p.x)||!Number.isFinite(p.y)||p.x<0||p.x>1||p.y<0||p.y>1))throw new Error('Normalized points must be between 0 and 1.');
    if(kind==='text'&&!this.text.control.value.trim())throw new Error('Enter a visible text label.');
    return{id:crypto.randomUUID(),kind,points:kind==='text'?[points[0]]:points,start_seconds:start,end_seconds:end,text:this.text.control.value,color:this.color.control.value,width:.004};
  }
  add(points){if(this.drawings.length>=100)throw new Error('Use at most 100 drawings per clip.');const d=this.drawing(points);this.undo.push(structuredClone(this.drawings));this.redo=[];this.drawings.push(d);this.renderList();}
  renderList(){this.list.replaceChildren(el('p','',`${this.drawings.length} drawings · revision ${this.record.version}${this.dirty()?' · unsaved edits':''}`));
    this.drawings.forEach(d=>{this.list.append(row(el('span','',`${d.kind}${d.text?': '+d.text:''} · ${duration(d.start_seconds,true)}–${duration(d.end_seconds,true)}`),action(this.owner,'Remove drawing',()=>{this.undo.push(structuredClone(this.drawings));this.redo=[];this.drawings=this.drawings.filter(x=>x.id!==d.id);this.renderList();})));});}
  time(){return this.decoded===null?this.video.currentTime:this.index.timestamps[this.decoded];}
  paint(){if(this.closed)return;const ctx=this.canvas.getContext('2d');ctx.clearRect(0,0,this.canvas.width,this.canvas.height);const time=this.time();
    this.drawings.filter(d=>d.start_seconds<=time&&time<d.end_seconds).forEach(d=>paintDrawing(ctx,d,this.canvas.width,this.canvas.height));
    if(this.active){try{paintDrawing(ctx,this.drawing(this.active),this.canvas.width,this.canvas.height);}catch{}}
    this.seek.control.value=String(time);this.clock.textContent=this.decoded===null?`${duration(time,true)} · browser playback/seek${this.index.status==='ready'?' · exact frame index available':' (approximate stepping)'}`:`Decoded frame ${this.decoded+1}/${this.index.count} · source PTS ${duration(time,true)}`;
    this.frame=requestAnimationFrame(()=>this.paint());}
  leaveExact(){this.imageToken++;this.decoded=null;this.image.hidden=true;this.video.hidden=false;}
  async exact(index){this.video.pause();index=clamp(index,0,this.index.count-1);const ticket=++this.imageToken;const image=new Image();image.src=localURL(`/api/study/clips/${this.clip.id}/frames/${index}`);await image.decode();if(ticket!==this.imageToken||this.closed)return;this.image.src=image.src;this.image.hidden=false;this.video.hidden=true;this.decoded=index;this.video.currentTime=this.index.timestamps[index];}
  async step(delta){this.video.pause();if(this.index.status==='ready')await this.exact((this.decoded??frameAt(this.index.timestamps,this.video.currentTime))+delta);
    else{this.leaveExact();this.video.currentTime=clamp(this.video.currentTime+delta/(this.clip.fps||30),0,this.clip.source_duration);this.owner.setStatus('Approximate browser step. Build the frame index for decoded-frame proof.');}}
  async save(){const sent=JSON.stringify(this.drawings);const record=await api(`/study/clips/${this.clip.id}/drawings`,{method:'PUT',body:{version:this.record.version,source_sha256:this.clip.source_sha256,drawings:JSON.parse(sent)}});this.record=record;this.baseline=JSON.stringify(record.drawings);if(JSON.stringify(this.drawings)===sent)this.drawings=structuredClone(record.drawings);this.renderList();this.owner.setStatus('Drawings saved as a new revision.');}
  async snapshot(){
    if(this.dirty())throw new Error('Save the drawing revision before exporting the still.');this.video.pause();
    if(this.video.seeking&&this.decoded===null)throw new Error('Wait for the selected frame to finish seeking.');
    const image=this.decoded===null?this.video:this.image;
    if(this.decoded===null&&(this.video.videoWidth!==this.clip.width||this.video.videoHeight!==this.clip.height))throw new Error('This display-geometry profile is not qualified for snapshot export.');
    const snapshot=document.createElement('canvas');snapshot.width=this.canvas.width;snapshot.height=this.canvas.height;const ctx=snapshot.getContext('2d');ctx.drawImage(image,0,0,snapshot.width,snapshot.height);
    const time=this.time();this.drawings.filter(d=>d.start_seconds<=time&&time<d.end_seconds).forEach(d=>paintDrawing(ctx,d,snapshot.width,snapshot.height));
    const result=await post(`/study/clips/${this.clip.id}/snapshots`,{source_sha256:this.clip.source_sha256,drawing_revision:this.record.version,at_seconds:time,png_base64:snapshot.toDataURL('image/png').split(',')[1]});this.list.prepend(downloadLink(result,'Download annotated still'));this.owner.setStatus('Annotated still saved with its source and drawing revision.');
  }
  dispose(){this.resizeObserver?.disconnect();this.closed=true;this.imageToken++;cancelAnimationFrame(this.frame);if(this.video){this.video.pause();this.video.removeAttribute('src');this.video.load();}}
}
