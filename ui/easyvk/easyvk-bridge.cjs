"use strict";

const fs = require("fs");
const path = require("path");
const { createRequire } = require("module");

const runtimeDir = process.env.ASTRA_EASYVK_RUNTIME_DIR;
const runtimeRequire = createRequire(path.join(runtimeDir || process.cwd(), "package.json"));
const AudioAPI = runtimeRequire("easyvk-audio");
const { Cookie, CookieJar } = runtimeRequire("tough-cookie");
const HTMLParser = runtimeRequire("node-html-parser");
const fileCookieStoreModule = runtimeRequire("tough-cookie-file-store");
const FileCookieStore = fileCookieStoreModule.FileCookieStore || fileCookieStoreModule.default || fileCookieStoreModule;
const RESULT_PREFIX = "ASTRA_EASYVK_RESULT:";
const { identity, normalizeAudios, ownTracks } = require("./audio-identity.cjs");
let requestInput = {};

function emit(value) {
  process.stdout.write(RESULT_PREFIX + JSON.stringify(value) + "\n", () => process.exit());
}

function safeError(error, secrets) {
  let message = String(error?.message || error || "VK вернул неизвестную ошибку");
  for (const secret of secrets) {
    if (secret) message = message.split(String(secret)).join("[скрыто]");
  }
  if (/Cannot read properties of undefined \(reading ['\"]playlist['\"]\)/i.test(message)) {
    message = "VK вернул новый или неполный формат ответа аудиопоиска; текущая версия EasyVK не может разобрать результаты.";
  } else if (/\bAccess Denied\b/i.test(message)) {
    message = "VK отказал в доступе к этому аудио-разделу. Проверьте вход в VK и доступ аккаунта к музыке.";
  }
  return message.replace(/[\r\n\t]+/g, " ").slice(0, 500);
}

function requestSecrets() {
  const secrets = [requestInput.token, requestInput.cookies_json];
  try {
    const parsed = JSON.parse(String(requestInput.cookies_json || ""));
    const rows = Array.isArray(parsed) ? parsed : Array.isArray(parsed?.cookies) ? parsed.cookies : [];
    for (const row of rows) {
      if (row?.value) secrets.push(String(row.value));
    }
  } catch (_) {}
  return secrets;
}

function resolveCookiePath(value) {
  const requested = String(value || "");
  const filename = path.basename(requested);
  const dataDirectory = process.env.ASTRA_MUSIC_DATA_DIR ||
    (process.env.APPDATA ? path.join(process.env.APPDATA, "music-controller") : "");
  if (!requested || !filename || !dataDirectory) return requested;

  const expectedDirectory = path.resolve(dataDirectory);
  const requestedDirectory = path.resolve(path.dirname(requested));
  // The plugin sends this path over stdin. Older Windows runtimes can encode
  // that text with the active code page while Node reads UTF-8, corrupting a
  // Cyrillic user profile. Rebuild the known plugin data path from the Unicode
  // Windows environment block and keep only the filename from the input.
  if (
    requested.includes("\uFFFD") ||
    requestedDirectory.toLowerCase() !== expectedDirectory.toLowerCase()
  ) {
    return path.join(expectedDirectory, filename);
  }
  return requested;
}

function cookieHost(rawDomain) {
  const domain = String(rawDomain || "").trim().toLowerCase().replace(/^\./, "");
  if (domain !== "vk.com" && !domain.endsWith(".vk.com") && domain !== "vk.ru" && !domain.endsWith(".vk.ru")) return "";
  return domain;
}

function authCookieAlias(domain, key) {
  if (!/^(?:remixsid\d*|remixstid|p)$/i.test(key)) return "";
  if (domain === "vk.com" || domain.endsWith(".vk.com")) return "vk.ru";
  if (domain === "vk.ru" || domain.endsWith(".vk.ru")) return "vk.com";
  return "";
}

async function importCookies(raw, cookiePath) {
  let parsed;
  try {
    parsed = JSON.parse(String(raw || ""));
  } catch (_) {
    throw new Error("Cookies должны быть JSON-массивом из Cookie-Editor.");
  }
  const rows = Array.isArray(parsed) ? parsed : parsed && Array.isArray(parsed.cookies) ? parsed.cookies : [];
  if (!rows.length) throw new Error("В JSON нет списка cookies.");

  fs.mkdirSync(path.dirname(cookiePath), { recursive: true });
  try { fs.unlinkSync(cookiePath); } catch (_) {}
  const jar = new CookieJar(new FileCookieStore(cookiePath));
  let accepted = 0;
  let hasSession = false;

  for (const row of rows) {
    const domain = cookieHost(row?.domain);
    const key = String(row?.name || row?.key || "").trim();
    const value = String(row?.value ?? "");
    if (!domain || !key || /[\r\n;=]/.test(key) || /[\r\n]/.test(value)) continue;

    const options = {
      key,
      value,
      domain,
      path: String(row.path || "/").startsWith("/") ? String(row.path || "/") : "/",
      secure: Boolean(row.secure),
      httpOnly: Boolean(row.httpOnly),
      hostOnly: row.hostOnly === undefined ? !String(row.domain || "").startsWith(".") : Boolean(row.hostOnly),
    };
    const expiry = Number(row.expirationDate || row.expires || 0);
    if (!row.session && expiry > 0) options.expires = new Date(expiry < 100000000000 ? expiry * 1000 : expiry);
    const sameSite = String(row.sameSite || "").toLowerCase();
    if (["strict", "lax", "none"].includes(sameSite)) options.sameSite = sameSite;

    const cookie = new Cookie(options);
    const url = `https://${domain}${options.path}`;
    await jar.setCookie(cookie, url);
    const alias = authCookieAlias(domain, key);
    if (alias) {
      // VK login may now finish on vk.ru while the audio API still calls vk.com.
      // Copy only first-party auth cookies between VK's own domains.
      const aliasOptions = { ...options, domain: alias };
      await jar.setCookie(new Cookie(aliasOptions), `https://${alias}${options.path}`);
    }
    accepted += 1;
    if (/^remixsid\d*$/i.test(key)) hasSession = true;
  }

  if (!accepted) throw new Error("Не нашёл cookies доменов vk.com или vk.ru. Экспортируйте cookies VK после входа.");
  if (!hasSession) throw new Error("В экспорте нет сессионной cookie VK (remixsid). Экспортируйте cookies vk.com или vk.ru после входа в аккаунт.");
}

async function verifyWebSession(cookiePath) {
  const jar = new CookieJar(new FileCookieStore(cookiePath));
  const isVkHost = host =>
    host === "vk.com" || host.endsWith(".vk.com") ||
    host === "vk.ru" || host.endsWith(".vk.ru");
  const isLoginUrl = target =>
    /(^|\.)login\./i.test(target.hostname) || /\/(?:login|auth|authorize)(?:\/|$)/i.test(target.pathname);
  const failures = [];

  // VK login can issue its active session on either first-party domain. Probe
  // both instead of treating a redirect from vk.com as proof that vk.ru cookies
  // are expired (or vice versa).
  for (const startUrl of ["https://vk.com/feed", "https://vk.ru/feed"]) {
    let url = new URL(startUrl);
    let tryOtherDomain = false;
    for (let redirects = 0; redirects <= 5; redirects += 1) {
      try {
        const cookieHeader = await jar.getCookieString(url.href);
        if (!/\bremixsid\d*=/i.test(cookieHeader)) {
          failures.push("В локальной сессии нет remixsid для " + url.hostname + ".");
          tryOtherDomain = true;
          break;
        }
        const response = await fetch(url, {
          redirect: "manual",
          headers: {
            cookie: cookieHeader,
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/132 Safari/537.36",
            accept: "text/html,application/xhtml+xml",
          },
          signal: AbortSignal.timeout(12000),
        });
        const location = response.headers.get("location");
        // Preserve cookies rotated by VK instead of keeping the original
        // sign-in export forever. FileCookieStore persists session cookies too.
        for (const cookie of response.headers.getSetCookie()) {
          await jar.setCookie(cookie, url.href);
        }
        if (response.status >= 300 && response.status < 400 && location) {
          const next = new URL(location, url);
          if (!isVkHost(next.hostname)) {
            failures.push("VK перенаправил проверку сессии на посторонний домен.");
            tryOtherDomain = true;
            break;
          }
          if (isLoginUrl(next)) {
            failures.push("VK перенаправил " + url.hostname + " на страницу входа.");
            tryOtherDomain = true;
            break;
          }
          url = next;
          continue;
        }
        if (!response.ok) {
          failures.push(`VK не подтвердил сессию через ${url.hostname} (HTTP ${response.status}).`);
          tryOtherDomain = true;
          break;
        }
        if (isLoginUrl(url)) {
          failures.push("VK открыл страницу входа вместо профиля.");
          tryOtherDomain = true;
          break;
        }
        return { session_valid: true, validated_host: url.hostname };
      } catch (error) {
        failures.push(String(error?.message || error));
        tryOtherDomain = true;
        break;
      }
    }
    if (!tryOtherDomain) failures.push("VK перенаправлял проверку сессии слишком много раз.");
  }
  if (failures.filter(message => /страницу входа|нет remixsid/i.test(message)).length >= 2) {
    throw new Error("VK_SESSION_EXPIRED: VK больше не принимает сохранённую сессию. Войдите снова через VK.");
  }
  throw new Error("Не удалось проверить сессию VK. Проверьте интернет и повторите проверку; сохранённый вход оставлен на месте.");
}

function cleanTrack(audio) {
  const id = String(audio?.id ?? "");
  const ownerId = String(audio?.owner_id ?? audio?.ownerId ?? "");
  if (!id || !ownerId) return null;
  const title = String(audio.title || "Без названия");
  const artist = String(audio.performer || audio.artist || "Неизвестный исполнитель");
  const albumValue = audio.album;
  const album = typeof albumValue === "string" ? albumValue : String(albumValue?.title || "");
  const cover = audio.coverUrl_s || audio.coverUrl_p || audio.cover_url || audio.coverUrl || "";
  return {
    service: "vk",
    track_id: id,
    title,
    artist,
    album,
    duration_seconds: Number(audio.duration || 0),
    url: `https://vk.com/audio${ownerId}_${id}`,
    cover_url: String(cover || ""),
    extra: {
      owner_id: ownerId,
      access_key: String(audio.access_key || audio.accessKey || ""),
      reload_id: (() => {
        const hashes = String(audio.raw?.[13] || "").split("/");
        return hashes[2] && hashes[5] ? `${ownerId}_${id}_${hashes[2]}_${hashes[5]}` : "";
      })(),
    },
    stream_url: String(audio.url || ""),
  };
}

function cleanPlaylist(playlist) {
  const ownerId = String(playlist?.owner_id ?? playlist?.ownerId ?? "");
  const playlistId = String(playlist?.playlist_id ?? playlist?.id ?? "");
  if (!ownerId || !playlistId) return null;
  return {
    service: "vk",
    playlist_id: `${ownerId}:${playlistId}:${String(playlist.access_hash || playlist.accessHash || "")}`,
    title: String(playlist.title || "Плейлист VK"),
    track_count: Number(playlist.size || playlist.totalCount || 0),
    cover_url: String(playlist.cover_url || playlist.coverUrl || playlist.thumb || ""),
    url: `https://vk.com/music/playlist/${ownerId}_${playlistId}`,
  };
}

function searchAudioCandidates(response, api, query) {
  const entries = new Map();
  const queryWords = [...new Set(String(query || "").normalize("NFKC").toLowerCase()
    .replace(/ё/g, "е").match(/[\p{L}\p{N}]+/gu) || [])].filter(word => word !== "от");
  const sourcePriority = { mine: 3, songs: 2, text: 1, other: 0 };
  const add = (raw, source) => {
    try {
      const audio = api.audio.getAudioAsObject(raw);
      const key = `${String(audio?.owner_id ?? "")}:${String(audio?.id ?? "")}`;
      if (key === ":") return;
      const titleWords = String(audio.title || "").normalize("NFKC").toLowerCase()
        .replace(/ё/g, "е").match(/[\p{L}\p{N}]+/gu) || [];
      const allWords = new Set((String(audio.title || "") + " " + String(audio.performer || audio.artist || ""))
        .normalize("NFKC").toLowerCase().replace(/ё/g, "е").match(/[\p{L}\p{N}]+/gu) || []);
      const coverage = queryWords.length
        ? queryWords.filter(word => allWords.has(word)).length / queryWords.length : 0;
      const titleMatch = titleWords.length > 0 && titleWords.every(word => queryWords.includes(word)) ? 1 : 0;
      const priority = sourcePriority[source] || 0;
      const entry = { raw, coverage, titleMatch, priority };
      const previous = entries.get(key);
      if (!previous || coverage > previous.coverage ||
          (coverage === previous.coverage && priority > previous.priority)) entries.set(key, entry);
    } catch (_) {
      // Ignore a single malformed audio row; other sections may still be usable.
    }
  };

  // The HTML contains separate catalog blocks for the user's tracks and
  // ordinary song matches. Parse only catalog rows: other audio rows in this
  // HTML belong to unrelated recommendation cards.
  const html = response?.payload?.[1]?.[0];
  if (typeof html === "string" && html.includes("data-audio=")) {
    const document = HTMLParser.parse(html);
    for (const node of document.querySelectorAll(".CatalogBlock__itemsContainer [data-audio]")) {
      let block = node;
      while (block && !String(block.classNames || "").split(/\s+/).includes("CatalogBlock")) {
        block = block.parentNode;
      }
      const heading = String(block?.querySelector(".CatalogBlock__title")?.text || "");
      const source = /Мои треки|Моя музыка|My tracks/i.test(heading) ? "mine"
        : /Найдено в тексте|Found in text/i.test(heading) ? "text"
          : /Песни|Треки|Аудиозаписи|Songs|Tracks/i.test(heading) ? "songs" : "other";
      try {
        for (const row of api.audio.builderHTML(node.outerHTML)) add(row, source);
      } catch (_) {}
    }
  }

  // VK also includes the text-match section as structured data. It may be the
  // only section, or duplicate rows already present in the HTML above.
  const data = response?.payload?.[1]?.[1];
  let structuredRows = null;
  for (const key of ["playlist", "playlistData"]) {
    const list = data?.[key]?.list;
    if (Array.isArray(list)) {
      structuredRows = list;
      break;
    }
  }
  if (structuredRows) for (const row of structuredRows) add(row, "text");
  if (!structuredRows && !entries.size) return null;
  return [...entries.values()].sort((a, b) =>
    b.coverage - a.coverage || b.titleMatch - a.titleMatch || b.priority - a.priority
  ).map(entry => entry.raw);
}

async function main() {
  requestInput = JSON.parse(fs.readFileSync(0, "utf8"));
  const input = requestInput;
  const token = String(input.token || "");
  let userId = Number(input.user_id || 0);
  const cookiePath = resolveCookiePath(input.cookie_path);
  if (userId && (!Number.isSafeInteger(userId) || userId <= 0)) throw new Error("VK передал некорректный ID аккаунта.");
  if (!token && !userId) throw new Error("VK не передал ID аккаунта. Войдите заново через окно VK.");
  if (!cookiePath) throw new Error("Не указан локальный файл VK cookies.");
  if (input.cookies_json) await importCookies(input.cookies_json, cookiePath);
  if (!fs.existsSync(cookiePath)) throw new Error("Сначала импортируйте VK cookies во вкладке «Музыка».");
  const session = await verifyWebSession(cookiePath);

  // Cookie session is sufficient for EasyVK's web endpoints. The placeholder
  // token only satisfies vk-io's constructor; no VK API method is called.
  const credits = { cookies: cookiePath };
  if (userId) credits.user = userId;
  const api = await new AudioAPI(token || "browser-session").login(credits);
  for (const audio of [api.audio, api.playlists.AudioRequests]) {
    audio.normalize = normalizeAudios;
    audio.getById = async function (params) {
      const response = await this.request({ act: "reload_audio", al: 1, ids: params.ids });
      const rows = response?.payload?.[1]?.[0];
      if (!Array.isArray(rows)) throw new Error("VK не вернул ссылки на выбранные треки.");
      return rows;
    };
  }
  if (session.validated_host === "vk.ru") {
    // EasyVK hardcodes vk.com for al_audio.php. VK may issue a working session
    // only on vk.ru, while the same cookies redirect to login.vk.com. Use the
    // host that just served an authenticated profile for all web audio calls.
    api.client.requestEndpoint = async (params = {}, post = true, isMobile = false, file = "al_audio.php", options = {}) => {
      const endpoint = isMobile ? "m.vk.ru" : "vk.ru";
      const filename = isMobile && file === "al_audio.php" ? "audio.php" : file;
      if (!/^[a-z0-9_.-]+$/i.test(filename)) throw new Error("Некорректный путь VK audio endpoint.");
      const response = await api.client.request(`https://${endpoint}/${filename}`, params, post, options);
      return api.client.parseJSON(response);
    };
  }
  userId = userId || Number(api.vk.user || 0);
  if (!Number.isSafeInteger(userId) || userId <= 0) throw new Error("EasyVK не смог определить ID аккаунта VK.");
  const account = String(input.account || `VK ID ${userId}`);
  const operation = String(input.operation || "test");

  if (operation === "test") {
    emit({
      success: true,
      account,
      user_id: String(api.vk.user || ""),
      session_saved: true,
      session_valid: Boolean(session.session_valid),
      audio_ready: false,
      audio_status: "not_checked",
    });
    return;
  }

  if (operation === "search") {
    const query = String(input.query || "").trim();
    if (!query) throw new Error("Введите исполнителя или название трека.");
    const count = Math.max(1, Math.min(50, Number(input.limit) || 20));
    const response = await api.search.getSection({ section: "search", owner_id: userId, q: query });
    const rows = searchAudioCandidates(response, api, query);
    if (!rows) {
      throw new Error("VK вернул результаты поиска в неизвестном формате; список аудиотреков не найден в ответе.");
    }
    const audios = await api.audio.parseAudios(rows, { count });
    const tracks = (Array.isArray(audios) ? audios : []).map(cleanTrack).filter(Boolean);
    for (const track of tracks) track.extra.search_query = query;
    emit({ success: true, account, tracks });
    return;
  }

  if (operation === "playlists") {
    let source = "owner_playlists";
    let rows;
    try {
      const result = await api.playlists.get({ offset: 0 });
      rows = Array.isArray(result?.playlists) ? result.playlists : [];
    } catch (error) {
      if (!/Access Denied/i.test(String(error?.message || error))) throw error;
      source = "music_page";
      rows = await api.general.usersPlaylists();
      if (!Array.isArray(rows) || !rows.length) {
        throw new Error("VK отказал в выдаче плейлистов, а страница музыки не вернула список. Проверьте доступ к музыке в аккаунте VK.");
      }
    }
    const playlists = rows.map(cleanPlaylist).filter(Boolean);
    emit({ success: true, account, playlists, source });
    return;
  }

  if (operation === "my_tracks") {
    const rows = await ownTracks(api, userId);
    const tracks = rows.map(cleanTrack).filter(Boolean);
    emit({ success: true, account, tracks });
    return;
  }

  if (operation === "playlist_tracks") {
    const ownerId = Number(input.owner_id);
    const playlistId = Number(input.playlist_id);
    if (!Number.isFinite(ownerId) || !Number.isFinite(playlistId)) throw new Error("Неверный VK playlist ID.");
    const result = await api.playlists.getPlaylist({
      owner_id: ownerId,
      playlist_id: playlistId,
      access_hash: String(input.access_hash || ""),
      list: true,
      count: Math.max(1, Math.min(500, Number(input.limit) || 50)),
    });
    const tracks = (Array.isArray(result?.list) ? result.list : []).map(cleanTrack).filter(Boolean);
    for (const track of tracks) {
      track.extra.playlist_owner_id = String(ownerId);
      track.extra.playlist_id = String(playlistId);
      track.extra.playlist_access_hash = String(input.access_hash || "");
    }
    emit({ success: true, account, tracks });
    return;
  }

  if (operation === "resolve") {
    const trackId = String(input.track_id || "");
    const ownerId = String(input.owner_id || "");
    if (!trackId || !ownerId) throw new Error("Для потока нужны ID трека и владельца.");
    const matches = item => String(item?.id) === trackId && String(item?.owner_id) === ownerId;
    let audio;
    const reloadId = String(input.reload_id || "");
    if (reloadId.startsWith(`${ownerId}_${trackId}_`) && /^[\w-]+$/.test(reloadId)) {
      const rows = await api.audio.getById({ ids: reloadId });
      const row = rows.find(row => identity(row) === `${ownerId}_${trackId}`);
      if (row) audio = api.audio.getAudioAsObject(row);
    }
    const playlistOwnerId = Number(input.playlist_owner_id);
    const playlistId = Number(input.playlist_id);
    if (!audio?.url && Number.isSafeInteger(playlistOwnerId) && playlistOwnerId !== 0 &&
        Number.isSafeInteger(playlistId) && playlistId > 0) {
      try {
        const playlist = await api.playlists.getPlaylist({
          owner_id: playlistOwnerId,
          playlist_id: playlistId,
          access_hash: String(input.playlist_access_hash || ""),
          list: true,
          count: 500,
        });
        audio = (Array.isArray(playlist?.list) ? playlist.list : []).find(matches);
      } catch (_) {
        // A playlist may have been removed or become private since it was shown.
      }
    }
    const originalQuery = String(input.search_query || "").trim();
    if (!audio && originalQuery) {
      const response = await api.search.getSection({ section: "search", owner_id: userId, q: originalQuery });
      const rows = searchAudioCandidates(response, api, originalQuery);
      if (rows) {
        const audios = await api.audio.parseAudios(rows, { count: 50 });
        audio = (Array.isArray(audios) ? audios : []).find(matches);
      }
    }
    if (!audio) {
      const artist = String(input.artist || "").trim();
      const title = String(input.title || "").trim();
      const queries = [...new Set([[artist, title].filter(Boolean).join(" "), title, artist].filter(Boolean))];
      for (const query of queries) {
        const result = await api.search.query({ q: query, count: 50 });
        audio = (Array.isArray(result?.audios) ? result.audios : []).find(matches);
        if (audio?.url) break;
      }
    }
    if (!audio?.url) throw new Error("EasyVK не нашёл свежую ссылку на этот трек. Повторите поиск VK.");
    emit({ success: true, account, stream_url: String(audio.url), duration_seconds: Number(audio.duration || 0) });
    return;
  }

  throw new Error("Неизвестная операция EasyVK.");
}

main().catch(error => {
  emit({ success: false, error: safeError(error, requestSecrets()) });
  process.exitCode = 1;
});
