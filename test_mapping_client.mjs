/** Mapping checkbox/control flow without a browser; geometry is tested by test_mapping.py. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';

const html=await fs.readFile(new URL('./web_generalization/index.html',import.meta.url),'utf8');
class Element{
  constructor(tag='div'){this.tagName=tag.toUpperCase();this.value='';this.checked=false;this.disabled=false;this.hidden=false;this.children=[];}
  replaceChildren(...children){this.children=[];this.append(...children);}
  append(...children){this.children.push(...children);if(this.tagName==='SELECT'&&!this.value&&children.length)this.value=children[0].value;}
  showModal(){this.open=true;}close(){this.open=false;}
}
const elements=new Map();
for(const match of html.matchAll(/<([\w-]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){
  const e=new Element(match[1]);e.value=match[2].match(/\bvalue="([^"]*)"/)?.[1]||'';
  e.checked=/\bchecked\b/.test(match[2]);elements.set(match[3],e);
}
class Option extends Element{constructor(text,value){super('option');this.textContent=text;this.value=value;}}
const el=id=>{assert.ok(elements.has('mapping-'+id),id);return elements.get('mapping-'+id);};
const document={getElementById:id=>{assert.ok(elements.has(id),id);return elements.get(id);},createElement:tag=>new Element(tag)};
const clone=value=>structuredClone(value);
const state={revision:0,layers:{},groups:[],suggestions:{edges:[],proximity_candidates:[]},automatic_review:{},
  settings:{mode:'hybrid',match_by_id:false,source_z:5,target_z:15,geometry_tolerance:.01,overlap_fraction:.25,search_distance:10,
    source_label:'',target_label:'',source_scale:null,target_scale:null,source_time:'',target_time:''},
  default_paths:{source:'source.geojson',target:'target.geojson'},has_preview:false,has_result:false,preview_id:null};
const layer=side=>({name:side,crs:'EPSG:2180',count:2,notes:[],overlaps:[],features:{type:'FeatureCollection',features:[0,1].map(i=>({
  type:'Feature',geometry:{type:'Polygon',coordinates:[]},properties:{uid:(side==='source'?'s':'t')+(i+1),original_id:['A','B'][i]}}))}});
const groups=only=>[0,1].map(i=>({id:'g'+(i+1),sources:['s'+(i+1)],targets:['t'+(only?2-i:i+1)],decision:'map',confirmed:true,anchor:null}));
const requests=[];let failed=false,gate=null,jobFailed=false;
async function api(path,body){
  if(path==='/api/mapping/state')return clone(state);
  if(path.startsWith('/api/mapping/scene'))return {revision:state.revision,halfedges:[]};
  if(path==='/api/jobs/job')return jobFailed?{status:'failed',error:'Test failed matching'}:{status:'complete'};
  requests.push({path,body:clone(body)});
  if(gate)await gate;
  jobFailed=failed;
  if(!failed){
    const action=path.split('/').at(-1),p=body.parameters;
    if(p.settings)Object.assign(state.settings,p.settings);
    if(action==='load')state.layers[p.side]=layer(p.side);
    if(Object.keys(state.layers).length===2)state.groups=groups(state.settings.match_by_id);
    if(action==='analyze'||action==='review')state.preview_id=null,state.has_preview=false;
    if(action==='build'){
      state.has_preview=p.preview;state.has_result=!p.preview||state.has_result;
      state.preview_id=p.preview?'preview':null;
    }
    state.revision++;
  }
  return {job_id:'job'};
}
const statuses=[];
const context=vm.createContext({document,window:{},Option,URL,Blob,setTimeout,fetch:()=>{throw new Error('Unexpected upload');}});
vm.runInContext(await fs.readFile(new URL('./web_generalization/mapping.js',import.meta.url),'utf8'),context);
const controller=context.window.createMapping({api,status:(message,error)=>statuses.push({message,error}),setBusy(){},changed(){},fitInputs(){}});
await controller.activate();
assert.equal(el('match-by-id').checked,false);
el('match-by-id').checked=true;el('match-by-id').value='false';
let release;gate=new Promise(resolve=>{release=resolve;});
const pending=el('match-by-id').onchange();await Promise.resolve();
assert.equal(el('match-by-id').disabled,true);release();await pending;gate=null;
assert.equal(requests.at(-1).path,'/api/mapping/analyze');
assert.equal(requests.at(-1).body.parameters.settings.match_by_id,true);
assert.equal(el('match-by-id').disabled,false);
assert.equal(el('overlap-fraction').disabled,true);assert.equal(el('search-distance').disabled,true);
assert.equal(el('geometry-tolerance').disabled,false);assert.equal(el('regroup').disabled,true);
assert.equal(controller.groups.length,0); // Can choose the mode before importing layers.
for(const side of ['source','target'])await el('default-'+side).onclick();
assert.equal(controller.groups.length,2);
assert.ok(el('group-rows').children.every(row=>row.children[4].children[0].disabled));
const before=requests.length;await el('build').onclick();
assert.deepEqual(requests.slice(before).map(r=>r.path),['/api/mapping/review','/api/mapping/build']);
assert.equal(requests[before].body.parameters.groups.length,2); // One build for every ID group.
assert.equal(requests[before].body.parameters.settings.match_by_id,true);
state.layers.source.overlaps=[{ids:['s1','s2'],area:2}];await controller.refresh();
const overlap=el('overlaps').children[0];
assert.equal(overlap.children.find(e=>e.textContent==='Union features').disabled,true);
assert.equal(overlap.children.find(e=>e.textContent==='Assign overlap').disabled,false);
state.layers.source.overlaps=[];
el('match-by-id').checked=false;el('match-by-id').value='true';await el('match-by-id').onchange();
assert.equal(requests.at(-1).body.parameters.settings.match_by_id,false);
assert.equal(el('regroup').disabled,false);assert.equal(el('search-distance').disabled,false);
assert.ok(el('group-rows').children.every(row=>!row.children[4].children[0].disabled));
failed=true;el('match-by-id').checked=true;await el('match-by-id').onchange();
assert.equal(el('match-by-id').checked,false);assert.equal(el('regroup').disabled,false);
assert.ok(statuses.at(-1).error);assert.equal(state.settings.match_by_id,false);
failed=false;state.settings.match_by_id=true;await controller.refresh();
assert.equal(el('match-by-id').checked,true);assert.equal(el('split-group').disabled,true);
console.log('Mapping client passed: checkbox serialization, busy state, pre-import setting, batch build, disabled ID edits/union, mode restoration, and failure recovery.');
