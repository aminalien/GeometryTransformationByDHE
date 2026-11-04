import * as maplibregl from 'maplibre-gl';
import {MapLibreOverlay} from 'https://esm.sh/@deck.gl/maplibre@9.4.1?external=@deck.gl/core,maplibre-gl';
import {SolidPolygonLayer,PathLayer,LineLayer,ScatterplotLayer,TextLayer} from 'https://esm.sh/@deck.gl/layers@9.4.1?external=@deck.gl/core';

const $=id=>document.getElementById(id);
const empty={type:'FeatureCollection',features:[]};
const state={scene:null,preview:null,section:null,revision:0,selected:new Set(),edge:null,busy:false,
  sliceSerial:0,sliceTimer:null,mapReady:false,miniReady:false,mode:null,drag:null,drawBounds:null,
  camera:null,modeCamera:null,config:null,upload:null,markers:[],custom:null,background:'osm',fitOnNext:true};
const help={simplification:'Reduce boundary detail with a tolerance in metres.',smoothing:'Round corners using the existing Chaikin construction.',point:'Collapse to a point visible from every source boundary vertex.',polyline:'Collapse to a verified interior skeleton ridge or the existing fallback.',merging:'Grow the winner smoothly to the union of touching features.',aggregation:'Grow separate sources to one convex-hull target, retaining their lower DHE cells.'};
function status(message,error=false){$('status').textContent=message;$('status').style.color=error?'#ffd4b2':'';}
function number(id){const value=Number($(id).value);if(!Number.isFinite(value))throw new Error('Enter a finite value for '+id.replaceAll('-',' '));return value;}
function color(a){return `rgb(${a[0]},${a[1]},${a[2]})`;}
function displayScene(){return state.preview||state.scene;}
function baseStyle(kind){
  let tiles=[],attribution='',tileSize=256;
  if(kind==='osm'){tiles=['https://tile.openstreetmap.org/{z}/{x}/{y}.png'];attribution='© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>';}
  if(kind==='satellite'){tiles=['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'];attribution='Tiles © Esri — <a href="https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer" target="_blank" rel="noopener">World Imagery contributors</a>';}
  if(kind.startsWith('google-'))tiles=[location.origin+`/api/google/tiles/${kind.slice(7)}/{z}/{x}/{y}`];
  if(kind==='custom'&&state.custom){tiles=[state.custom.url];attribution=state.custom.credit.replace(/[<>]/g,'');}
  if(kind==='light'||kind==='dark')return `https://tiles.openfreemap.org/styles/${kind==='light'?'positron':'dark'}`;
  return {version:8,sources:tiles.length?{background:{type:'raster',tiles,tileSize,attribution,maxzoom:kind==='osm'?19:22}}:{},
    layers:[{id:'paper',type:'background',paint:{'background-color':kind==='dark'?'#152b32':'#f5f7f4'}},...(tiles.length?[{id:'background',type:'raster',source:'background',paint:{'raster-opacity':.95}}]:[])]};
}
const map=new maplibregl.Map({container:'map',style:baseStyle('osm'),center:[17.0123,51.1307],zoom:17.5,pitch:55,bearing:-20,antialias:true,maxPitch:80});
const mini=new maplibregl.Map({container:'mini-map',style:baseStyle('osm'),center:[17.0123,51.1307],zoom:17,pitch:0,bearing:0,attributionControl:false});
map.addControl(new maplibregl.NavigationControl({visualizePitch:true}),'top-right');
map.addControl(new maplibregl.ScaleControl({unit:'metric'}),'bottom-left');
const overlay=new MapLibreOverlay({interleaved:true,layers:[]});
map.addControl(overlay);
map.on('style.load',()=>{state.mapReady=true;addDrawingSource(map);render();});
mini.on('style.load',()=>{state.miniReady=true;addMiniLayers();addDrawingSource(mini);updateMini();});
let lastTileWarning=0;
for(const m of [map,mini])m.on('error',event=>{
  console.warn('Map layer:',event.error?.message);
  if(Date.now()-lastTileWarning>10000){lastTileWarning=Date.now();$('map-warning').hidden=false;$('map-warning').textContent='A background layer could not load. DHE geometry remains available; try another background.';}
});

