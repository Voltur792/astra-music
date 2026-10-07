"""Astra Music — search, playlists, playback control, and audio-reactive UI."""

import ast
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from astra_plugin_sdk import Field, NotConfigured, Plugin, action, tool, ui_call, ui_effect, ui_page
from astra_plugin_sdk.capability_types import UiContribution
from .tab_icon import TAB_ICON_SVG
from .playback_host import PlaybackHost
from .system_audio import SystemAudio
from .desktop_widget import DesktopWidget

log = logging.getLogger("astra-music")

VISUALIZER_DEFAULTS = {"mode": "music", "widget": True, "background": False, "style": "waves", "intensity": 0.65}
VISUALIZER_STYLES = {"spectrum", "waves", "liquid", "ripple", "ribbon", "orbit", "particles", "mesh", "binary", "terrain", "silk", "splash", "rain", "contour"}


def _visualizer_settings(value: Any) -> dict[str, Any]:
    settings = dict(VISUALIZER_DEFAULTS)
    if not isinstance(value, dict):
        return settings
    if value.get("mode") in {"off", "all", "music"}:
        settings["mode"] = value["mode"]
    if value.get("style") in VISUALIZER_STYLES:
        settings["style"] = value["style"]
    for key in ("widget", "background"):
        if isinstance(value.get(key), bool):
            settings[key] = value[key]
    try:
        intensity = float(value.get("intensity", settings["intensity"]))
        if 0.2 <= intensity <= 1.0:
            settings["intensity"] = intensity
    except (ValueError, TypeError):
        pass
    return settings


@dataclass
class Track:
    service: str
    track_id: str
    title: str
    artist: str
    album: str = ""
    url: str = ""
    context_id: str = ""
    extra: dict[str, Any] | None = None
    cover_url: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "track_id": self.track_id,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "url": self.url,
            "context_id": self.context_id,
            "extra": self.extra or {},
            "cover_url": self.cover_url,
        }


class MusicError(RuntimeError):
    pass


def _yandex_cover_url(value: Any) -> str:
    """Expand and validate a Yandex cover template before returning it to UI."""
    cover = str(value or "").strip()
    if not cover:
        return ""
    if cover.startswith("//"):
        cover = "https:" + cover
    elif not cover.startswith(("https://", "http://")):
        cover = "https://" + cover.lstrip("/")
    cover = cover.replace("%%", "200x200")
    parsed = urlsplit(cover)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not (
        host == "yandex.net" or host.endswith(".yandex.net")
        or host == "yandex.ru" or host.endswith(".yandex.ru")
    ):
        return ""
    return cover


def _vk_cover_url(value: Any) -> str:
    """Accept only secure VK-owned cover images from EasyVK metadata."""
    cover = str(value or "").strip()
    if cover.startswith("//"):
        cover = "https:" + cover
    if not cover:
        return ""
    parsed = urlsplit(cover)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not any(
        host == domain or host.endswith("." + domain)
        for domain in ("vk.com", "vkuserphoto.ru", "userapi.com", "vkuseraudio.net")
    ):
        return ""
    return cover


def _cover_url(service: str, value: Any) -> str:
    return _vk_cover_url(value) if service == "vk" else _yandex_cover_url(value)


def normalize_token(raw: Any) -> str:
    """Clean a token the user pasted.

    Yandex answers an unrecognised token with HTTP 200 and an anonymous
    account rather than 401, so a pasted `OAuth ` prefix (which the header
    adds itself) silently reads as "connected" and then as "no playlists".
    """
    text = str(raw or "").strip().strip("\"'").strip()
    for prefix in ("OAuth ", "oauth ", "Bearer ", "bearer "):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip().strip("\"'").strip()
    return " ".join(text.split())


