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
const tabs=['generalize','inspect'].map(tab=>{const e=new Element('button');e.dataset.tab=tab;return e;});
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
  easeTo(options){this.pitch=options.pitch??this.pitch;this.bearing=options.bearing??this.bearing;}
  setStyle(){this.sources={};this.layers={};setTimeout(()=>this.emit('style.load'),1);}
  getBounds(){const b=this.bounds;return {getWest:()=>b[0],getSouth:()=>b[1],getEast:()=>b[2],getNorth:()=>b[3],extend(){return this;}};}
}
class Marker{setLngLat(){return this;}addTo(){return this;}remove(){}}
class Overlay{constructor(props){this.props=props;overlays.push(this);}setProps(props){this.props=props;}}
class Layer{constructor(props){this.props=props;this.id=props.id;}}
const realFetch=globalThis.fetch;let submitted;
async function fetchLocal(path,options){
  if(path==='/api/apply'){submitted=JSON.parse(options.body);return {ok:true,json:async()=>({job_id:'failure-test'})};}
  if(path==='/api/jobs/failure-test')return {ok:true,json:async()=>({status:'failed',error:'Test construction rejection'})};
  return realFetch(new URL(path,origin),options);
}
const context=vm.createContext({console,document,window:{},location:{origin},fetch:fetchLocal,Option:OptionElement,URLSearchParams,FormData,setTimeout,clearTimeout,setInterval:()=>0});
const source=await fs.readFile(new URL('./web_generalization/app.js',import.meta.url),'utf8');
const module=new vm.SourceTextModule(source,{context});
await module.link(async specifier=>{
  const exports=specifier==='maplibre-gl'?{Map:FakeMap,Marker,NavigationControl:class{},ScaleControl:class{}}:
    specifier.includes('/maplibre@')?{MapLibreOverlay:Overlay}:Object.fromEntries(['SolidPolygonLayer','PathLayer','LineLayer','ScatterplotLayer','TextLayer'].map(key=>[key,Layer]));
  return new vm.SyntheticModule(Object.keys(exports),function(){for(const [key,value] of Object.entries(exports))this.setExport(key,value);},{context});
});
await module.evaluate();
async function waitFor(predicate,label){const deadline=Date.now()+15000;while(Date.now()<deadline){if(predicate())return;await pause(25);}throw new Error(label+': '+elements.get('status').textContent);}
const el=id=>elements.get(id);
const layer=id=>overlays[0].props.layers.find(layer=>layer.id===id)?.props;
await waitFor(()=>context.window.dheAtlas?.loaded&&layer('slice-fills')?.data.length,'App boot');
assert.ok(layer('dhe-faces').data.length);assert.ok(layer('primal-vertices').data.length);
const revision=context.window.dheAtlas.revision;
el('show-3d').checked=false;await el('show-3d').emit('change');
await waitFor(()=>!layer('dhe-faces')&&layer('slice-fills')?.data.length,'Flat mode');
assert.equal(maps[0].maxPitch,0);assert.ok(layer('slice-fills').data.every(d=>d.rings.every(r=>r.every(p=>p[2]===.04))));
el('show-3d').checked=true;await el('show-3d').emit('change');assert.equal(maps[0].pitch,55);
el('dual').checked=true;await el('dual').emit('input');assert.ok(layer('dual-vertices').data.length);assert.ok(layer('dual-edges').data.every(d=>!d.exterior));
el('dual-size').value='9';await el('dual-size').emit('input');assert.equal(layer('dual-vertices').getRadius,9);
el('exaggeration').value='6';await el('exaggeration').emit('input');const edge=layer('primal-edges').data.find(d=>d.source[2]>0);assert.equal(layer('primal-edges').getSourcePosition(edge)[2],edge.source[2]*6);
el('slice-step').value='.5';await el('slice-step').emit('change');const top=Number(el('slice-value').value);await el('lower').emit('click');
await waitFor(()=>el('slice-label').textContent.includes(String(top-.5)),'Slicing step');
assert.equal(el('slice-slider').step,.5);
el('orientation').value='Vertical';await el('orientation').emit('change');el('show-3d').checked=false;await el('show-3d').emit('change');
await waitFor(()=>el('slice-label').textContent.startsWith('Vertical')&&layer('slice-outlines')?.data.length,'Vertical sections');
await pause(150);assert.equal(layer('slice-fills').data.length,0);assert.ok(layer('slice-outlines').data.every(d=>d.path.every(p=>p[2]===.04)));
el('orientation').value='Inclined';await el('orientation').emit('change');await waitFor(()=>el('slice-label').textContent.startsWith('Inclined')&&layer('slice-fills')?.data.length,'Inclined sections');
el('basemap').value='blank';await el('basemap').emit('change');await pause(25);assert.ok(layer('slice-fills'));
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
console.log('Client integration passed: boot, real sections, 3D/flat modes, primal/dual, sizing, exaggeration, step, vertical/inclined projection, background changes, selection/box drawing, OSM BBOX workflow, and failed-operation preservation.');
