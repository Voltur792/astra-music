const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');

test('prepared media is silent and promoted with its buffer; stale preparation is disposed',async()=>{
  const media=()=>({paused:false,duration:100,currentTime:80,readyState:3,playCalls:0,
    addEventListener(){},removeEventListener(){},load(){},removeAttribute(){},pause(){this.paused=true;},play(){this.playCalls++;}});
  const old=media(), next=media();
  const ctx=vm.createContext({document:{querySelector:()=>old,createElement:()=>next},
    window:{},Date, current:{revision:1},parse:x=>x,
    call:async()=>({revision:1,track_id:'next',queue_index:1,service:'yandex',url:'http://localhost/song',expires_at:Date.now()/1000+30}),
    releaseAudioMeter(){},destroyHls(){}});
  const script=fs.readFileSync('ui/player-v12.js','utf8').split('const player =')[0];
  vm.runInContext(script,ctx);
  await vm.runInContext('prepareNextAudio()',ctx);
  assert.equal(next.src,'http://localhost/song'); assert.equal(next.playCalls,0);
  assert.equal(vm.runInContext("adoptPrepared({track_id:'next',queue_index:1,service:'yandex'},1)",ctx),true);
  assert.equal(vm.runInContext('audio',ctx),next);
  assert.equal(old.paused,true); assert.equal(next.playCalls,0);
  vm.runInContext('lastPrepareAt=0; current.revision=2',ctx);
  await vm.runInContext('prepareNextAudio()',ctx);
  assert.equal(vm.runInContext("adoptPrepared({track_id:'different',queue_index:1,service:'yandex'},2)",ctx),false);
  assert.equal(vm.runInContext('preparedAudio',ctx),null);
});
