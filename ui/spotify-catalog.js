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
    const list = new Map();
    for (const a of library.querySelectorAll('a[href*="/playlist/"],a[href="/collection/tracks"]')) {
      const href = a.getAttribute('href'), id = href.match(/^\/playlist\/([a-zA-Z0-9]+)\/?$/)?.[1] || (href === '/collection/tracks' ? 'liked' : '');
      if (!id) continue;
      const card = a.closest('[data-testid="card"],[role="listitem"],li') || a.parentElement;
      const title = text(a) || a.getAttribute('aria-label') || card?.querySelector('img')?.alt;
      if (title) list.set(id,{service:'spotify',playlist_id:id,title:title.slice(0,300),url:'https://open.spotify.com'+href,cover_url:picture(card)});
    }
    return {ready:true,playlists:[...list.values()].slice(0,100),partial:true};
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
