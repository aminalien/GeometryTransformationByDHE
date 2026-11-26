/* Export the rendered scene and validate the slice request before sending it. */

export function sliceExportParameters(count, start, end, bounds){
  if(!Number.isInteger(count)||count<1||count>50)throw new Error('Choose between 1 and 50 slices.');
  if(count===1)end=start;
  const [lower,upper]=bounds;
  if(!Number.isFinite(start)||!Number.isFinite(end)||start<lower||end>upper||start>end)
    throw new Error('Enter Start Z and End Z within the displayed range.');
  if(count>1&&start===end)throw new Error('End Z must be greater than Start Z for multiple slices.');
  return {count,start_z:start,end_z:end};
}

export function exportCredit(background,customCredit){
  if(background==='light'||background==='dark')return 'OpenFreeMap · © OpenMapTiles · © OpenStreetMap contributors · openstreetmap.org/copyright';
  if(background==='osm')return '© OpenStreetMap contributors · openstreetmap.org/copyright';
  if(background==='satellite')return 'World Imagery © Esri, DigitalGlobe, GeoEye, i-cubed, USDA FSA, USGS, AEX, Getmapping, Aerogrid, IGN, IGP, swisstopo, and the GIS User Community';
  return background==='custom'?customCredit||'':'';
}

export function fileName(scene,kind,extension){
  return `dhe_${scene.workspace||'generalize'}_${kind}_${scene.snapshot_id.slice(0,8)}.${extension}`;
}

function addCredit(ctx,credit,width,height,ratio){
  const padding=6*ratio,lineHeight=14*ratio;
  ctx.font=`${11*ratio}px Arial,sans-serif`;
  const lines=[];
  let line='';
  for(const word of credit.split(/\s+/)){
    const combined=line?line+' '+word:word;
    if(line&&ctx.measureText(combined).width>width-4*padding){
      lines.push(line);line=word;
    }else line=combined;
  }
  if(line)lines.push(line);
  const boxWidth=Math.min(width,Math.max(...lines.map(text=>ctx.measureText(text).width))+2*padding);
  const boxHeight=lines.length*lineHeight+2*padding;
  const left=width-boxWidth-padding,top=height-boxHeight-padding;
  ctx.fillStyle='rgba(255,255,255,.94)';
  ctx.fillRect(left,top,boxWidth,boxHeight);
  ctx.fillStyle='#172629';
  ctx.textBaseline='top';
  lines.forEach((text,i)=>ctx.fillText(text,left+padding,top+padding+i*lineHeight,boxWidth-2*padding));
}

export async function captureFigure({Map,Overlay,style,camera,width,height,layers,effects,credit,transparent}){
  if(!width||!height)throw new Error('The viewer has no visible canvas.');
  const container=document.createElement('div');
  container.setAttribute('aria-hidden','true');
  Object.assign(container.style,{position:'fixed',left:'-10000px',top:'0',width:width+'px',height:height+'px',pointerEvents:'none'});
  document.body.append(container);
  let renderer,timer,repaint;
  const pixelRatio=Math.min(2,4096/Math.max(width,height));
  try{
    await new Promise((resolve,reject)=>{
      const fail=cause=>reject(new Error('Figure rendering failed. '+(cause?.message||'Try exporting again.')));
      timer=setTimeout(()=>fail(new Error('The map did not finish rendering. Try again or turn off the background.')),18000);
      const {padding,...view}=camera;
      renderer=new Map({container,style,...view,maxPitch:80,interactive:false,attributionControl:false,
        trackResize:false,pixelRatio,fadeDuration:0,
        canvasContextAttributes:{alpha:true,antialias:true,preserveDrawingBuffer:true}});
      renderer.on('error',event=>fail(event.error));
      renderer.on('webglcontextlost',()=>fail(new Error('The WebGL context was lost.')));
      const mapReady=renderer.once('load');
      let overlay;
      const deckReady=new Promise(loaded=>{
        overlay=new Overlay({interleaved:true,layers:[],effects,onError:fail,onLoad:loaded});
        renderer.addControl(overlay);
      });
      Promise.all([mapReady,deckReady]).then(()=>{
        if(padding)renderer.jumpTo({padding});
        overlay.setProps({layers});
        renderer.once('idle',()=>{
          // The overlay may still be uploading layers on the frame that settles the map.
          repaint=requestAnimationFrame(()=>{
            renderer.once('render',resolve);
            renderer.triggerRepaint();
          });
        });
        renderer.triggerRepaint();
      }).catch(fail);
    });
    const source=renderer.getCanvas(),canvas=document.createElement('canvas');
    canvas.width=source.width;canvas.height=source.height;
    const context=canvas.getContext('2d');
    if(!context||!canvas.width||!canvas.height)throw new Error('The figure canvas could not be captured.');
    context.drawImage(source,0,0);
    if(transparent){
      const pixels=context.getImageData(0,0,canvas.width,canvas.height).data;
      let visible=false;
      for(let index=3;index<pixels.length;index+=4){
        if(pixels[index]){visible=true;break;}
      }
      if(!visible)throw new Error('No geometry is visible. Reframe the scene or enable its layers.');
    }
    if(credit)addCredit(context,credit,canvas.width,canvas.height,pixelRatio);
    return canvas;
  }finally{
    clearTimeout(timer);
    if(repaint!==undefined)cancelAnimationFrame(repaint);
    try{renderer?.remove();}finally{container.remove();}
  }
}

function imageBlob(canvas,type,quality){
  return new Promise((resolve,reject)=>canvas.toBlob(blob=>{
    if(blob?.type===type)resolve(blob);else reject(new Error('The browser could not encode the figure.'));
  },type,quality));
}

export async function encodeFigure(canvas,format){
  if(format==='png')return imageBlob(canvas,'image/png');
  if(format==='jpeg'){
    const context=canvas.getContext('2d');
    context.globalCompositeOperation='destination-over';
    context.fillStyle='#fff';
    context.fillRect(0,0,canvas.width,canvas.height);
    context.globalCompositeOperation='source-over';
    return imageBlob(canvas,'image/jpeg',.95);
  }
  if(format==='svg'){
    const {width,height}=canvas;
    const image=canvas.toDataURL('image/png');
    const xml=`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><title>LoD Transition Space · 3D figure</title><image width="${width}" height="${height}" href="${image}"/></svg>`;
    return new Blob([xml],{type:'image/svg+xml'});
  }
  const png=await imageBlob(canvas,'image/png');
  let PDFDocument;
  try{({PDFDocument}=await import('https://esm.sh/pdf-lib@1.17.1?bundle'));}
  catch{throw new Error('The PDF encoder did not load. Check your connection and try again.');}
  const pdf=await PDFDocument.create();
  pdf.setTitle('LoD Transition Space · 3D figure');
  const image=await pdf.embedPng(await png.arrayBuffer());
  const scale=Math.min(792/image.width,612/image.height);
  const width=image.width*scale,height=image.height*scale;
  pdf.addPage([width,height]).drawImage(image,{x:0,y:0,width,height});
  return new Blob([await pdf.save()],{type:'application/pdf'});
}

export function saveBlob(blob,name){
  const url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download=name;a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
