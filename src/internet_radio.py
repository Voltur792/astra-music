"""Public radio catalogue and bounded, same-origin live audio relay."""
import hashlib
from contextlib import nullcontext
import ipaddress
import json
import random
import re
import socket
import threading
import time
from urllib.parse import urljoin, urlsplit

import httpx


def public_url(value, resolve=False):
    value = str(value).strip()
    parsed = urlsplit(value)
    if (len(value) > 4096 or parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError("Нужна прямая HTTP(S)-ссылка на публичный аудиопоток.")
    host = parsed.hostname
    if host.lower() == "localhost" or host.lower().endswith((".local", ".localhost")):
        raise ValueError("Локальные адреса радиостанций не поддерживаются.")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)] if resolve else []
    if any(not address.is_global for address in addresses):
        raise ValueError("Локальные адреса радиостанций не поддерживаются.")
    return value


class RadioCatalogue:
    def __init__(self, directory):
        self.path = directory / "internet-radio.json"
        self.lock = threading.RLock()
        self.http = httpx.Client(timeout=8, trust_env=False, headers={"User-Agent": "AstraMusic/2.4.0 (github.com/Voltur792/astra-music)"})
        self.cache = {}
        self.servers = []

    def close(self):
        self.http.close()

    def mirrors(self):
        if not self.servers:
            found = set()
            try:
                for info in socket.getaddrinfo("all.api.radio-browser.info", 443, type=socket.SOCK_STREAM):
                    name = socket.gethostbyaddr(info[4][0])[0]
                    if re.fullmatch(r"[a-z0-9-]+\.api\.radio-browser\.info", name):
                        found.add(name)
            except OSError:
                pass
            self.servers = list(found) or ["de1.api.radio-browser.info", "fi1.api.radio-browser.info"]
            random.shuffle(self.servers)
        return self.servers[:3]

    @staticmethod
    def station(raw):
        url = public_url(raw.get("url_resolved") or raw.get("url") or "")
        return {"station_id": str(raw.get("stationuuid") or raw.get("station_id") or "custom-" + hashlib.sha256(url.encode()).hexdigest()[:24]),
                "name": str(raw.get("name") or "Радиостанция")[:200], "url": url,
                "country": str(raw.get("countrycode") or raw.get("country") or "")[:2], "tags": str(raw.get("tags") or "")[:300],
                "codec": str(raw.get("codec") or "")[:30], "bitrate": int(raw.get("bitrate") or 0),
                "hls": bool(raw.get("hls")) or ".m3u8" in urlsplit(url).path.lower()}

    def search(self, query="", country="", tag="", offset=0):
        params = {"name": str(query).strip()[:100], "countrycode": str(country).upper()[:2], "tag": str(tag).strip()[:80],
                  "offset": max(0, min(100000, int(offset))), "limit": 50, "hidebroken": "true", "order": "votes", "reverse": "true"}
        key = json.dumps(params, sort_keys=True)
        with self.lock:
            cached = self.cache.get(key)
        if cached and time.monotonic() - cached[0] < 600:
            return {**cached[1], "cached": True}
        for server in self.mirrors():
            try:
                response = self.http.get(f"https://{server}/json/stations/search", params=params)
                response.raise_for_status()
                raw = response.json()
                if not isinstance(raw, list):
                    continue
                items, seen = [], set()
                for entry in raw[:50]:
                    try:
                        item = self.station(entry)
                    except (ValueError, TypeError, KeyError):
                        continue
                    if item["url"] not in seen:
                        seen.add(item["url"]); items.append(item)
                result = {"stations": items, "offset": params["offset"], "has_more": len(raw) == 50, "source": "Radio Browser"}
                with self.lock:
                    if len(self.cache) >= 100:
                        self.cache.pop(next(iter(self.cache)))
                    self.cache[key] = (time.monotonic(), result)
                return result
            except (httpx.HTTPError, ValueError):
                continue
        if cached:
            return {**cached[1], "cached": True, "message": "Каталог недоступен. Показана сохранённая страница."}
        raise ValueError("Каталог радио сейчас недоступен. Избранное и свои ссылки доступны без каталога.")

    def favorites(self):
        with self.lock:
            try:
                items = json.loads(self.path.read_text(encoding="utf-8"))
                return items if isinstance(items, list) else []
            except (OSError, ValueError):
                return []

    def save(self, raw, remove=False):
        item = self.station(raw)
        with self.lock:
            items = [value for value in self.favorites() if value.get("station_id") != item["station_id"]]
            if not remove:
                if len(items) >= 500:
                    raise ValueError("В избранном уже 500 станций.")
                items.append(item)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
        return {"stations": items}


