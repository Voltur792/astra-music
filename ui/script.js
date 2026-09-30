/* Tab logic for the Music plugin.
 *
 * Three rules shape this file:
 *  - No window.prompt/alert/confirm: the iframe is sandboxed, those calls
 *    return nothing and the tab looks frozen. Tokens get real inputs.
 *  - No window.open/target=_blank: the sandbox is `allow-scripts allow-forms`,
 *    so the plugin process opens the track and reports how it went.
 *  - No native <select>: its popup is painted by the OS outside this document,
 *    in the OS colours, and ignores Astra's theme entirely. Menus are drawn here.
 */
(function () {
  "use strict";

  const SERVICES = [
    {
      id: "yandex",
      label: "Яндекс Музыка",
      token: "Токен аккаунта Музыки (Device Flow или oauth.yandex.ru)",
      extra: [],
    },
    { id: "vk", label: "VK Музыка", token: "VK API-токен (необязательно, старый способ)", extra: [] },
  ];

  const MODES = [
    { id: "auto", label: "Авто" },
    { id: "desktop", label: "Только приложение" },
    { id: "browser", label: "Только браузер" },
  ];

  const state = { service: "yandex", mode: "auto", status: {}, tracks: [], selectedTrack: null };
  const UI_STATE_KEY = "music-controller-ui-v1";
  const SEARCH_CACHE_VERSION = 2;
  let persistTimer = null;
  let refreshLegacyVkSearch = false;

  const $ = (id) => document.getElementById(id);

  function parseResult(result) {
    if (typeof result === "string") {
      try {
        return JSON.parse(result);
      } catch (_) {
        return { message: result };
      }
    }
    return result || {};
  }

  function callBackend(method, params) {
    if (!window.astra || !window.astra.callBackend) {
      return Promise.reject(new Error("Astra bridge недоступен: страница открыта вне Astra"));
    }
    return window.astra.callBackend(method, params || {}).then(parseResult);
  }

  function awaitBridge() {
    return new Promise((resolve, reject) => {
      if (window.astra && window.astra.callBackend) return resolve();
      let waited = 0;
      const timer = setInterval(() => {
        if (window.astra && window.astra.callBackend) {
          clearInterval(timer);
          resolve();
        } else if ((waited += 100) > 5000) {
          clearInterval(timer);
          reject(new Error("Astra bridge не появился за 5 секунд"));
        }
      }, 100);
    });
  }

  function persistUiState() {
    clearTimeout(persistTimer);
    persistTimer = setTimeout(() => {
      if (typeof window.astra?.setData !== "function") return;
      const value = JSON.stringify({
        query: $("query").value,
        service: state.service,
        search_cache_version: SEARCH_CACHE_VERSION,
        tracks: state.tracks,
        selectedTrack: state.selectedTrack,
      });
      window.astra.setData(UI_STATE_KEY, value).catch((error) => {
        console.warn("Astra Music: could not save search state", error);
      });
    }, 180);
  }

  async function restoreUiState() {
    if (typeof window.astra?.getData !== "function") return;
    try {
      const saved = parseResult(await window.astra.getData(UI_STATE_KEY));
      if (typeof saved.query === "string") $("query").value = saved.query;
      if (SERVICES.some((service) => service.id === saved.service)) {
        state.service = saved.service;
        serviceDropdown.set(saved.service);
      }
      refreshLegacyVkSearch = state.service === "vk" && saved.search_cache_version !== SEARCH_CACHE_VERSION
        && typeof saved.query === "string" && Boolean(saved.query.trim())
        && Array.isArray(saved.tracks) && saved.tracks.length > 0;
      state.tracks = Array.isArray(saved.tracks) && !refreshLegacyVkSearch
        ? saved.tracks.filter((track) => track && track.service === state.service).slice(0, 50)
        : [];
      $("results").replaceChildren(...state.tracks.map(trackRow));
      state.selectedTrack = saved.selectedTrack && typeof saved.selectedTrack === "object"
        ? saved.selectedTrack
        : null;
      const playback = parseResult(await callBackend("music_playback_status").catch(() => ({})));
      if (playback.track_id) {
        state.selectedTrack = {
          service: "yandex",
          track_id: String(playback.track_id),
          title: playback.title || "",
          artist: playback.artist || "",
        };
      }
      if (state.selectedTrack) {
        $("playerTitle").textContent = state.selectedTrack.service === "yandex" ? "Яндекс Музыка" : "Плеер";
        $("playerSubtitle").textContent = [state.selectedTrack.artist, state.selectedTrack.title].filter(Boolean).join(" — ");
        $("playerCard").hidden = false;
        if (!playback.track_id && state.selectedTrack.service === "yandex" && /^\d+$/.test(String(state.selectedTrack.track_id || ""))) {
          const prepared = await callBackend("music_yandex_start", {
            track_id: String(state.selectedTrack.track_id),
            title: state.selectedTrack.title || "",
            artist: state.selectedTrack.artist || "",
          });
          if (prepared.error) throw new Error(prepared.error);
        }
      }
    } catch (error) {
      console.warn("Astra Music: could not restore search state", error);
    }
  }

  function message(text, kind) {
    const node = $("message");
    node.textContent = text || "";
    node.classList.toggle("good", kind === "good");
    node.classList.toggle("bad", kind === "bad");
  }

  function setPill(node, text, kind) {
    node.textContent = text;
    node.classList.remove("good", "bad");
    if (kind) node.classList.add(kind);
  }

  function busy(button, isBusy, busyText) {
    if (!button) return;
    if (isBusy) {
      if (!button.dataset.label) button.dataset.label = button.textContent;
      button.textContent = busyText || "…";
      button.disabled = true;
    } else {
      if (button.dataset.label) button.textContent = button.dataset.label;
      button.disabled = false;
    }
  }

  // --- drawn dropdown -------------------------------------------------------

  function dropdown(trigger, menu, options, currentId, onPick) {
    let current = currentId;

    function label(id) {
      const found = options.filter((option) => option.id === id)[0];
      return found ? found.label : id;
    }

    function render() {
      menu.replaceChildren(
        ...options.map((option) => {
          const item = document.createElement("li");
          item.textContent = option.label;
          item.dataset.value = option.id;
          item.setAttribute("role", "option");
          item.setAttribute("aria-selected", String(option.id === current));
          item.addEventListener("click", () => {
            current = option.id;
            trigger.textContent = label(current);
            close();
            onPick(current);
          });
          return item;
        })
      );
    }

    function open() {
      menu.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
    }

    function close() {
      menu.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
    }

    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      if (menu.hidden) open();
      else close();
    });

    document.addEventListener("click", (event) => {
      if (!menu.hidden && !menu.contains(event.target)) close();
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") close();
    });

    render();
    trigger.textContent = label(current);

    return {
      set(id) {
        current = id;
        trigger.textContent = label(current);
        render();
      },
    };
  }

  // --- connection cards -----------------------------------------------------

  function field(service, option) {
    const input = document.createElement("input");
    input.type = option.secret ? "password" : "text";
    input.placeholder = option.label;
    input.autocomplete = "off";
    input.spellcheck = false;
    input.dataset.field = option.key;
    input.setAttribute("aria-label", service.label + ": " + option.label);
    return input;
  }

  function serviceCard(service) {
    const card = document.createElement("div");
    card.className = "service";
    card.dataset.service = service.id;

    const head = document.createElement("div");
    head.className = "head";
    const title = document.createElement("strong");
    title.textContent = service.label;
    const badge = document.createElement("span");
    badge.className = "pill";
    badge.textContent = "проверка…";
    head.append(title, badge);

    const fields = document.createElement("div");
    fields.className = "fields";
    fields.append(field(service, { key: "token", label: service.token, secret: true }));
    service.extra.forEach((option) => fields.append(field(service, option)));
    let manualAuth = null;
    if (service.id === "vk") {
      const cookies = document.createElement("textarea");
      cookies.rows = 4;
      cookies.placeholder = "Локальный экспорт cookies VK в формате JSON (Cookie-Editor)";
      cookies.autocomplete = "off";
      cookies.spellcheck = false;
      cookies.dataset.field = "session_cookies";
      cookies.setAttribute("aria-label", "VK Музыка: локальный экспорт cookies");
      fields.append(cookies);

      manualAuth = document.createElement("details");
      manualAuth.className = "service-help manual-auth";
      manualAuth.innerHTML = `
        <summary>Ручное подключение (старый способ)</summary>
        <div class="help-body">
          <p>Используйте этот вариант, если встроенное окно недоступно. Он требует API-токен и экспорт cookies; оба значения обрабатываются локально и сохраняются в папке данных Astra.</p>
        </div>`;
    }

    let help = null;
    if (service.id === "yandex") {
      help = document.createElement("details");
      help.className = "service-help";
      help.innerHTML = `
        <summary>Как подключить Яндекс Музыку и пользоваться</summary>
        <div class="help-body">
          <p><strong>Где взять токен.</strong> Открой ссылку ниже, войди в нужный аккаунт Яндекса и подтверди доступ. В адресе страницы найди значение после <code>access_token=</code> и до следующего символа <code>&amp;</code>. Скопируй только само значение токена.</p>
          <p><code>https://oauth.yandex.ru/authorize?response_type=token&amp;client_id=23cabbbdc6cd418abb4b39c32c41195d</code></p>
          <p>Вставь токен в поле выше и нажми «Сохранить», затем «Проверить». Для прослушивания нужна активная подписка Яндекс Плюс. Токен принимается именно от аккаунта Яндекс Музыки; токен обычного OAuth-приложения может не подойти.</p>
          <p><strong>Как пользоваться.</strong> Выбери Яндекс Музыку в поиске и введи исполнителя или песню. «Открыть в Яндекс Музыке» откроет приложение, если оно установлено, иначе страницу сервиса; начало воспроизведения подтверди в самом плеере. В чате попроси: <em>«Найди в Яндекс Музыке …»</em> или <em>«Открой песню … в Яндекс Музыке»</em>.</p>
        </div>`;
    } else if (service.id === "vk") {
      help = document.createElement("details");
      help.className = "service-help";
      help.innerHTML = `
        <summary>Как подключить VK Музыку и пользоваться</summary>
        <div class="help-body">
          <p>Нажмите «Войти через VK»: откроется окно самого VK. Войдите удобным способом, в том числе по QR-коду, если VK предложит его. Astra сохранит локальную сессию; отдельный API-токен и ручной экспорт cookies не нужны.</p>
          <p>Пароль вводится только на странице VK. Окно служит для авторизации, а поиск и воспроизведение остаются во вкладке и плеере Astra. Данные сессии не передаются в чат.</p>
        </div>`;
    }

    const actions = document.createElement("div");
    actions.className = "row actions";
    const save = document.createElement("button");
    save.type = "button";
    save.className = "primary";
    save.dataset.action = "save";
    save.textContent = "Сохранить";
    const test = document.createElement("button");
    test.type = "button";
    test.dataset.action = "test";
    test.textContent = "Проверить";
    const forget = document.createElement("button");
    forget.type = "button";
    forget.className = "danger";
    forget.textContent = "Забыть";
    forget.hidden = true;
    if (service.id === "vk") {
      const login = document.createElement("button");
      login.type = "button";
      login.className = "primary";
      login.textContent = "Войти через VK";
      login.setAttribute("aria-label", "Войти в VK Музыку через окно авторизации");
      login.addEventListener("click", () => vkOAuthLogin(card, login));
      actions.append(login, test, forget);
      manualAuth.querySelector(".help-body").append(fields, save);
    } else {
      actions.append(save, test, forget);
    }

    save.addEventListener("click", () => saveToken(service, card));
    test.addEventListener("click", () => testToken(service, card));
    forget.addEventListener("click", () => forgetToken(service, card));

    card.append(head);
    if (manualAuth) card.append(manualAuth);
    else card.append(fields);
    if (help) card.append(help);
    card.append(actions);
    return card;
  }

  function fieldValue(card, key) {
    const input = card.querySelector('[data-field="' + key + '"]');
    return input ? input.value.trim() : "";
  }

  function clearTokenFields(card) {
    card.querySelectorAll("input, textarea").forEach((input) => {
      input.value = "";
    });
  }

  async function saveToken(service, card) {
    const token = fieldValue(card, "token");
    const sessionCookies = fieldValue(card, "session_cookies");
    if (!token && !(service.id === "vk" && sessionCookies)) {
      message("Сначала введите токен " + service.label + " или вставьте cookies, если токен уже сохранён.");
      card.querySelector('[data-field="token"]').focus();
      return;
    }
    const button = card.querySelector('[data-action="save"]');
    busy(button, true, "Проверяю…");
    try {
      const result = await callBackend("music_save_token", {
        service: service.id,
        token: token,
        session_cookies: sessionCookies,
      });
      if (result.error) throw new Error(result.error);
      clearTokenFields(card);
      const account = result.connection && result.connection.account;
      const plan = result.connection && result.connection.subscription ? ", подписка есть" : ", подписка не найдена";
      if (service.id === "vk") {
        message(result.connection && result.connection.session_saved
          ? "Сессия VK сохранена" + (result.connection.audio_account ? ": " + result.connection.audio_account : "") + ". Проверьте доступ к музыке поиском VK."
          : "VK API-токен сохранён. Для входа во встроенный плеер нажмите «Войти через VK».", "good");
      } else {
        message(service.label + ": токен принят" + (account ? ", аккаунт " + account : "") + plan + ".", "good");
      }
      await refreshStatus();
    } catch (error) {
      message(service.label + ": " + error.message, "bad");
      setPill(card.querySelector(".pill"), "ошибка", "bad");
    } finally {
      busy(button, false);
    }
  }

  async function vkOAuthLogin(card, button) {
    busy(button, true, "Открываю окно…");
    message("Запускаю локальное подключение VK…");
    try {
      const started = await callBackend("music_vk_oauth_login");
      if (started.error) throw new Error(started.error);
      if (!started.job_id) throw new Error("Astra не вернула номер задачи входа VK.");

      const startedAt = Date.now();
      let lastMessage = "";
      let lastButtonStatus = "";
      const buttonLabels = {
        preparing: "Подготавливаю…",
        waiting_for_login: "Жду вход в VK…",
        validating: "Проверяю сессию…",
      };
      let result;
      while (Date.now() - startedAt < 12 * 60 * 1000) {
        const status = await callBackend("music_vk_oauth_status", { job_id: started.job_id });
        if (status.error) throw new Error(status.error);
        if (status.status !== lastButtonStatus) {
          lastButtonStatus = status.status;
          busy(button, true, buttonLabels[status.status] || "Вход VK…");
        }
        if (status.message && status.message !== lastMessage) {
          lastMessage = status.message;
          message(status.message);
        }
        if (status.status === "completed") {
          result = status;
          break;
        }
        if (status.status === "failed") throw new Error(status.error || "Вход VK не завершён.");
        await new Promise((resolve) => setTimeout(resolve, 900));
      }
      if (!result) throw new Error("Вход VK выполняется дольше 12 минут. Проверьте окно авторизации и попробуйте ещё раз.");

      clearTokenFields(card);
      const connection = result.connection || {};
      const sessionValid = connection.session_saved && connection.session_valid;
      message(sessionValid
        ? "Вход и сессия VK подтверждены" + (connection.audio_account ? ": " + connection.audio_account : "") + ". Поиск и доступ к аудио проверьте отдельно."
        : "VK не подтвердил действительность сессии. Нажмите «Войти через VK» и авторизуйтесь заново.", sessionValid ? "good" : "bad");
      await refreshStatus();
    } catch (error) {
      message("Не удалось войти в VK: " + error.message, "bad");
    } finally {
      busy(button, false);
    }
  }

  async function testToken(service, card) {
    const button = card.querySelector('[data-action="test"]');
    busy(button, true, "Проверяю…");
    try {
      const result = await callBackend("music_test_connection", { service: service.id });
      if (result.error) throw new Error(result.error);
      const plus = result.subscription ? ", подписка " + result.subscription : "";
      if (service.id === "vk" && result.session_saved && result.session_valid) {
        setPill(card.querySelector(".pill"), "сессия действительна", "good");
        message("VK подтвердил локальную сессию. Этот тест не проверяет поиск и плейлисты; проверьте их отдельными запросами.", "good");
      } else if (service.id === "vk" && result.session_saved) {
        setPill(card.querySelector(".pill"), "нужен новый вход VK", "bad");
        message("Cookies сохранены, но VK не подтвердил действующую сессию. Нажмите «Войти через VK» и авторизуйтесь заново.", "bad");
      } else if (service.id === "vk" && !result.audio_ready) {
        setPill(card.querySelector(".pill"), "нужен вход VK", "bad");
        message("VK API-токен отвечает, но локальной сессии для встроенной музыки нет. Нажмите «Войти через VK»." + (result.audio_error ? " " + result.audio_error : ""), "bad");
      } else {
        setPill(card.querySelector(".pill"), "подключена: " + (result.account || "ок"), "good");
        message(service.label + " отвечает" + plus + ".", "good");
      }
    } catch (error) {
      setPill(card.querySelector(".pill"), "ошибка", "bad");
      message(service.label + ": " + error.message, "bad");
    } finally {
      busy(button, false);
    }
  }

  async function forgetToken(service, card) {
    try {
      const result = await callBackend("music_remove_token", { service: service.id });
      if (result.error) throw new Error(result.error);
      clearTokenFields(card);
      message(service.label + ": токен удалён.", "good");
      await refreshStatus();
    } catch (error) {
      message(service.label + ": " + error.message, "bad");
    }
  }

  async function refreshStatus() {
    const result = await callBackend("music_connection_status");
    if (result.error) throw new Error(result.error);
    state.status = result.services || {};
    if (result.open_mode) {
      state.mode = result.open_mode;
      modeDropdown.set(result.open_mode);
    }
    $("desktop").textContent = result.desktop_player
      ? "Приложение Яндекс Музыки найдено: " + result.desktop_player
      : "Приложение Яндекс Музыки не найдено — треки будут открываться в браузере.";
    document.querySelectorAll(".service").forEach((card) => {
      const info = state.status[card.dataset.service] || {};
      const isVk = card.dataset.service === "vk";
      const badgeText = !info.configured
        ? "не подключена"
        : isVk
          ? info.session_valid ? "сессия VK подтверждена · аудио не проверено" : info.audio_session ? "сессия VK не подтверждена · войдите заново" : "API подключён · нужен вход VK"
          : "подключена" + (info.subscription ? ", " + info.subscription : "");
      setPill(
        card.querySelector(".pill"),
        badgeText,
        info.configured ? (isVk && !info.session_valid ? "bad" : "good") : "bad"
      );
      card.querySelector("button.danger").hidden = !info.configured;
    });
    const connected = Object.keys(state.status).filter((key) => state.status[key].configured);
    setPill(
      $("status"),
      connected.length ? connected.length + " из " + Object.keys(state.status).length : "нет подключений",
      connected.length ? "good" : "bad"
    );
    if (!state.status[state.service] || !state.status[state.service].configured) {
      if (connected[0]) {
        state.service = connected[0];
        serviceDropdown.set(connected[0]);
        state.tracks = [];
        $("results").replaceChildren();
        $("playlists").replaceChildren();
        $("playlistTracks").replaceChildren();
        $("radioBtn").disabled = state.service !== "yandex";
        persistUiState();
      }
    }
  }

  // --- tracks and playlists -------------------------------------------------

  function trackRow(track) {
    const row = document.createElement("div");
    row.className = "item";

    const who = document.createElement("div");
    who.className = "who";
    const title = document.createElement("div");
    title.className = "title";
    title.textContent = track.title || "Без названия";
    const sub = document.createElement("div");
    sub.className = "sub";
    sub.textContent = [track.artist, track.album].filter(Boolean).join(" — ") || "неизвестный исполнитель";
    who.append(title, sub);

    const actions = document.createElement("div");
    actions.className = "actions";
    const play = document.createElement("button");
    play.type = "button";
    play.className = "primary";
    play.textContent = ["yandex", "vk"].includes(track.service) ? "Играть в Astra" : "Открыть трек";
    play.addEventListener("click", () => {
      if (track.service === "yandex") playYandexInline(track);
      else if (track.service === "vk") playVkInline(track);
      else runTrack("music_play_track", track);
    });
    const open = document.createElement("button");
    open.type = "button";
    open.textContent = "Открыть";
    open.addEventListener("click", () => runTrack("music_open_track", track));
    actions.append(play, open);

    row.append(who, actions);
    return row;
  }

  async function playYandexInline(track) {
    const id = String(track.track_id || "").trim();
    if (!/^\d+$/.test(id)) {
      message("Не удалось запустить встроенный плеер: неверный ID трека.", "bad");
      return;
    }
    message("Получаю ссылку на поток Яндекс Музыки…");
    try {
      const result = await callBackend("music_yandex_start", {
        track_id: id, title: track.title || "", artist: track.artist || "",
        cover_url: track.cover_url || "", auto_play: true,
      });
      if (result.error) throw new Error(result.error);
      $("playerTitle").textContent = "Яндекс Музыка";
      $("playerSubtitle").textContent = [track.artist, track.title].filter(Boolean).join(" — ");
      $("playerCard").hidden = false;
      state.selectedTrack = { service: "yandex", track_id: id, title: track.title || "", artist: track.artist || "" };
      persistUiState();
      $("playerCard").scrollIntoView({ behavior: "smooth", block: "nearest" });
      message("Передала трек встроенному плееру Astra. Если автозапуск заблокирован, нажмите ▶ в карточке.", "good");
    } catch (error) {
      message(error.message + " Можно открыть трек кнопкой «Открыть».", "bad");
    }
  }

  async function playVkInline(track) {
    const id = String(track.track_id || "").trim();
    const extra = track.extra && typeof track.extra === "object" ? track.extra : {};
    if (!/^\d+$/.test(id) || !String(extra.owner_id || "")) {
      message("VK не передал ID владельца трека. Обновите поиск и попробуйте ещё раз.", "bad");
      return;
    }
    message("Получаю VK HLS-поток для встроенного плеера…");
    try {
      const result = await callBackend("music_play_track", {
        service: "vk",
        track_id: id,
        title: track.title || "",
        artist: track.artist || "",
        album: track.album || "",
        url: track.url || "",
        context_id: track.context_id || "",
        cover_url: track.cover_url || "",
        extra,
      });
      if (result.error) throw new Error(result.error);
      $("playerTitle").textContent = "VK Музыка";
      $("playerSubtitle").textContent = [track.artist, track.title].filter(Boolean).join(" — ");
      $("playerCard").hidden = false;
      state.selectedTrack = { service: "vk", track_id: id, title: track.title || "", artist: track.artist || "" };
      persistUiState();
      $("playerCard").scrollIntoView({ behavior: "smooth", block: "nearest" });
      message("VK-трек передан встроенному плееру Astra.", "good");
    } catch (error) {
      message(error.message + " Проверьте подключение VK в карточке сервиса.", "bad");
    }
  }

  $("closePlayer").addEventListener("click", () => {
    callBackend("music_playback_stop").catch(() => {});
    $("playerCard").hidden = true;
    state.selectedTrack = null;
    persistUiState();
  });

  // The whole track object goes to the backend: a bare id costs an extra API
  // call, and for VK an id alone is not enough to build a link (owner_id).
  async function runTrack(method, track) {
    message("Открываю трек…");
    try {
      const result = await callBackend(method, {
        service: track.service,
        track_id: track.track_id,
        title: track.title,
        artist: track.artist,
        album: track.album,
        url: track.url,
        context_id: track.context_id,
        extra: track.extra || {},
      });
      if (result.error) throw new Error(result.error);
      const name = (track.artist ? track.artist + " — " : "") + (track.title || "");
      if (result.playing) {
        message("Играет: " + name, "good");
        return;
      }
      if (result.opened_via === "desktop") {
        message("Открыто в приложении Яндекс Музыка: " + name + ". Если не заиграло само — нажмите «Воспроизвести».", "good");
        return;
      }
      if (result.opened_via === "browser") {
        message("Открыто в браузере: " + name + ". Нажмите «Воспроизвести» в плеере.", "good");
        return;
      }
      offerLink(result.url || track.url || "");
    } catch (error) {
      message(error.message, "bad");
    }
  }

  function offerLink(url) {
    const box = $("linkBox");
    if (!url) {
      box.hidden = true;
      message("Ссылка на трек не известна.", "bad");
      return;
    }
    $("linkValue").value = url;
    box.hidden = false;
    $("linkValue").select();
    message("Открыть не удалось — ссылка в поле ниже.", "bad");
  }

  async function searchTracks() {
    const query = $("query").value.trim();
    if (!query) {
      message("Введите исполнителя или название трека.");
      $("query").focus();
      return;
    }
    const service = state.service;
    state.tracks = [];
    $("results").replaceChildren();
    persistUiState();
    const button = $("searchBtn");
    busy(button, true, "Ищу…");
    try {
      const result = await callBackend("music_search", { service, query: query, limit: 12 });
      if (service !== state.service) return;
      if (result.error) throw new Error(result.error);
      const tracks = result.tracks || [];
      state.tracks = tracks;
      $("results").replaceChildren(...tracks.map(trackRow));
      persistUiState();
      message(tracks.length ? "Найдено треков: " + tracks.length : "Ничего не найдено.", tracks.length ? "good" : "");
    } catch (error) {
      if (service === state.service) message(error.message, "bad");
    } finally {
      busy(button, false);
    }
  }

  async function loadPlaylists() {
    const service = state.service;
    $("playlists").replaceChildren();
    $("playlistTracks").replaceChildren();
    const button = $("playlistBtn");
    busy(button, true, "Загрузка…");
    try {
      const result = await callBackend("music_list_playlists", { service });
      if (service !== state.service) return;
      if (result.error) throw new Error(result.error);
      const playlists = result.playlists || [];
      $("playlistTracks").replaceChildren();
      $("playlists").replaceChildren(
        ...playlists.map((playlist) => {
          const row = document.createElement("div");
          row.className = "item";
          const who = document.createElement("div");
          who.className = "who";
          const title = document.createElement("div");
          title.className = "title";
          title.textContent = playlist.title || "Без названия";
          const sub = document.createElement("div");
          sub.className = "sub";
          sub.textContent = playlist.track_count ? playlist.track_count + " треков" : "плейлист";
          who.append(title, sub);
          const actions = document.createElement("div");
          actions.className = "actions";
          if (["yandex", "vk"].includes(playlist.service)) {
            const playAll = document.createElement("button");
            playAll.type = "button";
            playAll.className = "primary";
            playAll.textContent = "Играть в Astra";
            playAll.addEventListener("click", () => playlist.service === "vk"
              ? playVkPlaylistInline(playlist)
              : playYandexPlaylistInline(playlist));
            actions.append(playAll);
          }
          const open = document.createElement("button");
          open.type = "button";
          open.textContent = "Треки";
          open.addEventListener("click", () => loadPlaylistTracks(playlist));
          actions.append(open);
          row.append(who, actions);
          return row;
        })
      );
      message(playlists.length ? "Плейлистов: " + playlists.length : "Плейлистов нет.", "");
    } catch (error) {
      if (service === state.service) message(error.message, "bad");
    } finally {
      busy(button, false);
    }
  }

  async function loadPlaylistTracks(playlist) {
    const service = state.service;
    const list = $("playlistTracks");
    list.replaceChildren();
    message("Загружаю треки плейлиста…");
    try {
      const result = await callBackend("music_list_playlist_tracks", {
        service: playlist.service,
        playlist_id: playlist.playlist_id,
        limit: 50,
      });
      if (service !== state.service) return;
      if (result.error) throw new Error(result.error);
      const tracks = result.tracks || [];
      list.replaceChildren(...tracks.map(trackRow));
      message("Треков в плейлисте: " + tracks.length, "");
    } catch (error) {
      if (service === state.service) message(error.message, "bad");
    }
  }

  async function playYandexPlaylistInline(playlist) {
    message("Загружаю плейлист во встроенный плеер…");
    try {
      const result = await callBackend("music_play_playlist", {
        service: "yandex",
        playlist_id: playlist.playlist_id,
        playlist_name: playlist.title || "",
      });
      if (result.error) throw new Error(result.error);
      const track = result.track || {};
      $("playerTitle").textContent = "Яндекс Музыка";
      $("playerSubtitle").textContent = [track.artist, track.title].filter(Boolean).join(" — ") || playlist.title;
      $("playerCard").hidden = false;
      state.selectedTrack = { service: "yandex", track_id: track.track_id || "", title: track.title || playlist.title || "", artist: track.artist || "" };
      persistUiState();
      message("Плейлист «" + playlist.title + "» передан плееру Astra (" + (result.queue_count || 0) + " треков).", "good");
    } catch (error) {
      message(error.message, "bad");
    }
  }

  async function playVkPlaylistInline(playlist) {
    message("Загружаю VK-плейлист во встроенный плеер…");
    try {
      const result = await callBackend("music_play_playlist", {
        service: "vk",
        playlist_id: playlist.playlist_id,
        playlist_name: playlist.title || "",
      });
      if (result.error) throw new Error(result.error);
      const track = result.track || {};
      $("playerTitle").textContent = "VK Музыка";
      $("playerSubtitle").textContent = [track.artist, track.title].filter(Boolean).join(" — ") || playlist.title;
      $("playerCard").hidden = false;
      state.selectedTrack = { service: "vk", track_id: track.track_id || "", title: track.title || playlist.title || "", artist: track.artist || "" };
      persistUiState();
      message("VK-плейлист «" + playlist.title + "» передан плееру Astra (" + (result.queue_count || 0) + " треков).", "good");
    } catch (error) {
      message(error.message, "bad");
    }
  }

  async function playYandexRadioInline() {
    if (state.service !== "yandex") {
      message("Сначала выберите Яндекс Музыку.");
      return;
    }
    message("Подключаю «Мою волну» Яндекс Музыки…");
    try {
      const result = await callBackend("music_play_yandex_radio", { station: "user:onyourwave" });
      if (result.error) throw new Error(result.error);
      const track = result.track || {};
      $("playerTitle").textContent = "Яндекс Музыка · Моя волна";
      $("playerSubtitle").textContent = [track.artist, track.title].filter(Boolean).join(" — ");
      $("playerCard").hidden = false;
      state.selectedTrack = { service: "yandex", track_id: track.track_id || "", title: track.title || "Моя волна", artist: track.artist || "" };
      persistUiState();
      message("«Моя волна» передана встроенному плееру Astra.", "good");
    } catch (error) {
      message(error.message, "bad");
    }
  }

  // --- init -----------------------------------------------------------------

  $("myVkMusicBtn").addEventListener("click", async () => {
    const button = $("myVkMusicBtn");
    button.disabled = true;
    message("Загружаю мои треки ВК в исходном порядке…");
    try {
      const started = await callBackend("music_play_vk_my_music");
      if (started.error) throw new Error(started.error);
      if (!started.job_id) throw new Error("Плагин не вернул номер запуска. Перезапустите Astra после обновления.");
      let result;
      for (let attempt = 0; attempt < 180; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 750));
        const job = await callBackend("music_vk_my_music_status", { job_id: started.job_id });
        if (job.error || job.status === "failed") throw new Error(job.error || "Не удалось загрузить мои треки ВК.");
        if (job.status === "ready") { result = job.result; break; }
        message(job.message || "Загружаю мои треки ВК…");
      }
      if (!result) throw new Error("Загрузка ВК занимает слишком много времени. Проверьте интернет и повторите.");
      if (result.error) throw new Error(result.error);
      const track = result.track || {};
      $("playerTitle").textContent = "VK · Мои треки";
      $("playerSubtitle").textContent = [track.artist, track.title].filter(Boolean).join(" — ");
      $("playerCard").hidden = false;
      state.selectedTrack = { service: "vk", ...track };
      persistUiState();
      // Preparing a stream is not proof that the browser started playing it.
      let playback;
      for (let attempt = 0; attempt < 20; attempt++) {
        playback = await callBackend("music_playback_state");
        if (Number(playback.revision) !== Number(result.revision)) break;
        if (["playing", "blocked", "failed"].includes(playback.status)) break;
        await new Promise(resolve => setTimeout(resolve, 500));
      }
      if (Number(playback?.revision) !== Number(result.revision)) throw new Error("Плеер переключился на другой запрос.");
      if (playback.status === "playing") {
        message("Играют мои треки ВК (" + (result.queue_count || 0) + " треков).", "good");
      } else if (playback.status === "blocked") {
        message("Мои треки ВК загружены, но Astra заблокировала автозапуск. Нажмите ▶ в музыкальном виджете на главной странице.", "bad");
      } else {
        message("Мои треки ВК загружены. Откройте главную страницу и нажмите ▶ в музыкальном виджете.", "");
      }
    } catch (error) {
      message(error.message, "bad");
    } finally {
      button.disabled = false;
    }
  });

  const serviceDropdown = dropdown($("serviceTrigger"), $("serviceMenu"), SERVICES, state.service, (id) => {
    if (state.service === id) return;
    state.service = id;
    state.tracks = [];
    $("results").replaceChildren();
    $("playlists").replaceChildren();
    $("playlistTracks").replaceChildren();
    $("radioBtn").disabled = id !== "yandex";
    message("");
    persistUiState();
  });
  $("radioBtn").disabled = state.service !== "yandex";
  $("radioBtn").addEventListener("click", playYandexRadioInline);

  const modeDropdown = dropdown($("modeTrigger"), $("modeMenu"), MODES, state.mode, (id) => {
    state.mode = id;
    callBackend("music_set_open_mode", { mode: id })
      .then((result) => {
        if (result.error) throw new Error(result.error);
        message("Треки будут открываться так: " + id, "good");
      })
      .catch((error) => message(error.message, "bad"));
  });

  $("searchForm").addEventListener("submit", (event) => {
    event.preventDefault();
    searchTracks();
  });
  $("searchBtn").addEventListener("click", searchTracks);
  $("query").addEventListener("input", persistUiState);
  $("playlistBtn").addEventListener("click", loadPlaylists);
  $("services").replaceChildren(...SERVICES.map(serviceCard));

  awaitBridge()
    .then(async () => {
      await restoreUiState();
      await refreshStatus();
      if (refreshLegacyVkSearch && state.service === "vk" && state.status.vk?.configured) {
        refreshLegacyVkSearch = false;
        await searchTracks();
      }
    })
    .catch((error) => {
      setPill($("status"), "мост недоступен", "bad");
      message(error.message, "bad");
    });
})();
