/* Own Edge profile + CDP transport. No Spotify API, no extraction of streams. */
"use strict";
const fs = require('node:fs/promises');
const path = require('node:path');
const {spawn} = require('node:child_process');
const readline = require('node:readline');
const spotifyPage = require('./spotify-page.js');
const spotifyCatalog = require('./spotify-catalog.js');
const spotifyAudio = require('./spotify-audio.js');
const spotifyMeter = require('./spotify-meter.js');
const {musicFrequencyBands} = require('./audio-bands.js');
const {randomBytes} = require('node:crypto');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const output = value => process.stdout.write(JSON.stringify(value) + '\n');
function spotifyUrl(value = '') {
  if (!value) return 'https://open.spotify.com/';
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.hostname !== 'open.spotify.com' || url.username || url.password || url.port ||
      !/^\/(?:$|(?:track|album|playlist|artist)\/[a-zA-Z0-9]+\/?$|genre\/[a-zA-Z0-9-]+\/?$|collection\/(?:tracks|playlists)\/?$|search(?:\/[^/?#]+(?:\/tracks)?)?\/?$)/.test(url.pathname)) throw new Error('Используйте ссылку на трек или плейлист open.spotify.com.');
  url.search = ''; url.hash = ''; return url.href;
}
class CDP {
  constructor(socket) {
    this.socket = socket; this.id = 0; this.pending = new Map();
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if(message.method) { this.onEvent?.(message); return; }
      const item = this.pending.get(message.id);
      if (!item) return;
      clearTimeout(item.timer); this.pending.delete(message.id);
      if (message.error) item.reject(new Error('Браузер Spotify не смог выполнить команду.')); else item.resolve(message.result || {});
    });
    socket.addEventListener('close', () => { for (const item of this.pending.values()) {clearTimeout(item.timer); item.reject(new Error('Окно Spotify закрыто.'));} this.pending.clear(); });
  }
  static async connect(url) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {socket.close(); reject(new Error('Spotify: браузер не ответил.'));}, 6000);
      socket.addEventListener('open', () => {clearTimeout(timer); resolve();}, {once:true});
      socket.addEventListener('error', () => {clearTimeout(timer); reject(new Error('Не удалось подключиться к окну Spotify.'));}, {once:true});
    });
    return new CDP(socket);
  }
  call(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++this.id;
      const timer = setTimeout(() => {this.pending.delete(id); reject(new Error('Spotify не ответил на команду.'));}, 5000);
      this.pending.set(id, {resolve,reject,timer});
      try {this.socket.send(JSON.stringify({id,method,params}));} catch (_) {clearTimeout(timer); this.pending.delete(id); reject(new Error('Окно Spotify недоступно.'));}
    });
  }
}
async function claimProfile(profile) {
  await fs.mkdir(profile,{recursive:true});
  const file=path.join(profile,'astra-spotify-helper.json');
  for(let attempt=0;attempt<3;attempt++) {
    try {
      const handle=await fs.open(file,'wx');
      await handle.writeFile(JSON.stringify({pid:process.pid})); await handle.close();
      return async()=>{try{if(JSON.parse(await fs.readFile(file,'utf8')).pid===process.pid)await fs.rm(file,{force:true});}catch(_){} };
    } catch(error) {
      if(error.code!=='EEXIST') throw error;
      let pid;
      try {pid=JSON.parse(await fs.readFile(file,'utf8')).pid;} catch(_) {throw new Error('Профиль Spotify занят. Повторите подключение.');}
      if(Number.isInteger(pid) && pid>0) {
        try {process.kill(pid,0);throw new Error('Spotify уже управляется другим процессом Astra.');}
        catch(error) {if(error.code!=='ESRCH')throw error;}
      }
      await fs.rm(file,{force:true});
    }
  }
  throw new Error('Не удалось подключить профиль Spotify.');
}
async function closeOrphanBrowser(profile) {
  const portFile=path.join(profile,'DevToolsActivePort');
  let port, endpoint;
  try {const lines=(await fs.readFile(portFile,'utf8')).trim().split(/\r?\n/);port=Number(lines[0]);endpoint=lines[1];}catch(_){return;}
  if(!Number.isInteger(port)||port<1||port>65535||!/^\/devtools\/browser\/[a-zA-Z0-9-]+$/.test(endpoint||''))return;
  let alive;
  try {alive=await fetch(`http://127.0.0.1:${port}/json/version`,{signal:AbortSignal.timeout(1000)});}catch(_){return;}
  if(!alive.ok)return;
  const info=await alive.json();
  if(info.webSocketDebuggerUrl!==`ws://127.0.0.1:${port}${endpoint}`)throw new Error('Не удалось проверить окно Spotify.');
  const connection=await CDP.connect(info.webSocketDebuggerUrl);
  try {await connection.call('Browser.close');} finally {connection.socket.close();}
  for(let n=0;n<60;n++) {
    await delay(100);
    try {await fetch(`http://127.0.0.1:${port}/json/version`,{signal:AbortSignal.timeout(300)});} catch(_){return;}
  }
  throw new Error('Прежнее окно Spotify ещё закрывается. Повторите подключение.');
}
async function main() {
  if (typeof WebSocket !== 'function') throw new Error('Для подключения Spotify требуется Node.js 22 или новее.');
  const [edge, profile, initial, mode = 'hidden'] = process.argv.slice(2);
  const releaseProfile=await claimProfile(profile);
  try {
  await closeOrphanBrowser(profile);
  await fs.mkdir(profile, {recursive:true});
  const portFile = path.join(profile, 'DevToolsActivePort');
  // Another running copy owns this profile. Preserve its discovery file and session.
  let existingPort = 0;
  try {existingPort=Number((await fs.readFile(portFile,'utf8')).split(/\r?\n/)[0]);} catch (_) {}
  if(existingPort > 0 && existingPort < 65536) {
    let existing;
    try {existing=await fetch(`http://127.0.0.1:${existingPort}/json/version`,{signal:AbortSignal.timeout(1500)});} catch (_) {}
    if(existing?.ok) throw new Error('Spotify уже запущен другим процессом Astra. Закройте прежнее подключение и повторите.');
  }
  await fs.rm(portFile, {force:true});
  const presentation = mode === 'visible' ? ['--app='+spotifyUrl(initial)] : ['--headless=new','--window-size=1280,900','--autoplay-policy=no-user-gesture-required','--auto-accept-this-tab-capture',spotifyUrl(initial)];
  const browser = spawn(edge, ['--user-data-dir='+profile, '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1', '--no-first-run', '--no-default-browser-check', '--disable-background-media-suspend', '--disable-background-timer-throttling', '--disable-renderer-backgrounding', ...presentation], {stdio:'ignore',windowsHide:mode !== 'visible'});
  let ended = false, page, catalog, browserConnection, busy = false, polling;
  let audioLease = '', audioScript = '', lastAudio = 0;
  const audioBinding = 'astraAudio' + randomBytes(16).toString('hex');
  const audioKey = 'astraCapture' + randomBytes(16).toString('hex');
  const meterKey = 'astraMeter' + randomBytes(16).toString('hex');
  let meterEnabled=false, meterPending=false, meterSuspended=false, lastMeterAttempt=0, meterTask=Promise.resolve();
  const stop = async () => {
    if (ended) return; ended = true; clearInterval(polling);
    try {await browserConnection?.call('Browser.close');} catch (_) {browser.kill();}
    page?.socket.close(); catalog?.socket.close(); browserConnection?.socket.close();
    await releaseProfile();
  };
  process.on('SIGTERM', ()=>void stop()); process.on('SIGINT', ()=>void stop());
  browser.on('exit', async () => {ended=true; clearInterval(polling); output({type:'closed',message:'Окно Spotify закрыто. Вход сохранён.'}); await releaseProfile(); process.exit(0);});
  browser.on('error', async () => {ended=true; output({type:'failure',message:'Не удалось открыть Microsoft Edge.'}); await releaseProfile(); process.exit(1);});
  try {
    let port, endpoint;
    for (let n=0; n<120 && !ended; n++) {
      try {const lines=(await fs.readFile(portFile,'utf8')).trim().split(/\r?\n/); port=Number(lines[0]); endpoint=lines[1]; if(port>0 && /^\/devtools\/browser\//.test(endpoint)) break;} catch (_) {}
      await delay(100);
    }
    if (!port || !endpoint) throw new Error('Не удалось запустить отдельное окно Spotify. Закройте его и повторите подключение.');
    browserConnection = await CDP.connect(`ws://127.0.0.1:${port}${endpoint}`);
    const attach = async () => {
      if (page?.socket.readyState === WebSocket.OPEN) return;
      const response=await fetch(`http://127.0.0.1:${port}/json/list`,{signal:AbortSignal.timeout(3000)});
      let target=(await response.json()).find(t=>t.type==='page' && /^https:\/\/(?:open\.spotify\.com|accounts\.spotify\.com)\//.test(t.url));
      if (!target) {
        const created=await browserConnection.call('Target.createTarget',{url:spotifyUrl(initial),background:mode==='hidden'});
        target=(await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find(t=>t.id===created.targetId);
      }
      if (!target) throw new Error('Не удалось восстановить страницу Spotify.');
      page=await CDP.connect(target.webSocketDebuggerUrl);
      page.onEvent = message => {
        if(message.method !== 'Runtime.bindingCalled' || message.params?.name !== audioBinding) return;
        try {
          const event=JSON.parse(message.params.payload);
          if(event.meter === meterKey && meterEnabled) {
            if(Array.isArray(event.bands) && event.bands.length===48 && event.bands.every(n=>Number.isFinite(n) && n>=0 && n<=1)) output({type:'visualizer',bands:event.bands});
            return;
          }
          if(!audioLease) return;
          if(event.lease !== audioLease) return;
          if(event.error) {output({type:'audio-failure',lease:audioLease}); return;}
          if(typeof event.pcm === 'string' && event.pcm.length === 10924 && /^[A-Za-z0-9+/=]+$/.test(event.pcm)) {
            lastAudio=Date.now(); output({type:'audio',lease:audioLease,pcm:event.pcm});
          }
        } catch(_) {}
      };
      await page.call('Runtime.enable');
      await page.call('Runtime.addBinding',{name:audioBinding});
    };
    const meterExpression = enabled => `(${spotifyMeter.toString()})(${JSON.stringify({key:meterKey,audioKey,binding:audioBinding,enabled})},${musicFrequencyBands.toString()})`;
    const stopMeter = async () => {
      await meterTask.catch(()=>{});
      await page.call('Runtime.evaluate',{expression:meterExpression(false),awaitPromise:true});
    };
    const syncMeter = async () => {
      if(meterPending || meterSuspended || !meterEnabled || Date.now()-lastMeterAttempt<2000) return;
      meterPending=true; lastMeterAttempt=Date.now();
      try {meterTask=page.call('Runtime.evaluate',{expression:meterExpression(true),awaitPromise:true,userGesture:true}); await meterTask;}
      catch(_) {} finally {meterPending=false;}
    };
    const captureExpression = lease => `(${spotifyAudio.toString()})(${JSON.stringify({key:audioKey,binding:audioBinding,lease})})`;
    const stopCapture = async lease => {
      if(!audioLease || lease && lease !== audioLease) return;
      const previousSuspended=meterSuspended; meterSuspended=true;
      try {
        await stopMeter();
        audioLease=''; lastAudio=0;
        if(audioScript) {await page.call('Page.removeScriptToEvaluateOnNewDocument',{identifier:audioScript});audioScript='';}
        await page.call('Runtime.evaluate',{expression:`(${spotifyAudio.toString()})(${JSON.stringify({key:audioKey,action:'stop'})})`,awaitPromise:true});
      } finally {meterSuspended=previousSuspended; lastMeterAttempt=0;}
    };
    const startCapture = async lease => {
      if(mode !== 'hidden' || !/^[a-f0-9]{32}$/.test(lease)) throw new Error('Трансляция Spotify доступна только без окна браузера.');
      meterSuspended=true;
      try {
        await stopCapture(); audioLease=lease;
        await stopMeter();
        await page.call('Runtime.enable');
        await page.call('Runtime.addBinding',{name:audioBinding});
        // A new Spotify document reattaches capture before its player starts audio.
        const source=captureExpression(lease)+`.catch(()=>window[${JSON.stringify(audioBinding)}](JSON.stringify({lease:${JSON.stringify(lease)},error:true})));`;
        audioScript=(await page.call('Page.addScriptToEvaluateOnNewDocument',{source})).identifier;
        const result=await page.call('Runtime.evaluate',{expression:captureExpression(lease),awaitPromise:true,returnByValue:true,userGesture:true});
        if(result.exceptionDetails || !result.result?.value?.local_suppressed || result.result.value.sample_rate !== 48000) throw new Error();
      } catch(_) {await stopCapture().catch(()=>{});throw new Error('Не удалось передать звук Spotify в Discord с отключением локального звучания.');}
      finally {meterSuspended=false; lastMeterAttempt=0;}
    };
    let lastTitle = '', lastPosition = 0, confirmedPlaying = false;
    const evaluate = async request => {
      await attach();
      const result=await page.call('Runtime.evaluate',{expression:`(${spotifyPage.toString()})(${JSON.stringify(request || {})})`,returnByValue:true});
      if(result.exceptionDetails || !result.result?.value) throw new Error('Страница Spotify ещё загружается.');
      const state = result.result.value;
      if (!request?.action) {
        if(state.title !== lastTitle || state.status !== 'attempting') confirmedPlaying = false;
        if(state.title === lastTitle && state.status === 'attempting' && state.position_seconds > lastPosition + .05) confirmedPlaying = true;
        lastTitle = state.title; lastPosition = state.position_seconds;
        if(confirmedPlaying && state.status === 'attempting') state.status = 'playing';
      }
      return state;
    };
    const readCatalog = async (connection, request) => {
      const result = await connection.call('Runtime.evaluate',{expression:`(${spotifyCatalog.toString()})(${JSON.stringify(request)})`,returnByValue:true});
      return result.result?.value || {pending:true};
    };
    const click = async (connection, point) => {
      await connection.call('Input.dispatchMouseEvent',{type:'mousePressed',x:point.x,y:point.y,button:'left',clickCount:1});
      await connection.call('Input.dispatchMouseEvent',{type:'mouseReleased',x:point.x,y:point.y,button:'left',clickCount:1});
    };
    const browse = async request => {
      const url = spotifyUrl(request.url);
      if (!catalog || catalog.socket.readyState !== WebSocket.OPEN) {
        const {targetId} = await browserConnection.call('Target.createTarget',{url:'about:blank',background:true});
        const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
        catalog = await CDP.connect(targets.find(t=>t.id===targetId).webSocketDebuggerUrl);
      }
      await catalog.call('Page.navigate',{url});
      let result, visitedHub = false, selectedMusic = false;
      for (let n=0;n<80;n++) {
        await delay(250); result=await readCatalog(catalog,request);
        if(result.error) throw new Error(result.error);
        if(request.kind==='mixes' && !result.playlists?.length && result.music_filter && !selectedMusic && !visitedHub) {
          selectedMusic = true; await click(catalog,result.music_filter); continue;
        }
        if(request.kind==='mixes' && !result.playlists?.length && result.hub_url && !visitedHub) {
          visitedHub = true; await catalog.call('Page.navigate',{url:spotifyUrl(result.hub_url)}); continue;
        }
        if(result.ready && ((result.tracks || result.playlists)?.length || ['playlists','mixes'].includes(request.kind) && n>=40 || n>=79)) return result;
      }
      throw new Error('Не удалось загрузить список Spotify. Повторите действие или откройте его окно.');
    };
    const sample = async () => {
      if(busy || ended) return; busy=true;
      try {
        if(audioLease && lastAudio && Date.now()-lastAudio > 15000) {const lease=audioLease;await stopCapture();output({type:'audio-failure',lease});}
        output({type:'state',state:await evaluate()});
        void syncMeter();
      } catch(error) {output({type:'state',state:{message:error.message,status:'ready'}});} finally {busy=false;}
    };
    output({type:'ready',browser_pid:browser.pid}); await sample(); polling=setInterval(sample,500);
    const input=readline.createInterface({input:process.stdin});
    let queue=Promise.resolve();
    input.on('line',line=>{ queue=queue.then(async()=>{
      let request, expectedTitle = '';
      try {
        request=JSON.parse(line);
        if(request.action==='close') {await stop(); return;}
        await attach();
        if(request.action==='visualizer') {
          meterEnabled=Boolean(request.enabled); lastMeterAttempt=0;
          if(meterEnabled) await syncMeter(); else await stopMeter();
          output({type:'reply',id:request.id,success:true}); return;
        } else if(request.action==='queue-next') {
          const point=await evaluate({action:'queue-open'});
          if(point.error) throw new Error(point.error);
          if(!point.done) {await click(page,point); await delay(250);}
          output({type:'reply',id:request.id,success:true,...await evaluate({action:'queue-read'})}); return;
        } else if(request.action==='capture-start') {
          await startCapture(request.lease);
          const volumePoint=await evaluate({action:'volume',value:1});
          if(volumePoint.error) {await stopCapture();throw new Error(volumePoint.error);}
          await click(page,volumePoint);
          output({type:'reply',id:request.id,success:true,local_suppressed:true}); return;
        } else if(request.action==='capture-stop') {
          if(audioLease===request.lease) {
            const volumePoint=await evaluate({action:'volume',value:request.value});
            if(!volumePoint.error && !volumePoint.done) await click(page,volumePoint);
          }
          await stopCapture(request.lease);
          output({type:'reply',id:request.id,success:true}); return;
        } else if(request.action==='browse') {
          output({type:'reply',id:request.id,success:true,catalog:await browse(request)}); return;
        } else if(request.action==='start') {
          confirmedPlaying=false; lastTitle=''; lastPosition=0;
          await page.call('Page.navigate',{url:spotifyUrl(request.url)});
          let point;
          for(let n=0;n<60;n++) {
            await delay(250); point=await readCatalog(page,{action:'start'});
            if(point.error) throw new Error(point.error);
            if(point.x !== undefined) break;
          }
          if(point?.x===undefined) throw new Error('Spotify не показал кнопку воспроизведения выбранной музыки.');
          expectedTitle=point.expected_title || '';
          if(!point.done) await click(page,point);
        } else if(request.action==='open') {
          if(request.url) await page.call('Page.navigate',{url:spotifyUrl(request.url)});
          if(mode==='visible') await page.call('Page.bringToFront');
        } else {
          const point=await evaluate(request);
          if(point.error) throw new Error(point.error);
          if(!point.done) {
            await click(page,point);
          }
        }
        await delay(200);
        output({type:'reply',id:request.id,success:true,expected_title:expectedTitle,state:await evaluate()});
      } catch(error) {output({type:'reply',id:request?.id,success:false,message:error.message});}
    });});
    input.on('close',()=>void stop());
  } catch(error) {await stop(); throw error;}
  } catch(error) {await releaseProfile();throw error;}
}
async function prepareLogin(profile) {
  const release=await claimProfile(profile);
  try {await closeOrphanBrowser(profile);} finally {await release();}
}
module.exports={spotifyUrl,CDP,claimProfile,prepareLogin};
if(require.main===module) (process.argv[2]==='--prepare-login' ? prepareLogin(process.argv[3]) : main()).catch(error=>{output({type:'failure',message:error.message});process.exitCode=1;});

