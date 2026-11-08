"""Internet radio and explicit playlist/queue tools, independent of UI lifetime."""
import hashlib
import json
import re
import secrets
import threading
import time
from urllib.parse import quote

import httpx
from astra_plugin_sdk import tool, ui_call

from .internet_radio import RadioCatalogue, RadioRelay


def identifiers(value):
    values = json.loads(value) if isinstance(value, str) else value
    if not isinstance(values, list) or not 1 <= len(values) <= 100:
        raise ValueError("Передайте от 1 до 100 идентификаторов треков списком JSON.")
    result = []
    for item in values:
        if not isinstance(item, (str, int)) or isinstance(item, bool) or not re.fullmatch(r"\d{1,24}(?::\d{1,24})?", str(item)):
            raise ValueError("Нужны идентификаторы треков из результатов поиска Яндекс Музыки.")
        identifier = str(item).split(":")[0]
        if identifier not in result:
            result.append(identifier)
    return result


class MusicExtensions:
    def _init_extensions(self):
        self._internet_radio = RadioCatalogue(self.data_dir)
        self._radio_relay = RadioRelay(self)
        self._playlist_creation_lock = threading.Lock()
        self._station_job_lock = threading.Lock()
        self._station_job = {}

    def _yandex_request(self, path, data=None, params=None):
        client = self._client("yandex")
        try:
            response = (client.http.get(client.base_url + path, params=params) if data is None
                        else client.http.post(client.base_url + path, data=data))
            client._raise(response)
            return response.json().get("result")
        except httpx.HTTPError:
            raise ValueError("Яндекс Музыка не ответила. Проверьте соединение и состояние плейлиста перед повтором.") from None

    def _selection_tracks(self, track_ids):
        client = self._client("yandex")
        tracks = []
        for identifier in identifiers(track_ids):
            track = client.get_track(identifier)
            album = str((track.extra or {}).get("album_id") or "")
            if not track.title or not album.isdigit():
                raise ValueError("Не удалось проверить один из треков. Плейлист не изменён.")
            tracks.append(track)
        return tracks

    def _artist_selection(self, artist, limit):
        limit = max(1, min(100, int(limit)))
        name = str(artist).strip()
        if not name or len(name) > 200:
            raise ValueError("Укажите имя исполнителя.")
        raw = self._yandex_request("/search", params={"text": name, "type": "artist", "page": 0}) or {}
        candidates = (raw.get("artists") or {}).get("results") or []
        exact = [item for item in candidates if " ".join(str(item.get("name", "")).casefold().split()) == " ".join(name.casefold().split())]
        if len(exact) != 1:
            return {"needs_artist_choice": True, "candidates": [{"artist_id": str(item.get("id")), "name": item.get("name")} for item in candidates[:10]],
                    "message": "Уточните исполнителя: однозначного совпадения нет."}
        selected = exact[0]
        artist_id = str(selected.get("id", ""))
        if not artist_id.isdigit():
            raise ValueError("Не удалось определить исполнителя.")
        info = self._yandex_request(f"/artists/{artist_id}/brief-info") or {}
        tracks, seen = [], set()
        client = self._client("yandex")
        for raw in info.get("popularTracks") or []:
            if not any(str(item.get("id")) == artist_id for item in raw.get("artists") or []):
                continue
            track = client._track(raw)
            if track and track.track_id not in seen:
                tracks.append(track.as_dict()); seen.add(track.track_id)
            if len(tracks) == limit:
                break
        return {"service": "yandex", "artist_id": artist_id, "artist": selected.get("name"), "tracks": tracks,
                "requested": limit, "count": len(tracks), "source": "artist_popular_tracks",
                "message": "Популярные треки самого исполнителя. Если их меньше запрошенного, не дополняйте список каверами или выдуманными ID."}

    @tool("Find popular Yandex tracks by an exact artist identity, excluding other artists' covers. Use before assembling an artist playlist, instead of repeated generic song searches. Returns actual track IDs in popularity order; fewer results is not a service-wide limit. If needs_artist_choice, ask the user to clarify the artist.")
    async def find_yandex_artist_tracks(self, artist: str = "", limit: int = 15, kwargs: str = ""):
        args = self._args(kwargs, artist=artist, limit=None if limit == 15 else limit)
        return await self._guarded(self._artist_selection, self._pick(args, "artist"), args.get("limit", 15))

    def _create_selection_playlist(self, title, track_ids):
        title = str(title).strip()
        if not 1 <= len(title) <= 100:
            raise ValueError("Название плейлиста должно содержать от 1 до 100 символов.")
        ids = identifiers(track_ids)
        uid = quote(self._client("yandex").user_id, safe="")
        operation = hashlib.sha256(json.dumps([uid, title, ids], ensure_ascii=False).encode()).hexdigest()
        journal = self.data_dir / "playlist-creation.json"
        with self._playlist_creation_lock:
            try:
                saved = json.loads(journal.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = {}
            previous = saved.get(operation)
            if previous:
                return {**previous, "repeated_request": True}
            tracks = self._selection_tracks(ids)  # Validate the whole selection BEFORE writing to the account.
            def remember(result):
                saved[operation] = result
                if len(saved) > 100:
                    saved.pop(next(iter(saved)))
                journal.parent.mkdir(parents=True, exist_ok=True)
                temporary = journal.with_suffix(".tmp")
                temporary.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
                temporary.replace(journal)
                return result
            remember({"success": False, "status": "unknown", "message": "Создание уже запрашивалось. Проверьте плейлисты в Яндексе перед повтором, чтобы не создать дубль."})
            created = self._yandex_request(f"/users/{uid}/playlists/create", data={"title": title, "visibility": "private"}) or {}
            kind = str(created.get("kind", ""))
            if not kind.isdigit():
                return saved[operation]
            url = f"https://music.yandex.ru/users/{uid}/playlists/{kind}"
            partial = {"success": False, "created": True, "status": "partial", "playlist_id": kind, "url": url,
                       "message": "Плейлист создан, но заполнение не подтверждено. Проверьте его по ссылке; повторный запрос не создаст дубль."}
            remember(partial)
            diff = [{"op": "insert", "at": 0, "tracks": [{"id": t.track_id, "albumId": str(t.extra["album_id"])} for t in tracks]}]
            try:
                changed = self._yandex_request(f"/users/{uid}/playlists/{kind}/change",
                    data={"revision": str(created.get("revision", 1)), "diff": json.dumps(diff)}) or {}
                if int(changed.get("trackCount", -1)) != len(tracks):
                    return partial
                verified = self._yandex_request(f"/users/{uid}/playlists/{kind}") or {}
                entries = verified.get("tracks") or []
                verified_ids = [str(entry.get("id") or (entry.get("track") or {}).get("id") or "").split(":")[0] for entry in entries]
                if verified_ids != [track.track_id for track in tracks]:
                    return partial
            except Exception:
                return partial
            return remember({"success": True, "created": True, "status": "created", "playlist_id": kind, "url": url,
                             "title": title, "track_count": len(tracks), "service": "yandex"})

    @tool("Create a PRIVATE playlist in the connected Yandex Music account ONLY when the user asks to save/create it. First select tracks using search_tracks/find_yandex_artist_tracks; track_ids_json is a JSON array of their real IDs in desired order, max 100. Checks every track before creating. Repeating the same title and ordered IDs returns the prior operation instead of creating duplicates. Report success only when success=true; partial/unknown means inspect the returned playlist before retrying. Other services are not supported by this tool.")
    async def create_yandex_playlist(self, title: str = "", track_ids_json: str = "", kwargs: str = ""):
        args = self._args(kwargs, title=title, track_ids_json=track_ids_json)
        return await self._guarded(self._create_selection_playlist, self._pick(args, "title"), args.get("track_ids_json", ""))

    def _play_selection(self, track_ids, title):
        return self._start_yandex_queue_sync(self._selection_tracks(track_ids), source="selection", source_title=str(title)[:100], auto_play=True)

    @tool("Play an ordered temporary selection of Yandex tracks without saving a playlist to the account. track_ids_json is a JSON array of real IDs from search results, max 100. Use for 'включи эту подборку': all chosen songs become the actual queue. Report playing only when playing=true.")
    async def play_yandex_selection(self, track_ids_json: str = "", title: str = "Подборка Astra", kwargs: str = ""):
        args = self._args(kwargs, track_ids_json=track_ids_json, title=None if title == "Подборка Astra" else title)
        return await self._wait_for_playback_start(await self._guarded(self._play_selection, args.get("track_ids_json", ""), args.get("title", "Подборка Astra")))

    @tool("Search worldwide internet radio stations in the public Radio Browser catalogue. No Yandex/VK login needed. query is station name; country is ISO two-letter code (RU, DE, etc.), tag is genre (rock, jazz, etc.), offset pages by 50. Use returned station IDs/URLs with play_radio_station. The catalogue is broad but not exhaustive and streams may be unavailable.")
    async def search_radio_stations(self, query: str = "", country: str = "", tag: str = "", offset: int = 0, kwargs: str = ""):
        args = self._args(kwargs, query=query, country=country, tag=tag, offset=None if offset == 0 else offset)
        return await self._guarded(self._internet_radio.search, self._pick(args, "query"), args.get("country", ""), args.get("tag", ""), args.get("offset", 0))

    def _start_internet_radio(self, raw):
        station = self._internet_radio.station(raw)
        # Public address validation happens before taking over the current player.
        from .internet_radio import public_url
        public_url(station["url"], resolve=True)
        base = self._audio_host.ensure_server()
        self._spotify.release()
        self._internet_radio_current = station
        with self._playback_lock:
            self._playback_revision += 1
            self._playback_command_revision += 1
            revision = self._playback_revision
            path = self._radio_relay.start(station["url"], revision, station["hls"])
            self._playback_queue, self._playback_queue_index = [], -1
            self._playback_source, self._playback_source_title = "internet_radio", station["name"]
            self._playback_state = {"service": "internet_radio", "source": "internet_radio", "source_title": station["name"],
                "revision": revision, "command_revision": self._playback_command_revision, "command": "play", "status": "play_requested",
                "url": base.split('/' + self._audio_host.token)[0] + path, "track_id": station["station_id"], "title": station["name"],
                "artist": "Прямой эфир", "cover_url": "", "duration_seconds": 0, "position_seconds": 0,
                "volume": self._playback_volume, "expires_at": 1e12, "queue_index": -1, "queue_count": 0, "live": True}
        self._audio_host.ensure()
        return {"success": True, "playing": False, "playback_requested": True, "revision": revision}

    @tool("Play an internet radio station from search_radio_stations or a direct public HTTP(S) audio stream requested by the user. Pass url, name and station_id from the result, hls=true for HLS even when URL lacks .m3u8. This is live radio: no next song, likes or seeking. Report playing only when playing=true.")
    async def play_radio_station(self, url: str = "", name: str = "Радиостанция", station_id: str = "", hls: bool = False, kwargs: str = ""):
        args = self._args(kwargs, url=url, name=None if name == "Радиостанция" else name, station_id=station_id, hls=None if hls is False else hls)
        raw = {"url": self._pick(args, "url"), "name": args.get("name", "Радиостанция"), "stationuuid": args.get("station_id", ""), "hls": args.get("hls", False)}
        return await self._wait_for_playback_start(await self._guarded(self._start_internet_radio, raw))

    @tool("List the user's locally saved favorite internet radio stations, optionally filtered by name. Use for 'какие радиостанции у меня в избранном'. Does not query the public catalogue. Use play_favorite_radio_station to play one by its saved name or station_id.")
    async def list_favorite_radio_stations(self, query: str = "", kwargs: str = ""):
        args = self._args(kwargs, query=query)
        query = " ".join(str(self._pick(args, "query")).casefold().split())
        stations = self._internet_radio.favorites()
        if query:
            stations = [station for station in stations if query in " ".join(str(station.get("name", "")).casefold().split())]
        return {"stations": stations, "count": len(stations), "source": "favorites"}

    def _play_favorite_radio(self, name, station_id):
        stations = self._internet_radio.favorites()
        normalize = lambda value: " ".join(str(value).casefold().split())
        wanted = normalize(name)
        if station_id:
            matches = [station for station in stations if station.get("station_id") == str(station_id)]
        elif wanted:
            matches = [station for station in stations if normalize(station.get("name", "")) == wanted]
            if not matches:
                matches = [station for station in stations if wanted in normalize(station.get("name", ""))]
        else:
            matches = stations
        if not matches:
            return {"success": False, "playing": False, "message": "Такой станции нет в избранном. Сначала сохраните её во вкладке «Радиостанции» или уточните название.", "source": "favorites"}
        if len(matches) != 1:
            return {"success": False, "playing": False, "needs_station_choice": True,
                    "stations": [{key: station.get(key, "") for key in ("station_id", "name", "country")} for station in matches],
                    "message": "В избранном несколько подходящих станций. Уточните, какую включить; затем передайте её station_id."}
        return self._start_internet_radio(matches[0])

    @tool("Play a station from the user's locally saved FAVORITE internet radio stations. Use for 'включи избранное радио ...' or 'включи ... из избранного'. name is the saved station name; station_id can disambiguate results from list_favorite_radio_stations. A unique partial name is accepted; ambiguous names return needs_station_choice without changing playback. Never silently substitute a station from the public catalogue. Report playback only when playing=true.")
    async def play_favorite_radio_station(self, name: str = "", station_id: str = "", kwargs: str = ""):
        args = self._args(kwargs, name=name, station_id=station_id)
        result = await self._guarded(self._play_favorite_radio, args.get("name", ""), args.get("station_id", ""))
        return await self._wait_for_playback_start(result)

    @ui_call("music_stations_search")
    async def ui_stations_search(self, **params):
        if params.get("operation") == "poll":
            with self._station_job_lock:
                return dict(self._station_job) if params.get("job_id") == self._station_job.get("job_id") else {"error": "Поиск заменён новым запросом."}
        job_id = secrets.token_hex(16)
        with self._station_job_lock:
            self._station_job = {"job_id": job_id, "pending": True}
        def load():
            try:
                result = self._internet_radio.search(str(params.get("query", "")), str(params.get("country", "")), str(params.get("tag", "")), params.get("offset", 0))
            except Exception:
                result = {"error": "Каталог радио недоступен. Попробуйте позже; избранное и свои ссылки доступны отдельно."}
            with self._station_job_lock:
                if self._station_job.get("job_id") == job_id:
                    self._station_job = {**result, "job_id": job_id, "pending": False}
        threading.Thread(target=load, name="radio-catalogue", daemon=True).start()
        return {"job_id": job_id, "pending": True}

    @ui_call("music_stations_saved")
    async def ui_stations_saved(self, **params):
        if params.get("station") is not None:
            return await self._guarded(self._internet_radio.save, params["station"], bool(params.get("remove")))
        return {"stations": self._internet_radio.favorites()}

    @ui_call("music_stations_play")
    async def ui_stations_play(self, **params):
        # UI returns promptly; the actual audio host reports success/failure in the player.
        return await self._guarded(self._start_internet_radio, params.get("station") or {})