async function api(path,body){
  const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  let data;try{data=await response.json();}catch{throw new Error('The local server returned an unreadable response.');}
  if(!response.ok){
    if(response.status===409)await refreshScene(false);
    const detail=Array.isArray(data.detail)?data.detail.map(x=>x.msg).join('; '):data.detail;
    throw new Error(detail||'Request failed.');
  }
  return data;
}
function setBusy(busy,label='Constructing DHE…'){
  state.busy=busy;$('busy-overlay').hidden=!busy;$('busy-label').textContent=label;
  for(const id of ['apply','preview','undo','load-default','load-upload','draw-bbox','open-import','history'])$(id).disabled=busy;
  for(const id of ['operation','dz','tolerance','iterations','ratio','winner','initial-height'])$(id).disabled=busy;
}
async function waitJob(id){
  for(;;){
    const job=await api('/api/jobs/'+id);
    if(job.status==='failed'){
      if(job.diagnostic){$('notes').textContent=JSON.stringify(job.diagnostic,null,2);$('notes-title').textContent='Operation diagnostics';}
      throw new Error(job.error);
    }
    if(job.status==='complete')return job.result;
    await new Promise(resolve=>setTimeout(resolve,250));
  }
}
async function runJob(path,parameters,label){
  if(state.busy)return;
  setBusy(true,label);status(label);
  try{
    const {job_id}=await api(path,{revision:state.revision,...parameters});
    const result=await waitJob(job_id);
    if(result.preview_id){
      state.preview=await api('/api/scene?preview_id='+encodeURIComponent(result.preview_id));
      resetSlice(true);$('discard-preview').hidden=false;render();
      status('Validated preview. Apply to add this transition to history.');
    }else{
      if(['/api/load-default','/api/load-upload','/api/osm'].includes(path)){state.selected.clear();state.fitOnNext=true;}
      state.preview=null;await refreshScene(true);status('Ready · '+(state.scene.operation?state.scene.operation.name.replaceAll('_',' '):'initial DHE extrusion'));
    }
    return result;
  }catch(error){status(error.message,true);$('import-message').textContent=error.message;throw error;}
  finally{setBusy(false);}
}
function guard(action){return (...args)=>Promise.resolve().then(()=>action(...args)).catch(error=>status(error.message,true));}

async function refreshScene(reset=false){
  const response=await fetch('/api/scene');if(response.status===404)return;
  if(!response.ok)throw new Error('Could not refresh the scene.');
  const scene=await response.json();const changed=!state.scene||state.scene.history[0]?.step_id!==scene.history[0]?.step_id||state.fitOnNext;
  state.scene=scene;state.preview=null;state.revision=scene.revision;
  const active=new Set(scene.features.features.map(f=>f.properties.index));state.selected=new Set([...state.selected].filter(k=>active.has(k)));
  state.edge=null;$('discard-preview').hidden=true;
  $('dataset-name').textContent=scene.dataset;$('scene-subtitle').textContent=`${scene.cells} cells · transition Z ${scene.levels.z_b}–${scene.levels.z_t}`;
  $('model-count').textContent=`${scene.features.features.length} current features · ${scene.cells} retained cells`;
  $('data-details').textContent=`${scene.crs} · ${scene.dataset}. Coordinates are georeferenced; Z is a transition coordinate.`;
  $('notes').replaceChildren();
  for(const note of scene.notes){const p=document.createElement('p');p.textContent=note;$('notes').append(p);}
  if(!scene.notes.length)$('notes').textContent='No excluded records or construction notes.';
  $('notes-title').textContent=`Import & construction notes (${scene.notes.length})`;
  $('history').replaceChildren(...scene.history.map(h=>new Option(`${h.index} · ${h.label}`,h.index)));
  $('history').value=String(scene.cursor);
  updateFeatureList();updateEdges();updateMini();render();
  if(reset||!state.section)resetSlice(true);else requestSlice();
  if(changed){fitData();state.fitOnNext=false;}
  return scene;
}

