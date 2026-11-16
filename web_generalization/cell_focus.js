/* Camera-only fitting; the viewport constructor is injected for numeric tests. */
export function fitCellCamera(points,{Viewport,width,height,pitch,bearing,minZoom=0,maxZoom=22,padding={top:75,bottom:40,left:48,right:220}}){
  if(!points.length||points.some(p=>p.length<3||p.some(x=>!Number.isFinite(x))))throw new Error('This cell has no finite geometry to frame.');
  const left=Math.min(padding.left,width*.2),right=Math.min(padding.right,width*.35);
  const top=Math.min(padding.top,height*.2),bottom=Math.min(padding.bottom,height*.2);
  const target=[(left+width-right)/2,(top+height-bottom)/2];
  const lo=[Infinity,Infinity,Infinity],hi=[-Infinity,-Infinity,-Infinity];
  for(const p of points)for(let i=0;i<3;i++){lo[i]=Math.min(lo[i],p[i]);hi[i]=Math.max(hi[i],p[i]);}
  const base=[(lo[0]+hi[0])/2,(lo[1]+hi[1])/2];
  // Give projected points/lines a small amount of map context.
  const dx=1/(111320*Math.max(.01,Math.cos(base[1]*Math.PI/180))),dy=1/111320;
  const extentLo=[Math.min(lo[0],base[0]-dx),Math.min(lo[1],base[1]-dy)],extentHi=[Math.max(hi[0],base[0]+dx),Math.max(hi[1],base[1]+dy)];
  const fitPoints=[...points];for(const x of [extentLo[0],extentHi[0]])for(const y of [extentLo[1],extentHi[1]])for(const z of [lo[2],hi[2]])fitPoints.push([x,y,z]);
  function candidate(zoom){
    let center=[...base],viewport,bounds;
    for(let pass=0;pass<10;pass++){
      viewport=new Viewport({width,height,longitude:center[0],latitude:center[1],zoom,pitch,bearing});
      bounds=[Infinity,Infinity,-Infinity,-Infinity];
      for(const point of fitPoints){
        const world=viewport.projectPosition(point),m=viewport.viewProjectionMatrix;
        const w=m[3]*world[0]+m[7]*world[1]+m[11]*world[2]+m[15];
        const p=viewport.project(point);if(w<=1e-9||p.some(x=>!Number.isFinite(x))||p[2]<0||p[2]>1)return null;
        bounds[0]=Math.min(bounds[0],p[0]);bounds[1]=Math.min(bounds[1],p[1]);bounds[2]=Math.max(bounds[2],p[0]);bounds[3]=Math.max(bounds[3],p[1]);
      }
      const shift=[(bounds[0]+bounds[2])/2-target[0],(bounds[1]+bounds[3])/2-target[1]];
      if(Math.hypot(...shift)<.1)break;
      const a=viewport.unproject(target,{targetZ:0}),b=viewport.unproject([target[0]+shift[0],target[1]+shift[1]],{targetZ:0});
      if([...a,...b].some(x=>!Number.isFinite(x)))return null;
      const origin=viewport.projectPosition([...center,0]),from=viewport.projectPosition([a[0],a[1],0]),to=viewport.projectPosition([b[0],b[1],0]);
      center=viewport.unprojectPosition([origin[0]+to[0]-from[0],origin[1]+to[1]-from[1],0]).slice(0,2);
      if(pass===9)return null;
    }
    if(bounds[0]<left||bounds[2]>width-right||bounds[1]<top||bounds[3]>height-bottom)return null;
    return {center,zoom,pitch,bearing};
  }
  let upper=maxZoom,lower=maxZoom,best=candidate(lower);
  while(!best&&lower>minZoom){upper=lower;lower=Math.max(minZoom,lower-1);best=candidate(lower);}
  if(!best)throw new Error('This cell cannot be framed at the current viewing angle. Reduce pitch or Z display factor.');
  if(lower!==maxZoom)for(let i=0;i<14;i++){const mid=(lower+upper)/2,result=candidate(mid);if(result){best=result;lower=mid;}else upper=mid;}
  return {...best,duration:450};
}
