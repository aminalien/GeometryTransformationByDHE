/* Presentation only: scientific state and geometry remain owned by the app/server. */
export const formatNumber=value=>value==null?'—':new Intl.NumberFormat('en',{maximumFractionDigits:2}).format(value);

export function createAtlasUI({document,storage,matchMedia,onTheme,onLayout}){
  const $=id=>document.getElementById(id);
  let theme='light',resultsOpen=Boolean(matchMedia?.('(min-width: 1440px)').matches),workflowOpen=false;
  try{theme=storage?.getItem('lts-theme')==='dark'?'dark':'light';}catch{}
  function applyTheme(){
    document.documentElement.dataset.theme=theme;
    $('theme-toggle').textContent=theme==='dark'?'☀ Light':'◐ Dark';
    $('theme-toggle').setAttribute('aria-pressed',String(theme==='dark'));
  }
  function layout(){
    document.body.classList.toggle('results-open',resultsOpen);
    document.body.classList.toggle('workflow-open',workflowOpen);
    $('toggle-results').setAttribute('aria-expanded',String(resultsOpen));
    $('toggle-workflow').setAttribute('aria-expanded',String(workflowOpen));
    onLayout?.();
  }
  $('theme-toggle').onclick=()=>{
    theme=theme==='light'?'dark':'light';applyTheme();
    try{storage?.setItem('lts-theme',theme);}catch{}
    onTheme?.(theme);
  };
  $('toggle-results').onclick=()=>{resultsOpen=!resultsOpen;workflowOpen=false;layout();};
  $('close-results').onclick=()=>{resultsOpen=false;layout();$('toggle-results').focus?.();};
  $('toggle-workflow').onclick=()=>{workflowOpen=!workflowOpen;resultsOpen=false;layout();};
  $('close-workflow').onclick=()=>{workflowOpen=false;layout();$('toggle-workflow').focus?.();};
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){workflowOpen=false;if(!matchMedia?.('(min-width: 1440px)').matches)resultsOpen=false;layout();}});
  applyTheme();layout();
  const card=(label,value,unit='')=>{
    const div=document.createElement('div');div.className='metric';
    const title=document.createElement('span');title.textContent=label;
    const strong=document.createElement('strong');strong.textContent=value;
    const small=document.createElement('small');small.textContent=unit;
    div.append(title,strong,small);return div;
  };
  function diagnosticRows(container,value){
    container.replaceChildren();
    function append(parent,data){
      if(data===null||data===undefined){parent.textContent='Not reported';return;}
      if(typeof data!=='object'){parent.textContent=String(data);return;}
      const entries=Object.entries(data);
      if(!entries.length){parent.textContent='No records';return;}
      for(const [key,item] of entries){
        const row=document.createElement('details'),label=document.createElement('summary'),body=document.createElement('div');
        label.textContent=Array.isArray(data)?`Record ${Number(key)+1}`:String(key).replaceAll('_',' ');row.append(label,body);parent.append(row);
        if(item!==null&&typeof item==='object'){
          let populated=false;
          row.addEventListener('toggle',()=>{if(row.open&&!populated){populated=true;append(body,item);}});
        }else body.textContent=item==null?'Not reported':String(item);
      }
    }
    append(container,value);
  }
  return {
    get theme(){return theme;},
    closeWorkflow(){workflowOpen=false;layout();},
    error(message,diagnostic){
      $('action-message').hidden=!message;$('action-message').textContent=message||'';
      if(diagnostic){diagnosticRows($('result-diagnostics'),diagnostic);$('research-diagnostics').open=true;}
    },
    update(scene,mappingState){
      const m=scene?.measurements||mappingState?.measurements;
      const op=scene?.operation;
      $('result-state').textContent=scene?(scene.stale?'Earlier inputs':scene.preview?'Preview':'Constructed model'):mappingState?'Prepared inputs':'No model';
      $('result-state').dataset.state=scene?.stale?'stale':scene?.preview?'preview':'ready';
      $('result-title').textContent=op?op.name.replaceAll('_',' '):mappingState?'Layer correspondence':'Transition overview';
      $('result-context').textContent=scene?.stale?'Measurements belong to the previously constructed inputs.':m?.scope||'Load data to explore the transition space.';
      const count=scene?.features?.features.length??m?.after.features;
      $('result-metrics').replaceChildren(card('Current features',formatNumber(count)),card('DHE cells / primitives',formatNumber(scene?.cells)),
        card('Transition interval',scene?`${formatNumber(scene.levels.z_s)} → ${formatNumber(scene.levels.z_t)}`:'—','Z coordinate'),
        card('Selected',formatNumber(mappingState?.selectedCount??0),'features'));
      const body=$('measurement-rows');body.replaceChildren();
      $('comparison-label').textContent=m?`${m.before_label} → ${m.after_label}`:'No comparison available';
      for(const [key,label,unit] of [['features','Features',''],['vertices','Vertices',''],['polygon_area','Polygon area','m²'],['polygon_perimeter','Polygon perimeter','m']]){
        const tr=document.createElement('tr');
        const delta=m?.changes[key];
        for(const text of [label,formatNumber(m?.before[key]),formatNumber(m?.after[key]),delta?`${formatNumber(delta.absolute)}${unit?' '+unit:''} (${formatNumber(delta.percent)}${delta.percent==null?'':'%'})`:'—']){
          const td=document.createElement('td');td.textContent=text;tr.append(td);
        }
        tr.firstChild.textContent+=unit?` (${unit})`:'';body.append(tr);
      }
      const types=value=>Object.entries(value).map(([kind,n])=>`${formatNumber(n)} ${kind.replace(/^Multi/,'multipart ').toLowerCase()} feature${n===1?'':'s'}`).join(', ')||'No features';
      $('geometry-types').textContent=m?`${m.before_label}: ${types(m.before.geometry_types)}. ${m.after_label}: ${types(m.after.geometry_types)}.`:'';
      diagnosticRows($('result-diagnostics'),{
        validation:scene?.mapping_report?.endpoint_validation||'No separate validation report supplied',
        operation:op||'No operation applied',
        mapping:scene?.mapping_report||mappingState?.automatic_review||'Not applicable',
        correspondence: mappingState?.groups||'Not applicable',
        automatic_generalization:scene?.automatic_report||'No automatic run',
        construction_notes:scene?.notes||[]
      });
    },
    selection(count){const strong=$('result-metrics').lastChild?.querySelector('strong');if(strong)strong.textContent=formatNumber(count);},
    levels(scene){
      const list=$('level-markers');list.replaceChildren();if(!scene)return;
      const values=[scene.levels.z_b,...scene.history.map(h=>h.z),scene.levels.z_s,scene.levels.z_t];
      for(const value of [...new Set(values)].sort((a,b)=>a-b)){
        const option=document.createElement('option');option.value=value;option.label='Z '+formatNumber(value);list.append(option);
      }
      $('slice-bounds').textContent=`Z ${formatNumber(scene.levels.z_b)} — ${formatNumber(scene.levels.z_t)}`;
    }
  };
}