function feature(k){return state.scene?.features.features.find(f=>f.properties.index===Number(k));}
function selectFeature(k,add=false){
  if(state.busy)return;const f=feature(k);
  if(!f){status('This is a retained historical cell. Choose a current feature from the selection map.');return;}
  if(f.properties.kind!=='Polygon'){status('Point and polyline symbols are terminal; choose a current polygon.');return;}
  discardPreview();
  if(add){if(state.selected.has(k))state.selected.delete(k);else state.selected.add(k);}
  else state.selected=new Set(state.selected.size===1&&state.selected.has(k)?[]:[k]);
  updateFeatureList();updateMini();render();
}
function updateFeatureList(){
  $('feature-list').replaceChildren();
  for(const f of state.scene?.features.features||[]){
    const p=f.properties;const row=document.createElement('div');row.className='feature-row'+(state.selected.has(p.index)?' selected':'');
    const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=state.selected.has(p.index);checkbox.disabled=p.kind!=='Polygon';checkbox.tabIndex=-1;
    const swatch=document.createElement('span');swatch.className='swatch';swatch.style.background=color(p.color);
    const name=document.createElement('span');name.className='feature-name';name.textContent=`${p.index} · ${p.original_id}`;name.title=p.name;
    const kind=document.createElement('span');kind.className='kind';kind.textContent=p.kind==='Polygon'?'':p.kind;
    row.append(checkbox,swatch,name,kind);row.onclick=()=>selectFeature(p.index,true);$('feature-list').append(row);
  }
  $('selection-count').textContent=`${state.selected.size} selected`;
  const previous=$('winner').value;$('winner').replaceChildren(...[...state.selected].sort((a,b)=>a-b).map(k=>new Option('Feature '+k,k)));
  if(state.selected.has(Number(previous)))$('winner').value=previous;
  $('indices').value=[...state.selected].sort((a,b)=>a-b).join(', ');
}
function discardPreview(){
  if(!state.preview)return;state.preview=null;state.edge=null;$('discard-preview').hidden=true;state.section=null;updateEdges();resetSlice(false);render();
}
function operationParams(){
  if(!state.selected.size)throw new Error('Select one or more current polygons first.');
  return {operation:$('operation').value,selected:[...state.selected].sort((a,b)=>a-b),dz:number('dz'),tolerance:number('tolerance'),
    iterations:number('iterations'),ratio:number('ratio'),winner:$('operation').value==='merging'?Number($('winner').value):null};
}
function operationFields(){
  const op=$('operation').value;$('smoothing-fields').hidden=op!=='smoothing';$('tolerance-field').hidden=op!=='simplification';$('winner-field').hidden=op!=='merging';$('operation-help').textContent=help[op];
}

function addMiniLayers(){
  if(mini.getSource('current'))return;
  mini.addSource('current',{type:'geojson',data:empty});
  mini.addLayer({id:'current-fill',type:'fill',source:'current',filter:['==',['geometry-type'],'Polygon'],paint:{'fill-color':['get','css_color'],'fill-opacity':.3}});
  mini.addLayer({id:'current-line',type:'line',source:'current',paint:{'line-color':['get','css_color'],'line-width':1.3}});
  mini.addLayer({id:'current-point',type:'circle',source:'current',filter:['==',['geometry-type'],'Point'],paint:{'circle-color':['get','css_color'],'circle-radius':5,'circle-stroke-color':'#fff','circle-stroke-width':1}});
}
function updateMini(){
  if(!state.miniReady||!mini.getSource('current')||!state.scene)return;
  const collection={...state.scene.features,features:state.scene.features.features.map(f=>({...f,properties:{...f.properties,css_color:color(f.properties.color)}}))};
  mini.getSource('current').setData(collection);
  const selected=['in',['get','index'],['literal',[...state.selected]]];
  mini.setPaintProperty('current-fill','fill-opacity',['case',selected,.65,.22]);
  mini.setPaintProperty('current-line','line-width',['case',selected,3,1.2]);
  for(const marker of state.markers)marker.remove();state.markers=[];
  if(state.scene.features.features.length<=300)for(const f of state.scene.features.features){
    const el=document.createElement('span');el.textContent=f.properties.index;el.style.cssText='font-size:10px;font-weight:700;color:#163e39;text-shadow:0 0 3px white;pointer-events:none';
    state.markers.push(new maplibregl.Marker({element:el}).setLngLat(f.properties.label_position).addTo(mini));
  }
}
mini.on('click',event=>{
  if(state.mode)return;
  const hits=mini.queryRenderedFeatures(event.point,{layers:['current-fill','current-line','current-point'].filter(id=>mini.getLayer(id))});
  if(hits.length)selectFeature(Number(hits[0].properties.index),event.originalEvent.shiftKey);
});
mini.on('mousemove',event=>{if(!state.mode)mini.getCanvas().style.cursor=mini.queryRenderedFeatures(event.point,{layers:['current-fill'].filter(id=>mini.getLayer(id))}).length?'pointer':'';});

