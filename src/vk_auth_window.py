"""Small VK sign-in window used only to establish a local audio session.

The plugin's own Astra iframe is sandboxed and cannot open a popup. This
helper runs in its own process so pywebview can provide a small native browser
window without launching a separate music application. It passes the VK account
id and cookies only to its parent process over stdout; they are never written
to a log or returned to the plugin UI.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from http.cookies import Morsel, SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

RESULT_PREFIX = "ASTRA_VK_AUTH_RESULT:"
VK_COOKIE_PROBE_URLS = (
    "https://vk.com/",
    "https://id.vk.com/",
    "https://vk.ru/",
    "https://id.vk.ru/",
)
# Use VK's own web sign-in (including QR flow) instead of the legacy OAuth
# authorize page, which currently fails with "Unknown method passed [3]".
AUTH_URL = "https://vk.com/"


def _is_vk_domain(domain: str) -> bool:
    normalized = domain.strip().lower().lstrip(".")
    return (
        normalized == "vk.com"
        or normalized.endswith(".vk.com")
        or normalized == "vk.ru"
        or normalized.endswith(".vk.ru")
    )


def _fallback_vk_domain(host: str) -> str:
    normalized = host.lower()
    if normalized == "vk.com" or normalized.endswith(".vk.com"):
        return ".vk.com"
    if normalized == "vk.ru" or normalized.endswith(".vk.ru"):
        return ".vk.ru"
    return ""


def emit(result: dict[str, object]) -> None:
    sys.stdout.write(RESULT_PREFIX + json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _cookie_rows(cookie_sets: object, current_url: str) -> list[dict[str, object]]:
    """Convert pywebview SimpleCookie values to the importer's JSON format."""
    host = (urlsplit(current_url).hostname or "").lower()
    fallback_domain = _fallback_vk_domain(host)
    rows: list[dict[str, object]] = []

    if isinstance(cookie_sets, SimpleCookie):
        cookie_sets = [cookie_sets]
    elif isinstance(cookie_sets, dict) and {"name", "value"}.issubset(cookie_sets):
        cookie_sets = [cookie_sets]
    else:
        try:
            cookie_sets = list(cookie_sets)  # type: ignore[arg-type]
        except TypeError:
            return rows
    for cookie_set in cookie_sets:
        if isinstance(cookie_set, dict) and "name" in cookie_set and "value" in cookie_set:
            name = str(cookie_set.get("name") or "")
            domain = str(cookie_set.get("domain") or fallback_domain).strip().lower()
            if name and _is_vk_domain(domain):
                row: dict[str, object] = {
                    "name": name,
                    "value": str(cookie_set.get("value") or ""),
                    "domain": domain or fallback_domain,
                    "path": str(cookie_set.get("path") or "/"),
                    "secure": str(cookie_set.get("secure", "")).lower() in {"1", "true", "yes"},
                    "httpOnly": str(cookie_set.get("httpOnly", cookie_set.get("httponly", ""))).lower()
                    in {"1", "true", "yes"},
                    "hostOnly": not domain.startswith("."),
                    "session": not bool(cookie_set.get("expirationDate") or cookie_set.get("expires")),
                }
                expiration = cookie_set.get("expirationDate")
                if isinstance(expiration, (int, float)):
                    row["expirationDate"] = float(expiration)
                    row["session"] = False
                same_site = str(cookie_set.get("sameSite", cookie_set.get("samesite", ""))).lower()
                if same_site in {"strict", "lax", "none"}:
                    row["sameSite"] = same_site
                rows.append(row)
            continue

        morsels: object = cookie_set.values() if isinstance(cookie_set, SimpleCookie) else cookie_set
        try:
            morsels = list(morsels)  # type: ignore[arg-type]
        except TypeError:
            continue
        for morsel in morsels:
            if not isinstance(morsel, Morsel):
                continue
            domain = str(morsel["domain"] or fallback_domain).strip().lower()
            if not _is_vk_domain(domain):
                continue
            row: dict[str, object] = {
                "name": morsel.key,
                "value": morsel.value,
                "domain": domain or fallback_domain,
                "path": str(morsel["path"] or "/"),
                "secure": bool(morsel["secure"]),
                "httpOnly": bool(morsel["httponly"]),
                "hostOnly": not domain.startswith("."),
                "session": not bool(morsel["expires"]),
            }
            expires = str(morsel["expires"] or "").strip()
            if expires:
                try:
                    dt = parsedate_to_datetime(expires)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    row["expirationDate"] = dt.timestamp()
                    row["session"] = False
                except (TypeError, ValueError, OverflowError):
                    pass
            same_site = str(morsel["samesite"] or "").lower()
            if same_site in {"strict", "lax", "none"}:
                row["sameSite"] = same_site
            rows.append(row)
    return rows


