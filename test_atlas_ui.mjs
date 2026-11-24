import test from 'node:test';
import assert from 'node:assert/strict';
import {createSlicePlayback,formatNumber} from './web_generalization/atlas_ui.js';

const flush=async()=>{for(let i=0;i<6;i++)await Promise.resolve();};
function harness({initial=0,step=.5,advance,ready=()=>true}={}){
  let value=initial,id=0;
  const tasks=new Map(),values=[],states=[];
  const player=createSlicePlayback({step:()=>step,position:()=>value,bounds:()=>[0,1],ready,
    advance:async next=>{values.push(next);await advance?.(next);value=next;},
    onPlaying:p=>states.push(p),schedule:(callback,delay)=>{assert.ok(delay>=200);tasks.set(++id,callback);return id;},cancel:key=>tasks.delete(key)});
  return {player,tasks,values,states,async next(){const [key,callback]=tasks.entries().next().value;tasks.delete(key);await callback();await flush();}};
}
test('playback walks actual Z steps and stops at upper bound',async()=>{
  const h=harness();await h.player.play();await flush();
  while(h.tasks.size)await h.next();
  assert.deepEqual(h.values,[.5,1]);assert.equal(h.player.playing,false);
});
test('playing at the endpoint restarts from the lower bound',async()=>{
  const h=harness({initial:1});await h.player.play();await flush();
  assert.deepEqual(h.values,[0]);await h.next();assert.deepEqual(h.values,[0,.5]);h.player.pause();assert.equal(h.tasks.size,0);
});
test('slow responses never queue overlapping playback requests',async()=>{
  let finish;const h=harness({advance:()=>new Promise(resolve=>finish=resolve)});
  await h.player.play();await flush();assert.deepEqual(h.values,[.5]);assert.equal(h.tasks.size,0);
  // A second press pauses instead of launching another request.
  await h.player.play();finish();await flush();
  assert.equal(h.player.playing,false);assert.equal(h.tasks.size,0);assert.equal(h.values.length,1);
});
test('a paused pending response cannot restart the timer',async()=>{
  let finish;const h=harness({advance:()=>new Promise(resolve=>finish=resolve)});
  await h.player.play();h.player.pause();finish();await flush();
  assert.equal(h.tasks.size,0);assert.equal(h.player.playing,false);
});
test('request failure stops playback',async()=>{
  const h=harness({advance:async()=>{throw new Error('Server failed');}});
  await h.player.play();await flush();assert.equal(h.player.playing,false);assert.equal(h.tasks.size,0);
});
test('busy renderer waits and invalid step never sends geometry requests',async()=>{
  const busy=harness({ready:()=>false});await busy.player.play();await flush();
  assert.equal(busy.values.length,0);assert.equal(busy.tasks.size,1);busy.player.pause();
  const invalid=harness({step:0});await invalid.player.play();await flush();
  assert.equal(invalid.values.length,0);assert.equal(invalid.player.playing,false);
  const invalidEnd=harness({initial:1,step:0});await invalidEnd.player.play();await flush();
  assert.equal(invalidEnd.values.length,0);
});
test('zero values remain numeric and missing measurements are explicit',()=>{
  assert.equal(formatNumber(0),'0');assert.equal(formatNumber(null),'—');
});