function zpoint(p,flat=false){return [p[0],p[1],flat?0.04:p[2]*number('exaggeration')];}
function inspectEdge(id){
  const scene=displayScene();const edge=scene?.halfedges.find(e=>e.id===id);if(!edge)return;
  state.edge=id;$('edge-select').value=id;
  $('edge-details').textContent=`${edge.label} · raw ID ${edge.raw_id}\nCell ${edge.owner} · face ${edge.face}\nZ ${edge.source[2].toFixed(3)} → ${edge.target[2].toFixed(3)}\nNF: ${edge.nf??'—'} · S: ${edge.s??'—'}\nAdjacent: ${edge.adjacent??'exterior'}`;render();
}
function updateEdges(){
  const scene=displayScene();$('edge-select').replaceChildren(new Option('Choose an edge…',''),...(scene?.halfedges||[]).map(e=>new Option(`${e.label} · cell ${e.owner}`,e.id)));
  $('edge-details').textContent='Choose a face to inspect.';
}
function pickFace(faceId,event){
  if(state.mode)return;
  const face=displayScene().faces.find(f=>f.id===faceId);inspectEdge(face.edge);
  if($('inspect-tab').hidden)selectFeature(face.owner,Boolean(event?.srcEvent?.shiftKey));
}
function hover(info,label){
  if(!info.object||state.mode){$('tooltip').hidden=true;return;}
  const tooltip=$('tooltip');tooltip.textContent=label(info.object);tooltip.style.left=`${info.x+12}px`;tooltip.style.top=`${info.y+12}px`;tooltip.hidden=false;
}
function render(){
  const scene=displayScene();if(!state.mapReady||!scene)return;
  const show3d=$('show-3d').checked&&!state.mode?.startsWith('osm');const primal=show3d&&$('primal').checked;
  const dual=show3d&&$('dual').checked;const chosen=scene.halfedges.find(e=>e.id===state.edge);const layers=[];
  const faceMap=new Map(scene.faces.map(f=>[f.id,f]));const opacity=number('opacity');
  if(primal){
    if($('fill').checked)layers.push(new SolidPolygonLayer({id:'dhe-faces',data:scene.triangles.filter(t=>t.face!==chosen?.face),_full3d:true,
      getPolygon:t=>t.polygon.map(p=>zpoint(p)),getFillColor:t=>faceMap.get(t.face).color,opacity,material:false,pickable:true,
      onClick:(info,event)=>info.object&&pickFace(info.object.face,event),onHover:info=>hover(info,t=>`Cell ${faceMap.get(t.face).owners.join(' / ')} · DHE face ${t.face}`)}));
    layers.push(new LineLayer({id:'primal-edges',data:scene.edges.slice(),getSourcePosition:e=>zpoint(e.source),getTargetPosition:e=>zpoint(e.target),
      getColor:e=>e.color,getWidth:e=>state.selected.has(e.owner)?2:1,widthUnits:'pixels',opacity:.85,pickable:true,
      onClick:info=>{if(info.object&&!state.mode)inspectEdge(info.object.edge);},onHover:info=>hover(info,e=>`Cell ${e.owner} · half-edge ${e.edge}`)}));
    layers.push(new ScatterplotLayer({id:'primal-vertices',data:scene.vertices.slice(),getPosition:d=>zpoint(d.position),getFillColor:d=>d.color,getRadius:1.8,radiusUnits:'pixels',opacity:.9}));
    if(chosen){
      layers.push(new SolidPolygonLayer({id:'selected-face',data:scene.triangles.filter(t=>t.face===chosen.face),_full3d:true,getPolygon:t=>t.polygon.map(p=>zpoint(p)),getFillColor:rgba(chosen.owner),opacity:.5,material:false}));
      layers.push(new LineLayer({id:'current-halfedge',data:[chosen],getSourcePosition:e=>zpoint(e.source),getTargetPosition:e=>zpoint(e.target),getColor:[235,43,54],getWidth:3,widthUnits:'pixels'}));
    }
  }
  if(dual){
    const nodes=scene.duals.filter(d=>$('exterior').checked||d.owner!==null);
    layers.push(new ScatterplotLayer({id:'dual-vertices',data:nodes,getPosition:d=>zpoint(d.position),getFillColor:d=>d.color,getRadius:number('dual-size'),radiusUnits:'pixels',stroked:true,getLineColor:[22,43,44],lineWidthUnits:'pixels',getLineWidth:1,pickable:true,onHover:info=>hover(info,d=>'Dual vertex · cell '+d.id)}));
    layers.push(new LineLayer({id:'dual-edges',data:scene.dual_edges.filter(d=>$('exterior').checked||!d.exterior),getSourcePosition:d=>zpoint(d.source),getTargetPosition:d=>zpoint(d.target),
      getColor:d=>d.exterior?[145,67,194]:d.source[2]===d.target[2]?[38,156,116]:[229,131,57],getWidth:number('dual-width'),widthUnits:'pixels',pickable:true,
      onClick:info=>info.object&&inspectEdge(scene.faces.find(f=>f.id===info.object.face).edge),onHover:info=>hover(info,d=>'Dual interface · '+d.owners.join(' ↔ '))}));
    if($('dual-labels').checked)layers.push(new TextLayer({id:'dual-labels',data:nodes,getPosition:d=>zpoint(d.position),getText:d=>'d'+d.id,getSize:12,getPixelOffset:[9,-9],getColor:[20,45,44],getTextAnchor:'start'}));
  }
  const section=state.section;
  if(section&&section.revision===state.revision&&section.preview_id===(state.preview?.preview_id||null)){
    const flat=!show3d;const polygons=[],paths=[],points=[];
    for(const f of section.features.features){
      const g=f.geometry,p=f.properties;
      if(g.type==='Polygon'){
        if(!flat||Math.abs(section.normal[2])>1e-12)polygons.push({rings:g.coordinates.map(r=>r.map(q=>zpoint(q,flat))),...p});
        for(const ring of g.coordinates)paths.push({path:ring.map(q=>zpoint(q,flat)),...p});
      }else if(g.type==='Point')points.push({position:zpoint(g.coordinates,flat),...p});
      else paths.push({path:g.coordinates.map(q=>zpoint(q,flat)),...p});
    }
    layers.push(new SolidPolygonLayer({id:'slice-fills',data:polygons,_full3d:true,getPolygon:d=>d.rings,getFillColor:d=>d.color,opacity:show3d?0.73:0.58,material:false,pickable:true,onClick:info=>info.object&&selectFeature(info.object.owner,true),onHover:info=>hover(info,d=>`Section · cell ${d.owner}`)}));
    layers.push(new PathLayer({id:'slice-outlines',data:paths,getPath:d=>d.path,getColor:d=>d.color,getWidth:2,widthUnits:'pixels'}));
    layers.push(new ScatterplotLayer({id:'slice-points',data:points,getPosition:d=>d.position,getFillColor:d=>d.color,getRadius:5,radiusUnits:'pixels',stroked:true,getLineColor:[255,255,255],getLineWidth:1,lineWidthUnits:'pixels'}));
    if(show3d&&section.outline.length>=3){
      const ring=section.outline.map(q=>zpoint(q));
      layers.push(new SolidPolygonLayer({id:'cutting-plane',data:[ring],_full3d:true,getPolygon:d=>d,getFillColor:[133,73,163],opacity:.075,material:false}));
      layers.push(new PathLayer({id:'plane-outline',data:[[...ring,ring[0]]],getPath:d=>d,getColor:[133,73,163,150],getWidth:1,widthUnits:'pixels'}));
    }
  }
  overlay.setProps({layers});
  $('scene-title').textContent=state.preview?'Validated preview':show3d?'DHE transition space':'Projected cross-section';
  for(const id of ['primal','dual','fill','opacity','exaggeration'])$(id).disabled=!$('show-3d').checked;
}
function rgba(k){return displayScene().duals.find(d=>d.owner===k)?.color||[26,120,105,255];}

