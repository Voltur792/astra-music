/* Read only player metadata, never login fields, cookies or network tokens. */
"use strict";
function spotifyPage(request = {}) {
  if (location.origin !== "https://open.spotify.com") return {logged_in: false, message: "Завершите вход в окне Spotify."};
  const find = name => document.querySelector(`[data-testid="${name}"]`);
  const text = element => element?.textContent?.trim() || "";
  const footer = find('now-playing-bar');
  const progress = find('playback-progressbar')?.querySelector('[role="slider"],input[type="range"]');
  const volume = find('volume-bar')?.querySelector('[role="slider"],input[type="range"]');
  const number = (element, attr) => Number(element?.getAttribute(attr) ?? 0);
  const metadata = navigator.mediaSession?.metadata;
  const link = find('context-item-link') || footer?.querySelector('a[href*="/track/"]');
  const title = metadata?.title || text(link);
  const track = link?.getAttribute('href')?.match(/\/track\/([a-zA-Z0-9]+)/)?.[1] || "";
  // Spotify's Canvas videos are unrelated to the song. The player range uses
  // milliseconds; hidden native inputs snap .value to step, so read the React
  // value attribute for the precise position.
  const position = progress?.tagName === 'INPUT' ? number(progress, 'value') / 1000 : number(progress, 'aria-valuenow');
  const duration = progress?.tagName === 'INPUT' ? Number(progress.max) / 1000 : number(progress, 'aria-valuemax');
  const playing = /pause|пауза/i.test(find('control-button-playpause')?.getAttribute('aria-label') || '');
  const loggedIn = Boolean(find('user-widget-link') || find('user-widget') || find('user-widget-button') || document.querySelector('button[aria-label="Профиль"],button[aria-label="Profile"]')) && !find('login-button');
  const controls = {
    'queue-open': find('control-button-queue'),
    play: find('control-button-playpause'), pause: find('control-button-playpause'),
    next: find('control-button-skip-forward'), previous: find('control-button-skip-back'),
    seek: find('playback-progressbar')?.querySelector('[data-testid="progress-bar"]'),
    volume: find('volume-bar')?.querySelector('[data-testid="progress-bar"]')
  };
  const enabled = element => Boolean(element && !element.disabled && element.getAttribute('aria-disabled') !== 'true');
  if (request.action === 'queue-read') {
    const current = {title: String(title).slice(0,300), artist: String(metadata?.artist || text(find('context-item-info-artist'))).slice(0,300)};
    // Read only the actual queue, excluding its separate Now Playing row and
    // all playlist/library tables. DOM order preserves manually queued songs.
    const grids = [...document.querySelectorAll('ul[role="treegrid"]')];
    const now = grids.find(grid => /^(сейчас играет|now playing)$/i.test(grid.getAttribute('aria-label') || ''));
    const rowMetadata = grid => {
      const row = grid?.querySelector('[role="row"]');
      const group = row?.querySelector('[role="group"][aria-labelledby]');
      const label = group?.getAttribute('aria-labelledby')?.split(/\s+/)[0];
      const name = label ? text(document.getElementById(label)) : '';
      const artists = [...(row?.querySelectorAll('a[href*="/artist/"]') || [])].map(text).filter(Boolean);
      return name ? {title:name.slice(0,300), artist:artists.join(', ').slice(0,300)} : null;
    };
    const playingRow = rowMetadata(now);
    if (!loggedIn || !playingRow || playingRow.title !== current.title || playingRow.artist !== current.artist) return {current, next_track:null};
    // An active repeat control can mean repeat-one; without a reliable mode
    // value its queue must not be promised as the next audible song.
    const repeat = find('control-button-repeat');
    if (repeat && ['true', 'mixed'].includes(repeat.getAttribute('aria-checked'))) return {current, next_track:null};
    const next = grids.find(grid => /^(далее|следующ|next)/i.test(grid.getAttribute('aria-label') || '') && grid.querySelector('[role="row"]'));
    return {current, next_track:rowMetadata(next)};
  }
  if (request.action) {
    if (!loggedIn) return {error: "Войдите в Spotify и выберите песню в его окне."};
    if (request.action === 'queue-open' && controls['queue-open']?.getAttribute('aria-pressed') === 'true') return {done:true};
    if (['play', 'pause'].includes(request.action) && playing === (request.action === 'play')) return {done: true};
    const element = controls[request.action];
    if (!enabled(element)) return {error: "Эта команда сейчас недоступна в Spotify. Проверьте его окно."};
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return {error: "Плеер Spotify ещё загружается."};
    let ratio = .5;
    if (request.action === 'volume') ratio = Math.max(0, Math.min(1, request.value));
    if (request.action === 'seek') {
      if (!Number.isFinite(duration) || duration <= 0) return {error: "Spotify не разрешил перемотку текущего трека."};
      ratio = Math.max(0, Math.min(1, request.value / duration));
    }
    return {x: rect.left + Math.max(1, Math.min(rect.width - 1, rect.width * ratio)), y: rect.top + rect.height / 2};
  }
  const size = item => {const n=parseInt(item.sizes || '0');return n>0 && n<=300 ? n+10000 : n;};
  const pictures = [...(metadata?.artwork || [])].sort((a,b)=>size(b)-size(a)).map(item=>item.src);
  pictures.push(document.querySelector('[data-testid="cover-art-image"],[data-testid="context-item-info"] img')?.src, footer?.querySelector('img')?.src);
  const cover = pictures.find(src=>/^https:\/\/[^/]+\.scdn\.co\//.test(src || '')) || '';
  return {
    logged_in: loggedIn, track_id: title ? track || 'spotify-web' : '', title,
    artist: metadata?.artist || text(find('context-item-info-artist')), cover_url: cover,
    status: title ? playing ? 'attempting' : 'paused' : 'ready',
    position_seconds: position, duration_seconds: duration,
    volume: volume ? volume.tagName === 'INPUT' ? number(volume, 'value') / (Number(volume.max) || 1) : number(volume, 'aria-valuenow') / (number(volume, 'aria-valuemax') || 1) : null,
    can_next: enabled(controls.next), can_previous: enabled(controls.previous), can_seek: enabled(progress),
    message: loggedIn ? title ? '' : 'Выберите песню или плейлист в окне Spotify.' : 'Войдите в аккаунт в окне Spotify.'
  };
}
if (typeof module !== 'undefined') module.exports = spotifyPage;
