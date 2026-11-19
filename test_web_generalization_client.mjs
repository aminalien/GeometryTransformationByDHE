/** Client integration without a browser: real API sections, mock DOM/WebGL adapter.
 * Start the app, then run node --experimental-vm-modules test_web_generalization_client.mjs.
 * This verifies control/data flow, not visual WebGL rendering.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
import {setTimeout as pause} from 'node:timers/promises';

const origin=process.env.DHE_WEB_TEST_URL||'http://127.0.0.1:8765';
const html=await fs.readFile(new URL('./web_generalization/index.html',import.meta.url),'utf8');
const elements=new Map();
const downloads=[],blobs=new Map();let blobSerial=0;
class Element{
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.value='';this.checked=false;this.hidden=false;this.style={};this.children=[];this.events={};this.dataset={};this.files=[];this._classes=new Set();this.classList={toggle:(k,force)=>{const add=force??!this._classes.has(k);add?this._classes.add(k):this._classes.delete(k);},add:k=>this._classes.add(k)};}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text||'';}
  replaceChildren(...children){this.children=[];this._text='';if(this.tagName==='SELECT')this.value='';this.append(...children);}
  append(...children){this.children.push(...children);if(this.tagName==='SELECT'&&!this.value&&this.children.length)this.value=this.children[0].value;}
  addEventListener(type,callback){(this.events[type]??=[]).push(callback);}
  async emit(type,event={}){for(const callback of this.events[type]||[])await callback(event);if(this['on'+type])await this['on'+type](event);}
  querySelector(selector){const match=selector.match(/option\[value="(.*)"\]/);return this.children.find(c=>c.value===match?.[1]);}
  showModal(){this.open=true;}
  close(){this.open=false;}
  click(){if(this.tagName==='A')downloads.push({filename:this.download,blob:blobs.get(this.href)});}
}
class OptionElement extends Element{constructor(text,value){super('option');this.textContent=text;this.value=String(value);}}
for(const match of html.matchAll(/<([\w-]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){
  const e=new Element(match[1]),attrs=match[2];e.id=match[3];
  e.value=attrs.match(/\bvalue="([^"]*)"/)?.[1]||'';e.checked=/\bchecked\b/.test(attrs);e.hidden=/\bhidden\b/.test(attrs);
  for(const key of ['min','max','step'])e[key]=attrs.match(new RegExp(`\\b${key}="([^"]*)"`))?.[1]||'';
  elements.set(e.id,e);
}
for(const match of html.matchAll(/<select[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/select>/g)){
  const e=elements.get(match[1]);for(const option of match[2].matchAll(/<option([^>]*)>(.*?)<\/option>/g))e.append(new OptionElement(option[2],option[1].match(/value="([^"]*)"/)?.[1]||option[2]));
}
const tabs=['generalize','inspect','mapping'].map(tab=>{const e=new Element('button');e.dataset.tab=tab;return e;});
const hops=['nf','s','adjacent'].map(hop=>{const e=new Element('button');e.dataset.hop=hop;return e;});
const documentEvents={};
const document={hidden:false,activeElement:new Element('body'),getElementById:id=>{assert.ok(elements.has(id),id);return elements.get(id);},
  createElement:tag=>new Element(tag),createTextNode:text=>({textContent:text}),querySelectorAll:selector=>selector==='[data-tab]'?tabs:selector==='[data-hop]'?hops:[],addEventListener(event,callback){documentEvents[event]=callback;}};
const maps=[];const overlays=[];
class FakeMap{
  constructor(options){this.options=options;this.events={};this.sources={};this.layers={};this.pitch=options.pitch;this.bearing=options.bearing;this.canvas={style:{}};this.dragPan={enable(){},disable(){}};this.bounds=[17.01,51.13,17.02,51.14];maps.push(this);setTimeout(()=>this.emit('style.load'),5);}
  on(event,callback){(this.events[event]??=[]).push(callback);return this;}
  async emit(event,payload={}){for(const f of this.events[event]||[])await f(payload);}
  addSource(id,source){this.sources[id]={...source,setData(data){this.data=data;}};}
  getSource(id){return this.sources[id];}
  addLayer(layer){this.layers[layer.id]=layer;}
  getLayer(id){return this.layers[id];}
  addControl(){}
  getCanvas(){return this.canvas;}
  setPaintProperty(id,key,value){this.layers[id].paint[key]=value;}
  queryRenderedFeatures(){return [];}
  resize(){}
  fitBounds(bounds){this.bounds=[...bounds[0],...bounds[1]];}
  getPitch(){return this.pitch;}
  getBearing(){return this.bearing;}
  getZoom(){return 19;}
  setMaxPitch(pitch){this.maxPitch=pitch;if(this.pitch>pitch)this.pitch=pitch;}
  easeTo(options){this.lastCamera=options;this.pitch=options.pitch??this.pitch;this.bearing=options.bearing??this.bearing;}
  setStyle(style){this.style=style;this.sources={};this.layers={};setTimeout(()=>this.emit('style.load'),1);}
  getBounds(){const b=this.bounds;return {getWest:()=>b[0],getSouth:()=>b[1],getEast:()=>b[2],getNorth:()=>b[3],extend(){return this;}};}
}
class Marker{setLngLat(){return this;}addTo(){return this;}remove(){}}
class Overlay{constructor(props){this.props=props;overlays.push(this);}setProps(props){this.props={...this.props,...props};}}
class Layer{constructor(props){this.props=props;this.id=props.id;}}
// UI wiring only; test_cell_focus.mjs checks the helper using real deck.gl math.
class FocusViewport{
  constructor(o){Object.assign(this,o);this.scale=512*2**o.zoom/360;this.viewProjectionMatrix=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1];}
  projectPosition(p){return p;}unprojectPosition(p){return p;}
  project(p){return [(p[0]-this.longitude)*this.scale+this.width/2,(this.latitude-p[1])*this.scale+this.height/2-p[2]*this.scale/70000*Math.sin(this.pitch*Math.PI/180),.5];}
  unproject(p){return [this.longitude+(p[0]-this.width/2)/this.scale,this.latitude-(p[1]-this.height/2)/this.scale,0];}
}
const realFetch=globalThis.fetch;let submitted,automaticSubmitted,activeSlices=0,maxActiveSlices=0,slowSlices=false;
async function fetchLocal(path,options){
  if(path==='/api/apply'){submitted=JSON.parse(options.body);return {ok:true,json:async()=>({job_id:'failure-test'})};}
  if(path==='/api/jobs/failure-test')return {ok:true,json:async()=>({status:'failed',error:'Test construction rejection'})};
  if(path==='/api/automatic'){automaticSubmitted=JSON.parse(options.body);return {ok:true,json:async()=>({job_id:'automatic-failure'})};}
  if(path==='/api/jobs/automatic-failure')return {ok:true,json:async()=>({status:'failed',error:'Test atomic automatic rejection'})};
  if(path==='/api/scene'&&process.env.DHE_WEB_STRESS_INSPECTOR==='1'){
    // Synthetic menu stress only: repeat actual topology records in the mock client.
    // This does not alter server geometry or constitute a DHE validity test.
    const response=await realFetch(new URL(path,origin),options),scene=await response.json();
    const original=scene.halfedges;
    scene.halfedges=[...original,...Array.from({length:40},(_,copy)=>original.map(e=>({...e,id:e.id+'-menu-'+copy}))).flat()];
    return {ok:true,status:200,json:async()=>scene};
  }
  if(path==='/api/slice'){
    activeSlices++;maxActiveSlices=Math.max(maxActiveSlices,activeSlices);
    try{if(slowSlices)await pause(200);return await realFetch(new URL(path,origin),options);}finally{activeSlices--;}
  }
  return realFetch(new URL(path,origin),options);
}
class DownloadURL extends URL{
  static createObjectURL(blob){const key='blob:test-'+(++blobSerial);blobs.set(key,blob);return key;}
  static revokeObjectURL(key){blobs.delete(key);}
}
const context=vm.createContext({console,document,window:{},location:{origin},fetch:fetchLocal,Option:OptionElement,URL:DownloadURL,Blob,URLSearchParams,FormData,setTimeout,clearTimeout,setInterval:()=>0});
vm.runInContext(await fs.readFile(new URL('./web_generalization/mapping.js',import.meta.url),'utf8'),context);
const source=await fs.readFile(new URL('./web_generalization/app.js',import.meta.url),'utf8');
const module=new vm.SourceTextModule(source,{context});
await module.link(async specifier=>{
  if(specifier==='./cell_focus.js')return new vm.SourceTextModule(await fs.readFile(new URL('./web_generalization/cell_focus.js',import.meta.url),'utf8'),{context});
  const exports=specifier==='maplibre-gl'?{Map:FakeMap,Marker,NavigationControl:class{},ScaleControl:class{}}:
    specifier.includes('/maplibre@')?{MapLibreOverlay:Overlay}:{...Object.fromEntries(['SolidPolygonLayer','PathLayer','LineLayer','ScatterplotLayer','TextLayer','SimpleMeshLayer','AmbientLight','DirectionalLight','LightingEffect'].map(key=>[key,Layer])),WebMercatorViewport:FocusViewport};
  return new vm.SyntheticModule(Object.keys(exports),function(){for(const [key,value] of Object.entries(exports))this.setExport(key,value);},{context});
});
await module.evaluate();
async function waitFor(predicate,label){const deadline=Date.now()+15000;while(Date.now()<deadline){if(predicate())return;await pause(25);}throw new Error(label+': '+elements.get('status').textContent);}
const el=id=>elements.get(id);
const layer=id=>overlays[0].props.layers.find(layer=>layer.id===id)?.props;
await waitFor(()=>context.window.dheAtlas?.loaded&&layer('slice-fills')?.data.length,'App boot');
assert.ok(layer('dhe-faces').data.length);assert.ok(layer('primal-vertices').data.length);
const revision=context.window.dheAtlas.revision;
assert.equal(el('focus-cell').disabled,true);assert.equal(el('export-primal').disabled,false);
await el('export-primal').emit('click');assert.ok(downloads.at(-1).filename.endsWith('.zip'));
assert.equal(new Uint8Array(await downloads.at(-1).blob.arrayBuffer())[0],80);
el('export-coordinates').value='projected';await el('export-duality').emit('click');
const exported=JSON.parse(await downloads.at(-1).blob.text());assert.equal(exported.metadata.coordinates,'projected');
assert.equal(exported.metadata.revision,revision);assert.ok(exported.dhe_records.length);assert.equal(el('export-duality').disabled,false);
for(const kind of ['primal-edges','dual-edges']){
  assert.equal(el('export-'+kind).disabled,false);const pending=el('export-'+kind).emit('click');
  assert.equal(el('export-primal-edges').disabled,true);assert.equal(el('export-dual-edges').disabled,true);await pending;
  const download=downloads.at(-1);assert.ok(download.filename.endsWith('.obj'));
  const text=await download.blob.text(),rows=text.trim().split('\n');
  const meta=JSON.parse(rows.find(line=>line.startsWith('# {')).slice(2));
  assert.equal(meta.coordinates,'projected');assert.equal(meta.revision,revision);
  assert.equal(meta.edge_scope,kind==='primal-edges'?'bounded_primal':'finite_face_adjacency');
  assert.ok(rows.every(line=>line.startsWith('#')||line.startsWith('v ')||line.startsWith('l ')));
  assert.equal(rows.filter(line=>line.startsWith('l ')).length,meta.edges);
}
el('show-3d').checked=false;await el('show-3d').emit('change');
await waitFor(()=>!layer('dhe-faces')&&layer('slice-fills')?.data.length,'Flat mode');
assert.equal(maps[0].maxPitch,0);assert.ok(layer('slice-fills').data.every(d=>d.rings.every(r=>r.every(p=>p[2]===.04))));
el('show-3d').checked=true;await el('show-3d').emit('change');assert.equal(maps[0].pitch,55);
el('dual').checked=true;await el('dual').emit('input');assert.ok(layer('dual-vertices').data.length);assert.ok(layer('dual-edges').data.every(d=>!d.exterior));
assert.ok(layer('dual-vertices').mesh.attributes.positions.value.length>100);assert.ok(layer('dual-vertices').mesh.indices.value.length>100);
const radius=layer('dual-vertices').sizeScale;el('dual-size').value='9';await el('dual-size').emit('input');assert.ok(Math.abs(layer('dual-vertices').sizeScale/radius-9/5)<1e-10);
el('dual-style').value='dots';await el('dual-style').emit('input');assert.equal(layer('dual-vertices').getRadius,9);
el('dual-style').value='spheres';await el('dual-style').emit('input');assert.ok(layer('dual-vertices').mesh);
assert.equal(layer('cutting-plane'),undefined);
el('show-plane').checked=true;await el('show-plane').emit('change');assert.ok(layer('cutting-plane'));
el('show-sections').checked=false;await el('show-sections').emit('change');
await waitFor(()=>!layer('slice-fills')&&el('slice-summary').textContent.includes('hidden'),'Hidden slice features');assert.ok(layer('cutting-plane'));
el('show-plane').checked=false;await el('show-plane').emit('change');assert.equal(layer('cutting-plane'),undefined);
el('show-sections').checked=true;await el('show-sections').emit('change');await waitFor(()=>layer('slice-fills')?.data.length,'Restore sections');
assert.ok(el('edge-select').children.length<=302);assert.ok(el('edge-cell').children.length);
const firstEdge=el('edge-select').children[1];el('edge-select').value=firstEdge.value;await el('edge-select').emit('change');assert.ok(layer('selected-face'));
const selectedFace=layer('selected-face').data[0].face,pitch=maps[0].pitch,bearing=maps[0].bearing;
assert.equal(el('focus-cell').disabled,false);await el('focus-cell').emit('click');
assert.ok(maps[0].lastCamera.center);assert.equal(maps[0].pitch,pitch);assert.equal(maps[0].bearing,bearing);
assert.equal(layer('selected-face').data[0].face,selectedFace);assert.equal(el('edge-select').value,firstEdge.value);
assert.equal(layer('selected-face').opacity,.5);
el('edge-search').value='not-a-halfedge';await el('edge-search').emit('input');assert.ok(el('edge-select').children.length<=2);
el('edge-search').value='';await el('edge-search').emit('input');
el('exaggeration').value='6';await el('exaggeration').emit('input');const edge=layer('primal-edges').data.find(d=>d.source[2]>0);assert.equal(layer('primal-edges').getSourcePosition(edge)[2],edge.source[2]*6);
el('slice-step').value='.5';await el('slice-step').emit('change');const top=Number(el('slice-value').value);await el('lower').emit('click');
await waitFor(()=>el('slice-label').textContent.includes(String(top-.5)),'Slicing step');
assert.equal(el('slice-slider').step,.5);
await pause(100);slowSlices=true;
for(const value of ['1','1.5','2','2.5']){el('slice-value').value=value;await el('slice-value').emit('input');await pause(100);}
await waitFor(()=>layer('slice-fills')?.data.some(d=>Math.abs(d.rings[0][0][2]-15)<1e-6)&&activeSlices===0,'Latest queued section');
assert.equal(maxActiveSlices,1);slowSlices=false;
el('orientation').value='Vertical';await el('orientation').emit('change');el('show-3d').checked=false;await el('show-3d').emit('change');
await waitFor(()=>el('slice-label').textContent.startsWith('Vertical')&&layer('slice-outlines')?.data.length,'Vertical sections');
await pause(150);assert.equal(layer('slice-fills').data.length,0);assert.ok(layer('slice-outlines').data.every(d=>d.path.every(p=>p[2]===.04)));
el('orientation').value='Inclined';await el('orientation').emit('change');await waitFor(()=>el('slice-label').textContent.startsWith('Inclined')&&layer('slice-fills')?.data.length,'Inclined sections');
el('basemap').value='blank';await el('basemap').emit('change');await pause(25);assert.ok(layer('slice-fills'));
assert.equal(maps[0].style.layers[0].paint['background-color'],'#ffffff');
await maps[0].emit('error',{sourceId:'background',error:new Error('tile test failure')});assert.ok(el('map-warning').textContent.includes('background'));assert.ok(layer('slice-fills'));
await maps[0].emit('sourcedata',{sourceId:'background',isSourceLoaded:true});assert.equal(el('map-warning').hidden,true);
overlays[0].props.onError?.(new Error('mesh test failure'),{id:'dual-vertices'});
assert.ok(el('status').textContent.includes('3D renderer / dual-vertices'));assert.ok(layer('slice-fills'));
el('basemap').value='light';await el('basemap').emit('change');
await maps[0].emit('error',{error:new Error('style fetch test failure')});
assert.deepEqual(Object.keys(maps[0].style.sources),[]);await pause(25);assert.ok(layer('slice-fills'));
assert.ok(maps[1].getSource('current').data.features.length);
await el('select-box').emit('click');
const bounds=maps[1].bounds;
await maps[1].emit('mousedown',{originalEvent:{button:0},lngLat:{lng:bounds[0],lat:bounds[1]},preventDefault(){}});
await maps[1].emit('mousemove',{lngLat:{lng:bounds[2],lat:bounds[3]}});
await maps[1].emit('mouseup',{lngLat:{lng:bounds[2],lat:bounds[3]}});
assert.equal(el('selection-count').textContent,maps[1].getSource('current').data.features.length+' selected');
await el('draw-bbox').emit('click');
await maps[0].emit('mousedown',{originalEvent:{button:0},lngLat:{lng:17.01,lat:51.13},preventDefault(){}});
await maps[0].emit('mouseup',{lngLat:{lng:17.02,lat:51.14}});
assert.ok(el('mode-banner').children.some(c=>c.textContent==='Download footprints'));
documentEvents.keydown({key:'Escape'});assert.equal(el('mode-banner').hidden,true);
el('indices').value='1';await el('select-indices').emit('click');assert.equal(el('selection-count').textContent,'1 selected');
el('operation').value='smoothing';await el('operation').emit('change');await el('apply').emit('click');
assert.equal(submitted.operation,'smoothing');assert.deepEqual(submitted.selected,[1]);assert.equal(submitted.revision,revision);
assert.ok(el('status').textContent.includes('Test construction rejection'));assert.equal(context.window.dheAtlas.revision,revision);assert.equal(el('apply').disabled,false);
assert.equal((await (await realFetch(origin+'/api/state')).json()).revision,revision);
el('operation').value='point';await el('operation').emit('change');await el('level-add').emit('click');
assert.equal(el('level-actions').children.length,1);
await el('level-add').emit('click');assert.ok(el('status').textContent.includes('Already assigned'));
el('indices').value='3';await el('select-indices').emit('click');el('operation').value='smoothing';await el('operation').emit('change');
await el('level-add').emit('click');assert.equal(el('level-actions').children.length,2);
assert.equal(el('preview').textContent,'Preview level');await el('preview').emit('click');
await waitFor(()=>el('busy-overlay').hidden&&el('scene-title').textContent==='Validated preview','Mixed-level preview');
await el('export-duality').emit('click');assert.ok(JSON.parse(await downloads.at(-1).blob.text()).metadata.preview_id);
assert.equal(context.window.dheAtlas.revision,revision);
await el('discard-preview').emit('click');await el('level-clear').emit('click');assert.equal(el('level-actions').children.length,0);
await el('open-automatic').emit('click');assert.ok(el('automatic-dialog').open);
el('auto-kind').value='polygon';await el('run-automatic').emit('click');
assert.equal(automaticSubmitted.settings.final_kind,'polygon');assert.deepEqual(automaticSubmitted.selected,[3]);
assert.ok(el('status').textContent.includes('atomic automatic rejection'));assert.equal(context.window.dheAtlas.revision,revision);
assert.equal(el('cancel-automatic').hidden,true);
console.log('Client integration passed: OBJ/full DHE JSON downloads, selected-cell zoom, map/section controls, primal/dual sizing, level preview, automatic settings, and atomic failure preservation.');
if(process.env.DHE_WEB_TEST_MAPPING==='1'){
  // Use an isolated server whose mapping defaults are disposable fixture layers.
  const previous=await (await realFetch(origin+'/api/scene')).json();
  await tabs.find(t=>t.dataset.tab==='mapping').emit('click');
  assert.equal(el('mapping-tab').hidden,false);assert.equal(el('generalize-tab').hidden,true);
  for(const side of ['source','target'])await el('mapping-default-'+side).emit('click');
  assert.ok(layer('mapping-input-fills').data.length);
  el('mapping-mode').value='classic';
  const accepted=await (await realFetch(origin+'/api/mapping/state')).json();
  assert.ok(accepted.automatic_review.automatic);assert.ok(accepted.groups.every(g=>g.confirmed&&g.decision!=='unresolved'));
  assert.ok(el('mapping-group-rows').children.length);await el('mapping-review-open').emit('click');
  assert.ok(el('mapping-review-dialog').open);
  assert.ok(el('mapping-summary').textContent.includes('Automatic review'));
  assert.ok(el('mapping-group-rows').children.every(row=>row.children[0].children[0].disabled));
  // Preview works directly after loading, without analysis or confirmation.
  await el('mapping-preview').emit('click');
  await waitFor(()=>el('scene-title').textContent==='Validated preview'&&layer('slice-fills')?.data.length,'Mapping preview');
  assert.ok(layer('dhe-faces')?.data.length===undefined||layer('dhe-faces').data.length>0);
  const previewState=await (await realFetch(origin+'/api/mapping/state')).json();assert.ok(previewState.has_preview);assert.equal(previewState.has_result,false);
  await el('mapping-discard').emit('click');assert.equal((await (await realFetch(origin+'/api/mapping/state')).json()).has_preview,false);
  // Optional edits remain available after automatic review.
  el('mapping-edit-sources').value='s1';el('mapping-edit-targets').value='t1';
  el('mapping-edit-decision').value='map';await el('mapping-regroup').emit('click');
  await el('mapping-save-review').emit('click');
  await el('mapping-build').emit('click');
  const report=await (await realFetch(origin+'/api/mapping/report')).json();assert.equal(report.endpoint_validation,'passed');
  assert.ok(report.routing.every(g=>g.builder==='classic'));
  await el('export-duality').emit('click');const mappingExport=JSON.parse(await downloads.at(-1).blob.text());
  assert.equal(mappingExport.metadata.workspace,'mapping');assert.equal(mappingExport.metadata.mapping_report.endpoint_validation,'passed');
  for(const kind of ['primal-edges','dual-edges']){
    await el('export-'+kind).emit('click');assert.ok(downloads.at(-1).filename.endsWith('.obj'));
    const text=await downloads.at(-1).blob.text(),meta=JSON.parse(text.split('\n').find(line=>line.startsWith('# {')).slice(2));
    assert.equal(meta.workspace,'mapping');assert.equal(meta.snapshot_id,mappingExport.metadata.snapshot_id);
  }
  // Force a pair of moving cells to cross and verify the approved Hybrid retry
  // appears as actual final routing in the UI, with valid Smooth cells retained.
  el('mapping-mode').value='hybrid';
  for(const [source,target] of [['s1','t2'],['s2','t1']]){
    el('mapping-edit-sources').value=source;el('mapping-edit-targets').value=target;
    el('mapping-edit-decision').value='map';await el('mapping-regroup').emit('click');
  }
  await el('mapping-build').emit('click');
  const hybrid=await (await realFetch(origin+'/api/mapping/report')).json();
  assert.equal(hybrid.endpoint_validation,'passed');assert.ok(hybrid.retry_history.length);
  assert.ok(hybrid.routing.some(g=>g.fallback&&g.initial_builder==='smooth'&&g.builder==='classic'));
  assert.ok(hybrid.routing.some(g=>g.builder==='smooth'));
  assert.ok(el('mapping-summary').textContent.includes('retried through Classic'));
  assert.ok(el('mapping-group-rows').children.some(row=>row.children[5].textContent.includes('(initially smooth)')));
  assert.deepEqual(await (await realFetch(origin+'/api/scene')).json(),previous);
  el('show-3d').checked=true;await el('show-3d').emit('change');
  await tabs.find(t=>t.dataset.tab==='inspect').emit('click');
  el('edge-select').value=el('edge-select').children[1].value;await el('edge-select').emit('change');
  assert.ok(el('edge-details').textContent.includes('source')||el('edge-details').textContent.includes('target'));
  const mappingEdge=el('edge-select').value;await el('focus-cell').emit('click');assert.equal(el('edge-select').value,mappingEdge);
  await tabs.find(t=>t.dataset.tab==='generalize').emit('click');
  assert.equal(el('mapping-tab').hidden,true);assert.equal(context.window.dheAtlas.revision,revision);
  assert.equal(layer('mapping-input-fills'),undefined);
  console.log('Mapping client integration passed: full topology download, cell zoom, automatic review, optional regrouping, Classic slicing, Hybrid retries/final routing, inspector metadata, and restoration of Generalize.');
}
