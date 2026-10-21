"""Canonical playlist references; user links never become arbitrary HTTP targets."""
import re
from urllib.parse import unquote, urlsplit

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
OWNER = re.compile(r"owner:([A-Za-z0-9_.@-]{1,100}):([0-9]{1,20})")


def playlist_reference(value: str) -> tuple[str, str, str]:
    """Return (kind, identifier, owner); kind is own, owner, or uuid."""
    value = str(value).strip()
    if UUID.fullmatch(value):
        return "uuid", value.lower(), ""
    owner = OWNER.fullmatch(value)
    if owner and owner[1] not in {".", ".."}:
        return "owner", owner[2], owner[1]
    if re.fullmatch(r"[0-9]{1,20}", value):
        return "own", value, ""
    try:
        url = urlsplit(value)
        if (url.scheme != "https" or url.hostname not in {"music.yandex.ru", "music.yandex.com"}
                or url.username or url.password or url.port not in (None, 443)):
            raise ValueError
        path = unquote(url.path)
        modern = re.fullmatch(r"/playlists/(.+?)/?", path)
        if modern and UUID.fullmatch(modern[1]):
            return "uuid", modern[1].lower(), ""
        legacy = re.fullmatch(r"/users/([A-Za-z0-9_.@-]{1,100})/playlists/([0-9]{1,20})/?", path)
        if legacy and legacy[1] not in {".", ".."}:
            return "owner", legacy[2], legacy[1]
    except ValueError:
        pass
    raise ValueError("Неверная ссылка или идентификатор плейлиста Яндекс Музыки.")
