const test=require('node:test');
const assert=require('node:assert/strict');
const meter=require('../ui/spotify-meter.js');
const {musicFrequencyBands}=require('../ui/audio-bands.js');

test('local Spotify meter preserves sound, uses only tab audio, and releases its own capture',async()=>{
  let stops=0, options, samples;
  const stream={getAudioTracks:()=>[{readyState:'live',getSettings:()=>({suppressLocalAudioPlayback:false})}],getVideoTracks:()=>[{getSettings:()=>({displaySurface:'browser'})}],getTracks:()=>[{stop:()=>stops++}]};
  global.window={binding:value=>samples=JSON.parse(value)};
  global.location={origin:'https://open.spotify.com'};
  Object.defineProperty(global,'navigator',{value:{mediaDevices:{getDisplayMedia:async value=>{options=value;return stream;}}},configurable:true});
  const node={connect(next){return next;},disconnect(){}};
  let gain;
  global.AudioContext=class {
    destination=node;
    createMediaStreamSource(){return node;}
    createAnalyser(){return {...node,frequencyBinCount:512,getFloatFrequencyData:data=>data.fill(-40)};}
    createGain(){gain={...node,gain:{value:1}};return gain;}
    async resume(){} async close(){}
  };
  const request={key:'meter',audioKey:'capture',binding:'binding',enabled:true};
  try {
    await meter(request,musicFrequencyBands);
    assert.equal(options.audio.suppressLocalAudioPlayback,false);
    assert.equal(options.systemAudio,'exclude'); assert.equal(options.preferCurrentTab,true);
    assert.equal(gain.gain.value,0);
    await new Promise(resolve=>setTimeout(resolve,100));
    assert.equal(samples.bands.length,48);assert.ok(samples.bands.every(n=>n>0 && n<1));
    await meter({...request,enabled:false},musicFrequencyBands);
    assert.equal(stops,1);
    window.capture={stream}; options=null;
    await meter(request,musicFrequencyBands);
    assert.equal(options,null,'Discord capture must be reused');
    await meter({...request,enabled:false},musicFrequencyBands);
    assert.equal(stops,1,'Stopping meter must preserve Discord capture');
  } finally {
    await meter({...request,enabled:false},musicFrequencyBands);
    delete global.window;delete global.location;delete global.AudioContext;delete global.navigator;
  }
});
