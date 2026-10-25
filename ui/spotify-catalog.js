/* Public music metadata from the user's signed-in Spotify pages. */
"use strict";
function spotifyCatalog(request = {}) {
  if (location.origin !== 'https://open.spotify.com') return {error:'Завершите вход в Spotify.'};
  const main = document.querySelector('main');
  const text = n => n?.textContent?.trim() || '';
  const loggedIn = Boolean(document.querySelector('[data-testid="user-widget-link"],[data-testid="user-widget"],[data-testid="user-widget-button"],button[aria-label="Профиль"],button[aria-label="Profile"]')) && !document.querySelector('[data-testid="login-button"]');
  if (!loggedIn || !main) return {pending:true};
  const picture = node => [...(node?.querySelectorAll('img') || [])].map(i=>i.currentSrc || i.src).find(src=>/^https:\/\/[^/]+\.scdn\.co\//.test(src)) || '';
  if (request.action === 'start') {
    const button = main.querySelector('[data-testid="play-button"]') || [...main.querySelectorAll('button')].find(b=>/^(play|воспроизвести|слушать)(\s|$)/i.test(b.getAttribute('aria-label') || ''));
    if (!button || button.disabled) return {pending:true};
    const r = button.getBoundingClientRect();
    return r.width && r.height ? {x:r.left+r.width/2,y:r.top+r.height/2,done:/pause|пауза|приостановить/i.test(button.getAttribute('aria-label') || ''),expected_title:location.pathname.startsWith('/track/') ? text(main.querySelector('h1')) : ''} : {pending:true};
  }
  if (request.kind === 'playlists') {
    const library = document.querySelector('[data-testid="YourLibrary"],[data-testid="your-library"],[aria-label="Моя медиатека"],[aria-label="Your Library"]');
    if (!library) return {pending:true};
    // The sidebar is a virtual list: rows exist only near the viewport, so collect them while scrolling.
    const store = globalThis.__astraSpotifyLibrary = globalThis.__astraSpotifyLibrary || {seen:{},list:{}};
    const clean = s => String(s || '').replace(/\s+/g,' ').trim().slice(0,300);
    const add = (id, title, card) => {
      if (id && clean(title) && !store.list[id]) store.list[id] = {service:'spotify',playlist_id:id,title:clean(title),url:id === 'liked' ? 'https://open.spotify.com/collection/tracks' : 'https://open.spotify.com/playlist/'+id,cover_url:picture(card)};
    };
    for (const p of library.querySelectorAll('[id^="listrow-title-spotify:"]')) {
      const uri = String(p.id || '').slice('listrow-title-'.length);
      if (!uri) continue;
      store.seen[uri] = true;
      const id = uri.match(/^spotify:playlist:([a-zA-Z0-9]+)$/)?.[1] || (/^spotify:(?:user:[^:]+:)?collection(?::tracks)?$/.test(uri) ? 'liked' : '');
      add(id, text(p), p.closest('[role="row"]'));
    }
    for (const a of library.querySelectorAll('a[href*="/playlist/"],a[href="/collection/tracks"]')) {
      const href = a.getAttribute('href'), id = href.match(/^\/playlist\/([a-zA-Z0-9]+)\/?$/)?.[1] || (href === '/collection/tracks' ? 'liked' : '');
      if (id) store.seen[id] = true;
      const card = a.closest('[data-testid="card"],[role="listitem"],li') || a.parentElement;
      add(id, text(a) || a.getAttribute('aria-label') || card?.querySelector('img')?.alt, card);
    }
    const playlists = Object.values(store.list).slice(0,100), empty = !Object.keys(store.seen).length;
    // A filter such as "Albums" hides playlists; clearing it only changes the plugin's own Edge profile.
    const clear = library.parentElement?.closest('nav,aside,section,[id*="Sidebar"]')?.querySelector('[aria-label="Очистить фильтры"],[aria-label="Clear filters"]');
    const playlistChip = clear && [...clear.closest('[role="listbox"]')?.querySelectorAll('[aria-checked="true"]') || []].some(c=>/^(?:Плейлисты|Playlists)$/i.test(c.getAttribute('aria-label') || text(c)));
    const clearRect = clear && !playlistChip && !playlists.length ? clear.getBoundingClientRect() : null;
    if (clearRect?.width && clearRect.height) return {ready:false,empty,playlists,clear_filter:{x:clearRect.left+clearRect.width/2,y:clearRect.top+clearRect.height/2}};
    let scroller = library;
    while (scroller && typeof getComputedStyle === 'function' && !(scroller.scrollHeight > scroller.clientHeight + 2 && /auto|scroll|overlay/.test(getComputedStyle(scroller).overflowY))) scroller = scroller.parentElement;
    if (!empty && scroller?.scrollHeight && scroller.scrollTop + scroller.clientHeight < scroller.scrollHeight - 2) {
      scroller.scrollTop += Math.max(100, scroller.clientHeight * .7);
      return {ready:false,empty,playlists};
    }
    return {ready:!empty,empty,playlists,partial:playlists.length >= 100};
  }
  if (request.kind === 'mixes') {
    const mixes = new Map();
    const library = document.querySelector('[data-testid="YourLibrary"],[data-testid="your-library"],[aria-label="Моя медиатека"],[aria-label="Your Library"]');
    const anchors = [...main.querySelectorAll('a[href*="/playlist/"]'),...(library?.querySelectorAll('a[href*="/playlist/"]') || [])];
    for (const a of anchors) {
      let path;
      try { const url = new URL(a.getAttribute('href'),location.origin); if (url.origin !== location.origin) continue; path = url.pathname; } catch { continue; }
      const id = path.match(/^\/(?:intl-[a-zA-Z-]+\/)?playlist\/([a-zA-Z0-9]+)\/?$/)?.[1];
      const card = a.closest('[data-testid="card"],[data-testid="card-container"]') || a.parentElement;
      const labels = [text(card?.querySelector('[data-testid="card-title"],h3,h2')),a.getAttribute('aria-label'),text(a),card?.querySelector('img')?.alt];
      const label = labels.map(s => String(s || '').replace(/\s+/g,' ').trim().replace(/^(?:play|listen to|воспроизвести|слушать)\s+/iu,'')).find(s => /^(?:daylist|(?:Daily Mix|Микс дня|Ежедневный микс)\s*\d*|Discover Weekly|Открытия недели)(?=$|[\s·.,:—–-])/iu.test(s));
      if (!id || !label) continue;
      const title = label.match(/^(?:daylist|(?:Daily Mix|Микс дня|Ежедневный микс)\s*\d*|Discover Weekly|Открытия недели)/iu)[0].trim();
      const daylist = /^daylist$/iu.test(title), daily = /mix|микс/iu.test(title);
      mixes.set(id,{service:'spotify',playlist_id:id,title,priority:daylist ? 0 : daily ? 1 : 2});
    }
    const hub = [...main.querySelectorAll('a[href^="/genre/"]')].find(a=>/made for you|для тебя|для вас|специально для/i.test(text(a) || a.getAttribute('aria-label') || ''));
    const hubPath = hub?.getAttribute('href');
    const musicButton = [...main.querySelectorAll('button')].find(b => /^(?:Music|Музыка|Música)$/iu.test(text(b)) && !b.disabled && b.getAttribute('aria-pressed') !== 'true');
    const rect = musicButton?.getBoundingClientRect();
    return {ready:!main.querySelector('[role="progressbar"],[data-testid="loading-indicator"]'),playlists:[...mixes.values()].sort((a,b)=>a.priority-b.priority || a.title.localeCompare(b.title)),hub_url:/^\/genre\/[a-zA-Z0-9-]+\/?$/.test(hubPath || '') ? 'https://open.spotify.com'+hubPath : '',music_filter:rect?.width && rect.height ? {x:rect.left+rect.width/2,y:rect.top+rect.height/2} : null,partial:true};
  }
  const tracks = new Map();
  for (const row of main.querySelectorAll('[data-testid="tracklist-row"],[data-testid="track-list-row"],[role="row"]')) {
    const a = row.querySelector('a[href*="/track/"]'), id = a?.getAttribute('href')?.match(/^\/track\/([a-zA-Z0-9]+)\/?$/)?.[1];
    if (!id) continue;
    const artists = [...row.querySelectorAll('a[href*="/artist/"]')].map(text).filter(Boolean);
    tracks.set(id,{service:'spotify',track_id:id,title:text(a).slice(0,300),artist:[...new Set(artists)].join(', ').slice(0,300),url:'https://open.spotify.com/track/'+id,cover_url:picture(row),extra:{}});
  }
  const loading = main.querySelector('[role="progressbar"],[data-testid="loading-indicator"]');
  return {ready:!loading,tracks:[...tracks.values()].slice(0,Number(request.limit)||20),partial:true};
}
if (typeof module !== 'undefined') module.exports = spotifyCatalog;
