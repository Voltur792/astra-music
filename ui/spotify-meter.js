"use strict";
// Serialized into the owned player tab; no system audio or duplicate playback.
async function spotifyMeter(request, frequencyBands) {
  const previous = window[request.key];
  const borrowed = window[request.audioKey]?.stream;
  if (request.enabled && previous && previous.stream.getAudioTracks()[0]?.readyState === 'live' &&
      ((!borrowed && previous.owned) || borrowed === previous.stream)) return {active:true};
  if (previous) {
    clearInterval(previous.timer);
    previous.source.disconnect();
    if (previous.owned) previous.stream.getTracks().forEach(track => track.stop());
    await previous.context.close();
    delete window[request.key];
  }
  if (!request.enabled) return {active:false};
  if (location.origin !== 'https://open.spotify.com') return {active:false};
  const stream = borrowed || await navigator.mediaDevices.getDisplayMedia({
    video:{frameRate:1,width:16,height:16},
    audio:{suppressLocalAudioPlayback:false,echoCancellation:false,noiseSuppression:false,autoGainControl:false,channelCount:2},
    preferCurrentTab:true,selfBrowserSurface:'include',systemAudio:'exclude',surfaceSwitching:'exclude',
  });
  const owned = !borrowed;
  let context;
  try {
    if (!stream.getAudioTracks()[0] || stream.getVideoTracks()[0]?.getSettings().displaySurface !== 'browser' ||
        owned && stream.getAudioTracks()[0].getSettings().suppressLocalAudioPlayback === true) throw new Error('Spotify tab capture unavailable');
    context = new AudioContext({sampleRate:48000});
    const source = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser(); analyser.fftSize=1024; analyser.smoothingTimeConstant=.1;
    const sink = context.createGain(); sink.gain.value=0;
    source.connect(analyser).connect(sink).connect(context.destination);
    const decibels = new Float32Array(analyser.frequencyBinCount);
    const timer = setInterval(() => {
      analyser.getFloatFrequencyData(decibels);
      window[request.binding](JSON.stringify({meter:request.key,bands:frequencyBands(decibels)}));
    }, 80);
    window[request.key]={stream,owned,context,source,timer};
    await context.resume();
    return {active:true};
  } catch(error) {
    if (owned) stream.getTracks().forEach(track => track.stop());
    if (context) await context.close();
    const failed=window[request.key];
    if(failed) clearInterval(failed.timer);
    delete window[request.key];
    throw error;
  }
}
if(typeof module !== 'undefined') module.exports=spotifyMeter;
