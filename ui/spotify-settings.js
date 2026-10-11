(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  let working = false, polling = false, errorUntil = 0;
  const parse = value => typeof value === 'string' ? JSON.parse(value) : value || {};
  const call = async (method, params = {}) => parse(await window.astra.callBackend(method, params));
  function render(state) {
    $('spotifyHidden').checked = state.hidden !== false;
    const signedIn = state.logged_in === true && (state.running || state.active);
    $('spotifyStatus').textContent = state.connecting ? 'Проверяю вход…' : signedIn ? state.audio_route === 'discord' ? 'Вход выполнен · звук в Discord' : state.active ? 'Вход выполнен · подключён' : 'Вход выполнен' : state.login_open ? 'Ожидаю входа в Edge' : state.session_saved ? 'Вход сохранён · подключите управление' : 'Вход не выполнен';
    $('spotifyStatus').classList.toggle('good', signedIn);
    window.dispatchEvent(new CustomEvent('spotify-status', {detail: state}));
    $('spotifyOpen').textContent = signedIn ? 'Войти в другой аккаунт' : 'Войти в Spotify';
    $('spotifyLoginHelp').textContent = signedIn ? 'Вы вошли в Spotify. Повторный вход не нужен. Музыка и плейлисты доступны через Astra; сохранённый вход восстановится при следующем запуске.' : state.session_saved ? 'Профиль входа сохранён на этом компьютере. Нажмите «Управлять из Astra», чтобы проверить подключение. Если сессия истекла, войдите снова через Edge.' : 'Войдите в Spotify в обычном окне Edge, закройте это окно и нажмите «Управлять из Astra». Вход сохраняется на этом компьютере.';
    $('spotifyActivate').disabled = working || state.connecting || state.login_open || (!state.session_saved && !state.running);
    $('spotifyClose').disabled = working || !state.running;
    if(Date.now() >= errorUntil) $('spotifyMessage').textContent = state.connection_error || (state.connecting ? 'Жду загрузки Spotify. Это может занять до минуты.' : state.message || [state.artist, state.title].filter(Boolean).join(' — ') || 'Найдите песню или выберите свой плейлист ниже.');
  }
  async function sync() {
    if(polling || working || !window.astra?.callBackend) return;
    polling = true;
    try {render(await call('music_spotify_status'));} catch (_) { /* Bridge may still be starting. */ }
    finally {polling = false;}
  }
  async function action(method, params = {}) {
    if(working) return;
    working = true;
    const buttons = [...$('spotifySettings').querySelectorAll('button'), $('spotifyMix')];
    buttons.forEach(n=>n.disabled=true);
    try {
      const result = method === 'music_spotify_mix' ? await window.astraSpotifyJob('mix') : method === 'music_spotify_start' ? await window.astraSpotifyJob('start', params) : await call(method, params);
      if(result.error || result.success === false) throw new Error(result.error || result.message);
      errorUntil = 0; render(result);
    } catch(error) {
      $('spotifyMessage').textContent = error.message || 'Не удалось подключить Spotify.';
      if (method === 'music_spotify_mix') $('spotifyMixMessage').textContent = error.message || 'Не удалось включить микс Spotify.';
      errorUntil = Date.now() + 10000;
    } finally {working=false; buttons.forEach(n=>n.disabled=false); void sync();}
  }
  $('spotifyOpen').addEventListener('click',()=>void action('music_spotify_login'));
  $('spotifyMix').addEventListener('click', async () => {
    if (working) return;
    $('spotifyMixMessage').textContent = 'Загружаю ваш микс Spotify…';
    await action('music_spotify_mix');
      if (!errorUntil) $('spotifyMixMessage').textContent = 'Микс Spotify выбран. Управление — в виджете плеера.';
  });
  $('spotifyActivate').addEventListener('click',()=>void action('music_spotify_activate'));
  $('spotifyClose').addEventListener('click',()=>void action('music_spotify_close'));
  $('spotifyHidden').addEventListener('change',()=>void action('music_spotify_presentation',{hidden:$('spotifyHidden').checked}));
  $('spotifyForget').addEventListener('click',()=>void action('music_spotify_forget'));
  $('spotifySearchForm').addEventListener('submit', event=>{
    event.preventDefault();
    const value=$('spotifyQuery').value.trim();
    if(/^https?:\/\//i.test(value)) void action('music_spotify_start',{url:value});
    else if(value) window.dispatchEvent(new CustomEvent('spotify-search',{detail:value}));
  });
  const timer = setInterval(sync, 1200); void sync();
  window.addEventListener('beforeunload',()=>clearInterval(timer));
})();
