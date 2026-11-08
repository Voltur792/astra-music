(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let stations = [], favorites = [], offset = 0, busy = false, generation = 0;
  const message = text => { $('stationMessage').textContent = text; };
  async function call(method, params = {}) {
    let result = await window.astra.callBackend(method, params);
    for (let n = 0; n < 5; n++) {
      if (typeof result === 'string') result = JSON.parse(result);
      else if (typeof result?.result_json === 'string') result = result.result_json;
      else break;
    }
    if (result?.error) throw new Error(result.error);
    return result;
  }
  function renderList(container, items, saved) {
    container.replaceChildren();
    if (!items.length) {
      const empty = document.createElement('p'); empty.className = 'hint';
      empty.textContent = saved ? 'Сохраните станции из каталога или добавьте свою ссылку.' : 'Станции не найдены. Попробуйте другое название или уберите фильтры.';
      container.append(empty); return;
    }
    for (const station of items) {
      const row = document.createElement('div'); row.className = 'station-row';
      const info = document.createElement('div'); info.className = 'station-info';
      const title = document.createElement('strong'); title.textContent = station.name;
      const details = document.createElement('span'); details.className = 'hint';
      details.textContent = [station.country, station.hls ? 'HLS' : station.codec, station.bitrate ? station.bitrate + ' кбит/с' : '', station.tags].filter(Boolean).join(' · ');
      info.append(title, details);
      const play = document.createElement('button'); play.type = 'button'; play.textContent = 'Слушать'; play.setAttribute('aria-label', 'Слушать ' + station.name);
      play.addEventListener('click', async () => {
        play.disabled = true; message('Подключаю ' + station.name + '…');
        try { await call('music_stations_play', {station}); message('Станция передана плееру. Состояние эфира — в виджете.'); }
        catch (error) { message(error.message); }
        finally { play.disabled = false; }
      });
      const save = document.createElement('button'); save.type = 'button';
      const isSaved = saved || favorites.some(item => item.station_id === station.station_id);
      save.textContent = isSaved ? 'Убрать' : 'В избранное'; save.setAttribute('aria-label', (isSaved ? 'Убрать из избранного: ' : 'В избранное: ') + station.name);
      save.addEventListener('click', async () => {
        save.disabled = true;
        try {
          favorites = (await call('music_stations_saved', {station: {...station, stationuuid: station.station_id}, remove: isSaved})).stations;
          render();
        } catch (error) { message(error.message); save.disabled = false; }
      });
      row.append(info, play, save); container.append(row);
    }
  }
  function render() { renderList($('stationResults'), stations, false); renderList($('stationFavorites'), favorites, true); }
  async function search(reset = true) {
    if (busy) return;
    busy = true; const request = ++generation;
    if (reset) offset = 0;
    $('stationSearch').disabled = true; $('stationMore').disabled = true;
    message('Загружаю каталог радио…');
    try {
      let result = await call('music_stations_search', {query: $('stationQuery').value, country: $('stationCountry').value, tag: $('stationGenre').value, offset});
      for (let attempt = 0; result.pending && attempt < 150; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 400));
        result = await call('music_stations_search', {operation: 'poll', job_id: result.job_id});
      }
      if (result.pending) throw new Error('Каталог не ответил вовремя. Повторите поиск позже.');
      if (request !== generation) return;
      stations = reset ? result.stations : [...stations, ...result.stations];
      offset = result.offset + 50;
      $('stationMore').hidden = !result.has_more;
      render(); message(result.message || `Показано станций: ${stations.length}. Источник: Radio Browser.`);
    } catch (error) { message(error.message); }
    finally { busy = false; $('stationSearch').disabled = false; $('stationMore').disabled = false; }
  }
  $('stationForm').addEventListener('submit', event => { event.preventDefault(); void search(); });
  $('stationMore').addEventListener('click', () => search(false));
  $('stationCustom').addEventListener('submit', async event => {
    event.preventDefault(); const button = $('stationCustomSave'); button.disabled = true;
    try {
      const station = {name: $('stationName').value, url: $('stationUrl').value};
      favorites = (await call('music_stations_saved', {station})).stations;
      render(); message('Своя станция сохранена в избранном.'); $('stationCustom').reset();
    } catch (error) { message(error.message); }
    finally { button.disabled = false; }
  });
  let loaded = false;
  window.addEventListener('music-tab-change', async event => {
    if (event.detail !== 'stations' || loaded) return;
    loaded = true;
    try { favorites = (await call('music_stations_saved')).stations; render(); }
    catch (error) { message(error.message); loaded = false; }
    void search();
  });
})();