function sliceHeading(){
  const orientation=$('orientation').value,value=number('slice-value');
  $('slice-label').textContent=`${orientation} · ${orientation==='Horizontal'?'Z':'offset'} = ${Number(value.toFixed(5))}`;
}
function resetSlice(toTop){
  const scene=displayScene();if(!scene)return;
  if(toTop){$('orientation').value='Horizontal';$('slice-value').value=scene.levels.z_t;}
  $('plane-angles').hidden=$('orientation').value==='Horizontal';$('tilt-field').hidden=$('orientation').value==='Vertical';
  if($('orientation').value==='Horizontal'){
    $('slice-slider').min=scene.levels.z_b;$('slice-slider').max=scene.levels.z_t;
    $('slice-value').value=Math.max(scene.levels.z_b,Math.min(scene.levels.z_t,number('slice-value')));
  }
  $('slice-slider').value=$('slice-value').value;requestSlice();
}
function requestSlice(){
  clearTimeout(state.sliceTimer);const serial=++state.sliceSerial;
  state.sliceTimer=setTimeout(async()=>{
    if(!state.scene)return;
    try{
      sliceHeading();const section=await api('/api/slice',{revision:state.revision,preview_id:state.preview?.preview_id||null,
        orientation:$('orientation').value,value:number('slice-value'),tilt:number('tilt'),azimuth:number('azimuth')});
      if(serial!==state.sliceSerial)return;
      state.section=section;$('slice-slider').min=section.bounds[0];$('slice-slider').max=section.bounds[1];$('slice-slider').value=section.value;
      $('slice-value').min=section.bounds[0];$('slice-value').max=section.bounds[1];
      const count=section.features.features.length;
      $('slice-summary').textContent=`${count} actual section part${count===1?'':'s'}${section.missing.length?' · Missing terminal features: '+section.missing.join(', '):''}${!$('show-3d').checked?' · Projected onto XY':''}`;
      render();
    }catch(error){if(serial===state.sliceSerial)status(error.message,true);}
  },75);
}
function moveSlice(direction){
  const step=number('slice-step');if(step<=0)throw new Error('Step size must be positive.');
  const lower=Number($('slice-slider').min),upper=Number($('slice-slider').max);
  const value=Math.max(lower,Math.min(upper,number('slice-value')+direction*step));
  $('slice-value').value=Number(value.toPrecision(12));$('slice-slider').value=value;requestSlice();
}
function fitData(){
  if(!state.scene)return;const b=state.scene.bounds;
  const width=$('map').clientWidth||800;
  map.fitBounds([[b[0],b[1]],[b[2],b[3]]],{padding:{top:75,left:Math.min(50,width*.1),right:Math.min(250,width*.3),bottom:40},maxZoom:20,duration:400});
  mini.fitBounds([[b[0],b[1]],[b[2],b[3]]],{padding:22,maxZoom:20,duration:0});
}

