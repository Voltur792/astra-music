const test = require('node:test');
const assert = require('node:assert/strict');
const spotifyPage = require('../ui/spotify-page.js');
const {spotifyUrl} = require('../ui/spotify-browser.cjs');

test('personal mix prefers daylist and skips editorial playlists',()=>{
  const catalog=require('../ui/spotify-catalog.js');
  const link=(id,title)=>({textContent:title,getAttribute:n=>n==='href'?'/playlist/'+id:null,closest:()=>null,parentElement:null});
  const main={querySelector:()=>null,querySelectorAll:s=>s.includes('/playlist/') ? [link('editorial','Top Hits'),link('weekly','Discover Weekly'),link('daily','Daily Mix 1'),link('day','daylist')] : []};
  global.location={origin:'https://open.spotify.com',pathname:'/'};
  global.document={querySelector:s=>s==='main'?main:s.includes('user-widget')?{}:null};
  assert.deepEqual(catalog({kind:'mixes'}).playlists.map(p=>p.playlist_id),['day','daily','weekly']);
});

test('mixes recognize card headings, compound labels, query links and library cards',()=>{
 const catalog=require('../ui/spotify-catalog.js');
 const link=(href,label,heading='')=>({textContent:label,getAttribute:n=>n==='href'?href:n==='aria-label'?label:null,closest:()=>heading?{querySelector:s=>s.includes('card-title')?{textContent:heading}:null}:null,parentElement:null});
 const home=[link('/playlist/editorial','Top Hits'),link('/playlist/daily?si=test','Play Daily Mix 1. Metal, rock'),link('https://evil.test/playlist/bad','daylist')];
 const library=[link('https://open.spotify.com/intl-ru/playlist/day?si=test','Other label','daylist · evening')];
 const main={querySelector:()=>null,querySelectorAll:s=>s.includes('/playlist/')?home:[]};
 global.location={origin:'https://open.spotify.com',pathname:'/'};
 global.document={querySelector:s=>s==='main'?main:s.includes('user-widget')?{}:s.includes('YourLibrary')?{querySelectorAll:()=>library}:null};
 assert.deepEqual(catalog({kind:'mixes'}).playlists.map(p=>p.playlist_id),['day','daily']);
 assert.equal(spotifyUrl('https://open.spotify.com/genre/made-for-x-hub'),'https://open.spotify.com/genre/made-for-x-hub');
});

test('mix browsing can reveal Music when the home page is filtered to podcasts',()=>{
 const catalog=require('../ui/spotify-catalog.js');
 const button={textContent:'Музыка',getAttribute:()=>null,getBoundingClientRect:()=>({left:10,top:20,width:80,height:30})};
 const main={querySelector:()=>null,querySelectorAll:s=>s==='button'?[button]:[]};
 global.location={origin:'https://open.spotify.com',pathname:'/'};
 global.document={querySelector:s=>s==='main'?main:s.includes('user-widget')?{}:null};
 assert.deepEqual(catalog({kind:'mixes'}).music_filter,{x:50,y:35});
 button.getAttribute=()=> 'true';assert.equal(catalog({kind:'mixes'}).music_filter,null);
});

function fixture() {
  const element = (attrs={}, rect={left:10,top:20,width:100,height:12}) => ({
    tagName:'DIV', disabled:false, getAttribute:name=>attrs[name] ?? null,
    getBoundingClientRect:()=>rect, querySelector:()=>null, textContent:''
  });
  const progressInput=element({value:'31388'}); progressInput.tagName='INPUT'; progressInput.max='155623';
  // .value snaps to a 5 second step. The React attribute remains precise.
  progressInput.value='30000';
  const volumeInput=element({value:'.3'}); volumeInput.tagName='INPUT'; volumeInput.max='1';
  const seekBar=element(), volumeBar=element({}, {left:100,top:30,width:80,height:12});
  const ids={
    'user-widget-link':element(),
    'control-button-playpause':element({'aria-label':'Пауза'}),
    'control-button-skip-forward':element(),
    'control-button-skip-back':element(),
    'playback-progressbar':{querySelector:selector=>selector.includes('progress-bar') ? seekBar : progressInput},
    'volume-bar':{querySelector:selector=>selector.includes('progress-bar') ? volumeBar : volumeInput}
  };
  global.location={origin:'https://open.spotify.com'};
  Object.defineProperty(global,'navigator',{value:{mediaSession:{metadata:{title:'Song',artist:'Artist',artwork:[]},playbackState:'none'}}, configurable:true});
  global.document={querySelector:selector=>ids[selector.match(/data-testid="([^"]+)/)?.[1]] || null,
    querySelectorAll:()=>[{tagName:'VIDEO',readyState:4,duration:8,currentTime:.5,paused:false}]};
  return {ids,seekBar,volumeBar};
}

test('song timing ignores Spotify Canvas video and native range rounding',()=>{
  fixture();
  const state=spotifyPage();
  assert.equal(state.position_seconds,31.388);
  assert.equal(state.duration_seconds,155.623);
  assert.equal(state.volume,.3);
  assert.equal(state.status,'attempting'); // Transport confirms actual progress.
});

test('slider commands target the visible custom bar, not hidden native input',()=>{
  fixture();
  const volume=spotifyPage({action:'volume',value:.25});
  assert.equal(volume.x,120); assert.equal(volume.y,36);
  const seek=spotifyPage({action:'seek',value:155.623/2});
  assert.equal(seek.x,60);
});