def _cookie_metadata(
    cookie_sets: object,
    current_url: str = "",
    probe_info: dict[str, object] | None = None,
) -> str:
    """Describe returned VK cookie names/domains without exposing cookie values."""
    host = (urlsplit(current_url).hostname or "").lower()
    fallback_domain = _fallback_vk_domain(host)
    if isinstance(cookie_sets, SimpleCookie):
        cookie_sets = [cookie_sets]
    elif isinstance(cookie_sets, dict) and {"name", "value"}.issubset(cookie_sets):
        cookie_sets = [cookie_sets]
    else:
        try:
            cookie_sets = list(cookie_sets)  # type: ignore[arg-type]
        except TypeError:
            return "WebView2 не вернул список cookies."

    items: set[str] = set()
    count = 0
    for cookie_set in cookie_sets:
        if isinstance(cookie_set, dict) and "name" in cookie_set:
            name = str(cookie_set.get("name") or "").strip()
            domain = str(cookie_set.get("domain") or fallback_domain).strip().lower()
            if name and _is_vk_domain(domain):
                count += 1
                items.add(f"{domain or '(домен не указан)'}:{name}")
            continue

        morsels: object = cookie_set.values() if isinstance(cookie_set, SimpleCookie) else cookie_set
        try:
            morsels = list(morsels)  # type: ignore[arg-type]
        except TypeError:
            continue
        for morsel in morsels:
            if not isinstance(morsel, Morsel):
                continue
            domain = str(morsel["domain"] or fallback_domain).strip().lower()
            if not _is_vk_domain(domain):
                continue
            count += 1
            items.add(f"{domain or '(домен не указан)'}:{morsel.key}")

    detail = ", ".join(sorted(items)[:12]) or "не найдено"
    suffix = " …" if len(items) > 12 else ""
    message = f"WebView2 вернул {count} cookies VK: {detail}{suffix}."
    if probe_info:
        checked = probe_info.get("checked")
        counts = probe_info.get("counts")
        backend = str(probe_info.get("backend") or "не определён")
        if isinstance(checked, list) and isinstance(counts, dict):
            count_text = ", ".join(f"{host}={amount}" for host, amount in counts.items())
            checked_text = ", ".join(str(host) for host in checked) or "нет"
            message += f" Проверено через {backend}: {checked_text}; cookies по адресу: {count_text or 'нет данных'}."
    return message