function addDrawingSource(m){
  if(m.getSource('draw-box'))return;m.addSource('draw-box',{type:'geojson',data:empty});
  m.addLayer({id:'draw-box-fill',type:'fill',source:'draw-box',paint:{'fill-color':'#19826f','fill-opacity':.16}});
  m.addLayer({id:'draw-box-line',type:'line',source:'draw-box',paint:{'line-color':'#136b5e','line-width':2,'line-dasharray':[3,2]}});
}
function boxGeometry(a,b){const west=Math.min(a.lng,b.lng),east=Math.max(a.lng,b.lng),south=Math.min(a.lat,b.lat),north=Math.max(a.lat,b.lat);return {bounds:[west,south,east,north],data:{type:'Feature',properties:{},geometry:{type:'Polygon',coordinates:[[[west,south],[east,south],[east,north],[west,north],[west,south]]]}}};}
function endMode(){
  state.mode=null;state.drag=null;state.drawBounds=null;map.dragPan.enable();mini.dragPan.enable();map.getCanvas().style.cursor='';mini.getCanvas().style.cursor='';
  for(const m of [map,mini])m.getSource('draw-box')?.setData(empty);
  $('mode-banner').hidden=true;
  if(state.modeCamera){map.easeTo({...state.modeCamera,duration:300});state.modeCamera=null;}
  render();
}
function beginMode(mode){
  if(state.busy)return;endMode();state.mode=mode;$('mode-banner').hidden=false;
  if(mode==='osm'){
    state.modeCamera={pitch:map.getPitch(),bearing:map.getBearing()};map.easeTo({pitch:0,bearing:0,duration:300});map.dragPan.disable();map.getCanvas().style.cursor='crosshair';
    $('mode-banner').textContent='Drag a rectangle to download OSM footprints. Esc cancels.';
  }else{
    $('mini-panel').hidden=false;$('show-mini').hidden=true;$('mini-panel').classList.add('expanded');mini.resize();mini.dragPan.disable();mini.getCanvas().style.cursor='crosshair';
    $('mode-banner').textContent='Drag a rectangle on the Current top map. Esc cancels.';
  }
  render();
}
function bindDraw(m,mode){
  m.on('mousedown',event=>{if(state.mode!==mode||event.originalEvent.button!==0)return;event.preventDefault();state.drag=event.lngLat;});
  m.on('mousemove',event=>{if(state.mode!==mode||!state.drag)return;m.getSource('draw-box')?.setData(boxGeometry(state.drag,event.lngLat).data);});
  m.on('mouseup',guard(async event=>{
    if(state.mode!==mode||!state.drag)return;
    const result=boxGeometry(state.drag,event.lngLat);state.drag=null;
    if(result.bounds[2]-result.bounds[0]<1e-7||result.bounds[3]-result.bounds[1]<1e-7){endMode();return;}
    if(mode==='box'){
      const answer=await api('/api/select-box',{revision:state.revision,bounds:result.bounds});
      state.selected=new Set(answer.indices);endMode();discardPreview();updateFeatureList();updateMini();render();status(`Selected ${answer.indices.length} current polygons.`);
    }else{
      state.drawBounds=result.bounds;state.mode='osm-ready';
      $('mode-banner').replaceChildren(document.createTextNode('Area selected. '));
      const download=document.createElement('button');download.textContent='Download footprints';download.onclick=guard(async()=>{
        const bounds=state.drawBounds;endMode();
        await runJob('/api/osm',{bounds,initial_height:number('initial-height')},'Downloading complete OSM footprints…');
      });
      const cancel=document.createElement('button');cancel.textContent='Cancel';cancel.onclick=endMode;
      $('mode-banner').append(download,cancel);
    }
  }));
}
bindDraw(map,'osm');bindDraw(mini,'box');

