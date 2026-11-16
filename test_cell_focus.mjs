/** Numeric projection tests; install pinned deck.gl under artifacts/camera-test. */
import assert from 'node:assert/strict';
import {WebMercatorViewport} from './artifacts/camera-test/node_modules/@deck.gl/core/dist/index.js';
import {fitCellCamera} from './web_generalization/cell_focus.js';

function pointsFor(width,depth,height){
  const ps=[];for(const x of [0,width])for(const y of [0,depth])for(const z of [0,height])ps.push([17.01+x/70000,51.13+y/111320,z]);return ps;
}
let count=0;
for(const [width,height] of [[900,500],[420,320]])for(const pitch of [0,55,80])for(const bearing of [-20,90,170]){
  for(const ps of [pointsFor(10,5,15),pointsFor(.01,.01,1500),pointsFor(0,0,30),pointsFor(40,0,10),pointsFor(10,5,.04)]){
    const original=structuredClone(ps);
    const camera=fitCellCamera(ps,{Viewport:WebMercatorViewport,width,height,pitch,bearing});
    assert.equal(camera.pitch,pitch);assert.equal(camera.bearing,bearing);assert.deepEqual(ps,original);
    const vp=new WebMercatorViewport({width,height,longitude:camera.center[0],latitude:camera.center[1],zoom:camera.zoom,pitch,bearing});
    const padding=[Math.min(48,width*.2),Math.min(75,height*.2),width-Math.min(220,width*.35),height-Math.min(40,height*.2)];
    for(const point of ps){
      const p=vp.project(point);
      assert.ok(p.every(Number.isFinite));assert.ok(p[0]>=padding[0]-.1&&p[0]<=padding[2]+.1);
      assert.ok(p[1]>=padding[1]-.1&&p[1]<=padding[3]+.1);assert.ok(p[2]>=0&&p[2]<=1);
    }
    count++;
  }
}
assert.throws(()=>fitCellCamera([],{Viewport:WebMercatorViewport,width:900,height:500,pitch:55,bearing:0}),/finite geometry/);
assert.throws(()=>fitCellCamera([[NaN,51,0]],{Viewport:WebMercatorViewport,width:900,height:500,pitch:55,bearing:0}),/finite geometry/);
console.log(`Cell focus passed: ${count} real projection fits, ordinary/tall/thin/point/sheet footprints, flat/pitched views, clipping and selection-data preservation.`);
