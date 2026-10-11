/* Capture only this owned Spotify tab. Local playback is suppressed while routed. */
"use strict";
async function spotifyAudio(request) {
  const key = request.key;
  const previous = window[key];
  if (previous) {
    previous.stopped = true;
    previous.stream.getTracks().forEach(track => track.stop());
    previous.processor.port.onmessage = null;
    previous.processor.port.close();
    previous.processor.disconnect();
    await previous.context.close();
    delete window[key];
  }
  if (request.action === 'stop') return {stopped:true};
  if (location.origin !== 'https://open.spotify.com') throw new Error('Откройте Spotify перед трансляцией.');
  const stream = await navigator.mediaDevices.getDisplayMedia({
    video:{frameRate:1,width:16,height:16},
    audio:{suppressLocalAudioPlayback:true,echoCancellation:false,noiseSuppression:false,autoGainControl:false,channelCount:2},
    preferCurrentTab:true,selfBrowserSurface:'include',systemAudio:'exclude',surfaceSwitching:'exclude',
  });
  const audio = stream.getAudioTracks()[0];
  if (!audio || audio.getSettings().suppressLocalAudioPlayback !== true ||
      stream.getVideoTracks()[0]?.getSettings().displaySurface !== 'browser') {
    stream.getTracks().forEach(track=>track.stop());
    throw new Error('Браузер не подтвердил отключение локального звука Spotify.');
  }
  let context;
  try {
    context = new AudioContext({sampleRate:48000});
    const source = context.createMediaStreamSource(stream);
    const workletCode = `class Capture extends AudioWorkletProcessor {
      constructor(){super();this.bytes=new Uint8Array(8192);this.pcm=new DataView(this.bytes.buffer);this.n=0;}
      process(inputs){const channels=inputs[0];if(!channels?.length)return true;
        const left=channels[0],right=channels[1]||left;
        for(let i=0;i<left.length;i++){
          this.pcm.setInt16(this.n*4,Math.round(Math.max(-1,Math.min(1,left[i]))*32767),true);
          this.pcm.setInt16(this.n*4+2,Math.round(Math.max(-1,Math.min(1,right[i]))*32767),true);
          if(++this.n===2048){this.port.postMessage(this.bytes.buffer,[this.bytes.buffer]);this.bytes=new Uint8Array(8192);this.pcm=new DataView(this.bytes.buffer);this.n=0;}
        }return true;
      }
    } registerProcessor('astra-spotify-capture',Capture);`;
    const moduleUrl=URL.createObjectURL(new Blob([workletCode],{type:'text/javascript'}));
    try {await context.audioWorklet.addModule(moduleUrl);} finally {URL.revokeObjectURL(moduleUrl);}
    const processor = new AudioWorkletNode(context,'astra-spotify-capture',{numberOfInputs:1,numberOfOutputs:1,outputChannelCount:[2]});
    const sink = context.createGain(); sink.gain.value = 0;
    const state = {stream,processor,context,stopped:false}; window[key] = state;
    processor.port.onmessage = event => {
      if(state.stopped) return;
      const bytes = new Uint8Array(event.data);
      window[request.binding](JSON.stringify({lease:request.lease,pcm:btoa(String.fromCharCode(...bytes))}));
    };
    source.connect(processor).connect(sink).connect(context.destination);
    await context.resume();
    return {local_suppressed:true,sample_rate:context.sampleRate};
  } catch(error) {
    stream.getTracks().forEach(track=>track.stop());
    if(context) await context.close();
    delete window[key]; throw error;
  }
}
if(typeof module !== 'undefined') module.exports = spotifyAudio;