async function changeBackground(kind){
  if(kind==='custom'&&!state.custom){$('custom-dialog').showModal();$('basemap').value=state.background;return;}
  if(kind.startsWith('google-')&&!state.config.google_available)throw new Error('Google backgrounds require a configured Map Tiles API key.');
  state.background=kind;state.mapReady=false;state.miniReady=false;$('map-warning').hidden=true;
  map.setStyle(baseStyle(kind));mini.setStyle(baseStyle(kind));
  $('google-attribution').hidden=!kind.startsWith('google-');
  if(kind.startsWith('google-'))await googleAttribution();
}
async function googleAttribution(){
  if(!state.background.startsWith('google-'))return;
  try{
    const b=map.getBounds();if(!$('mini-panel').hidden)b.extend(mini.getBounds());
    const params=new URLSearchParams({kind:state.background.slice(7),zoom:Math.round(map.getZoom()),north:b.getNorth(),south:b.getSouth(),east:b.getEast(),west:b.getWest()});
    const result=await api('/api/google/attribution?'+params);
    $('google-attribution').textContent='Google Maps · '+(result.copyright||'');
  }catch(error){$('google-attribution').textContent='Google Maps';status(error.message,true);}
}
map.on('moveend',googleAttribution);
mini.on('moveend',googleAttribution);