/* One request at a time. Cancellation tokens prevent a late response restarting playback. */
export function createSlicePlayback({step,position,bounds,advance,ready,onPlaying,schedule=setTimeout,cancel=clearTimeout}){
  let playing=false,timer=null,generation=0,pending=false,restart=false;
  function pause(){playing=false;generation++;cancel(timer);timer=null;onPlaying(false);}
  function frame(){
    try{
      const increment=Number(step()),[low,high]=bounds(),current=Number(position());
      return [increment,low,high,current].every(Number.isFinite)&&increment>0&&high>low?{increment,low,high,current}:null;
    }catch{return null;}
  }
  async function tick(token){
    if(!playing||token!==generation)return;
    if(!ready()){timer=schedule(()=>tick(token),200);return;}
    const f=frame();if(!f){pause();return;}
    const {increment,low,high,current}=f;
    if(current>=high&&!restart){pause();return;}
    const next=restart?low:Math.min(high,Number((current+increment).toPrecision(12)));restart=false;
    pending=true;
    try{await advance(next);}
    catch{pause();return;}
    finally{pending=false;}
    if(playing&&generation===token)timer=schedule(()=>tick(token),200);
  }
  async function play(){
    if(playing){pause();return;}
    if(pending)return;
    const f=frame();if(!f){pause();return;}
    restart=f.current>=f.high;
    playing=true;const token=++generation;onPlaying(true);
    if(token===generation)tick(token);
  }
  return {play,pause,get playing(){return playing;}};
}
