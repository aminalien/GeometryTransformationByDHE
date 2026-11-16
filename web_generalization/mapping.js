/* Mapping controls are independent of Generalize; all construction stays on the server. */
window.createMapping=function({api,status,setBusy,changed,fitInputs}){
  const $=id=>document.getElementById(id);
  const controller={active:false,scene:null,revision:0,data:null,groups:[],jobId:null,busy:false,selected:new Set()};
  let upload=null,uploadSide='source',sequence=0;
  const fields={mode:'mode',source_z:'source-z',target_z:'target-z',geometry_tolerance:'geometry-tolerance',overlap_fraction:'overlap-fraction',search_distance:'search-distance',source_label:'source-label',target_label:'target-label',source_scale:'source-scale',target_scale:'target-scale',source_time:'source-time',target_time:'target-time'};
  const numeric=new Set(['source_z','target_z','geometry_tolerance','overlap_fraction','search_distance','source_scale','target_scale']);
  const controlIds=['default-source','default-target','upload-source','upload-target','analyze','save-review','confirm-matches','unmatched','regroup','split-group','preview','build','upload-load'];
  function settings(){
    const result={};
    for(const [key,id] of Object.entries(fields)){
      const value=$('mapping-'+id).value;
      result[key]=numeric.has(key)?(value===''&&key.endsWith('_scale')?null:Number(value)):value;
      if(numeric.has(key)&&result[key]!==null&&!Number.isFinite(result[key]))throw new Error('Enter a finite '+key.replaceAll('_',' '));
    }
    return result;
  }
  function safe(action){return (...args)=>Promise.resolve().then(()=>action(...args)).catch(e=>{status(e.message,true);$('mapping-message').textContent=e.message;});}
  function note(message){$('mapping-message').textContent=message;status(message);}
  function download(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function nextId(){let id;do{id='manual-'+(++sequence);}while(controller.groups.some(g=>g.id===id));return id;}
  function features(side){return controller.data?.layers[side]?.features.features||[];}
  function label(uid){for(const side of ['source','target']){const f=features(side).find(f=>f.properties.uid===uid);if(f)return uid+' · '+f.properties.original_id;}return uid;}
  controller.inputFeatures=()=>['source','target'].flatMap(side=>$('mapping-show-'+side).checked?features(side).map(f=>({...f,properties:{...f.properties,selected:controller.selected.has(f.properties.uid),z:Number($('mapping-'+side+'-z').value)||0}})):[]);
  controller.pick=uid=>{
    const g=controller.groups.find(g=>[...g.sources,...g.targets].includes(uid));
    if(g)selectGroup(g);else{controller.selected=new Set([uid]);changed(false);}
  };
  controller.pickCell=owner=>{const edge=controller.scene?.halfedges.find(e=>e.owner===Number(owner));if(edge)controller.pick(edge.feature_id);};
  function selectGroup(g){
    controller.selected=new Set([...g.sources,...g.targets]);
    $('mapping-edit-sources').value=g.sources.join(', ');$('mapping-edit-targets').value=g.targets.join(', ');
    $('mapping-edit-decision').value=g.decision;$('mapping-edit-anchor').value=g.anchor??'';
    const near=(controller.data?.suggestions.proximity_candidates||[]).filter(e=>controller.selected.has(e.source)||controller.selected.has(e.target)).slice(0,20);
    $('mapping-candidates').textContent=near.length?'Nearby proposals (not accepted automatically): '+near.map(e=>`${e.source} ↔ ${e.target}: ${e.distance.toFixed(2)} m, ${(100*e.overlap_fraction).toFixed(1)}% overlap`).join('; '):'No additional proximity candidates for this group.';
    changed(false);
  }
  function rows(){
    const body=$('mapping-group-rows');body.replaceChildren();
    for(const g of controller.groups){
      const row=document.createElement('tr');row.onclick=event=>{if(!['INPUT','SELECT','OPTION'].includes(event.target?.tagName))selectGroup(g);};
      const check=document.createElement('input');check.type='checkbox';check.checked=g.confirmed;check.onchange=()=>{g.confirmed=check.checked;summarize();};
      const td=document.createElement('td');td.append(check);row.append(td);
      for(const text of [g.id,g.sources.map(label).join(', ')||'—',g.targets.map(label).join(', ')||'—']){const cell=document.createElement('td');cell.textContent=text;row.append(cell);}
      const cell=document.createElement('td'),choice=document.createElement('select');
      for(const value of ['map','appearance','disappearance','exclude','unresolved'])choice.append(new Option(value,value));
      choice.value=g.decision;choice.onchange=()=>{g.decision=choice.value;g.confirmed=choice.value!=='unresolved';check.checked=g.confirmed;summarize();};cell.append(choice);row.append(cell);
      const route=document.createElement('td');route.textContent=(g.builder||'Save review to classify')+' · '+(g.reason||'');row.append(route);body.append(row);
    }
    summarize();
  }
  function summarize(){
    const confirmed=controller.groups.filter(g=>g.confirmed&&g.decision!=='unresolved').length;
    const overlaps=Object.values(controller.data?.layers||{}).reduce((n,l)=>n+l.overlaps.length,0);
    $('mapping-summary').textContent=`${controller.groups.length} groups · ${confirmed} confirmed · ${overlaps} input overlap(s) to resolve.`;
  }
  function overlapControls(){
    const body=$('mapping-overlaps');body.replaceChildren();
    for(const side of ['source','target'])for(const overlap of controller.data?.layers[side]?.overlaps||[]){
      const div=document.createElement('div');div.className='mapping-overlap';
      const p=document.createElement('p');p.textContent=`${side}: ${overlap.ids.map(label).join(' / ')} · ${overlap.area.toPrecision(5)} m² overlap`;div.append(p);
      const keeper=document.createElement('select');keeper.append(...overlap.ids.map(k=>new Option('Retain '+label(k),k)));div.append(keeper);
      for(const [resolution,title] of [['assign','Assign overlap'],['union','Union features'],['exclude','Exclude other']]){
        const button=document.createElement('button');button.textContent=title;button.onclick=safe(()=>job('overlap',{side,ids:resolution==='exclude'?overlap.ids.filter(k=>k!==keeper.value):overlap.ids,resolution,keeper:keeper.value}));div.append(button);
      }
      body.append(div);
    }
  }
  controller.refresh=async()=>{
    const data=await api('/api/mapping/state');controller.data=data;controller.revision=data.revision;controller.groups=data.groups.map(g=>({...g,sources:[...g.sources],targets:[...g.targets]}));
    for(const [key,id] of Object.entries(fields))$('mapping-'+id).value=data.settings[key]??'';
    for(const side of ['source','target']){const l=data.layers[side];$('mapping-'+side+'-name').textContent=l?`${l.name} · ${l.count} features · ${l.crs} · ${l.notes.length} repair/review note(s)`:(data.default_paths[side]||'Upload a layer');}
    const suffix=data.preview_id?'?preview_id='+encodeURIComponent(data.preview_id):'';
    controller.scene=(data.has_result||data.has_preview)?await api('/api/mapping/scene'+suffix):null;
    $('mapping-discard').hidden=!data.has_preview;rows();overlapControls();
    if(controller.active)changed(true);
    return data;
  };
  async function job(action,parameters={}){
    if(controller.busy)throw new Error('Wait for the current Mapping job to finish.');
    controller.busy=true;setBusy(true,'Mapping · '+action);for(const id of controlIds)$('mapping-'+id).disabled=true;
    try{
      const answer=await api('/api/mapping/'+action,{revision:controller.revision,parameters});controller.jobId=answer.job_id;$('mapping-cancel').hidden=false;
      for(;;){
        const j=await api('/api/jobs/'+controller.jobId);if(j.stage){setBusy(true,j.stage);note(j.stage);}
        if(j.status==='failed'){
          if(j.diagnostic)$('mapping-candidates').textContent=JSON.stringify(j.diagnostic,null,2);
          throw new Error(j.error);
        }
        if(j.status==='complete')break;
        await new Promise(resolve=>setTimeout(resolve,250));
      }
      await controller.refresh();
      note(action==='build'?(parameters.preview?'Mapping preview validated; published model preserved.':'Mapping built and validated. Use slicing and Inspect DHE.'):'Mapping '+action+' complete.');
      return controller.data;
    }finally{
      controller.busy=false;controller.jobId=null;$('mapping-cancel').hidden=true;setBusy(false);for(const id of controlIds)$('mapping-'+id).disabled=false;
    }
  }
  controller.activate=safe(async()=>{controller.active=true;await controller.refresh();fitInputs(controller.inputFeatures());});
  controller.deactivate=()=>{controller.active=false;};
  controller.poll=async()=>{if(controller.busy)return;const remote=await api('/api/mapping/state');if(remote.revision!==controller.revision)await controller.refresh();};
  for(const side of ['source','target']){
    $('mapping-default-'+side).onclick=safe(async()=>{await job('load',{side});fitInputs(controller.inputFeatures());});
    $('mapping-upload-'+side).onclick=()=>{uploadSide=side;$('mapping-upload-title').textContent='Upload '+side+' mapping layer';$('mapping-upload-dialog').showModal();};
    $('mapping-show-'+side).onchange=()=>changed(false);
  }
  $('mapping-analyze').onclick=safe(()=>job('analyze',{settings:settings()}));
  $('mapping-review-open').onclick=()=>{$('mapping-review-dialog').showModal();rows();};
  $('mapping-confirm-matches').onclick=()=>{for(const g of controller.groups)if(g.decision==='map')g.confirmed=true;rows();};
  $('mapping-unmatched').onclick=()=>{for(const g of controller.groups){if(!g.sources.length){g.decision='appearance';g.confirmed=true;}else if(!g.targets.length){g.decision='disappearance';g.confirmed=true;}}rows();};
  $('mapping-save-review').onclick=safe(()=>job('review',{groups:controller.groups,settings:settings()}));
  function ids(id){return [...new Set($(id).value.trim().split(/[\s,]+/).filter(Boolean))];}
  $('mapping-regroup').onclick=safe(()=>{
    const ss=ids('mapping-edit-sources'),ts=ids('mapping-edit-targets');if(!ss.length&&!ts.length)throw new Error('Choose source and/or target IDs.');
    for(const [side,keys] of [['source',ss],['target',ts]])if(keys.some(k=>!features(side).some(f=>f.properties.uid===k)))throw new Error('Unknown '+side+' ID. Use the s… / t… IDs shown in the table.');
    const used=new Set([...ss,...ts]),affected=controller.groups.filter(g=>[...g.sources,...g.targets].some(k=>used.has(k)));
    controller.groups=controller.groups.filter(g=>!affected.includes(g));
    for(const old of affected)for(const k of [...old.sources,...old.targets].filter(k=>!used.has(k)))controller.groups.push({id:nextId(),sources:k.startsWith('s')?[k]:[],targets:k.startsWith('t')?[k]:[],decision:'unresolved',confirmed:false,anchor:null});
    const anchor=$('mapping-edit-anchor').value;const decision=$('mapping-edit-decision').value;
    controller.groups.push({id:nextId(),sources:ss,targets:ts,decision,confirmed:decision!=='unresolved',anchor:anchor===''?null:Number(anchor)});rows();
  });
  $('mapping-split-group').onclick=()=>{
    const used=new Set([...ids('mapping-edit-sources'),...ids('mapping-edit-targets')]);
    for(const g of controller.groups.filter(g=>[...g.sources,...g.targets].some(k=>used.has(k)))){
      controller.groups=controller.groups.filter(x=>x!==g);
      for(const k of [...g.sources,...g.targets])controller.groups.push({id:nextId(),sources:k.startsWith('s')?[k]:[],targets:k.startsWith('t')?[k]:[],decision:'unresolved',confirmed:false,anchor:null});
    }
    rows();
  };
  for(const [id,preview] of [['mapping-preview',true],['mapping-build',false]])$(id).onclick=safe(async()=>{
    await job('review',{groups:controller.groups,settings:settings()});await job('build',{preview});
  });
  $('mapping-discard').onclick=safe(()=>job('discard-preview'));
  $('mapping-cancel').onclick=safe(async()=>{if(controller.jobId){await api('/api/jobs/'+controller.jobId+'/cancel',{});note('Cancellation requested; the published model will be preserved.');}});
  $('mapping-report').onclick=safe(async()=>{const suffix=controller.scene?.preview_id?'?preview_id='+encodeURIComponent(controller.scene.preview_id):'';download(await api('/api/mapping/report'+suffix),'dhe_mapping_report.json');});
  $('mapping-upload-file').onchange=safe(async()=>{
    const files=[...$('mapping-upload-file').files];if(!files.length)return;const form=new FormData();for(const f of files)form.append('file',f);
    const response=await fetch('/api/mapping/uploads',{method:'POST',body:form});upload=await response.json();if(!response.ok)throw new Error(upload.detail||'Upload failed.');
    $('mapping-upload-layer').replaceChildren(...upload.layers.map(k=>new Option(k,k)));uploadMetadata();
  });
  function uploadMetadata(){const m=upload?.metadata[$('mapping-upload-layer').value];if(!m)return;$('mapping-upload-metadata').textContent=`${m.count} features · ${m.crs||'missing CRS'} · ${m.bounds.join(', ')}. ${m.warning}`;$('mapping-crs-choice').value=m.suspicious?'':'declared';$('mapping-override-crs').value=m.suggested_crs||'';}
  $('mapping-upload-layer').onchange=uploadMetadata;
  $('mapping-upload-load').onclick=safe(async()=>{
    if(!upload)throw new Error('Choose a dataset first.');const choice=$('mapping-crs-choice').value;if(!choice)throw new Error('Confirm or override the declared CRS.');
    const override=$('mapping-override-crs').value.trim();if(choice==='override'&&!override)throw new Error('Enter the override CRS.');
    await job('load',{side:uploadSide,upload_id:upload.upload_id,layer:$('mapping-upload-layer').value,crs:$('mapping-upload-crs').value.trim()||null,override_crs:choice==='override'?override:null,accept_declared_crs:choice==='declared'});
    $('mapping-upload-dialog').close();fitInputs(controller.inputFeatures());
  });
  return controller;
};