for(const button of document.querySelectorAll('[data-tab]'))button.onclick=()=>{
  for(const b of document.querySelectorAll('[data-tab]'))b.classList.toggle('active',b===button);
  $('generalize-tab').hidden=button.dataset.tab!=='generalize';$('inspect-tab').hidden=button.dataset.tab!=='inspect';
};
$('operation').onchange=()=>{discardPreview();operationFields();};
for(const id of ['dz','tolerance','iterations','ratio','winner'])$(id).addEventListener('input',discardPreview);
$('preview').onclick=guard(()=>runJob('/api/preview',operationParams(),'Planning and validating preview…'));
$('apply').onclick=guard(()=>runJob('/api/apply',operationParams(),'Constructing the next DHE level…'));
$('discard-preview').onclick=discardPreview;
$('clear-selection').onclick=()=>{if(state.busy)return;discardPreview();state.selected.clear();updateFeatureList();updateMini();render();};
$('select-all').onclick=()=>{if(state.busy)return;discardPreview();state.selected=new Set((state.scene?.features.features||[]).filter(f=>f.properties.kind==='Polygon').map(f=>f.properties.index));updateFeatureList();updateMini();render();};
$('select-indices').onclick=guard(()=>{
  if(state.busy)return;const selection=new Set();
  for(const token of $('indices').value.split(/[,;\s]+/).filter(Boolean)){
    const match=token.match(/^(\d+)(?:-(\d+))?$/);if(!match)throw new Error('Use indices such as 1, 3-6.');
    const a=Number(match[1]),b=Number(match[2]||a);if(b<a||b-a>10000)throw new Error('Use an increasing index range.');
    for(let k=a;k<=b;k++){if(feature(k)?.properties.kind!=='Polygon')throw new Error(`Feature ${k} is not a current polygon.`);selection.add(k);}
  }
  discardPreview();state.selected=selection;updateFeatureList();updateMini();render();
});
$('select-box').onclick=()=>state.scene&&beginMode('box');
$('draw-bbox').onclick=()=>beginMode('osm');
$('undo').onclick=guard(()=>state.scene?.cursor>0&&runJob('/api/history',{index:state.scene.cursor-1},'Restoring previous DHE level…'));
$('history').onchange=guard(()=>runJob('/api/history',{index:Number($('history').value)},'Restoring history…'));
$('fit').onclick=fitData;
$('hide-mini').onclick=()=>{$('mini-panel').hidden=true;$('show-mini').hidden=false;};
$('show-mini').onclick=()=>{$('mini-panel').hidden=false;$('show-mini').hidden=true;mini.resize();};
$('expand-mini').onclick=()=>{$('mini-panel').classList.toggle('expanded');mini.resize();};
$('show-3d').onchange=()=>{
  if(!$('show-3d').checked){state.camera={pitch:map.getPitch(),bearing:map.getBearing()};map.setMaxPitch(0);map.easeTo({pitch:0,bearing:0,duration:400});}
  else{map.setMaxPitch(80);map.easeTo({...state.camera||{pitch:55,bearing:-20},duration:400});state.camera=null;}
  render();requestSlice();
};
for(const id of ['primal','dual','fill','opacity','exaggeration','dual-size','dual-width','dual-labels','exterior'])$(id).addEventListener('input',guard(render));
$('edge-select').onchange=()=>inspectEdge($('edge-select').value);
for(const button of document.querySelectorAll('[data-hop]'))button.onclick=()=>{
  const edge=displayScene()?.halfedges.find(e=>e.id===state.edge);const target=edge?.[button.dataset.hop];
  if(target)inspectEdge(target);else status('This relationship reaches the exterior or no edge is selected.');
};
$('orientation').onchange=()=>{
  $('slice-value').value=$('orientation').value==='Horizontal'?(displayScene()?.levels.z_t||0):0;
  $('slice-slider').min=$('orientation').value==='Horizontal'?0:-100000;$('slice-slider').max=$('orientation').value==='Horizontal'?(displayScene()?.levels.z_t||5):100000;
  resetSlice(false);
};
$('slice-slider').oninput=()=>{$('slice-value').value=$('slice-slider').value;requestSlice();};
$('slice-value').oninput=guard(requestSlice);
for(const id of ['tilt','azimuth']){
  const update=guard(()=>{$('slice-value').value=0;requestSlice();});$(id).oninput=update;$(id).onchange=update;
}
const updateStep=guard(()=>{const step=number('slice-step');if(step<=0)throw new Error('Step size must be positive.');$('slice-slider').step=step;$('slice-value').step=step;});
$('slice-step').oninput=updateStep;$('slice-step').onchange=updateStep;
$('lower').onclick=guard(()=>moveSlice(-1));$('raise').onclick=guard(()=>moveSlice(1));
$('slice-slider').addEventListener('wheel',event=>{event.preventDefault();event.stopPropagation();try{moveSlice(event.deltaY<0?1:-1);}catch(error){status(error.message,true);}},{passive:false});
$('source-level').onclick=()=>{if(!displayScene())return;$('orientation').value='Horizontal';$('slice-value').value=displayScene().levels.z_s;resetSlice(false);};
$('target-level').onclick=()=>resetSlice(true);
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'){endMode();return;}
  if(['INPUT','SELECT','TEXTAREA'].includes(document.activeElement?.tagName))return;
  if(event.key==='ArrowUp'||event.key==='ArrowDown'){event.preventDefault();try{moveSlice(event.key==='ArrowUp'?1:-1);}catch(error){status(error.message,true);}}
});
$('basemap').onchange=guard(()=>changeBackground($('basemap').value));
$('custom-apply').onclick=guard(async()=>{
  const url=$('custom-url').value.trim();if(!/^https?:\/\//.test(url)||!['{z}','{x}','{y}'].every(t=>url.includes(t)))throw new Error('Enter an HTTP(S) XYZ tile URL containing {z}, {x}, and {y}.');
  if(!$('custom-credit').value.trim())throw new Error('Enter the layer attribution.');
  state.custom={url,credit:$('custom-credit').value};$('custom-dialog').close();$('basemap').value='custom';await changeBackground('custom');
});
$('open-import').onclick=()=>$('import-dialog').showModal();
$('load-default').onclick=guard(async()=>{
  $('import-dialog').close();endMode();
  await runJob('/api/load-default',{initial_height:number('initial-height')},'Loading default footprints…');
});
$('upload-file').onchange=guard(async()=>{
  const file=$('upload-file').files[0];if(!file)return;if(file.size>100*1024**2)throw new Error('Upload at most 100 MiB.');
  const form=new FormData();form.append('file',file);$('import-message').textContent='Reading layers…';
  const response=await fetch('/api/uploads',{method:'POST',body:form});const data=await response.json();if(!response.ok)throw new Error(data.detail||'Upload failed.');
  state.upload=data;$('upload-fields').hidden=false;$('upload-layer').replaceChildren(...data.layers.map(layer=>new Option(layer,layer)));$('import-message').textContent=`${data.name} · choose a layer, then load.`;
});
$('load-upload').onclick=guard(async()=>{
  if(!state.upload)return;
  const result=await runJob('/api/load-upload',{upload_id:state.upload.upload_id,layer:$('upload-layer').value,crs:$('upload-crs').value.trim()||null,initial_height:number('initial-height')},'Unioning input overlaps and constructing DHE…');
  if(result)$('import-dialog').close();
});

operationFields();
async function boot(){
  state.config=await api('/api/config');$('default-path').textContent=state.config.default_dataset;$('initial-height').value=state.config.initial_height;
  for(const value of ['google-roadmap','google-satellite']){
    const option=$('basemap').querySelector(`option[value="${value}"]`);option.disabled=!state.config.google_available;
    if(option.disabled)option.textContent+=' (API key required)';
  }
  const current=await api('/api/state');state.revision=current.revision;
  if(current.job_id){setBusy(true,'Constructing initial DHE cells…');try{await waitJob(current.job_id);}finally{setBusy(false);}}
  else if(!current.loaded&&current.recent_jobs[0]?.status==='failed')throw new Error(current.recent_jobs[0].error);
  if(await refreshScene(true))status('Ready · select features on the current map to begin.');
  else status('Load a polygon dataset to begin.');
}
boot().catch(error=>{setBusy(false);status(error.message,true);$('scene-subtitle').textContent='Load another polygon dataset to begin.';});
setInterval(async()=>{
  if(state.busy||document.hidden)return;
  try{const current=await api('/api/state');if(current.revision!==state.revision)await refreshScene(true);}catch{}
},5000);
// Immutable scene data stays on the server. This small handle supports local browser diagnostics.
window.dheAtlas={get revision(){return state.revision;},get loaded(){return Boolean(state.scene);}};
