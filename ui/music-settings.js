(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const tabs = [...document.querySelectorAll('[data-music-tab]')];
  function openTab(name, focus = false) {
    tabs.forEach(tab => { const active = tab.dataset.musicTab === name; tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1; if (active && focus) tab.focus(); });
    document.querySelectorAll('[data-music-panel]').forEach(panel => panel.hidden = panel.dataset.musicPanel !== name);
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => openTab(tab.dataset.musicTab));
    tab.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      openTab(tabs[next].dataset.musicTab, true);
    });
  });
  window.addEventListener('spotify-search', () => openTab('library'));
  let settings = { enabled: false, every: 3, when: 'end', announce_next: true }, loaded = false, saving = false, dirty = false;
  const controls = [...$('radioSettings').querySelectorAll('input, [data-radio-every], [data-radio-when]')];
  const parse = value => typeof value === 'string' ? JSON.parse(value) : value;
  function render() {
    $('radioEnabled').checked = settings.enabled;
    $('radioNext').checked = settings.announce_next;
    controls.forEach(control => control.disabled = !loaded);
    document.querySelectorAll('[data-radio-every]').forEach(button => button.setAttribute('aria-pressed', String(Number(button.dataset.radioEvery) === settings.every)));
    document.querySelectorAll('[data-radio-when]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.radioWhen === settings.when)));
  }
  async function save() {
    if (!loaded || saving) return;
    saving = true; dirty = false; $('radioSaveStatus').textContent = 'Сохранение…';
    try {
      const result = parse(await window.astra.callBackend('music_radio_set', {settings: {...settings}}));
      if (result.error || result.ok === false) throw new Error(result.error || 'Не удалось сохранить');
      $('radioSaveStatus').textContent = 'Сохранено'; $('radioRetry').hidden = true;
    } catch (_) { $('radioSaveStatus').textContent = 'Не сохранено'; $('radioRetry').hidden = false; }
    finally { saving = false; }
    if (dirty) void save();
  }
  $('radioEnabled').addEventListener('change', () => { settings.enabled = $('radioEnabled').checked; dirty = true; void save(); });
  $('radioNext').addEventListener('change', () => { settings.announce_next = $('radioNext').checked; dirty = true; void save(); });
  document.querySelectorAll('[data-radio-every]').forEach(button => button.addEventListener('click', () => { settings.every = Number(button.dataset.radioEvery); dirty = true; render(); void save(); }));
  document.querySelectorAll('[data-radio-when]').forEach(button => button.addEventListener('click', () => { settings.when = button.dataset.radioWhen; dirty = true; render(); void save(); }));
  $('radioRetry').addEventListener('click', () => loaded ? void save() : void sync());
  async function sync() {
    if (!window.astra?.callBackend || saving) return;
    try {
      const result = parse(await window.astra.callBackend('music_radio_get'));
      if (!result.settings || result.error) throw new Error('Настройки недоступны');
      if (!loaded) { settings = {...settings, ...result.settings}; loaded = true; render(); $('radioSaveStatus').textContent = 'Сохранено'; $('radioRetry').hidden = true; }
      $('radioMessage').textContent = result.message || '';
      $('radioProgress').textContent = result.progress || '';
    } catch (_) { if (!loaded) { $('radioSaveStatus').textContent = 'Настройки недоступны'; $('radioRetry').hidden = false; } }
  }
  const timer = setInterval(sync, 2500); void sync();
  window.addEventListener('beforeunload', () => clearInterval(timer));
})();