test('login pages and Spotify restrictions never produce a successful command',()=>{
  const {ids}=fixture();
  ids['control-button-skip-forward'].disabled=true;
  assert.match(spotifyPage({action:'next'}).error,/недоступна/);
  ids['login-button']={};
  assert.match(spotifyPage({action:'play'}).error,/Войдите/);
  global.location.origin='https://accounts.google.com';
  assert.equal(spotifyPage().logged_in,false);
});

test('browser navigation only accepts Spotify player links',()=>{
  assert.equal(spotifyUrl('https://open.spotify.com/playlist/abc?si=private'),'https://open.spotify.com/playlist/abc');
  assert.throws(()=>spotifyUrl('https://open.spotify.com.evil.test/'));
  assert.throws(()=>spotifyUrl('https://open.spotify.com@evil.test/'));
  assert.throws(()=>spotifyUrl('http://open.spotify.com/'));
});

test('cover selection skips inaccessible metadata and uses the player image',()=>{
  fixture();
  navigator.mediaSession.metadata.artwork=[{src:'blob:unusable',sizes:'640x640'}];
  const query=document.querySelector;
  document.querySelector=selector=>selector.includes('cover-art-image') ? {src:'https://i.scdn.co/image/album'} : query(selector);
  assert.equal(spotifyPage().cover_url,'https://i.scdn.co/image/album');
});

test('catalog reads personal playlists only from the library sidebar',()=>{
  const catalog=require('../ui/spotify-catalog.js');
  const playlist={textContent:'My playlist',getAttribute:n=>n==='href'?'/playlist/abc':null,closest:()=>null,parentElement:null};
  const sidebar={querySelectorAll:()=>[playlist]};
  global.location={origin:'https://open.spotify.com',pathname:'/'};
  global.document={querySelector:s=>s==='main'?{}:s.includes('user-widget')?{}:s.includes('YourLibrary')?sidebar:null};
  delete globalThis.__astraSpotifyLibrary;
  const result=catalog({kind:'playlists'});
  assert.equal(result.playlists.length,1);
  assert.equal(result.playlists[0].playlist_id,'abc');
  assert.equal(result.playlists[0].title,'My playlist');
  global.location.origin='https://accounts.google.com';
  assert(catalog({kind:'playlists'}).error);
});


test('catalog reads virtual library rows, liked songs and waits for an empty sidebar',()=>{
  const catalog=require('../ui/spotify-catalog.js');
  const row=(uri,title)=>({id:'listrow-title-'+uri,textContent:title,closest:()=>({querySelectorAll:()=>[{src:'https://i.scdn.co/image/x'}]})});
  let rows=[];
  const sidebar={querySelectorAll:s=>s.includes('listrow-title-')?rows:[],getAttribute:()=>null};
  global.location={origin:'https://open.spotify.com',pathname:'/'};
  global.document={querySelector:s=>s==='main'?{}:s.includes('user-widget')?{}:s.includes('YourLibrary')?sidebar:null};
  delete globalThis.__astraSpotifyLibrary;
  const waiting=catalog({kind:'playlists'});
  assert.equal(waiting.ready,false);
  assert.equal(waiting.empty,true);
  rows=[row('spotify:playlist:5Kaf','Мой плейлист № 2'),row('spotify:user:me:collection','Любимые треки'),row('spotify:album:zzz','Album')];
  const result=catalog({kind:'playlists'});
  assert.equal(result.ready,true);
  assert.deepEqual(result.playlists.map(p=>[p.playlist_id,p.title]),[['5Kaf','Мой плейлист № 2'],['liked','Любимые треки']]);
  assert.equal(result.playlists[0].url,'https://open.spotify.com/playlist/5Kaf');
  assert.equal(result.playlists[1].url,'https://open.spotify.com/collection/tracks');
  delete globalThis.__astraSpotifyLibrary;
});

test('queue reads only ordered Next rows and excludes Now Playing and playlist order',()=>{
  const {ids}=fixture();
  const groups=new Map();
  const grid=(label,title,artist,key)=>{
    groups.set(key,{textContent:title});
    const row={querySelector:()=>({getAttribute:()=>key}),querySelectorAll:()=>[{textContent:artist}]};
    return {getAttribute:()=>label,querySelector:()=>row};
  };
  const now=grid('Сейчас играет','Song','Artist','now');
  const playlist=grid('Playlist','Wrong','Wrong','playlist');
  const manual=grid('Next in queue','Queued first','Artist 2','manual');
  const auto=grid('Далее','Automatic','Artist 3','auto');
  document.querySelectorAll=()=>[playlist,now,manual,auto];
  document.getElementById=id=>groups.get(id);
  assert.deepEqual(spotifyPage({action:'queue-read'}),{current:{title:'Song',artist:'Artist'},next_track:{title:'Queued first',artist:'Artist 2'}});
  ids['control-button-repeat']={getAttribute:()=> 'mixed'};
  assert.equal(spotifyPage({action:'queue-read'}).next_track,null);
  delete ids['control-button-repeat'];
  document.querySelectorAll=()=>[now,auto];
  assert.equal(spotifyPage({action:'queue-read'}).next_track.title,'Automatic');
  document.querySelectorAll=()=>[now];
  assert.equal(spotifyPage({action:'queue-read'}).next_track,null);
  groups.set('now',{textContent:'Old song'});
  document.querySelectorAll=()=>[now,manual];
  assert.equal(spotifyPage({action:'queue-read'}).next_track,null);
});

test('opening an already open queue does not toggle it closed',()=>{
  const {ids}=fixture();
  ids['control-button-queue']={getAttribute:()=> 'true'};
  assert.deepEqual(spotifyPage({action:'queue-open'}),{done:true});
});