def _get_window_cookies(
    window: object,
    current_url: str,
    probe_vk_origins: bool = False,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Read VK cookies through pywebview's WebView2 cookie manager.

    On Windows pywebview stores WebView2.Source (a .NET Uri object) in
    EdgeChrome.url, then passes that object to GetCookiesAsync, whose argument
    is a URL string. Supply a string URL and, after VK reports a signed-in
    account, also query the VK login origins in case the session is host-only.
    """
    edge_view = None
    previous_url: object = None
    try:
        gui = getattr(window, "gui")
        browser_class = getattr(gui, "BrowserView", None)
        instances = getattr(browser_class, "instances", {})
        browser_form = instances.get(getattr(window, "uid", ""))
        edge_view = getattr(browser_form, "browser", None)
        if edge_view is not None and hasattr(edge_view, "url"):
            previous_url = edge_view.url
            if current_url and urlsplit(current_url).scheme in {"http", "https"}:
                edge_view.url = current_url
        else:
            edge_view = None
    except Exception:
        edge_view = None

    targets: list[str] = []
    if current_url and urlsplit(current_url).scheme in {"http", "https"}:
        targets.append(current_url)
        if probe_vk_origins:
            parsed = urlsplit(current_url)
            origin = f"{parsed.scheme}://{parsed.netloc}/"
            targets.append(origin)
            targets.extend(VK_COOKIE_PROBE_URLS)
    if probe_vk_origins:
        targets.extend(VK_COOKIE_PROBE_URLS)
    targets = list(dict.fromkeys(targets)) or [current_url]

    rows_by_key: dict[tuple[str, str, str], dict[str, object]] = {}
    counts: dict[str, int] = {}
    failures: list[str] = []
    successful_reads = 0
    backend = type(edge_view).__name__ if edge_view is not None else "pywebview"
    try:
        for target in targets:
            if edge_view is not None and target and urlsplit(target).scheme in {"http", "https"}:
                edge_view.url = target
            try:
                cookie_sets = window.get_cookies()  # type: ignore[attr-defined]
            except Exception as exc:
                failures.append(f"{urlsplit(target).hostname or 'текущая страница'}:{type(exc).__name__}")
                continue
            successful_reads += 1
            rows = _cookie_rows(cookie_sets, target or current_url)
            host = (urlsplit(target).hostname or "текущая страница").lower()
            counts[host] = len(rows)
            for row in rows:
                key = (
                    str(row.get("name") or "").lower(),
                    str(row.get("domain") or "").lower(),
                    str(row.get("path") or "/"),
                )
                rows_by_key.setdefault(key, row)

        if successful_reads == 0:
            raise RuntimeError("WebView2 не вернул результат чтения cookies.")
        checked = list(counts)
        if failures:
            checked.extend(failures)
        return list(rows_by_key.values()), {
            "backend": backend,
            "checked": checked,
            "counts": counts,
        }
    finally:
        if edge_view is not None:
            try:
                edge_view.url = previous_url
            except Exception:
                pass


def _user_id_from_window(window: object) -> str:
    """Read the signed-in profile id from VK's own page globals."""
    script = r"""(() => {
      const values = [
        window.vk && window.vk.id,
        window.vk && window.vk.user_id,
        window.vk && window.vk.userId,
        window.cur && window.cur.user_id,
        window.cur && window.cur.oid,
        window.user_id,
        window.userId,
        window.viewer_id,
        window.viewerId,
        window.currentUser && window.currentUser.id,
        window.user && window.user.id
      ];
      for (const value of values) {
        const id = String(value == null ? '' : value);
        if (/^[1-9]\d{0,14}$/.test(id)) return id;
      }
      const profileLink = [...document.querySelectorAll('a[href]')].find((anchor) => {
        const label = [anchor.getAttribute('aria-label'), anchor.getAttribute('title'), anchor.textContent]
          .filter(Boolean).join(' ').trim();
        return /профиль|profile/i.test(label);
      });
      if (profileLink) {
        const match = String(profileLink.getAttribute('href') || '').match(/(?:^|\/)id([1-9]\d{0,14})(?:$|[/?#])/i);
        if (match) return match[1];
      }
      const html = document.documentElement ? document.documentElement.innerHTML : '';
      const patterns = [
        /(?:window\.)?vk\.id\s*=\s*[\"']?(\d{1,15})/i,
        /[\"']?(?:user_id|viewer_id|userId|viewerId)[\"']?\s*[:=]\s*[\"']?([1-9]\d{0,14})/i
      ];
      for (const pattern of patterns) {
        const match = html.match(pattern);
        if (match && Number(match[1]) > 0) return match[1];
      }
      return '';
    })()"""
    try:
        value = window.evaluate_js(script)  # type: ignore[attr-defined]
    except Exception:
        return ""
    result = str(value or "").strip()
    return result if result.isdigit() and int(result) > 0 else ""


def _account_visible_from_window(window: object) -> bool:
    """Detect VK's signed-in shell even when its SPA no longer exposes user_id."""
    script = r"""(() => {
      const text = (document.body && document.body.innerText || '').toLocaleLowerCase();
      return text.includes('моя музыка') && (text.includes('профиль') || text.includes('лента'));
    })()"""
    try:
        return bool(window.evaluate_js(script))  # type: ignore[attr-defined]
    except Exception:
        return False


def _has_vk_session_cookie(rows: list[dict[str, object]]) -> bool:
    """VK may keep the logged-in session on either its vk.com or vk.ru domain."""
    for row in rows:
        name = str(row.get("name") or "").lower()
        domain = str(row.get("domain") or "").strip().lower().lstrip(".")
        if (
            name.startswith("remixsid")
            and bool(row.get("value"))
            and (
                domain == "vk.com"
                or domain.endswith(".vk.com")
                or domain == "vk.ru"
                or domain.endswith(".vk.ru")
            )
        ):
            return True
    return False


def main() -> int:
    try:
        import webview
    except Exception as exc:
        emit(
            {
                "success": False,
                "error": "Не удалось загрузить pywebview в runtime Astra "
                f"({type(exc).__name__}: {exc}).",
            }
        )
        return 1

    completed = threading.Event()
    result_box: dict[str, object] = {}
    window = webview.create_window(
        "Вход в VK — Astra Music",
        AUTH_URL,
        width=720,
        height=760,
        min_size=(520, 560),
        text_select=True,
    )

    def finish(payload: dict[str, object]) -> None:
        if completed.is_set():
            return
        result_box.update(payload)
        completed.set()
        try:
            window.destroy()
        except Exception:
            pass

    def watch_login() -> None:
        deadline = time.monotonic() + 300
        session_seen_at = 0.0
        account_seen_without_cookie_at = 0.0
        cookie_read_error_since = 0.0
        cookie_read_error_type = ""
        last_origin_probe_at = 0.0
        cookie_probe_info: dict[str, object] = {}
        origin_probe_info: dict[str, object] = {}
        while not completed.is_set() and time.monotonic() < deadline:
            try:
                current_url = str(window.get_current_url() or "")
                try:
                    rows, cookie_probe_info = _get_window_cookies(window, current_url)
                except Exception as exc:
                    now = time.monotonic()
                    cookie_read_error_since = cookie_read_error_since or now
                    cookie_read_error_type = type(exc).__name__
                    if now - cookie_read_error_since >= 20:
                        finish(
                            {
                                "success": False,
                                "error": "Не удалось прочитать сессию из встроенного окна VK "
                                f"({cookie_read_error_type}). Закройте окно входа и перезапустите Astra.",
                            }
                        )
                        return
                    time.sleep(1.0)
                    continue

                cookie_read_error_since = 0.0
                cookie_read_error_type = ""
                has_session_cookie = _has_vk_session_cookie(rows)
                user_id = _user_id_from_window(window)
                account_visible = _account_visible_from_window(window)

                if (
                    not has_session_cookie
                    and (user_id or account_visible)
                    and time.monotonic() - last_origin_probe_at >= 5
                ):
                    rows, origin_probe_info = _get_window_cookies(
                        window,
                        current_url,
                        probe_vk_origins=True,
                    )
                    last_origin_probe_at = time.monotonic()
                    has_session_cookie = _has_vk_session_cookie(rows)

                if has_session_cookie:
                    session_seen_at = session_seen_at or time.monotonic()
                    account_seen_without_cookie_at = 0.0
                    if user_id:
                        finish({"success": True, "user_id": user_id, "cookies": rows})
                        return
                    if time.monotonic() - session_seen_at >= 20:
                        finish(
                            {
                                "success": False,
                                "error": "VK сохранил сессию, но ID аккаунта не появился на странице. "
                                "Обновите страницу VK в окне входа и попробуйте снова.",
                            }
                        )
                        return
                elif user_id or account_visible:
                    account_seen_without_cookie_at = account_seen_without_cookie_at or time.monotonic()
                    if time.monotonic() - account_seen_without_cookie_at >= 30:
                        finish(
                            {
                                "success": False,
                                "error": "VK показывает вошедший аккаунт, но встроенное окно не передало "
                                "сессионную cookie. "
                                + _cookie_metadata(rows, current_url, origin_probe_info or cookie_probe_info)
                                + " Обновите страницу VK в окне авторизации и попробуйте снова.",
                            }
                        )
                        return
                else:
                    account_seen_without_cookie_at = 0.0
            except Exception:
                # Navigation may temporarily invalidate the native webview.
                # Persistent cookie API failures are reported above.
                pass
            time.sleep(1.0)
        if not completed.is_set():
            finish(
                {
                    "success": False,
                    "error": "Вход VK не завершён за 5 минут: Astra не обнаружила сессионную cookie "
                    "в окне авторизации. Обновите страницу VK и попробуйте снова.",
                }
            )

    def on_closed(*_args: object) -> None:
        if not completed.is_set():
            result_box.update({"success": False, "error": "Окно входа VK закрыто."})
            completed.set()

    window.events.closed += on_closed
    threading.Thread(target=watch_login, name="vk-auth-watch", daemon=True).start()

    try:
        # Start every explicit sign-in in a clean InPrivate profile so an
        # expired WebView cookie cannot make the watcher accept the old account
        # and close the window before the user can sign in again. The verified
        # cookies are exported while the window is open and stored by the plugin.
        webview.start(
            debug=False,
            private_mode=True,
        )
    except Exception as exc:
        if not completed.is_set():
            result_box.update(
                {
                    "success": False,
                    "error": "Не удалось запустить встроенное окно VK "
                    f"({type(exc).__name__}: {exc}). Проверьте WebView2 Runtime.",
                }
            )
            completed.set()

    if not result_box:
        result_box.update({"success": False, "error": "Окно авторизации VK завершилось без результата."})
    emit(result_box)
    return 0 if result_box.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