class RadioRelay:
    def __init__(self, plugin):
        self.plugin, self.lock = plugin, threading.RLock()
        self.assets = {}
        self.revision = -1
        self.http = httpx.Client(timeout=15, trust_env=False, limits=httpx.Limits(max_connections=12, max_keepalive_connections=6))

    def close(self):
        self.http.close()

    def start(self, url, revision, hls):
        public_url(url)
        with self.lock:
            self.revision, self.assets = revision, {}
            return self.asset(url, url, hls)

    def asset(self, reference, base, hls=False, revision=None):
        url = public_url(urljoin(base, reference))
        key = hashlib.sha256(url.encode()).hexdigest()
        with self.lock:
            if revision is not None and revision != self.revision:
                raise ValueError("Expired radio stream")
            if len(self.assets) >= 4000 and key not in self.assets:
                self.assets.pop(next(iter(self.assets)))
            self.assets[key] = url
            extension = "m3u8" if hls or urlsplit(url).path.lower().endswith(".m3u8") else "bin"
            return f"/{self.plugin._audio_host.token}/radio-stream/{self.revision}/{key}.{extension}"

    def serve(self, handler, revision, key):
        with self.lock:
            url = self.assets.get(key) if revision == self.revision else None
        if not url or self.plugin._playback_state.get("service") != "internet_radio" or revision != self.plugin._playback_state.get("revision"):
            handler.send_error(404); return
        try:
            with nullcontext(self.http) as client:
                for _ in range(6):
                    public_url(url, resolve=True)
                    headers = {"Accept-Encoding": "identity"}
                    requested_range = handler.headers.get("Range", "")
                    if re.fullmatch(r"bytes=\d+-\d*", requested_range):
                        headers["Range"] = requested_range
                    request = client.build_request("GET", url, headers=headers)
                    response = client.send(request, stream=True)
                    if response.is_redirect:
                        next_url = urljoin(url, response.headers.get("location", ""))
                        response.close(); url = next_url; continue
                    break
                else:
                    raise ValueError("Redirect limit")
                try:
                    response.raise_for_status()
                    mime = response.headers.get("content-type", "application/octet-stream")
                    if "mpegurl" in mime.lower() or handler.path.endswith(".m3u8") or urlsplit(url).path.lower().endswith(".m3u8"):
                        content = bytearray()
                        for chunk in response.iter_bytes():
                            content.extend(chunk)
                            if len(content) > 1024 * 1024:
                                raise ValueError("Manifest too large")
                        text = content.decode("utf-8-sig")
                        if not text.lstrip().startswith("#EXTM3U"):
                            raise ValueError("Not HLS")
                        lines = []
                        next_playlist = False
                        for line in text.splitlines():
                            if line.startswith("#"):
                                line = re.sub(r'URI="([^"]+)"', lambda m: 'URI="' + self.asset(m[1], url, hls=line.startswith(('#EXT-X-MEDIA:', '#EXT-X-I-FRAME-STREAM-INF:')), revision=revision) + '"', line)
                            elif line.strip():
                                line = self.asset(line.strip(), url, hls=next_playlist, revision=revision)
                            next_playlist = line.startswith("#EXT-X-STREAM-INF:")
                            lines.append(line)
                        data = ("\n".join(lines) + "\n").encode()
                        handler.send_response(200)
                        handler.send_header("Content-Type", "application/vnd.apple.mpegurl")
                        handler.send_header("Content-Length", str(len(data)))
                        handler.send_header("Cache-Control", "no-store")
                        handler.end_headers(); handler.wfile.write(data)
                    else:
                        handler.send_response(response.status_code)
                        for header in ("Content-Range", "Accept-Ranges", "Content-Length"):
                            if header in response.headers:
                                handler.send_header(header, response.headers[header])
                        handler.send_header("Content-Type", mime)
                        handler.send_header("Connection", "close")
                        handler.close_connection = True
                        handler.end_headers()
                        for chunk in response.iter_bytes(chunk_size=32768):
                            if revision != self.plugin._playback_state.get("revision"):
                                break
                            handler.wfile.write(chunk)
                finally:
                    response.close()
        except (httpx.HTTPError, ValueError, OSError):
            handler.close_connection = True