class MusicClient:
    service = ""
    display_name = ""

    def __init__(self, token: str):
        self.token = normalize_token(token)
        if not self.token:
            raise NotConfigured(self.service)
        self.http = httpx.Client(timeout=15.0, follow_redirects=True)
        self.http.headers.update({"User-Agent": "AstraMusic/0.1.0"})

    def close(self) -> None:
        self.http.close()

    def test_connection(self) -> dict[str, Any]:
        raise NotImplementedError

    def search(self, query: str, limit: int) -> list[Track]:
        raise NotImplementedError

    def get_track(self, track_id: str) -> Track:
        tracks = self.search(track_id, 10)
        for track in tracks:
            if track.track_id == track_id:
                return track
        if tracks:
            return tracks[0]
        raise MusicError(f"{self.display_name}: трек {track_id} не найден")

    def playlists(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def playlist_tracks(self, playlist_id: str, limit: int) -> list[Track]:
        raise NotImplementedError

    def play_track(self, track: Track) -> dict[str, Any]:
        raise NotImplementedError

    def open_track(self, track: Track) -> dict[str, Any]:
        raise NotImplementedError


class YandexMusicClient(MusicClient):
    service = "yandex"
    display_name = "Яндекс Музыка"
    base_url = "https://api.music.yandex.net"

    def __init__(self, token: str):
        super().__init__(token)
        self._stream_client = None
        # Яндекс Музыка принимает только схему `OAuth <token>`; стандартный
        # `Bearer` отвечает 401. Проверено по библиотеке yandex-music.
        self.http.headers.update({"Authorization": f"OAuth {self.token}"})
        # Яндекс отдаёт персональные ответы только клиенту, который представляется
        # приложением Яндекс Музыки.
        self.http.headers.update({"X-Yandex-Music-Client": "AstraMusic/1"})
        self._user_id: str | None = None

    def close(self) -> None:
        super().close()
        if self._stream_client is not None:
            session = getattr(self._stream_client, "session", None)
            close = getattr(session, "close", None)
            if callable(close):
                close()
            self._stream_client = None

    def test_connection(self) -> dict[str, Any]:
        response = self.http.get(f"{self.base_url}/account/status")
        self._raise(response)
        result = response.json().get("result", {})
        account = result.get("account") or {}
        # Яндекс отвечает 200 и на анонимный запрос — с `now`/`region`, но без
        # `uid`/`id` и `login`. Так выглядит и токен, который Яндекс не распознал
        # (чужой скоуп, опечатка, другой префикс авторизации): подписка при этом
        # читается как «нет», и пользователь узнаёт правду, а не «подключено».
        if account.get("uid") is None and account.get("id") is None and not account.get("login"):
            raise MusicError(
                "Яндекс Музыка: токен не распознан — запрос прошёл как анонимный. "
                "Нужен токен аккаунта Яндекс Музыки (OAuth Device Flow или "
                "oauth.yandex.ru с client_id приложения Музыки), скоупы "
                "стороннего приложения этот API не принимают."
            )
        user_id = account.get("uid") if account.get("uid") is not None else account.get("id")
        self._user_id = str(user_id) if user_id is not None else None
        account_id = account.get("login") or user_id
        status = str(result.get("status") or "")
        if status and status not in ("OK", "success"):
            raise MusicError(f"Яндекс Музыка: аккаунт {account_id} — {status}")
        # Подписка в разных версиях API живёт то в `subdb`, то в `permissions`.
        subdb = result.get("subdb") or {}
        permissions = result.get("permissions") or {}
        has_music = bool(subdb.get("has_music")) or bool(permissions.get("has_music"))
        plus = any(name in ("plus", "music") for name in (permissions.get("products") or []))
        return {
            "service": self.service,
            "connected": True,
            "account": account_id,
            "subscription": "plus" if (has_music or plus) else "",
        }

    @property
    def user_id(self) -> str:
        if self._user_id is None:
            # Запрашивает /account/status; без распознанного токена тот
            # отвечает 200 с пустым аккаунтом, и test_connection поднимает
            # ошибку — сюда «нет uid» уже не доходит молча.
            self.test_connection()
        if not self._user_id:
            raise MusicError(
                "Яндекс Музыка: аккаунт не определён — токен принят как анонимный. "
                "Сохраните токен аккаунта Музыки ещё раз (он выдаётся на год и "
                "не обновляется автоматически)."
            )
        return self._user_id

    @property
    def user_path(self) -> str:
        """Resolve the account UID before requesting user playlists."""
        return self.user_id

    def search(self, query: str, limit: int) -> list[Track]:
        response = self.http.get(
            f"{self.base_url}/search",
            params={
                "text": query,
                "type": "track",
                "page": 0,
                "nocorrect": "false",
            },
        )
        self._raise(response)
        result = response.json().get("result") or {}
        tracks = (result.get("tracks") or {}).get("results") or []
        out: list[Track] = []
        for item in tracks:
            parsed = self._track(item)
            if parsed:
                out.append(parsed)
            if len(out) >= limit:
                break
        return out

    def set_track_liked(self, track_id: str, liked: bool = True) -> None:
        """Add/remove a track in the account's Yandex Music likes."""
        if not str(track_id).isdigit():
            raise MusicError("Яндекс Музыка: неверный идентификатор трека.")
        try:
            if self._stream_client is None:
                from yandex_music import Client

                self._stream_client = Client(self.token).init()
            operation = (
                self._stream_client.users_likes_tracks_add
                if liked
                else self._stream_client.users_likes_tracks_remove
            )
            result = operation([int(track_id)])
            if result is False:
                raise MusicError("Яндекс Музыка не подтвердила изменение понравившихся треков.")
        except MusicError:
            raise
        except Exception as exc:
            # Request exceptions can include authentication details; never relay them.
            raise MusicError(
                "Не удалось отметить трек в Яндекс Музыке. Проверьте OAuth-токен и подключение."
            ) from exc

    def get_track(self, track_id: str) -> Track:
        # Реальные данные трека: `/tracks?trackIds=<id>` — единственный способ
        # получить название и исполнителя, когда у вызывающего есть только id.
        response = self.http.get(
            f"{self.base_url}/tracks",
            params={"trackIds": track_id, "format": "json"},
        )
        self._raise(response)
        items = response.json().get("result") or []
        parsed = self._track(items[0]) if items else None
        if parsed:
            return parsed
        return Track(
            service=self.service,
            track_id=track_id,
            title="",
            artist="",
            url=f"https://music.yandex.ru/track/{track_id}",
        )

    def playlists(self) -> list[dict[str, Any]]:
        # `/playlists/list` отдаёт сами плейлисты без треков — `/playlists` без
        # параметра kinds отвечает ошибкой, а с треками тянет мегабайты.
        user_id = self._user_id or "me"
        response = self.http.get(f"{self.base_url}/users/{user_id}/playlists/list")
        self._raise(response)
        result = response.json().get("result") or []
        out: list[dict[str, Any]] = []
        for item in result:
            kind = item.get("kind")
            if kind is None:
                continue
            count = item.get("trackCount")
            if not isinstance(count, int):
                count = item.get("track_count")
            out.append(
                {
                    "service": self.service,
                    "playlist_id": str(kind),
                    "title": item.get("title") or "Без названия",
                    "track_count": count if isinstance(count, int) else len(item.get("tracks") or []),
                    "url": f"https://music.yandex.ru/users/{user_id}/playlists/{kind}",
                }
            )
        return out

    def playlist_tracks(self, playlist_id: str, limit: int) -> list[Track]:
        response = self.http.get(
            f"{self.base_url}/users/{self.user_path}/playlists/{quote(playlist_id)}"
        )
        self._raise(response)
        result = response.json().get("result") or {}
        # Свежий формат — `list` из записей {"track": {...}}; старый — `tracks`
        # со списком треков целиком. Поддерживаем оба, иначе плейлист пуст.
        entries = result.get("list") or result.get("tracks") or []
        out: list[Track] = []
        for item in entries:
            track = item.get("track") if isinstance(item, dict) else None
            if not isinstance(track, dict):
                track = item if isinstance(item, dict) and item.get("id") else None
            if not track:
                continue
            parsed = self._track(track)
            if parsed:
                parsed.context_id = playlist_id
                out.append(parsed)
            if len(out) >= limit:
                break
        return out

    def stream_url(self, track_id: str) -> dict[str, Any]:
        """Resolve a short-lived Yandex CDN URL for the in-app HTML audio player.

        The library handles Yandex's changing download-info response and direct
        link signing. Only browser-friendly lossy codecs are returned here; the
        Astra iframe cannot decode every format or the encrypted MA transport.
        """
        if not track_id.isdigit():
            raise MusicError("Яндекс Музыка: неверный идентификатор трека")

        try:
            from yandex_music import Client
        except ImportError as exc:
            raise MusicError(
                "Для встроенного плеера не установлена библиотека yandex-music."
            ) from exc

        try:
            if self._stream_client is None:
                self._stream_client = Client(self.token).init()
            options = self._stream_client.tracks_download_info(
                track_id, get_direct_links=True
            ) or []
        except Exception as exc:
            # Do not expose request exceptions: some include signed CDN URLs.
            raise MusicError("Яндекс Музыка не выдала ссылку на поток.") from exc

        codec_priority = {"mp3": 0, "aac": 1, "aac-mp4": 2, "he-aac": 3}
        playable = [
            option
            for option in options
            if str(getattr(option, "codec", "")).lower() in codec_priority
            and getattr(option, "direct_link", "")
            and not getattr(option, "preview", False)
        ]
        if not playable:
            raise MusicError(
                "Яндекс Музыка не вернула доступный для Astra MP3/AAC поток. "
                "Откройте трек в приложении Яндекс Музыки."
            )

        selected = min(
            playable,
            key=lambda option: (
                codec_priority[str(option.codec).lower()],
                -int(getattr(option, "bitrate_in_kbps", 0) or 0),
            ),
        )
        return {
            "url": selected.direct_link,
            "codec": str(selected.codec).lower(),
            "bitrate_in_kbps": int(getattr(selected, "bitrate_in_kbps", 0) or 0),
        }

    def radio_batch(self, station: str, queue: str = "") -> tuple[list[Track], str]:
        """Get one personalized Yandex Radio batch (for example My Wave)."""
        try:
            if self._stream_client is None:
                from yandex_music import Client

                self._stream_client = Client(self.token).init()
            result = self._stream_client.rotor_station_tracks(
                station, queue=queue or None
            )
        except Exception as exc:
            raise MusicError("Яндекс Музыка не смогла загрузить волну.") from exc
        if result is None:
            raise MusicError("Яндекс Музыка не вернула треки для этой волны.")

        tracks: list[Track] = []
        batch_id = str(getattr(result, "batch_id", "") or "")
        for entry in getattr(result, "sequence", None) or []:
            raw = getattr(entry, "track", None)
            if raw is None:
                continue
            track_id = str(getattr(raw, "id", None) or getattr(raw, "track_id", "") or "")
            title = str(getattr(raw, "title", "") or "")
            if not track_id.isdigit() or not title:
                continue
            artists = [str(getattr(item, "name", "") or "") for item in getattr(raw, "artists", None) or []]
            albums = getattr(raw, "albums", None) or []
            tracks.append(
                Track(
                    service="yandex",
                    track_id=track_id,
                    title=title,
                    artist=", ".join(name for name in artists if name) or "Неизвестный исполнитель",
                    album=str(getattr(albums[0], "title", "") or "") if albums else "",
                    url=f"https://music.yandex.ru/track/{track_id}",
                    cover_url=_yandex_cover_url(
                        getattr(raw, "cover_uri", "")
                        or (getattr(albums[0], "cover_uri", "") if albums else "")
                    ),
                )
            )
        if not tracks:
            raise MusicError("Волна Яндекс Музыки пока не вернула доступных треков.")
        return tracks, batch_id

    def radio_feedback(self, station: str, event: str, track_id: str = "", batch_id: str = "", played_seconds: float = 0) -> None:
        """Send playback feedback so Yandex can maintain the radio queue."""
        if self._stream_client is None:
            from yandex_music import Client

            self._stream_client = Client(self.token).init()
        client = self._stream_client
        if event == "started":
            client.rotor_station_feedback_radio_started(
                station=station, from_="web-radio", batch_id=batch_id or None
            )
        elif event == "track_started":
            client.rotor_station_feedback_track_started(
                station=station, track_id=track_id, batch_id=batch_id or None
            )
        elif event == "track_finished":
            client.rotor_station_feedback_track_finished(
                station=station, track_id=track_id, batch_id=batch_id or None,
                # The SDK omits a literal zero, but Yandex requires this field.
                total_played_seconds=max(0.001, played_seconds),
            )
        elif event == "skip":
            client.rotor_station_feedback_skip(
                station=station, track_id=track_id, batch_id=batch_id or None,
                total_played_seconds=max(0.001, played_seconds),
            )

    def play_track(self, track: Track) -> dict[str, Any]:
        return self.open_track(track)

    def open_track(self, track: Track) -> dict[str, Any]:
        extra = track.extra or {}
        artist_id = str(extra.get("artist_id") or "")
        album_id = str(extra.get("album_id") or "")
        url = track.url or (
            f"https://music.yandex.ru/artist/{artist_id}/track/{track.track_id}"
            if artist_id
            else f"https://music.yandex.ru/track/{track.track_id}"
        )
        # Формат внутреннего роута десктоп-плеера: `yandexmusic://<путь>`,
        # путь совпадает с веб-роутом (`/album/<id>/track/<id>` → `/track/<id>`).
        deep_link = (
            f"yandexmusic://album/{album_id}/track/{track.track_id}"
            if album_id
            else f"yandexmusic://track/{track.track_id}"
        )
        return {
            "service": self.service,
            "action": "open",
            "url": url,
            "deep_link": deep_link,
            "track_id": track.track_id,
            "playing": False,
            "needs_user_action": True,
            "message": (
                "Публичный API Яндекс Музыки не умеет запускать воспроизведение "
                "удалённо: страница трека открыта, воспроизведение нужно нажать "
                "в плеере. Не сообщай пользователю, что трек уже играет."
            ),
        }

    @staticmethod
    def _track(item: dict[str, Any]) -> Track | None:
        track_id = str(item.get("id") or "")
        if not track_id:
            return None
        title = item.get("title")
        # В ответах Яндекса title бывает и строкой, и списком вариантов.
        if isinstance(title, list):
            title = ", ".join(str(t) for t in title if t)
        if not title:
            return None
        artists = item.get("artists") or []
        albums = item.get("albums") or []
        artist_id = str(artists[0].get("id") or "") if artists else ""
        album_id = str(albums[0].get("id") or "") if albums else ""
        return Track(
            service="yandex",
            track_id=track_id,
            title=str(title),
            artist=", ".join(str(a.get("name") or "") for a in artists if a.get("name")) or "Неизвестный исполнитель",
            album=str(albums[0].get("title") or "") if albums else "",
            url=f"https://music.yandex.ru/track/{track_id}",
            extra={"artist_id": artist_id, "album_id": album_id},
            cover_url=_yandex_cover_url(
                item.get("coverUri") or item.get("cover_uri")
                or (albums[0].get("coverUri") or albums[0].get("cover_uri") if albums else "")
            ),
        )

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.is_success:
            return
        # Яндекс сообщает ошибку в корне: {"error": "...", "errorDescription": "..."}.
        message = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                message = str(body.get("errorDescription") or body.get("error") or "")
        except Exception:
            message = response.text[:300]
        if response.status_code == 401:
            raise MusicError(
                "Яндекс Музыка: токен отклонён или истёк. Нужен OAuth-токен с правом "
                "доступа к Яндекс Музыке (схема OAuth, не Bearer)."
            )
        if response.status_code == 403:
            raise MusicError(
                "Яндекс Музыка: доступ к этому разделу запрещён (HTTP 403). "
                "Проверьте аккаунт и доступ к музыке."
            )
        raise MusicError(f"Яндекс Музыка: HTTP {response.status_code}: {message or 'ошибка API'}")


class VkMusicClient(MusicClient):
    service = "vk"
    display_name = "VK Музыка"
    base_url = "https://api.vk.com/method"
    api_version = "5.236"

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        """VK отвечает 200 и кладёт ошибку в тело — проверяем и то, и другое."""
        response = self.http.get(
            f"{self.base_url}/{method}",
            params={"access_token": self.token, "v": self.api_version, **params},
        )
        self._raise(response)
        body = response.json()
        if isinstance(body, dict) and body.get("error"):
            error = body["error"]
            detail = error.get("error_msg") if isinstance(error, dict) else str(error)
            raise MusicError(f"VK Музыка: {detail or 'ошибка API'}")
        return (body or {}).get("response") if isinstance(body, dict) else None

    def test_connection(self) -> dict[str, Any]:
        users = self._call("users.get", {}) or []
        user = users[0] if isinstance(users, list) and users else {}
        return {
            "service": self.service,
            "connected": True,
            "account": " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or user.get("id"),
        }

    def search(self, query: str, limit: int) -> list[Track]:
        result = self._call("audio.search", {"q": query, "count": max(1, min(limit, 300))}) or {}
        items = result.get("items", []) if isinstance(result, dict) else []
        return [self._track(item) for item in items if item.get("id")]

    def playlists(self) -> list[dict[str, Any]]:
        return [
            {
                "service": self.service,
                "playlist_id": "audio",
                "title": "Мои аудиозаписи",
                "track_count": 0,
                "url": "https://vk.com/audios",
            }
        ]

    def playlist_tracks(self, playlist_id: str, limit: int) -> list[Track]:
        if playlist_id != "audio":
            raise MusicError("VK Музыка: публичный API не возвращает плейлисты пользователя")
        result = self._call("audio.get", {"count": max(1, min(limit, 200))}) or {}
        items = result.get("items", []) if isinstance(result, dict) else []
        tracks = [self._track(item) for item in items if item.get("id")]
        for track in tracks:
            track.context_id = playlist_id
        return tracks[:limit]

    def play_track(self, track: Track) -> dict[str, Any]:
        return self.open_track(track)

    def open_track(self, track: Track) -> dict[str, Any]:
        owner_id = (track.extra or {}).get("owner_id", "")
        url = track.url or f"https://vk.com/audio{owner_id}_{track.track_id}"
        return {
            "service": self.service,
            "action": "open",
            "url": url,
            "deep_link": "",
            "track_id": track.track_id,
            "playing": False,
            "needs_user_action": True,
            "message": (
                "Публичный API VK не запускает воспроизведение удалённо: ссылку "
                "нужно открыть и нажать «Воспроизвести». Не сообщай, что трек играет."
            ),
        }

    @staticmethod
    def _track(item: dict[str, Any]) -> Track:
        owner_id = str(item.get("owner_id") or "")
        track_id = str(item.get("id") or "")
        return Track(
            service="vk",
            track_id=track_id,
            title=str(item.get("title") or "Без названия"),
            artist=str(item.get("artist") or "Неизвестный исполнитель"),
            album=str(item.get("album") or ""),
            url=f"https://vk.com/audio{owner_id}_{track_id}",
            extra={"owner_id": owner_id},
        )

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.is_success:
            return
        if response.status_code == 401:
            raise MusicError("VK Музыка: токен отклонён или истёк")
        raise MusicError(f"VK Музыка: HTTP {response.status_code}: {response.text[:300]}")


CLIENTS = {
    "yandex": YandexMusicClient,
    "vk": VkMusicClient,
}
SERVICE_LABELS = {
    "yandex": "Яндекс Музыка",
    "vk": "VK Музыка",
}
# Words a person or the model actually uses for a service, in any case.
SERVICE_ALIASES = {
    "yandex": ("yandex", "яндекс", "yamusic", "я.музыка", "ямusic"),
    "vk": ("vk", "вк", "vkmusic", "вконтакте", "vk music"),
}


def open_in_browser(url: str) -> bool:
    """Open a link in the user's browser from the plugin process.

    The plugin iframe cannot do this: its sandbox is `allow-scripts
    allow-forms`, so `window.open` and `target=_blank` are both blocked.
    """
    if not url.lower().startswith(("http://", "https://")):
        return False
    try:
        if sys.platform == "win32":
            os.startfile(url)  # type: ignore[attr-defined]
        else:
            subprocess.Popen([
                "open" if sys.platform == "darwin" else "xdg-open",
                url,
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        log.exception("could not open %s", url)
        return False


_desktop_exe_cache: dict[str, str | None] = {}


def desktop_player_exe(service: str) -> str | None:
    """Path to the installed desktop player, or None.

    Yandex Music's desktop app registers the `yandexmusic://` deep link and
    reads the last argv entry as that link (verified in its app.asar:
    `checkIsDeeplink` matches `yandexmusic://.*`, `transformUrlToInternal`
    turns it into an internal route). The registry key is often missing until
    the app has run once, so the executable is launched directly.
    """
    if service in _desktop_exe_cache:
        return _desktop_exe_cache[service]
    candidates: list[Path] = []
    if service == "yandex":
        local = os.environ.get("LOCALAPPDATA", "")
        program_files = [os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", "")]
        names = ["Яндекс Музыка.exe", "Yandex Music.exe", "yandex-music.exe"]
        roots = [Path(local) / "Programs" / "YandexMusic"] if local else []
        roots += [Path(pf) / "Yandex" / "YandexMusic" for pf in program_files if pf]
        candidates = [root / name for root in roots for name in names]
    found = next((str(path) for path in candidates if path.exists()), None)
    _desktop_exe_cache[service] = found
    return found


def open_in_desktop_player(service: str, deep_link: str) -> bool:
    exe = desktop_player_exe(service)
    if not exe or not deep_link:
        return False
    try:
        subprocess.Popen(
            [exe, deep_link],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        return True
    except Exception:
        log.exception("could not start desktop player %s", exe)
        return False


OPEN_MODES = ("auto", "desktop", "browser")


def _data_dir() -> Path:
    override = os.environ.get("ASTRA_MUSIC_DATA_DIR")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "music-controller"
    return Path(__file__).resolve().parent.parent / "data"


@ui_page(
    "music",
    "Музыка",
    "index.html",
    icon_svg=TAB_ICON_SVG,
)
@ui_effect("visualizer.html", id="music-visualizer", audio=True)
class AstraMusic(Plugin):
    """Astra plugin: music-controller."""

    def __init__(self):
        super().__init__()
        self.data_dir = _data_dir()
        self.settings_file = self.data_dir / "settings.json"
        self.playback_selection_file = self.data_dir / "playback-selection.json"
        self._settings_lock = threading.Lock()
        self._audio_host = PlaybackHost(self)
        from .discord_music import DiscordSource
        self._discord_music = DiscordSource(self)
        self._desktop_widget = DesktopWidget(self)
        self._system_audio = SystemAudio()
        self._visualizer_settings = _visualizer_settings(self._read_settings().get("visualizer"))
        self._music_audio_bands: list[float] = []
        self._music_audio_sample_at = 0.0
        self._music_audio_sample_revision = -1
        self._playback_lock = threading.RLock()
        self._easyvk_lock = threading.RLock()
        self._vk_oauth_job_lock = threading.Lock()
        self._vk_music_job_lock = threading.Lock()
        self._vk_music_job: dict[str, Any] = {"job_id": "", "status": "idle"}
        self._vk_oauth_job: dict[str, Any] = {
            "job_id": "",
            "status": "idle",
            "message": "",
            "error": "",
            "connection": {},
        }
        self._vk_stream_cache: dict[str, tuple[str, float]] = {}
        self.clients: dict[str, MusicClient] = {}
        # The persistent native audio process owns the stream; visible iframes
        # only send commands and display this shared state.
        self._playback_revision = 0
        self._playback_command_revision = 0
        self._playback_state_probe_logged_revision: int | None = None
        self._playback_queue: list[dict[str, Any]] = []
        self._playback_queue_index = -1
        self._playback_source = "track"
        self._playback_source_title = ""
        self._radio_station = ""
        self._radio_batch_id = ""
        self._radio_started_revision = -1
        try:
            saved_volume = float(self._read_settings().get("playback_volume", 0.8))
            self._playback_volume = max(
                0.0, min(1.0, saved_volume)
            )
        except (TypeError, ValueError):
            self._playback_volume = 0.8
        self._duck_restore_volume: float | None = None
        self._duck_reasons: set[str] = set()
        self._playback_state: dict[str, Any] = {
            "revision": 0, "url": "", "title": "", "artist": "",
            # `error` is reserved by Astra's SDK response envelope. Keeping it
            # in an otherwise successful state dict makes the SDK return an
            # empty result_json instead of serializing the state.
            "status": "stopped", "playback_error": "",
            "position_seconds": 0.0, "duration_seconds": 0.0,
            "volume": self._playback_volume,
            "command": "", "command_revision": 0,
            "queue_index": -1, "queue_count": 0,
            "source": "track", "source_title": "",
        }

    async def call_tool(self, name: str, arguments_json: str) -> dict[str, Any]:
        result = await super().call_tool(name, arguments_json)
        if isinstance(result, dict) and isinstance(result.get("result"), str):
            try:
                # The SDK serializes dicts with ensure_ascii=True by default.
                # Keep Cyrillic playlist titles readable in Astra's tool output.
                result["result"] = json.dumps(json.loads(result["result"]), ensure_ascii=False)
            except (TypeError, ValueError):
                pass
        return result

    async def on_config_changed(self, config: dict[str, Any]):
        self._load_clients()
        await asyncio.to_thread(self._desktop_widget.ensure)
        from .timer_integration import IntegrationServer
        if not hasattr(self, "_timer_bridge"):
            self._timer_bridge = IntegrationServer("music", {
                "search": self.ui_search, "current": self._timer_music_current,
                "play": self._timer_music_play, "pause": self._timer_music_pause,
                "stop": self._timer_music_stop,
                "discord_play": self._discord_music.play,
                "discord_state": self._discord_music.current,
                "discord_refresh": self._discord_music.refresh,
                "discord_like": self._discord_music.like,
                "discord_next": self._discord_music.advance,
                "discord_stop": self._discord_music_stop,
                "discord_playlists": self.ui_list_playlists,
            })
        self._timer_bridge.start(asyncio.get_running_loop())

    async def _discord_music_stop(self, session):
        return self._discord_music.stop(session)

    async def _timer_music_current(self):
        state = await self.ui_playback_state()
        selected = self._read_playback_selection() or {}
        track = state if state.get("track_id") else selected
        result = {key: track.get(key, state.get(key)) for key in (
            "service", "track_id", "title", "artist", "extra", "revision", "status", "playback_error")}
        # Playback state contains no VK restore metadata. Only merge selection
        # metadata when it belongs to the same track and provider.
        if (str(selected.get("track_id")) == str(track.get("track_id"))
                and selected.get("service") == track.get("service")):
            result["extra"] = {**(selected.get("extra") or {}), **(track.get("extra") or {})}
        return result

    async def _timer_music_play(self, service, track_id, title="", artist="", extra=None):
        if service == "yandex":
            result = await self.ui_yandex_start(track_id=track_id, title=title, artist=artist, auto_play=True)
        elif service == "vk":
            # Also repairs previously saved alarms that lost owner_id: the
            # regular playback path can restore metadata by searching VK.
            result = await self._guarded(self._play_sync, {
                "service": service, "track_id": track_id, "title": title,
                "artist": artist, "extra": extra or {},
            })
        else:
            return {"error": "Выберите Яндекс Музыку или VK"}
        if result.get("error"):
            return result
        result = await self._wait_for_playback_start(result)
        if result.get("error"):
            return result
        return await self._timer_music_current()

    async def _timer_music_pause(self):
        return await self.ui_playback_control(action="pause")

    async def _timer_music_stop(self, revision):
        state = await self.ui_playback_state()
        if state.get("revision") == revision:
            return await self.ui_playback_stop()
        return {"ok": True, "changed": True}

    def subscribed_events(self) -> list[str]:
        return ["state_changed"]

    async def on_state_changed(self, event: dict[str, Any]) -> None:
        """Duck only this plugin's player while Astra handles a voice request."""
        if not isinstance(event, dict):
            return
        raw_state = event.get("new_state", event.get("newState", event.get("state")))
        states = {0: "STOPPED", 1: "READY", 2: "LISTENING", 3: "PROCESSING",
                  4: "SPEAKING", 5: "ERROR"}
        if isinstance(raw_state, int) or str(raw_state or "").isdigit():
            state = states.get(int(raw_state), "")
        else:
            state = str(raw_state or "").upper().replace("CORE_STATE_", "").split(".")[-1]
        log.info("Astra core state event: %s; player status: %s", state or "unknown", self._playback_state.get("status"))
        if state in {"LISTENING", "PROCESSING", "SPEAKING"}:
            self._set_music_ducked(True, "core")
        elif state in {"READY", "STOPPED", "ERROR"}:
            self._set_music_ducked(False, "core")

    def _set_music_ducked(self, active: bool, reason: str) -> None:
        with self._playback_lock:
            if active:
                self._duck_reasons.add(reason)
            else:
                self._duck_reasons.discard(reason)
            if self._duck_reasons:
                if self._duck_restore_volume is not None:
                    return
                if self._playback_state.get("status") != "playing" or not self._playback_state.get("url"):
                    return
                self._duck_restore_volume = self._playback_volume
                self._playback_volume = min(self._playback_volume, 0.1)
            else:
                if self._duck_restore_volume is None:
                    return
                self._playback_volume = self._duck_restore_volume
                self._duck_restore_volume = None
            self._playback_command_revision += 1
            self._playback_state["command_revision"] = self._playback_command_revision
            self._playback_state["command"] = "volume"
            self._playback_state["volume"] = self._playback_volume
            log.info("Astra music volume %s (reason=%s)", "ducked" if self._duck_restore_volume is not None else "restored", reason)

    def _load_clients(self) -> None:
        for client in self.clients.values():
            client.close()
        self.clients.clear()
        settings = self._read_settings()
        for service, client_cls in CLIENTS.items():
            service_settings = settings.get(service) or {}
            token = service_settings.get("token", "")
            if token:
                self.clients[service] = client_cls(
                    token=token,
                )

    def _service_configured(self, service: str, settings: dict[str, Any] | None = None) -> bool:
        settings = settings if isinstance(settings, dict) else self._read_settings()
        entry = settings.get(service) or {}
        if normalize_token(entry.get("token", "")):
            return True
        return bool(
            service == "vk"
            and self._vk_cookie_file.is_file()
            and str(entry.get("user_id", "")).isdigit()
            and int(entry.get("user_id", 0)) > 0
        )

    def _read_settings(self) -> dict[str, Any]:
        with self._settings_lock:
            try:
                return json.loads(self.settings_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}

    def _write_settings(self, settings: dict[str, Any]) -> None:
        with self._settings_lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.settings_file.write_text(
                json.dumps(settings, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _saved_yandex_likes(self) -> set[str]:
        settings = self._read_settings()
        saved = settings.get("yandex_liked_track_ids", [])
        if not isinstance(saved, list):
            return set()
        return {str(track_id) for track_id in saved if str(track_id).isdigit()}

    def _remember_yandex_like(self, track_id: str) -> None:
        with self._settings_lock:
            try:
                settings = json.loads(self.settings_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                settings = {}
            if not isinstance(settings, dict):
                settings = {}
            saved = settings.get("yandex_liked_track_ids", [])
            ids = [str(item) for item in saved if str(item).isdigit()] if isinstance(saved, list) else []
            if track_id not in ids:
                ids.append(track_id)
            settings["yandex_liked_track_ids"] = ids[-5000:]
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.settings_file.write_text(
                json.dumps(settings, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _read_playback_selection(self) -> dict[str, Any]:
        with self._settings_lock:
            try:
                value = json.loads(self.playback_selection_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
        service = str(value.get("service", "yandex")) if isinstance(value, dict) else ""
        if (
            not isinstance(value, dict)
            or service not in {"yandex", "vk"}
            or not str(value.get("track_id", "")).isdigit()
        ):
            return {}
        return {
            "service": service,
            "track_id": str(value["track_id"]),
            "title": str(value.get("title", ""))[:300],
            "artist": str(value.get("artist", ""))[:500],
            "cover_url": _cover_url(service, value.get("cover_url", "")),
            "extra": value.get("extra", {}) if isinstance(value.get("extra"), dict) else {},
        }

    def _write_playback_selection(
        self,
        track_id: str,
        title: str,
        artist: str,
        cover_url: str = "",
        service: str = "yandex",
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        value = {
            "service": service if service in {"yandex", "vk"} else "yandex",
            "track_id": str(track_id),
            "title": str(title or "")[:300],
            "artist": str(artist or "")[:500],
            "cover_url": _cover_url(service, cover_url),
            "extra": extra if isinstance(extra, dict) else {},
        }
        temporary = self.playback_selection_file.with_suffix(".tmp")
        with self._settings_lock:
            temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.playback_selection_file)

    def _clear_playback_selection(self) -> None:
        with self._settings_lock:
            self.playback_selection_file.unlink(missing_ok=True)

    def _client(self, service: str) -> MusicClient:
        if service not in CLIENTS:
            raise MusicError(f"Неизвестный сервис: {service}")
        client = self.clients.get(service)
        if client:
            return client
        settings = self._read_settings().get(service) or {}
        token = settings.get("token", "")
        if not token:
            raise NotConfigured(service, f"{SERVICE_LABELS[service]} не подключена")
        client = CLIENTS[service](
            token=token,
        )
        self.clients[service] = client
        return client

    def _resolve_track(
        self,
        service: str,
        track_id: str,
        title: str = "",
        artist: str = "",
        album: str = "",
        url: str = "",
        context_id: str = "",
        extra: dict[str, Any] | None = None,
        cover_url: str = "",
    ) -> Track:
        extra = extra or {}
        if title or artist or url or extra.get("owner_id"):
            return Track(
                service=service,
                track_id=track_id,
                title=title,
                artist=artist,
                album=album,
                url=url,
                context_id=context_id,
                extra=extra,
                cover_url=_cover_url(service, cover_url),
            )
        return self._client(service).get_track(track_id)

    # --- argument handling -------------------------------------------------
    #
    # The model behind Astra does not follow the JSON Schema it is shown: it
    # packs arguments into one free-form `kwargs` string (a bare id, a JSON
    # object, or a Python literal with single quotes). Every tool therefore
    # accepts `kwargs` and reads the same fields out of it, so any call shape
    # still resolves to something sensible.

    _ALIASES: dict[str, tuple[str, ...]] = {
        "service": ("provider", "music_service", "platform", "source"),
        "query": ("q", "text", "search", "request", "phrase"),
        "limit": ("count", "max", "top", "number"),
        "playlist_id": ("playlist", "kind"),
        "track_id": ("track", "audio_id", "id"),
        "title": ("name",),
        "artist": ("artist_name", "performer"),
        "album": ("album_name",),
        "url": ("link", "href"),
        "context_id": ("context",),
        "cover_url": ("cover", "image_url", "coverUri"),
        "extra": ("meta", "metadata"),
        "token": ("access_token",),
    }

    @staticmethod
    def _as_map(raw: Any) -> dict[str, Any]:
        """Any shape the model sent — dict, JSON, Python literal, bare string."""
        if isinstance(raw, dict):
            return dict(raw)
        if raw is None:
            return {}
        text = str(raw).strip()
        if not text:
            return {}
        for load in (json.loads, ast.literal_eval):
            try:
                value = load(text)
            except (ValueError, SyntaxError):
                continue
            if isinstance(value, dict):
                return value
            if isinstance(value, (str, int, float)):
                return {"_raw": str(value)}
        return {"_raw": text}

    def _args(self, kwargs: Any = "", **explicit: Any) -> dict[str, Any]:
        merged = self._as_map(kwargs)
        out: dict[str, Any] = {k: v for k, v in explicit.items() if v not in (None, "", [])}
        for key, value in merged.items():
            if key == "_raw" or value in (None, "", []):
                continue
            out.setdefault(key, value)
        out["_raw"] = str(merged.get("_raw") or "")
        return out

    def _pick(self, args: dict[str, Any], key: str) -> Any:
        names = [key, *self._ALIASES.get(key, ())]
        for name in names:
            value = args.get(name)
            if value not in (None, "", []):
                return value
        return ""

    @staticmethod
    def _as_int(value: Any, default: int, low: int, high: int) -> int:
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            number = default
        return max(low, min(number, high))

    def _resolve_service(self, raw: Any) -> str:
        """Map whatever the user or model called a service onto a client id.

        A name that looks like a service but is not one ("deezer") is an error,
        never a silent fall back to whatever happens to be configured.
        """
        text = str(raw or "").strip().lower()
        if text in CLIENTS:
            return text
        for service, words in SERVICE_ALIASES.items():
            if text and any(word in text for word in words):
                return service
        if text:
            raise MusicError(
                f"Неизвестный сервис: {raw}. Доступны: " + ", ".join(SERVICE_LABELS.values()) + "."
            )
        settings = self._read_settings()
        configured = [service for service in CLIENTS if self._service_configured(service, settings)]
        if len(configured) == 1:
            return configured[0]
        if not configured:
            raise MusicError(
                "Ни один сервис не подключён. Откройте вкладку «Музыка» и сохраните "
                "токен Яндекс Музыки или VK Музыки."
            )
        raise MusicError(
            "Укажите, какой из подключённых сервисов иметь в виду: "
            + ", ".join(SERVICE_LABELS[s] for s in configured)
            + "."
        )

    # --- services ----------------------------------------------------------

    async def _guarded(self, fn: Any, *args: Any) -> dict[str, Any]:
        """Run a blocking service call off the event loop, errors as data."""
        try:
            return await asyncio.to_thread(fn, *args)
        except Exception as exc:
            log.exception("%s failed", getattr(fn, "__name__", fn))
            return {"success": False, "error": str(exc)}

    @property
    def _vk_cookie_file(self) -> Path:
        return self.data_dir / "vk-cookies.json"

    def _ensure_easyvk_runtime(self) -> tuple[str, Path]:
        node = shutil.which("node")
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not node:
            raise MusicError("Для встроенного VK-плеера не найден runtime Node.js Astra.")
        if not npm:
            raise MusicError("Node.js найден, но npm недоступен — не могу подготовить EasyVK.")

        source_dir = Path(__file__).resolve().parent.parent / "ui" / "easyvk"
        package = source_dir / "package.json"
        lockfile = source_dir / "package-lock.json"
        bridge = source_dir / "easyvk-bridge.cjs"
        if not package.is_file() or not lockfile.is_file() or not bridge.is_file():
            raise MusicError("В установке плагина отсутствуют файлы локального моста EasyVK.")

        runtime_dir = self.data_dir / "easyvk-node"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        lock_digest = hashlib.sha256(lockfile.read_bytes()).hexdigest()
        stamp = runtime_dir / ".astra-easyvk-lock"
        ready = (
            (runtime_dir / "node_modules" / "easyvk-audio" / "index.js").is_file()
            and stamp.is_file()
            and stamp.read_text(encoding="ascii", errors="ignore") == lock_digest
        )
        if not ready:
            shutil.copy2(package, runtime_dir / "package.json")
            shutil.copy2(lockfile, runtime_dir / "package-lock.json")
            npm_args = [npm, "ci", "--ignore-scripts", "--omit=dev", "--no-audit", "--no-fund"]
            if os.name == "nt":
                command: Any = subprocess.list2cmdline(npm_args)
            else:
                command = npm_args
            try:
                completed = subprocess.run(
                    command,
                    cwd=runtime_dir,
                    capture_output=True,
                    text=True,
                    timeout=240,
                    check=False,
                    shell=(os.name == "nt"),
                )
            except subprocess.TimeoutExpired as exc:
                raise MusicError("Установка локальных зависимостей EasyVK превысила 4 минуты.") from exc
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "").strip().splitlines()
                summary = detail[-1][:240] if detail else "npm ci завершился с ошибкой"
                raise MusicError("Не удалось установить локальные зависимости EasyVK: " + summary)
            stamp.write_text(lock_digest, encoding="ascii")
        return node, bridge

    @staticmethod
    def _easyvk_stream_url(value: Any) -> str:
        raw = str(value or "").strip()
        parsed = urlsplit(raw)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not raw or not parsed.path.lower().endswith(".m3u8"):
            return ""
        if not any(
            host == domain or host.endswith("." + domain)
            for domain in ("userapi.com", "vkuseraudio.net", "vkuseraudio.com", "vkuseraudio.ru", "vk.com")
        ):
            return ""
        return raw

    def _easyvk_call_sync(
        self,
        operation: str,
        *,
        token: str = "",
        cookie_path: Path | None = None,
        cookies_json: str = "",
        **params: Any,
    ) -> dict[str, Any]:
        settings = self._read_settings().get("vk") or {}
        api_token = normalize_token(token or settings.get("token", ""))
        try:
            user_id = int(params.pop("user_id", 0) or settings.get("user_id", 0) or 0)
        except (TypeError, ValueError):
            user_id = 0
        if not api_token and user_id <= 0:
            raise MusicError("Сначала войдите в VK через кнопку в карточке сервиса.")
        node, bridge = self._ensure_easyvk_runtime()
        target_cookie = cookie_path or self._vk_cookie_file
        payload = {
            "operation": operation,
            "token": api_token,
            "user_id": user_id,
            "cookie_path": str(target_cookie),
            **params,
        }
        if cookies_json:
            if len(cookies_json) > 2_000_000:
                raise MusicError("Экспорт cookies слишком большой (лимит 2 МБ).")
            payload["cookies_json"] = cookies_json

        with self._easyvk_lock:
            try:
                completed = subprocess.run(
                    [node, str(bridge)],
                    # The bridge reads stdin as UTF-8. Windows' locale encoding
                    # can corrupt a non-ASCII profile path (for example a
                    # Cyrillic username), so keep the pipe explicitly UTF-8 and
                    # JSON-escape non-ASCII characters as an additional guard.
                    input=json.dumps(payload, ensure_ascii=True),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=90,
                    check=False,
                    env={**os.environ, "ASTRA_EASYVK_RUNTIME_DIR": str(target_cookie.parent / "easyvk-node")},
                )
            except subprocess.TimeoutExpired as exc:
                raise MusicError("EasyVK не ответил за 90 секунд. Проверьте интернет и повторите.") from exc
            except OSError as exc:
                raise MusicError("Не удалось запустить локальный процесс EasyVK.") from exc

        marker = "ASTRA_EASYVK_RESULT:"
        result_line = next(
            (line[len(marker):] for line in reversed(completed.stdout.splitlines()) if line.startswith(marker)),
            "",
        )
        if not result_line:
            # Never surface raw process output: third-party errors may contain
            # request details or account data.
            raise MusicError("EasyVK завершился без ответа. Проверьте установку Node.js и cookies VK.")
        try:
            result = json.loads(result_line)
        except json.JSONDecodeError as exc:
            raise MusicError("EasyVK вернул ответ в неизвестном формате.") from exc
        if not isinstance(result, dict) or not result.get("success"):
            message = str(result.get("error", "EasyVK не смог выполнить запрос")) if isinstance(result, dict) else "EasyVK не смог выполнить запрос"
            for secret in (api_token, cookies_json):
                if secret:
                    message = message.replace(secret, "[скрыто]")
            raise MusicError(message[:500])
        return result

    @staticmethod
    def _vk_cache_key(owner_id: Any, track_id: Any) -> str:
        return f"{str(owner_id).strip()}:{str(track_id).strip()}"

    def _vk_track_from_easyvk(self, row: dict[str, Any]) -> Track:
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        owner_id = str(extra.get("owner_id", ""))
        track_id = str(row.get("track_id", ""))
        if not owner_id or not track_id:
            raise MusicError("EasyVK вернул аудиозапись без идентификатора владельца.")
        stream = self._easyvk_stream_url(row.get("stream_url"))
        if stream:
            self._vk_stream_cache[self._vk_cache_key(owner_id, track_id)] = (stream, time.time() + 45)
        return Track(
            service="vk",
            track_id=track_id,
            title=str(row.get("title") or "Без названия"),
            artist=str(row.get("artist") or "Неизвестный исполнитель"),
            album=str(row.get("album") or ""),
            url=str(row.get("url") or f"https://vk.com/audio{owner_id}_{track_id}"),
            extra=extra,
            cover_url=_vk_cover_url(row.get("cover_url", "")),
        )

    def _search_sync(self, service: str, query: str, limit: int) -> dict[str, Any]:
        if not query.strip():
            return {"error": "Пустой поисковый запрос", "service": service}
        if service == "vk" and self._vk_cookie_file.is_file():
            result = self._easyvk_call_sync("search", query=query, limit=limit)
            tracks = [
                self._vk_track_from_easyvk(item)
                for item in result.get("tracks", [])
                if isinstance(item, dict)
            ]
            return {
                "service": service,
                "query": query,
                "count": len(tracks),
                "tracks": [track.as_dict() for track in tracks],
            }
        tracks = self._client(service).search(query, limit)
        return {
            "service": service,
            "query": query,
            "count": len(tracks),
            "tracks": [track.as_dict() for track in tracks],
        }

    def _playlists_sync(self, service: str) -> dict[str, Any]:
        if service == "vk" and self._vk_cookie_file.is_file():
            result = self._easyvk_call_sync("playlists")
            playlists = [item for item in result.get("playlists", []) if isinstance(item, dict)]
            return {"service": service, "count": len(playlists), "playlists": playlists}
        playlists = self._client(service).playlists()
        return {"service": service, "count": len(playlists), "playlists": playlists}

    def _playlist_tracks_sync(self, service: str, playlist_id: str, limit: int) -> dict[str, Any]:
        if not playlist_id.strip():
            return {"error": "Не указан идентификатор плейлиста", "service": service}
        if service == "vk" and self._vk_cookie_file.is_file() and playlist_id != "audio":
            parts = playlist_id.split(":", 2)
            if len(parts) < 2 or not parts[0] or not parts[1]:
                raise MusicError("Неверный идентификатор VK-плейлиста.")
            owner_id, vk_playlist_id = parts[:2]
            access_hash = parts[2] if len(parts) > 2 else ""
            result = self._easyvk_call_sync(
                "playlist_tracks",
                owner_id=owner_id,
                playlist_id=vk_playlist_id,
                access_hash=access_hash,
                limit=limit,
            )
            tracks = [
                self._vk_track_from_easyvk(item)
                for item in result.get("tracks", [])
                if isinstance(item, dict)
            ]
            return {
                "service": service,
                "playlist_id": playlist_id,
                "count": len(tracks),
                "tracks": [track.as_dict() for track in tracks],
            }
        tracks = self._client(service).playlist_tracks(playlist_id, limit)
        return {
            "service": service,
            "playlist_id": playlist_id,
            "count": len(tracks),
            "tracks": [track.as_dict() for track in tracks],
        }

    def _deliver(self, result: dict[str, Any], service: str) -> dict[str, Any]:
        """Hand the track to the user's player: desktop app first, then browser.

        The iframe cannot open anything (sandbox has no `allow-popups`), so the
        plugin process does it. Whatever the outcome, the wording says what
        really happened: the model repeats this message to the user, and an
        earlier "Трек открыт в веб-плеере" on a call that opened nothing is how
        Astra ended up announcing "песня уже должна играть" over silence.
        """
        if result.get("status") == "started" or result.get("playing") is True:
            result["opened"] = False
            result["playing"] = True
            return result
        mode = str(self._read_settings().get("open_mode") or "auto").lower()
        deep_link = str(result.get("deep_link") or "")
        url = str(result.get("url") or "")
        via = ""
        if mode in ("auto", "desktop") and deep_link and open_in_desktop_player(service, deep_link):
            via = "desktop"
        elif mode in ("auto", "browser") and url and open_in_browser(url):
            via = "browser"
        result["opened"] = bool(via)
        result["opened_via"] = via
        result["playing"] = False
        result["needs_user_action"] = True
        if via == "desktop":
            result["message"] = (
                "Открыл трек в приложении Яндекс Музыка. Если воспроизведение не "
                "началось само — нажми «Воспроизвести» в нём."
            )
        elif via == "browser":
            result["message"] = (
                "Открыл страницу трека в браузере. Нажми «Воспроизвести» в плеере — "
                "публичный API не запускает звук сам."
            )
        else:
            result["message"] = (
                "Открыть плеер не удалось. Скажи пользователю, что трек найден, "
                "и передай ссылку из поля url."
            )
        return result

    def _play_sync(self, args: dict[str, Any]) -> dict[str, Any]:
        service = self._resolve_service(self._pick(args, "service"))
        track_id = str(self._pick(args, "track_id")).strip()
        if not track_id:
            return {"error": "Не указан идентификатор трека", "service": service}
        extra = self._as_map(self._pick(args, "extra"))
        if service == "vk" and not extra.get("owner_id") and self._vk_cookie_file.is_file():
            query = " ".join(
                value for value in (
                    str(self._pick(args, "artist") or "").strip(),
                    str(self._pick(args, "title") or "").strip(),
                ) if value
            ) or track_id
            metadata = self._easyvk_call_sync("search", query=query, limit=50)
            match = next(
                (row for row in metadata.get("tracks", []) if str(row.get("track_id", "")) == track_id),
                None,
            )
            if match:
                track = self._vk_track_from_easyvk(match)
                return self._start_vk_queue_sync([track], auto_play=True)
        track = self._resolve_track(
            service,
            track_id,
            str(self._pick(args, "title")),
            str(self._pick(args, "artist")),
            str(self._pick(args, "album")),
            str(self._pick(args, "url")),
            str(self._pick(args, "context_id")),
            extra,
            str(self._pick(args, "cover_url") or ""),
        )
        if service == "vk" and not (track.extra or {}).get("owner_id"):
            raise MusicError("VK не передал владельца трека. Повторите поиск песни в VK и выберите найденный результат.")
        if service == "yandex":
            if not track.cover_url:
                try:
                    track.cover_url = self._client(service).get_track(track.track_id).cover_url
                except Exception:
                    log.debug("Could not load Yandex cover for selected track", exc_info=True)
            return self._start_yandex_queue_sync([track], auto_play=True)
        if service == "vk":
            if not self._vk_cookie_file.is_file():
                raise MusicError("Для встроенного VK-плеера сначала войдите в VK через кнопку в карточке сервиса.")
            return self._start_vk_queue_sync([track], auto_play=True)
        result = self._client(service).play_track(track)
        result["track"] = track.as_dict()
        return self._deliver(result, service)

    def _yandex_stream_sync(self, track_id: str) -> dict[str, Any]:
        client = self._client("yandex")
        if not isinstance(client, YandexMusicClient):
            raise MusicError("Клиент Яндекс Музыки недоступен")
        return client.stream_url(track_id.strip())

    @staticmethod
    def _playback_track_item(track: Track, batch_id: str = "") -> dict[str, Any]:
        return {
            "service": track.service,
            "track_id": str(track.track_id),
            "title": str(track.title or "Без названия"),
            "artist": str(track.artist or "Неизвестный исполнитель"),
            "album": str(track.album or ""),
            "cover_url": _cover_url(track.service, track.cover_url),
            "url": str(track.url or ""),
            "context_id": str(track.context_id or ""),
            "extra": track.extra or {},
            "radio_batch_id": batch_id,
        }

    def _start_vk_queue_sync(
        self,
        tracks: list[Track],
        *,
        source: str = "track",
        source_title: str = "",
        auto_play: bool = True,
    ) -> dict[str, Any]:
        if not tracks:
            raise MusicError("В очереди VK нет доступных треков.")
        previous = (
            self._playback_source,
            self._playback_source_title,
            self._playback_queue,
            self._playback_queue_index,
            self._radio_station,
            self._radio_batch_id,
        )
        self._playback_source = source
        self._playback_source_title = source_title
        self._playback_queue = [self._playback_track_item(track) for track in tracks]
        self._playback_queue_index = 0
        self._radio_station = ""
        self._radio_batch_id = ""
        try:
            return self._activate_vk_queue_item_sync(auto_play=auto_play)
        except Exception:
            (
                self._playback_source,
                self._playback_source_title,
                self._playback_queue,
                self._playback_queue_index,
                self._radio_station,
                self._radio_batch_id,
            ) = previous
            raise

    def _activate_vk_queue_item_sync(
        self, *, auto_play: bool, position_seconds: float = 0.0
    ) -> dict[str, Any]:
        index = self._playback_queue_index
        if index < 0 or index >= len(self._playback_queue):
            raise MusicError("В очереди VK нет выбранного трека.")
        item = self._playback_queue[index]
        track_id = str(item.get("track_id", ""))
        extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
        owner_id = str(extra.get("owner_id", ""))
        cache_key = self._vk_cache_key(owner_id, track_id)
        cached = self._vk_stream_cache.get(cache_key)
        stream_url = cached[0] if cached and cached[1] > time.time() else ""
        duration = 0.0
        if not stream_url:
            result = self._easyvk_call_sync(
                "resolve",
                owner_id=owner_id,
                track_id=track_id,
                title=item.get("title", ""),
                artist=item.get("artist", ""),
                search_query=extra.get("search_query", ""),
                reload_id=extra.get("reload_id", ""),
                playlist_owner_id=extra.get("playlist_owner_id", ""),
                playlist_id=extra.get("playlist_id", ""),
                playlist_access_hash=extra.get("playlist_access_hash", ""),
            )
            stream_url = self._easyvk_stream_url(result.get("stream_url"))
            duration = float(result.get("duration_seconds", 0) or 0)
            if not stream_url:
                raise MusicError("EasyVK вернул неподдерживаемую ссылку VK HLS-аудиопотока.")
            self._vk_stream_cache[cache_key] = (stream_url, time.time() + 45)

        try:
            self._write_playback_selection(
                track_id,
                item.get("title", ""),
                item.get("artist", ""),
                item.get("cover_url", ""),
                service="vk",
                extra=extra,
            )
        except OSError:
            log.exception("Could not persist selected VK track")

        with self._playback_lock:
            self._playback_revision += 1
            self._playback_command_revision += 1
            revision = self._playback_revision
            command_revision = self._playback_command_revision
            self._playback_state = {
                "revision": revision,
                "service": "vk",
                "url": stream_url,
                "track_id": track_id,
                "title": item.get("title", ""),
                "artist": item.get("artist", ""),
                "album": item.get("album", ""),
                "cover_url": _vk_cover_url(item.get("cover_url", "")),
                "liked": False,
                "expires_at": time.time() + 40,
                "status": "play_requested" if auto_play else "ready",
                "playback_error": "",
                "position_seconds": max(0.0, position_seconds),
                "duration_seconds": duration,
                "volume": self._playback_volume,
                "command": "play" if auto_play else "load",
                "command_revision": command_revision,
                "queue_index": index,
                "queue_count": len(self._playback_queue),
                "source": self._playback_source,
                "source_title": self._playback_source_title,
                "radio_station": "",
                "radio_batch_id": "",
            }
        log.info("VK playback stream resolved (revision %s)", revision)
        self._audio_host.ensure()
        return {
            "success": True,
            "playing": False,
            "playback_requested": bool(auto_play),
            "status": "play_requested" if auto_play else "ready",
            "revision": revision,
            "track": {key: item[key] for key in ("track_id", "title", "artist", "album", "cover_url")},
            "source": self._playback_source,
            "source_title": self._playback_source_title,
            "queue_count": len(self._playback_queue),
            "message": "VK-поток передан встроенному плееру Astra.",
        }

    def _start_vk_single_sync(
        self,
        track_id: str,
        title: str = "",
        artist: str = "",
        auto_play: bool = False,
        cover_url: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        extra = extra if isinstance(extra, dict) else {}
        owner_id = str(extra.get("owner_id", ""))
        if not track_id or not owner_id:
            raise MusicError("Для восстановления VK-трека нужен его owner_id. Найдите трек заново.")
        track = Track(
            service="vk",
            track_id=str(track_id),
            title=str(title or "Без названия"),
            artist=str(artist or "VK Музыка"),
            url=f"https://vk.com/audio{owner_id}_{track_id}",
            extra=extra,
            cover_url=_vk_cover_url(cover_url),
        )
        return self._start_vk_queue_sync([track], auto_play=auto_play)

    def _start_vk_playlist_sync(self, playlist_id: str, playlist_name: str = "") -> dict[str, Any]:
        if playlist_id == "audio" or (playlist_name or "").strip().casefold() in {"моя музыка", "мои треки"}:
            return self._start_vk_my_music_sync()
        if not self._vk_cookie_file.is_file():
            raise MusicError("Для встроенных VK-плейлистов сначала войдите в VK через кнопку в карточке сервиса.")
        playlists = self._playlists_sync("vk").get("playlists", [])
        wanted = (playlist_name or playlist_id).strip()
        playlist = next(
            (row for row in playlists if str(row.get("playlist_id", "")) == wanted),
            None,
        )
        if playlist is None:
            normalized = " ".join(wanted.casefold().split())
            matches = [
                row for row in playlists
                if " ".join(str(row.get("title", "")).casefold().split()) == normalized
            ]
            if len(matches) == 1:
                playlist = matches[0]
            elif not matches:
                raise MusicError("VK-плейлист не найден. Сначала вызовите list_playlists(service='vk').")
            else:
                raise MusicError("Нашлось несколько одноимённых VK-плейлистов: " + ", ".join(str(row.get("title", "")) for row in matches[:5]))
        found_id = str(playlist.get("playlist_id", ""))
        result = self._playlist_tracks_sync("vk", found_id, 500)
        tracks = [
            self._resolve_track(
                "vk",
                str(row.get("track_id", "")),
                str(row.get("title", "")),
                str(row.get("artist", "")),
                str(row.get("album", "")),
                str(row.get("url", "")),
                extra=row.get("extra") if isinstance(row.get("extra"), dict) else {},
                cover_url=str(row.get("cover_url", "")),
            )
            for row in result.get("tracks", [])
        ]
        if not tracks:
            raise MusicError("В этом VK-плейлисте нет доступных треков.")
        title = str(playlist.get("title", "VK-плейлист"))
        playback = self._start_vk_queue_sync(tracks, source="playlist", source_title=title, auto_play=True)
        playback["playlist_id"] = found_id
        return playback

    def _start_vk_my_music_sync(self) -> dict[str, Any]:
        if not self._vk_cookie_file.is_file():
            raise MusicError("Сначала войдите в VK во вкладке «Музыка».")
        result = self._easyvk_call_sync("my_tracks")
        tracks = [self._vk_track_from_easyvk(row) for row in result.get("tracks", []) if isinstance(row, dict)]
        return self._start_vk_queue_sync(tracks, source="library", source_title="Мои треки", auto_play=True)

    def _start_vk_music_job(self) -> dict[str, Any]:
        # CallFromUi has a 10s bridge deadline, regardless of tool timeouts.
        # Network work must continue outside that request.
        with self._vk_music_job_lock:
            if self._vk_music_job.get("status") == "loading":
                return dict(self._vk_music_job)
            job_id = f"vk-music-{time.time_ns()}"
            self._vk_music_job = {"job_id": job_id, "status": "loading", "message": "Загружаю мои треки ВК…"}

        def load() -> None:
            try:
                result = self._start_vk_my_music_sync()
                log.info("VK personal library prepared: %s tracks, revision %s", result.get("queue_count"), result.get("revision"))
                update = {"status": "ready", "result": result}
            except Exception as exc:
                log.error("VK library preparation failed: %s", type(exc).__name__)
                update = {"status": "failed", "error": str(exc)}
            with self._vk_music_job_lock:
                if self._vk_music_job.get("job_id") == job_id:
                    self._vk_music_job.update(update)

        threading.Thread(target=load, name="vk-library-start", daemon=True).start()
        return {"job_id": job_id, "status": "loading", "message": "Загружаю мои треки ВК…"}

    def _vk_music_job_status(self, job_id: str) -> dict[str, Any]:
        with self._vk_music_job_lock:
            if self._vk_music_job.get("job_id") != job_id:
                return {"status": "failed", "error": "Запрос заменён новым запуском музыки."}
            return dict(self._vk_music_job)

    def _start_yandex_queue_sync(
        self,
        tracks: list[Track],
        *,
        source: str = "track",
        source_title: str = "",
        station: str = "",
        batch_id: str = "",
        auto_play: bool = True,
    ) -> dict[str, Any]:
        if not tracks:
            raise MusicError("В очереди нет доступных треков.")
        self._playback_source = source
        self._playback_source_title = source_title
        self._playback_queue = [
            self._playback_track_item(track, batch_id if source == "radio" else "")
            for track in tracks
        ]
        self._playback_queue_index = 0
        self._radio_station = station
        self._radio_batch_id = batch_id
        return self._activate_yandex_queue_item_sync(auto_play=auto_play)

    def _start_yandex_single_sync(
        self,
        track_id: str,
        title: str = "",
        artist: str = "",
        auto_play: bool = False,
        cover_url: str = "",
    ) -> dict[str, Any]:
        track_id = str(track_id).strip()
        if not cover_url:
            try:
                metadata = self._client("yandex").get_track(track_id)
                cover_url = metadata.cover_url
                title = title or metadata.title
                artist = artist or metadata.artist
            except Exception:
                log.debug("Could not load Yandex track cover during restore", exc_info=True)
        track = Track(
            service="yandex",
            track_id=track_id,
            title=str(title or "Без названия"),
            artist=str(artist or "Яндекс Музыка"),
            url=f"https://music.yandex.ru/track/{str(track_id).strip()}",
            cover_url=_yandex_cover_url(cover_url),
        )
        return self._start_yandex_queue_sync([track], auto_play=auto_play)

    def _activate_yandex_queue_item_sync(
        self, *, auto_play: bool, position_seconds: float = 0.0
    ) -> dict[str, Any]:
        index = self._playback_queue_index
        if index < 0 or index >= len(self._playback_queue):
            raise MusicError("В очереди нет выбранного трека.")
        item = self._playback_queue[index]
        track_id = str(item.get("track_id", ""))
        result = self._yandex_stream_sync(track_id)
        if result.get("error"):
            raise MusicError(str(result["error"]))
        stream = urlsplit(str(result.get("url", "")))
        if stream.scheme != "https" or not any(
            stream.hostname == domain or (stream.hostname or "").endswith("." + domain)
            for domain in ("yandex.ru", "yandex.net")
        ):
            raise MusicError("Яндекс вернул неподдерживаемый адрес аудиопотока.")
        try:
            self._write_playback_selection(
                track_id,
                item.get("title", ""),
                item.get("artist", ""),
                item.get("cover_url", ""),
                service="yandex",
            )
        except OSError:
            log.exception("Could not persist selected Yandex track")

        with self._playback_lock:
            self._playback_revision += 1
            self._playback_command_revision += 1
            revision = self._playback_revision
            command_revision = self._playback_command_revision
            self._playback_state = {
                "revision": revision,
                "service": "yandex",
                "url": result.get("url", ""),
                "track_id": track_id,
                "title": item.get("title", ""),
                "artist": item.get("artist", ""),
                "album": item.get("album", ""),
                "cover_url": item.get("cover_url", ""),
                "liked": track_id in self._saved_yandex_likes(),
                "expires_at": time.time() + 60,
                "status": "play_requested" if auto_play else "ready",
                "playback_error": "",
                "position_seconds": max(0.0, position_seconds),
                "duration_seconds": 0.0,
                "volume": self._playback_volume,
                "command": "play" if auto_play else "load",
                "command_revision": command_revision,
                "queue_index": index,
                "queue_count": len(self._playback_queue),
                "source": self._playback_source,
                "source_title": self._playback_source_title,
                "radio_station": self._radio_station,
                "radio_batch_id": item.get("radio_batch_id", ""),
            }
        log.info("Yandex playback stream resolved (revision %s)", revision)
        self._audio_host.ensure()
        return {
            "success": True,
            "playing": False,
            "playback_requested": bool(auto_play),
            "status": "play_requested" if auto_play else "ready",
            "revision": revision,
            "track": {key: item[key] for key in ("track_id", "title", "artist", "album", "cover_url")},
            "source": self._playback_source,
            "source_title": self._playback_source_title,
            "queue_count": len(self._playback_queue),
            "message": "Запрос отправлен во встроенный плеер Astra.",
        }

    def _start_yandex_playlist_sync(self, playlist_id: str, playlist_name: str = "") -> dict[str, Any]:
        client = self._client("yandex")
        playlists = client.playlists()
        wanted = (playlist_name or playlist_id).strip()
        playlist = next(
            (entry for entry in playlists if str(entry.get("playlist_id", "")) == wanted),
            None,
        )
        if playlist is None:
            normalized = " ".join(wanted.casefold().split())
            matches = [
                entry for entry in playlists
                if " ".join(str(entry.get("title", "")).casefold().split()) == normalized
            ]
            if len(matches) == 1:
                playlist = matches[0]
            elif not matches:
                raise MusicError("Плейлист не найден. Сначала посмотрите названия через list_playlists.")
            else:
                choices = ", ".join(str(entry.get("title", "")) for entry in matches[:5])
                raise MusicError("Нашлось несколько одноимённых плейлистов: " + choices)
        found_id = str(playlist.get("playlist_id", ""))
        tracks = client.playlist_tracks(found_id, limit=5000)
        if not tracks:
            raise MusicError("В этом плейлисте нет треков.")
        for track in tracks:
            track.context_id = found_id
        self._playback_source = "playlist"
        self._playback_source_title = str(playlist.get("title", "Плейлист"))
        result = self._start_yandex_queue_sync(
            tracks, source="playlist", source_title=self._playback_source_title, auto_play=True
        )
        result["playlist_id"] = found_id
        result["source_title"] = self._playback_source_title
        return result

    def _start_yandex_radio_sync(self, station: str = "user:onyourwave") -> dict[str, Any]:
        station = station.strip() or "user:onyourwave"
        if station.casefold() in {"моя волна", "мою волну", "my wave", "mywave", "моя волна яндекс музыки"}:
            station = "user:onyourwave"
        client = self._client("yandex")
        if not isinstance(client, YandexMusicClient):
            raise MusicError("Клиент Яндекс Музыки недоступен")
        tracks, batch_id = client.radio_batch(station)
        self._playback_source = "radio"
        self._playback_source_title = "Моя волна" if station == "user:onyourwave" else station
        result = self._start_yandex_queue_sync(
            tracks,
            source="radio",
            source_title=self._playback_source_title,
            station=station,
            batch_id=batch_id,
            auto_play=True,
        )
        try:
            client.radio_feedback(station, "started", batch_id=batch_id)
        except Exception:
            log.exception("Could not report Yandex radio start")
        return result

    def _advance_yandex_queue_sync(
        self, direction: int, played_seconds: float = 0.0, finished: bool = False
    ) -> dict[str, Any]:
        state = self._playback_state
        if not state.get("track_id"):
            raise MusicError("Сейчас ничего не воспроизводится.")
        current_index = self._playback_queue_index
        if current_index < 0 or current_index >= len(self._playback_queue):
            raise MusicError("Очередь воспроизведения потеряна. Выберите трек ещё раз.")
        if self._playback_source == "radio" and direction > 0:
            current = self._playback_queue[current_index]
            client = self._client("yandex")
            if isinstance(client, YandexMusicClient):
                event = "track_finished" if finished else "skip"
                try:
                    client.radio_feedback(
                        self._radio_station,
                        event,
                        track_id=str(current.get("track_id", "")),
                        batch_id=str(current.get("radio_batch_id", "")),
                        played_seconds=played_seconds,
                    )
                except Exception:
                    log.exception("Could not report Yandex radio track completion")

        next_index = current_index + (1 if direction > 0 else -1)
        if next_index >= len(self._playback_queue) and self._playback_source == "radio":
            client = self._client("yandex")
            if isinstance(client, YandexMusicClient):
                previous_track = str(self._playback_queue[current_index].get("track_id", ""))
                tracks, batch_id = client.radio_batch(self._radio_station, previous_track)
                self._radio_batch_id = batch_id
                self._playback_queue.extend(
                    self._playback_track_item(track, batch_id) for track in tracks
                )
        if next_index < 0 or next_index >= len(self._playback_queue):
            return {"success": False, "playing": False, "message": "Больше треков в очереди нет."}
        self._playback_queue_index = next_index
        try:
            if str(state.get("service", "yandex")) == "vk":
                return self._activate_vk_queue_item_sync(auto_play=True)
            return self._activate_yandex_queue_item_sync(auto_play=True)
        except Exception:
            # Keep the current track and queue index intact if resolving the next
            # stream fails; the user can retry after reconnecting the service.
            self._playback_queue_index = current_index
            raise

    def _playback_control_sync(self, action: str, value: float = 0.0) -> dict[str, Any]:
        action = action.strip().lower()
        aliases = {
            "resume": "play", "continue": "play",
            "громче": "volume_up", "тише": "volume_down",
            "без звука": "mute", "включи звук": "unmute",
            "лайкни": "like", "лайк": "like", "нравится": "like",
        }
        action = aliases.get(action, action)
        if action == "stop":
            return self._stop_playback_sync()
        if action == "like":
            track_id = str(self._playback_state.get("track_id", ""))
            if not track_id:
                raise MusicError("Сначала выберите трек, который нужно добавить в понравившиеся.")
            if self._playback_state.get("service") == "vk":
                raise MusicError("Лайк VK из встроенного плеера пока не поддерживается.")
            if self._playback_state.get("liked"):
                return {"success": True, "action": "like", "liked": True, "message": "Трек уже в понравившихся."}
            client = self._client("yandex")
            if not isinstance(client, YandexMusicClient):
                raise MusicError("Клиент Яндекс Музыки недоступен.")
            client.set_track_liked(track_id, True)
            self._remember_yandex_like(track_id)
            with self._playback_lock:
                if str(self._playback_state.get("track_id", "")) == track_id:
                    self._playback_state["liked"] = True
            return {"success": True, "action": "like", "liked": True, "message": "Трек добавлен в понравившиеся Яндекс Музыки."}
        if action in {"next", "previous", "ended"}:
            seconds = float(self._playback_state.get("position_seconds") or 0)
            if action == "ended":
                seconds = max(seconds, value)
                duration = float(self._playback_state.get("duration_seconds") or 0)
                return self._advance_yandex_queue_sync(1, seconds, finished=duration <= 0 or seconds >= duration - 1)
            return self._advance_yandex_queue_sync(1 if action == "next" else -1, seconds)
        if action in {"volume_up", "volume_down", "mute", "unmute"}:
            step = abs(value) if value else 1.0
            if step > 10.0:
                step /= 10.0  # accept earlier commands expressed as percent
            step = max(0.0, min(10.0, step))
            with self._playback_lock:
                normal_volume = (
                    self._duck_restore_volume
                    if self._duck_restore_volume is not None
                    else self._playback_volume
                )
            if action == "volume_up":
                value = min(10.0, normal_volume * 10 + step)
            elif action == "volume_down":
                value = max(0.0, normal_volume * 10 - step)
            elif action == "mute":
                value = 0.0
            else:
                settings = self._read_settings()
                saved_volume = settings.get("playback_volume_before_mute")
                if saved_volume is None:
                    value = 8.0
                else:
                    saved_volume = float(saved_volume)
                    value = (
                        saved_volume * 10.0
                        if settings.get("playback_volume_before_mute_scale") == "0-1"
                        else saved_volume / 10.0  # former scale stored 0–100
                    )
            action = "volume"
        if action == "volume":
            if value > 10.0:
                value /= 10.0  # accept earlier voice calls expressed as percent
            value = max(0.0, min(10.0, value))
            desired_volume = value / 10.0
            with self._playback_lock:
                if self._duck_restore_volume is not None:
                    self._duck_restore_volume = desired_volume
                    self._playback_volume = min(desired_volume, 0.1)
                else:
                    self._playback_volume = desired_volume
            settings = self._read_settings()
            if desired_volume > 0:
                settings["playback_volume_before_mute"] = desired_volume
                settings["playback_volume_before_mute_scale"] = "0-1"
            settings["playback_volume"] = desired_volume
            self._write_settings(settings)
            command = "volume"
        elif action in {"seek", "forward", "backward"}:
            if not self._playback_state.get("track_id"):
                raise MusicError("Сейчас нечего перематывать.")
            current = float(self._playback_state.get("position_seconds") or 0)
            if action == "forward":
                value = current + abs(value)
            elif action == "backward":
                value = current - abs(value)
            duration = float(self._playback_state.get("duration_seconds") or 0)
            value = max(0.0, min(value, duration if duration > 0 else 86400.0))
            command = "seek"
        elif action in {"play", "pause"}:
            if not self._playback_state.get("track_id"):
                raise MusicError("Сначала выберите трек, плейлист или волну.")
            if action == "play" and float(self._playback_state.get("expires_at") or 0) < time.time() + 5:
                activate = (
                    self._activate_vk_queue_item_sync
                    if self._playback_state.get("service") == "vk"
                    else self._activate_yandex_queue_item_sync
                )
                return activate(auto_play=True, position_seconds=float(self._playback_state.get("position_seconds") or 0))
            command = action
        else:
            raise MusicError("Действие должно быть play, pause, stop, next, previous, like, seek, forward, backward или volume.")

        with self._playback_lock:
            self._playback_command_revision += 1
            self._playback_state["command_revision"] = self._playback_command_revision
            self._playback_state["command"] = command
            self._playback_state["volume"] = self._playback_volume
            if command == "seek":
                self._playback_state["seek_to_seconds"] = value
                self._playback_state["position_seconds"] = value
            elif command == "play":
                self._playback_state["status"] = "play_requested"
                self._playback_state["playback_error"] = ""
        if command == "play":
            self._audio_host.ensure()
        return {
            "success": True,
            "action": action,
            "revision": self._playback_revision,
            "value": value if action in {"volume", "seek", "forward", "backward"} else None,
            "status": self._playback_state.get("status", ""),
            "playing": self._playback_state.get("status") == "playing",
            "playback_requested": command == "play",
            "message": (
                f"Громкость музыки установлена на {value:g} из 10."
                if action == "volume" else "Команда передана встроенному плееру Astra."
            ),
        }

    def _stop_playback_sync(self) -> dict[str, Any]:
        try:
            self._clear_playback_selection()
        except OSError:
            log.exception("Could not clear selected Yandex track")
        with self._playback_lock:
            self._playback_queue = []
            self._playback_queue_index = -1
            self._radio_station = ""
            self._radio_batch_id = ""
            self._playback_revision += 1
            self._playback_command_revision += 1
            self._playback_state = {
                "revision": self._playback_revision,
                "service": "yandex",
                "url": "", "title": "", "artist": "", "track_id": "",
                "status": "stopped", "playback_error": "",
                "position_seconds": 0.0, "duration_seconds": 0.0,
                "volume": self._playback_volume,
                "command": "stop", "command_revision": self._playback_command_revision,
                "queue_index": -1, "queue_count": 0,
                "source": "track", "source_title": "",
            }
        return {"success": True, "playing": False, "status": "stopped"}

    async def _wait_for_playback_start(self, result: dict[str, Any]) -> dict[str, Any]:
        if not result.get("playback_requested"):
            return result
        revision = result.get("revision")
        for _ in range(20):
            state = self._playback_state
            if state.get("revision") != revision:
                break
            status = state.get("status")
            if status in {"playing", "blocked", "failed"}:
                return {
                    **result,
                    "success": status == "playing",
                    "status": status,
                    "playing": status == "playing",
                    "playback_error": state.get("playback_error", ""),
                    **(
                        {"error": str(state.get("playback_error") or "Автозапуск заблокирован Astra.")}
                        if status == "blocked"
                        else {"error": str(state.get("playback_error") or "Встроенный плеер Astra не смог запустить аудио.")}
                        if status == "failed"
                        else {}
                    ),
                    "message": (
                        "Трек играет во встроенном плеере Astra."
                        if status == "playing"
                        else "Astra подготовила трек, но автозапуск заблокирован. Нажмите ▶ в виджете."
                        if status == "blocked"
                        else "Плеер Astra не смог запустить трек. Проверьте виджет."
                    ),
                }
            await asyncio.sleep(0.25)
        state = self._playback_state
        if state.get("revision") == revision and state.get("status") == "playing":
            return {
                **result,
                "success": True,
                "status": "playing",
                "playing": True,
                "message": "Трек играет во встроенном плеере Astra.",
            }
        if state.get("revision") != revision:
            detail = "Плеер переключился на другой запрос до подтверждения запуска трека."
            status = "superseded"
        else:
            detail = "Встроенный плеер не подтвердил начало воспроизведения за 5 секунд."
            status = str(state.get("status", "play_requested"))
        return {
            **result,
            "success": False,
            "status": status,
            "playing": False,
            "error": detail,
            "message": detail,
        }

    def _open_sync(self, args: dict[str, Any]) -> dict[str, Any]:
        service = self._resolve_service(self._pick(args, "service"))
        track_id = str(self._pick(args, "track_id")).strip()
        if not track_id:
            return {"error": "Не указан идентификатор трека", "service": service}
        track = self._resolve_track(
            service,
            track_id,
            str(self._pick(args, "title")),
            str(self._pick(args, "artist")),
            str(self._pick(args, "album")),
            str(self._pick(args, "url")),
            str(self._pick(args, "context_id")),
            self._as_map(self._pick(args, "extra")),
        )
        result = self._client(service).open_track(track)
        result["track"] = track.as_dict()
        return self._deliver(result, service)

    def _save_token_sync(
        self,
        service: Any,
        token: Any,
        session_cookies: Any = "",
    ) -> dict[str, Any]:
        try:
            service = self._resolve_service(service)
        except MusicError as exc:
            return {"error": str(exc)}
        current_settings = self._read_settings()
        previous = current_settings.get(service) or {}
        token = normalize_token(token) or normalize_token(previous.get("token", ""))
        if not token:
            return {"error": "Для EasyVK нужен VK API-токен. Можно оставить поле пустым, если токен уже сохранён.", "service": service}
        try:
            probe = CLIENTS[service](token)
            try:
                connection = probe.test_connection()
            finally:
                probe.close()
        except Exception as exc:
            log.exception("token validation failed for %s", service)
            return {"error": str(exc), "service": service}

        cookie_export = str(session_cookies or "").strip() if service == "vk" else ""
        if service == "vk" and cookie_export:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            candidate = self.data_dir / "vk-cookies.importing.json"
            try:
                audio_connection = self._easyvk_call_sync(
                    "test", token=token, cookie_path=candidate, cookies_json=cookie_export
                )
                candidate.replace(self._vk_cookie_file)
                connection["audio_ready"] = False
                connection["session_saved"] = True
                connection["session_valid"] = bool(audio_connection.get("session_valid"))
                connection["audio_status"] = "not_checked"
                connection["audio_account"] = str(audio_connection.get("account", ""))
            except Exception as exc:
                candidate.unlink(missing_ok=True)
                return {"error": str(exc), "service": service}
            finally:
                candidate.unlink(missing_ok=True)
        elif service == "vk" and self._vk_cookie_file.is_file():
            try:
                audio_connection = self._easyvk_call_sync("test", token=token)
                connection["audio_ready"] = False
                connection["session_saved"] = True
                connection["session_valid"] = bool(audio_connection.get("session_valid"))
                connection["audio_status"] = "not_checked"
                connection["audio_account"] = str(audio_connection.get("account", ""))
            except Exception as exc:
                connection["audio_ready"] = False
                connection["audio_error"] = str(exc)[:300]
        elif service == "vk":
            connection["audio_ready"] = False

        settings = current_settings
        settings[service] = {"token": token}
        self._write_settings(settings)
        self._load_clients()
        self._remember_connection(service, connection)
        return {"success": True, "service": service, "configured": True, "connection": connection}

    def _set_vk_oauth_job(self, job_id: str, **updates: Any) -> None:
        with self._vk_oauth_job_lock:
            if self._vk_oauth_job.get("job_id") == job_id:
                self._vk_oauth_job.update(updates)

    def _vk_oauth_status_sync(self, job_id: Any) -> dict[str, Any]:
        with self._vk_oauth_job_lock:
            job = dict(self._vk_oauth_job)
        if not job_id or str(job_id) != job.get("job_id"):
            return {"error": "Сеанс входа VK не найден. Нажмите «Войти через VK» ещё раз."}
        result = {key: job[key] for key in ("job_id", "status", "message")}
        if job["status"] == "completed":
            result["connection"] = dict(job.get("connection") or {})
        elif job["status"] == "failed":
            result["error"] = job.get("error") or "Не удалось войти в VK."
        return result

    def _start_vk_oauth_login_sync(self) -> dict[str, Any]:
        """Start VK sign-in in the background so Astra's short UI bridge can poll it."""
        with self._vk_oauth_job_lock:
            current = self._vk_oauth_job
            if current.get("status") in {"preparing", "waiting_for_login", "validating"}:
                return {
                    "job_id": current["job_id"],
                    "status": current["status"],
                    "message": current.get("message", "Выполняется вход VK…"),
                }
            job_id = f"vk-{time.time_ns()}"
            self._vk_oauth_job = {
                "job_id": job_id,
                "status": "preparing",
                "message": "Подготавливаю локальное подключение VK…",
                "error": "",
                "connection": {},
            }
        try:
            worker = threading.Thread(
                target=self._run_vk_oauth_login,
                args=(job_id,),
                name="astra-vk-oauth",
                daemon=True,
            )
            worker.start()
        except RuntimeError:
            self._set_vk_oauth_job(
                job_id,
                status="failed",
                message="Не удалось запустить вход VK.",
                error="Не удалось запустить фоновую задачу входа VK.",
            )
            return {"error": "Не удалось запустить фоновую задачу входа VK."}
        return {
            "job_id": job_id,
            "status": "preparing",
            "message": "Подготавливаю локальное подключение VK…",
        }

    def _run_vk_oauth_login(self, job_id: str) -> None:
        cookie_secrets: list[str] = []

        def safe_error(value: Any) -> str:
            text = str(value or "Ошибка входа VK")
            for secret in cookie_secrets:
                if secret:
                    text = text.replace(secret, "[скрыто]")
            return text[:400]

        try:
            self._set_vk_oauth_job(
                job_id,
                status="preparing",
                message="Готовлю EasyVK. Первый запуск может занять несколько минут…",
            )
            self._ensure_easyvk_runtime()

            self._set_vk_oauth_job(
                job_id,
                status="waiting_for_login",
                message="Откройте окно VK, войдите в аккаунт или отсканируйте QR-код.",
            )
            project_dir = Path(__file__).resolve().parent.parent
            run_options: dict[str, Any] = {
                "cwd": project_dir,
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "timeout": 360,
                "check": False,
            }
            if os.name == "nt":
                run_options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            completed = subprocess.run(
                [sys.executable, "-m", "src.vk_auth_window"],
                **run_options,
            )

            marker = "ASTRA_VK_AUTH_RESULT:"
            line = next(
                (item[len(marker):] for item in reversed(completed.stdout.splitlines()) if item.startswith(marker)),
                "",
            )
            if not line:
                raise MusicError("Окно VK не вернуло результат входа. Проверьте WebView2 Runtime.")
            try:
                result = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MusicError("Окно VK вернуло ответ в неизвестном формате.") from exc
            if not isinstance(result, dict) or not result.get("success"):
                detail = result.get("error", "Вход VK не завершён.") if isinstance(result, dict) else "Вход VK не завершён."
                raise MusicError(str(detail))

            user_id = str(result.get("user_id", "")).strip()
            cookies = result.get("cookies")
            if isinstance(cookies, list):
                cookie_secrets = [
                    str(row.get("value", ""))
                    for row in cookies
                    if isinstance(row, dict) and row.get("value")
                ]
            if not user_id.isdigit() or int(user_id) <= 0 or not isinstance(cookies, list):
                raise MusicError("VK не передал ID аккаунта и локальную сессию.")

            self._set_vk_oauth_job(
                job_id,
                status="validating",
                message="Проверяю сессию VK и сохраняю подключение…",
            )
            saved = self._save_vk_session_sync(user_id, json.dumps(cookies, ensure_ascii=False))
            if saved.get("error"):
                raise MusicError(str(saved["error"]))
            connection = saved.get("connection") or {}
            self._set_vk_oauth_job(
                job_id,
                status="completed",
                message="Вход VK принят; локальная сессия сохранена. Доступ к музыке проверится при поиске.",
                connection={
                    "audio_ready": bool(connection.get("audio_ready")),
                    "session_saved": bool(connection.get("session_saved")),
                    "session_valid": bool(connection.get("session_valid")),
                    "audio_account": str(connection.get("audio_account", "")),
                },
            )
        except subprocess.TimeoutExpired:
            error = "Окно входа VK не завершилось за 6 минут."
            self._set_vk_oauth_job(job_id, status="failed", message="Вход VK не завершён.", error=error)
        except OSError:
            error = "Не удалось запустить встроенное окно авторизации VK."
            self._set_vk_oauth_job(job_id, status="failed", message="Вход VK не запущен.", error=error)
        except Exception as exc:
            error = safe_error(exc)
            log.warning("VK sign-in job failed: %s", error)
            self._set_vk_oauth_job(
                job_id,
                status="failed",
                message="Не удалось завершить вход VK.",
                error=error,
            )

    def _remember_connection(self, service: str, connection: dict[str, Any]) -> None:
        """Keep the account and subscription the service just confirmed.

        The tab shows them without asking the service again on every open.
        """
        if not connection.get("connected"):
            return
        settings = self._read_settings()
        entry = settings.get(service)
        if not isinstance(entry, dict):
            return
        if "account" in connection:
            entry["account"] = str(connection.get("account") or "")
        if "subscription" in connection:
            entry["subscription"] = str(connection.get("subscription") or "")
        if service == "vk" and "session_valid" in connection:
            entry["session_valid"] = bool(connection.get("session_valid"))
        self._write_settings(settings)

    def _mark_vk_session_invalid(self) -> None:
        """Clear a stale green session badge after an explicit failed check."""
        settings = self._read_settings()
        entry = settings.get("vk")
        if not isinstance(entry, dict):
            return
        entry["session_valid"] = False
        self._write_settings(settings)

    def _save_vk_session_sync(self, user_id: Any, cookies_json: str) -> dict[str, Any]:
        try:
            resolved_user_id = int(str(user_id).strip())
        except (TypeError, ValueError):
            return {"error": "VK не передал корректный ID аккаунта.", "service": "vk"}
        if resolved_user_id <= 0:
            return {"error": "VK не передал корректный ID аккаунта.", "service": "vk"}

        self.data_dir.mkdir(parents=True, exist_ok=True)
        candidate = self.data_dir / "vk-cookies.importing.json"
        try:
            audio = self._easyvk_call_sync(
                "test",
                cookie_path=candidate,
                cookies_json=cookies_json,
                user_id=resolved_user_id,
            )
            candidate.replace(self._vk_cookie_file)
        except Exception as exc:
            candidate.unlink(missing_ok=True)
            return {"error": str(exc), "service": "vk"}
        finally:
            candidate.unlink(missing_ok=True)

        settings = self._read_settings()
        settings["vk"] = {
            "user_id": resolved_user_id,
            "auth_mode": "browser-session",
            "account": str(audio.get("account") or f"VK ID {resolved_user_id}"),
            "session_valid": bool(audio.get("session_valid")),
        }
        self._write_settings(settings)
        self._vk_stream_cache.clear()
        self._load_clients()
        connection = {
            "connected": True,
            "audio_ready": False,
            "session_saved": True,
            "session_valid": bool(audio.get("session_valid")),
            "audio_status": "not_checked",
            "audio_account": str(audio.get("account") or f"VK ID {resolved_user_id}"),
        }
        self._remember_connection("vk", connection)
        return {"success": True, "service": "vk", "configured": True, "connection": connection}

    def _remove_token_sync(self, service: Any) -> dict[str, Any]:
        try:
            service = self._resolve_service(service)
        except MusicError as exc:
            return {"error": str(exc)}
        settings = self._read_settings()
        settings.pop(service, None)
        self._write_settings(settings)
        if service == "vk":
            self._vk_cookie_file.unlink(missing_ok=True)
            self._vk_stream_cache.clear()
            profile = (self.data_dir / "vk-browser-profile").resolve()
            if profile.is_relative_to(self.data_dir.resolve()) and profile.is_dir():
                shutil.rmtree(profile)
        self._load_clients()
        return {"success": True, "service": service, "configured": False}

    def _test_connection_sync(self, service: Any) -> dict[str, Any]:
        try:
            resolved = self._resolve_service(service)
        except MusicError as exc:
            return {"error": str(exc), "connected": False}
        if resolved == "vk" and self._vk_cookie_file.is_file():
            try:
                audio = self._easyvk_call_sync("test")
                connection = {
                    "connected": True,
                    "audio_ready": False,
                    "session_saved": True,
                    "session_valid": bool(audio.get("session_valid")),
                    "audio_status": "not_checked",
                    "account": str(audio.get("account") or "VK подключён"),
                    "audio_account": str(audio.get("account") or ""),
                }
                self._remember_connection(resolved, connection)
                return connection
            except Exception as exc:
                if "VK_SESSION_EXPIRED:" in str(exc):
                    self._mark_vk_session_invalid()
                return {
                    "error": str(exc),
                    "service": resolved,
                    "connected": False,
                    "audio_ready": False,
                    "audio_error": str(exc)[:300],
                }
        try:
            connection = self._client(resolved).test_connection()
        except Exception as exc:
            log.exception("connection test failed for %s", resolved)
            return {"error": str(exc), "service": resolved, "connected": False}
        if resolved == "vk":
            if self._vk_cookie_file.is_file():
                try:
                    audio = self._easyvk_call_sync("test")
                    connection["audio_ready"] = False
                    connection["session_saved"] = True
                    connection["session_valid"] = bool(audio.get("session_valid"))
                    connection["audio_status"] = "not_checked"
                    connection["audio_account"] = str(audio.get("account", ""))
                except Exception as exc:
                    connection["audio_ready"] = False
                    connection["audio_error"] = str(exc)[:300]
            else:
                connection["audio_ready"] = False
        self._remember_connection(resolved, connection)
        return connection

    def _connection_status_sync(self, full: bool = False) -> dict[str, Any]:
        settings = self._read_settings()
        status: dict[str, Any] = {
            "services": {
                service: {
                    "label": SERVICE_LABELS[service],
                    "configured": self._service_configured(service, settings),
                    **(
                        {
                            "audio_session": self._vk_cookie_file.is_file(),
                            "session_valid": bool((settings.get("vk") or {}).get("session_valid")),
                        }
                        if service == "vk"
                        else {}
                    ),
                }
                for service in CLIENTS
            }
        }
        if full:
            # Only the tab asks for this: an account name and a local path are
            # useless to the model and would only widen what leaves the plugin.
            for service, info in status["services"].items():
                entry = settings.get(service) or {}
                info["account"] = entry.get("account", "")
                info["subscription"] = entry.get("subscription", "")
            status["open_mode"] = str(settings.get("open_mode") or "auto")
            status["desktop_player"] = desktop_player_exe("yandex") or ""
        return status

    def _set_open_mode_sync(self, mode: Any) -> dict[str, Any]:
        mode = str(mode or "").strip().lower()
        if mode not in OPEN_MODES:
            return {"error": "Режим открытия: " + ", ".join(sorted(OPEN_MODES))}
        settings = self._read_settings()
        settings["open_mode"] = mode
        self._write_settings(settings)
        return {"success": True, "open_mode": mode, "desktop_player": desktop_player_exe("yandex") or ""}

    # --- tools -------------------------------------------------------------

    @tool("Search a song or artist by name in exactly one music service: service is 'yandex' or 'vk'. Use the service the user explicitly requested. VK search accepts names; no direct track URL is needed. If VK returns an error or zero tracks, report the actual result and stop: do not ask the user for a direct link or silently retry via Yandex or another service. After a VK result, pass its service, track_id, title, artist, album, url, cover_url and extra to play_track. Never claim playback succeeded unless play_track returns playing=true.")
    async def search_tracks(self, service: str = "", query: str = "", limit: int = 10, kwargs: str = "") -> dict[str, Any]:
        args = self._args(kwargs, service=service, query=query, limit=limit)
        query = str(self._pick(args, "query") or args.get("_raw") or "")
        try:
            resolved = self._resolve_service(self._pick(args, "service"))
        except MusicError as exc:
            return {"error": str(exc), "services": list(CLIENTS)}
        result = await self._guarded(
            self._search_sync, resolved, query, self._as_int(args.get("limit"), 10, 1, 20)
        )
        result.setdefault("service", resolved)
        if resolved == "vk":
            result["fallback_allowed"] = False
            result["needs_url"] = False
            if result.get("error"):
                result["playing"] = False
                result["user_action"] = "Сообщите ошибку поиска VK дословно; ссылку на трек запрашивать не нужно."
            elif not result.get("tracks"):
                result.setdefault("message", "VK не вернул совпадений. Не повторяйте поиск через другой сервис.")
        return result

    @tool("List the user's playlists in exactly one music service: service is 'yandex' or 'vk'. The authenticated local VK session DOES provide personal VK playlists; call this tool when asked whether you can see them. Never claim VK playlists are inaccessible without calling it. For a named playlist, play_playlist can accept its exact title directly.")
    async def list_playlists(self, service: str = "", kwargs: str = "") -> dict[str, Any]:
        args = self._args(kwargs, service=service)
        try:
            resolved = self._resolve_service(self._pick(args, "service"))
        except MusicError as exc:
            return {"error": str(exc), "services": list(CLIENTS)}
        result = await self._guarded(self._playlists_sync, resolved)
        result.setdefault("service", resolved)
        if resolved == "vk":
            result["fallback_allowed"] = False
        return result

    @tool("Show the user's own VK Music playlists through Astra Music's connected local VK session. Call this for 'покажи мои плейлисты в ВК' or 'ты видишь мои плейлисты в ВК?'. Return the real list or the actual connection error; never say VK access is unavailable before trying this tool.")
    async def list_vk_playlists(self) -> dict[str, Any]:
        return await self.list_playlists(service="vk")

    @tool("List tracks inside one playlist. Needs service ('yandex', 'vk') and playlist_id from list_playlists; limit is 1..100. To start a whole Yandex or VK playlist in Astra, use play_playlist instead.")
    async def list_playlist_tracks(self, service: str = "", playlist_id: str = "", limit: int = 50, kwargs: str = "") -> dict[str, Any]:
        args = self._args(kwargs, service=service, playlist_id=playlist_id, limit=limit)
        try:
            resolved = self._resolve_service(self._pick(args, "service"))
        except MusicError as exc:
            return {"error": str(exc), "services": list(CLIENTS)}
        found_id = str(self._pick(args, "playlist_id") or args.get("_raw") or "")
        return await self._guarded(
            self._playlist_tracks_sync, resolved, found_id, self._as_int(args.get("limit"), 50, 1, 100)
        )

    @tool("Play a track the user requested. Use only the music service explicitly named by the user. For a song request, first use search_tracks on that service, then pass the best matching result's service, track_id, title, artist, album, url, context_id, cover_url and extra here. If search or playback returns an error, report it and stop; never try another service without the user's permission. Never say the song is playing unless this tool returns playing=true.")
    async def play_track(
        self,
        service: str = "",
        track_id: str = "",
        title: str = "",
        artist: str = "",
        album: str = "",
        url: str = "",
        context_id: str = "",
        cover_url: str = "",
        extra: str = "",
        kwargs: str = "",
    ) -> dict[str, Any]:
        args = self._args(
            kwargs,
            service=service,
            track_id=track_id,
            title=title,
            artist=artist,
            album=album,
            url=url,
            context_id=context_id,
            cover_url=cover_url,
            extra=extra,
        )
        result = await self._guarded(self._play_sync, args)
        result = await self._wait_for_playback_start(result)
        try:
            if self._resolve_service(self._pick(args, "service")) == "vk":
                result["fallback_allowed"] = False
        except MusicError:
            pass
        return result

    @tool("Actually START a named Yandex Music or VK playlist inside Astra. Call this tool when the user asks to play a playlist; saying 'I am starting it' without a tool call does nothing. Pass the exact title in playlist_name directly; this tool looks up the user's playlists itself, so a separate list_playlists call is optional. Use the service the user named. Report success only when this tool returns playing=true; otherwise report its actual error. Never switch services silently.")
    async def play_playlist(
        self,
        service: str = "",
        playlist_id: str = "",
        playlist_name: str = "",
        kwargs: str = "",
    ) -> dict[str, Any]:
        args = self._args(kwargs, service=service, playlist_id=playlist_id, playlist_name=playlist_name)
        try:
            resolved = self._resolve_service(self._pick(args, "service"))
        except MusicError as exc:
            return {"error": str(exc)}
        if resolved == "vk":
            result = await self._guarded(
                self._start_vk_playlist_sync,
                str(args.get("playlist_id", "") or ""),
                str(args.get("playlist_name", "") or ""),
            )
        elif resolved == "yandex":
            result = await self._guarded(
                self._start_yandex_playlist_sync,
                str(args.get("playlist_id", "") or ""),
                str(args.get("playlist_name", "") or ""),
            )
        else:
            return {"error": "Встроенное воспроизведение плейлистов доступно для Яндекс Музыки и VK."}
        result = await self._wait_for_playback_start(result)
        if resolved == "vk":
            result["service"] = "vk"
            result["fallback_allowed"] = False
        return result

    @tool("PLAY a named VK Music playlist NOW inside Astra, for example 'включи плейлист Клуб 2024 в ВК'. Call this tool before saying it is playing. Give playlist_name exactly as the user said; this tool finds the VK playlist and waits for actual audio playback. If playing is false, tell the user the real error instead of claiming you started it.")
    async def play_vk_playlist(self, playlist_name: str = "", playlist_id: str = "", kwargs: str = "") -> dict[str, Any]:
        args = self._args(kwargs, playlist_name=playlist_name, playlist_id=playlist_id)
        return await self.play_playlist(
            service="vk",
            playlist_id=str(args.get("playlist_id") or ""),
            playlist_name=str(args.get("playlist_name") or args.get("_raw") or ""),
        )

    @tool("Play ALL the user's own VK Music tracks in their default VK order. Use for 'включи мою музыку в ВК' or 'включи мои треки', without choosing a named playlist. Report success only if playing=true; otherwise report the real playback status.")
    async def play_vk_my_music(self) -> dict[str, Any]:
        return await self.play_playlist(service="vk", playlist_id="audio")

    @action("Музыка: включить мои треки ВК")
    async def music_vk_my_music(self) -> str:
        log.info("Local voice action requested VK personal library")
        result = self._start_vk_music_job()
        if result.get("success") is False or result.get("status") == "failed":
            raise MusicError(str(result.get("error") or result.get("message") or "Не удалось загрузить мои треки ВК."))
        return str(result.get("message") or "Загружаю ваши треки ВК.")

    @tool("PLAY a named Yandex Music playlist NOW inside Astra. Call this tool when the user asks to start one of their Yandex playlists. Pass its exact name in playlist_name; no prior list call is required. Only say it started when playing=true is returned.")
    async def play_yandex_playlist(self, playlist_name: str = "", playlist_id: str = "", kwargs: str = "") -> dict[str, Any]:
        args = self._args(kwargs, playlist_name=playlist_name, playlist_id=playlist_id)
        return await self.play_playlist(
            service="yandex",
            playlist_id=str(args.get("playlist_id") or ""),
            playlist_name=str(args.get("playlist_name") or args.get("_raw") or ""),
        )

    @tool("Start Яндекс Музыка's personal radio 'Моя волна' inside Astra. Call this when the user asks for My Wave, a personal wave, or Yandex radio. Optional station is a Yandex station id; leave it empty for user:onyourwave.")
    async def play_yandex_radio(self, station: str = "user:onyourwave", kwargs: str = "") -> dict[str, Any]:
        args = self._args(kwargs, station=station)
        result = await self._guarded(
            self._start_yandex_radio_sync,
            str(args.get("station", "user:onyourwave") or "user:onyourwave"),
        )
        return await self._wait_for_playback_start(result)

    @tool("Control the current Yandex Music or VK playback inside Astra. action must be play, pause, stop, next, previous, like, seek, forward, backward, volume, volume_up, volume_down, mute or unmute. For a specific spoken music volume level from 1 to 10, call set_music_volume instead of any Windows/system volume tool. Use like for Yandex songs; VK like is not yet supported. For volume_up/down, value is the number of levels to change (default 1). Use for 'пауза', 'перемотай на 1:20', 'сделай музыку громче' and 'следующий трек'.")
    async def control_music_playback(self, action: str = "", value: float = 0, kwargs: str = "") -> dict[str, Any]:
        args = self._args(kwargs, action=action, value=value)
        try:
            found_value = float(self._pick(args, "value") or 0)
        except (TypeError, ValueError):
            found_value = 0.0
        result = await self._guarded(
            self._playback_control_sync,
            str(self._pick(args, "action") or ""),
            found_value,
        )
        if result.get("playback_requested"):
            return await self._wait_for_playback_start(result)
        return result

    @tool("Set the volume of the music playing INSIDE Astra, not the Windows or PC master volume. Call this for spoken commands 'громкость 1', 'громкость 5', 'громкость на 10', 'сделай громкость музыки 3' and any music-player volume from 1 to 10. level is an integer 1..10, where 1=10% of player volume and 10=100%. This tool must be used even when the speaker only says 'громкость N' while Astra Music is playing.")
    async def set_music_volume(self, level: Any = None, kwargs: Any = "", **extra: Any) -> dict[str, Any]:
        args = self._args(kwargs, level=level)
        args.update({key: value for key, value in extra.items() if key not in args})
        raw_level = self._pick(args, "level")
        if raw_level in (None, ""):
            for alias in ("volume", "value", "music_volume", "volume_level"):
                if args.get(alias) not in (None, ""):
                    raw_level = args[alias]
                    break
        if raw_level in (None, ""):
            raw_level = next((key for key in args if str(key).isdigit() and 1 <= int(key) <= 10), "")
        if isinstance(raw_level, dict):
            raw_level = raw_level.get("level", raw_level.get("volume", ""))
        raw_text = str(raw_level or args.get("_raw") or "").strip()
        try:
            requested_level = int(raw_text)
        except (TypeError, ValueError):
            match = re.search(r"(?<!\d)(10|[1-9])(?!\d)", raw_text)
            if match is None:
                return {"success": False, "error": "Укажите громкость музыкального плеера числом от 1 до 10."}
            requested_level = int(match.group(1))
        if not 1 <= requested_level <= 10:
            return {"success": False, "error": "Громкость музыкального плеера должна быть от 1 до 10."}
        return await self._guarded(self._playback_control_sync, "volume", float(requested_level))

    # Keep the model-facing schema small while accepting malformed argument
    # names from older Astra models through the **extra fallback above.
    set_music_volume._astra_tool_meta.parameters_json = json.dumps({
        "type": "object",
        "properties": {
            "level": {"type": "integer", "minimum": 1, "maximum": 10},
            "kwargs": {"type": "string"},
        },
        "required": ["level"],
    })

    @action(
        "Быстрое управление музыкой",
        fields=[
            Field.dropdown(
                "action", "Действие",
                options=[
                    ("next", "Следующий трек"),
                    ("previous", "Предыдущий трек"),
                    ("pause", "Пауза"),
                    ("play", "Продолжить"),
                    ("stop", "Остановить"),
                    ("volume_up", "Громче на 1"),
                    ("volume_down", "Тише на 1"),
                    ("set_level", "Громкость 1–10"),
                ],
            ),
            Field.number("level", "Уровень громкости", min=1, max=10, step=1, default="5"),
        ],
    )
    async def music_shortcut(self, action: str = "", level: Any = None) -> str:
        """Action for Astra text triggers; no AI tool selection is involved."""
        selected_action = str(action or "").strip().lower()
        allowed = {"next", "previous", "pause", "play", "stop", "volume_up", "volume_down", "set_level"}
        if selected_action not in allowed:
            raise MusicError("Неизвестное действие быстрого управления музыкой.")
        if selected_action == "set_level":
            try:
                requested_level = int(level)
            except (TypeError, ValueError):
                raise MusicError("Укажите уровень громкости музыки от 1 до 10.")
            if not 1 <= requested_level <= 10:
                raise MusicError("Уровень громкости музыки должен быть от 1 до 10.")
            selected_action = "volume"
            value = float(requested_level)
        else:
            value = 1.0 if selected_action in {"volume_up", "volume_down"} else 0.0
        result = await self._guarded(self._playback_control_sync, selected_action, value)
        if result.get("playback_requested"):
            result = await self._wait_for_playback_start(result)
        if not result.get("success"):
            raise MusicError(str(result.get("error") or result.get("message") or "Команда плеера не выполнена."))
        # Astra displays action results directly, whereas tools and UI calls
        # need structured data. Keep voice-command answers human-readable.
        if selected_action in {"volume", "volume_up", "volume_down"}:
            return str(result.get("message") or "Громкость музыки изменена.")
        if selected_action == "pause":
            return "Музыка на паузе."
        if selected_action == "stop":
            return "Музыка остановлена."
        if result.get("playing"):
            title = str(result.get("title") or self._playback_state.get("title") or "")
            return f"Играет «{title}»." if title else "Музыка играет."
        return str(result.get("message") or "Команда выполнена.")

    # Astra currently invokes command-graph plugin actions without their saved
    # field values. Give each exact voice command a parameter-free action so
    # transport and volume controls cannot silently receive action="".
    @action("Музыка: следующий трек")
    async def music_next(self) -> str:
        return await self.music_shortcut("next")

    @action("Музыка: предыдущий трек")
    async def music_previous(self) -> str:
        return await self.music_shortcut("previous")

    @action("Музыка: пауза")
    async def music_pause(self) -> str:
        return await self.music_shortcut("pause")

    @action("Музыка: продолжить")
    async def music_play(self) -> str:
        return await self.music_shortcut("play")

    @action("Музыка: остановить")
    async def music_stop(self) -> str:
        return await self.music_shortcut("stop")

    @action("Музыка: громче")
    async def music_volume_up(self) -> str:
        return await self.music_shortcut("volume_up")

    @action("Музыка: тише")
    async def music_volume_down(self) -> str:
        return await self.music_shortcut("volume_down")

    @action("Музыка: громкость 1")
    async def music_volume_1(self) -> str:
        return await self.music_shortcut("set_level", 1)

    @action("Музыка: громкость 2")
    async def music_volume_2(self) -> str:
        return await self.music_shortcut("set_level", 2)

    @action("Музыка: громкость 3")
    async def music_volume_3(self) -> str:
        return await self.music_shortcut("set_level", 3)

    @action("Музыка: громкость 4")
    async def music_volume_4(self) -> str:
        return await self.music_shortcut("set_level", 4)

    @action("Музыка: громкость 5")
    async def music_volume_5(self) -> str:
        return await self.music_shortcut("set_level", 5)

    @action("Музыка: громкость 6")
    async def music_volume_6(self) -> str:
        return await self.music_shortcut("set_level", 6)

    @action("Музыка: громкость 7")
    async def music_volume_7(self) -> str:
        return await self.music_shortcut("set_level", 7)

    @action("Музыка: громкость 8")
    async def music_volume_8(self) -> str:
        return await self.music_shortcut("set_level", 8)

    @action("Музыка: громкость 9")
    async def music_volume_9(self) -> str:
        return await self.music_shortcut("set_level", 9)

    @action("Музыка: громкость 10")
    async def music_volume_10(self) -> str:
        return await self.music_shortcut("set_level", 10)

    @tool("Open a track in the service's web player (a real browser window). Needs service ('yandex', 'vk') and track_id from search_tracks or list_playlist_tracks.")
    async def open_track(
        self,
        service: str = "",
        track_id: str = "",
        title: str = "",
        artist: str = "",
        album: str = "",
        url: str = "",
        context_id: str = "",
        extra: str = "",
        kwargs: str = "",
    ) -> dict[str, Any]:
        args = self._args(
            kwargs,
            service=service,
            track_id=track_id,
            title=title,
            artist=artist,
            album=album,
            url=url,
            context_id=context_id,
            extra=extra,
        )
        return await self._guarded(self._open_sync, args)

    @tool("Save an access token for Yandex Music or a legacy VK API connection. Needs service ('yandex', 'vk') and token. For VK built-in audio, use the Music tab's local browser sign-in instead; tokens are never returned.")
    async def save_token(
        self,
        service: str = "",
        token: str = "",
        kwargs: str = "",
    ) -> dict[str, Any]:
        args = self._args(kwargs, service=service, token=token)
        found_token = self._pick(args, "token") or (args.get("_raw") if not self._pick(args, "service") else "")
        return await self._guarded(
            self._save_token_sync,
            self._pick(args, "service"),
            found_token,
        )

    @tool("Forget one music service connection: service is 'yandex' or 'vk'. For VK this also removes the local browser session.")
    async def remove_token(self, service: str = "", kwargs: str = "") -> dict[str, Any]:
        args = self._args(kwargs, service=service)
        return await self._guarded(self._remove_token_sync, self._pick(args, "service"))

    @tool("Check that Yandex Music or the local VK browser session is connected. Needs service ('yandex', 'vk'). Never returns credentials.")
    async def test_connection(self, service: str = "", kwargs: str = "") -> dict[str, Any]:
        args = self._args(kwargs, service=service)
        return await self._guarded(self._test_connection_sync, self._pick(args, "service"))

    @tool("Report which music services (Яндекс Музыка и VK Музыка) are connected. VK uses a local browser session; if session_valid is false, ask the user to sign in to VK again from the Music tab before searching or playing. A saved cookie file alone does not mean the session is valid.")
    async def connection_status(self) -> dict[str, Any]:
        return await self._guarded(self._connection_status_sync)

    async def get_ui_contributions(self) -> list[UiContribution]:
        contributions = await super().get_ui_contributions()
        contributions.append(
            UiContribution(
                id="music-home-player-v13",
                slot="home.widgets",
                url="player-remote.html" if self._audio_host.enabled else "player-v12.html",
                height=168,
                transparent=True,
                props={} if self._audio_host.enabled else {"audio": "true"},
            )
        )
        for contribution in contributions:
            if contribution.id == "music":
                contribution.transparent = True
            elif contribution.id == "music-visualizer":
                contribution.slot = "background.behind"
                contribution.transparent = True
                contribution.pointer_events = False
                contribution.props = {}
        log.info("Registered UI contributions: %s", ", ".join(
            f"{item.id}@{item.slot}:{item.url}" for item in contributions
        ))
        return contributions

    # --- calls from this plugin's own iframe -------------------------------
    #
    # Handlers run on the daemon's event loop, so each one goes through
    # `_guarded`: a slow network call must not freeze Astra's window.

    @ui_call("music_desktop_get")
    async def ui_desktop_get(self, **params: Any) -> dict[str, Any]:
        return self._desktop_widget.snapshot()

    @ui_call("music_desktop_set")
    async def ui_desktop_set(self, **params: Any) -> dict[str, Any]:
        settings = params.get("settings")
        if not isinstance(settings, dict):
            return {"error": "Не переданы настройки виджета."}
        return await asyncio.to_thread(self._desktop_widget.set, settings, bool(params.get("reset_position")))

    @ui_call("music_desktop_report")
    async def ui_desktop_report(self, **params: Any) -> dict[str, Any]:
        return self._desktop_widget.report(params.get("session", ""), params.get("x"), params.get("y"), params.get("closed") is True)

    @ui_call("music_visualizer_get")
    async def ui_visualizer_get(self, **params: Any) -> dict[str, Any]:
        return dict(self._visualizer_settings)

    @ui_call("music_visualizer_set")
    async def ui_visualizer_set(self, **params: Any) -> dict[str, Any]:
        value = params.get("settings")
        if not isinstance(value, dict):
            return {"error": "Не переданы настройки цветомузыки."}
        updated = _visualizer_settings({**self._visualizer_settings, **value})
        with self._settings_lock:
            try:
                stored = json.loads(self.settings_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                stored = {}
            stored["visualizer"] = updated
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_file.with_suffix(".visualizer.tmp")
            temporary.write_text(json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.settings_file)
            self._visualizer_settings = updated
        if updated["mode"] != "all" or not (updated["widget"] or updated["background"]):
            self._system_audio.pause()
        return dict(updated)

    @ui_call("music_visualizer_state")
    async def ui_visualizer_state(self, **params: Any) -> dict[str, Any]:
        settings = dict(self._visualizer_settings)
        system_enabled = settings["mode"] == "all" and (settings["widget"] or settings["background"])
        if system_enabled:
            self._system_audio.start()
        else:
            self._system_audio.pause()
        with self._playback_lock:
            playing = self._playback_state.get("status") == "playing" and bool(self._playback_state.get("url"))
            revision = self._playback_state.get("revision", 0)
            fresh = (playing and self._music_audio_sample_revision == revision
                     and time.monotonic() - self._music_audio_sample_at < 0.8)
            bands = self._system_audio.snapshot() if system_enabled else list(self._music_audio_bands) if fresh else []
            return {"settings": settings, "playing": playing,
                    "revision": revision, "bands": bands,
                    "audio_error": self._system_audio.error if system_enabled else ""}

    @ui_call("music_visualizer_sample")
    async def ui_visualizer_sample(self, **params: Any) -> dict[str, Any]:
        bands = params.get("bands")
        if not isinstance(bands, list) or len(bands) > 128:
            return {"accepted": False}
        try:
            values = [max(0.0, min(1.0, float(value))) for value in bands]
        except (ValueError, TypeError):
            return {"accepted": False}
        with self._playback_lock:
            if (params.get("revision") != self._playback_state.get("revision")
                    or self._playback_state.get("status") != "playing"):
                return {"accepted": False}
            self._music_audio_bands = values
            self._music_audio_sample_at = time.monotonic()
            self._music_audio_sample_revision = params["revision"]
        return {"accepted": True}

    @ui_call("music_save_token")
    async def ui_save_token(self, **params: Any) -> dict[str, Any]:
        return await self._guarded(
            self._save_token_sync,
            params.get("service", ""),
            params.get("token", ""),
            params.get("session_cookies", ""),
        )

    @ui_call("music_vk_oauth_login")
    async def ui_vk_oauth_login(self, **params: Any) -> dict[str, Any]:
        return await self._guarded(self._start_vk_oauth_login_sync)

    @ui_call("music_vk_oauth_status")
    async def ui_vk_oauth_status(self, **params: Any) -> dict[str, Any]:
        return await self._guarded(self._vk_oauth_status_sync, params.get("job_id", ""))

    @ui_call("music_remove_token")
    async def ui_remove_token(self, **params: Any) -> dict[str, Any]:
        return await self._guarded(self._remove_token_sync, params.get("service", ""))

    @ui_call("music_connection_status")
    async def ui_connection_status(self, **params: Any) -> dict[str, Any]:
        return await self._guarded(self._connection_status_sync, True)

    @ui_call("music_set_open_mode")
    async def ui_set_open_mode(self, **params: Any) -> dict[str, Any]:
        return await self._guarded(self._set_open_mode_sync, params.get("mode", ""))

    @ui_call("music_test_connection")
    async def ui_test_connection(self, **params: Any) -> dict[str, Any]:
        return await self._guarded(self._test_connection_sync, params.get("service", ""))

    @ui_call("music_search")
    async def ui_search(self, **params: Any) -> dict[str, Any]:
        try:
            service = self._resolve_service(params.get("service", ""))
        except MusicError as exc:
            return {"error": str(exc), "services": list(CLIENTS)}
        return await self._guarded(
            self._search_sync,
            service,
            str(params.get("query", "") or ""),
            self._as_int(params.get("limit"), 10, 1, 20),
        )

    @ui_call("music_list_playlists")
    async def ui_list_playlists(self, **params: Any) -> dict[str, Any]:
        try:
            service = self._resolve_service(params.get("service", ""))
        except MusicError as exc:
            return {"error": str(exc), "services": list(CLIENTS)}
        return await self._guarded(self._playlists_sync, service)

    @ui_call("music_list_playlist_tracks")
    async def ui_list_playlist_tracks(self, **params: Any) -> dict[str, Any]:
        try:
            service = self._resolve_service(params.get("service", ""))
        except MusicError as exc:
            return {"error": str(exc), "services": list(CLIENTS)}
        return await self._guarded(
            self._playlist_tracks_sync,
            service,
            str(params.get("playlist_id", "") or ""),
            self._as_int(params.get("limit"), 50, 1, 100),
        )

    @ui_call("music_play_vk_my_music")
    async def ui_play_vk_my_music(self, **params: Any) -> dict[str, Any]:
        return self._start_vk_music_job()

    @ui_call("music_vk_my_music_status")
    async def ui_vk_my_music_status(self, **params: Any) -> dict[str, Any]:
        return self._vk_music_job_status(str(params.get("job_id", "")))

    @ui_call("music_play_playlist")
    async def ui_play_playlist(self, **params: Any) -> dict[str, Any]:
        try:
            service = self._resolve_service(params.get("service", "yandex"))
        except MusicError as exc:
            return {"error": str(exc)}
        if service == "vk":
            return await self._guarded(
                self._start_vk_playlist_sync,
                str(params.get("playlist_id", "") or ""),
                str(params.get("playlist_name", "") or ""),
            )
        if service == "yandex":
            return await self._guarded(
                self._start_yandex_playlist_sync,
                str(params.get("playlist_id", "") or ""),
                str(params.get("playlist_name", "") or ""),
            )
        return {"error": "Встроенное воспроизведение плейлистов доступно для Яндекс Музыки и VK."}

    @ui_call("music_play_yandex_radio")
    async def ui_play_yandex_radio(self, **params: Any) -> dict[str, Any]:
        return await self._guarded(
            self._start_yandex_radio_sync,
            str(params.get("station", "user:onyourwave") or "user:onyourwave"),
        )

    @ui_call("music_play_track")
    async def ui_play_track(self, **params: Any) -> dict[str, Any]:
        return await self._guarded(self._play_sync, self._ui_track_args(params))

    @ui_call("music_yandex_stream_url")
    async def ui_yandex_stream_url(self, **params: Any) -> dict[str, Any]:
        return await self._guarded(
            self._yandex_stream_sync, str(params.get("track_id", "") or "")
        )

    @ui_call("music_yandex_start")
    async def ui_yandex_start(self, **params: Any) -> dict[str, Any]:
        result = await self._guarded(
            self._start_yandex_single_sync,
            str(params.get("track_id", "") or ""),
            str(params.get("title", "") or ""),
            str(params.get("artist", "") or ""),
            bool(params.get("auto_play", False)),
            str(params.get("cover_url", "") or ""),
        )
        if result.get("error"):
            return result
        return {**result, "ok": True}

    @ui_call("music_playback_restore")
    async def ui_playback_restore(self, **params: Any) -> dict[str, Any]:
        selected = self._read_playback_selection()
        if not selected:
            return {"ok": False, "selected": False}
        service = str(selected.get("service", "yandex"))
        if service == "vk":
            result = await self._guarded(
                self._start_vk_single_sync,
                selected.get("track_id", ""),
                selected.get("title", ""),
                selected.get("artist", ""),
                False,
                selected.get("cover_url", ""),
                selected.get("extra", {}),
            )
        else:
            result = await self.ui_yandex_start(**selected)
        if result.get("error"):
            return {**result, "selected": True}
        # Return the fresh stream in the same response that resolved it. This
        # avoids a second bridge round-trip from the home iframe.
        return {**self._playback_state, "stream_url": self._playback_state.get("url", ""),
                "ok": True, "selected": True}

    @ui_call("music_playback_state")
    async def ui_playback_state(self, **params: Any) -> dict[str, Any]:
        with self._playback_lock:
            state = dict(self._playback_state)
        revision = int(state.get("revision", 0))
        if self._playback_state_probe_logged_revision != revision:
            self._playback_state_probe_logged_revision = revision
            log.info(
                "Home player queried playback state (revision %s, stream=%s)",
                revision,
                bool(state.get("url")),
            )
        state["stream_url"] = state.get("url", "")
        return state

    @ui_call("music_playback_report")
    async def ui_playback_report(self, **params: Any) -> dict[str, Any]:
        try:
            revision = int(params.get("revision", -1))
        except (TypeError, ValueError):
            return {"ok": False}
        if revision != self._playback_revision:
            return {"ok": False}
        status = str(params.get("status", ""))
        if status not in {"attempting", "playing", "paused", "blocked", "failed", "progress"}:
            return {"ok": False}
        error = str(params.get("error", ""))[:80]
        try:
            position = float(params.get("position_seconds", -1))
            duration = float(params.get("duration_seconds", -1))
        except (TypeError, ValueError):
            position = duration = -1
        send_radio_started = False
        with self._playback_lock:
            previous_status = self._playback_state.get("status")
            if status != "progress":
                self._playback_state["status"] = status
                self._playback_state["playback_error"] = error
            if position >= 0:
                self._playback_state["position_seconds"] = position
            if duration >= 0:
                self._playback_state["duration_seconds"] = duration
            # The player reports its current audio.volume with a slight delay.
            # Never let an in-flight report overwrite a newer voice or ducking
            # command. Slider changes are sent through music_playback_control.
            if (
                status == "playing"
                and self._playback_state.get("source") == "radio"
                and self._radio_started_revision != revision
            ):
                self._radio_started_revision = revision
                send_radio_started = True
                radio_station = str(self._playback_state.get("radio_station", ""))
                radio_batch_id = str(self._playback_state.get("radio_batch_id", ""))
                radio_track_id = str(self._playback_state.get("track_id", ""))
            else:
                radio_station = radio_batch_id = radio_track_id = ""
        if send_radio_started:
            try:
                client = self._client("yandex")
                if isinstance(client, YandexMusicClient):
                    await asyncio.to_thread(
                        client.radio_feedback,
                        radio_station,
                        "track_started",
                        radio_track_id,
                        radio_batch_id,
                    )
            except Exception:
                log.exception("Could not report Yandex radio track start")
        if status == "attempting":
            log.info("Yandex playback attempt (revision %s)", revision)
            return {"ok": True}
        if status != "progress" and status != previous_status:
            log.info("Yandex playback %s (revision %s, error=%s)", status, revision, error or "none")
        return {"ok": True}

    @ui_call("music_playback_status")
    async def ui_playback_status(self, **params: Any) -> dict[str, Any]:
        # Do not send the short-lived signed CDN URL to the visible page.
        with self._playback_lock:
            state = dict(self._playback_state)
        return {
            key: state.get(key)
            for key in (
                "revision", "service", "track_id", "title", "artist", "cover_url", "liked", "status", "playback_error", "position_seconds",
                "duration_seconds", "volume", "queue_index", "queue_count",
                "source", "source_title",
            )
        }

    @ui_call("music_playback_control")
    async def ui_playback_control(self, **params: Any) -> dict[str, Any]:
        try:
            value = float(params.get("value", 0) or 0)
        except (TypeError, ValueError):
            value = 0.0
        action = str(params.get("action", "") or "")
        return await self._guarded(self._playback_control_sync, action, value)

    @ui_call("music_playback_stop")
    async def ui_playback_stop(self, **params: Any) -> dict[str, Any]:
        result = self._stop_playback_sync()
        return {**result, "ok": True}

    @ui_call("music_open_track")
    async def ui_open_track(self, **params: Any) -> dict[str, Any]:
        return await self._guarded(self._open_sync, self._ui_track_args(params))

    def _ui_track_args(self, params: dict[str, Any]) -> dict[str, Any]:
        """The iframe sends the whole track object it rendered — keep its metadata.

        Without it a bare `track_id` would mean an extra API round-trip, and for
        VK an id alone is not enough to build a link (owner_id is needed).
        """
        extra = params.get("extra")
        return self._args(
            "",
            service=params.get("service", ""),
            track_id=params.get("track_id", ""),
            title=params.get("title", ""),
            artist=params.get("artist", ""),
            album=params.get("album", ""),
            url=params.get("url", ""),
            context_id=params.get("context_id", ""),
            cover_url=params.get("cover_url", ""),
            extra=extra if isinstance(extra, dict) else "",
        )

    async def on_shutdown(self) -> None:
        self._discord_music.stop(self._discord_music.session)
        if hasattr(self, "_timer_bridge"):
            await asyncio.to_thread(self._timer_bridge.close)
        self._desktop_widget.close()
        self._audio_host.close()
        self._system_audio.close()
        for client in self.clients.values():
            client.close()
        self.clients.clear()


if __name__ == "__main__":
    AstraMusic().run()
