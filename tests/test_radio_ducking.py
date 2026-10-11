import asyncio
import time
from types import SimpleNamespace

import pytest

from src.plugin import AstraMusic


def chunk(kind, text=""):
    return SimpleNamespace(text=text, error="provider failed", WhichOneof=lambda _: kind)


def player(tmp_path, monkeypatch, service="vk", volume=.7):
    monkeypatch.setenv("ASTRA_MUSIC_DATA_DIR", str(tmp_path))
    plugin = AstraMusic()
    plugin._playback_volume = volume
    plugin._playback_state.update(service=service, track_id="song", title="Song", artist="Artist",
                                  status="playing", url="mock-audio", volume=volume)
    plugin._write_settings({"playback_volume": volume})
    return plugin


@pytest.mark.parametrize("service", ["vk", "yandex", "spotify"])
def test_radio_ducks_before_request_and_waits_for_voice_after_chat_done(tmp_path, monkeypatch, service):
    async def run():
        plugin = player(tmp_path, monkeypatch, service)
        radio = plugin._presenter
        radio.VOICE_QUIET_GRACE = .01
        requests = []
        monkeypatch.setattr(plugin._spotify, "request", lambda action, value=0: requests.append((action, value)))
        started, reply_done = asyncio.Event(), asyncio.Event()

        class Host:
            async def send_chat_message(self, prompt, **kwargs):
                assert plugin._playback_volume == .1
                assert plugin._playback_state["command"] == "volume"
                started.set()
                yield chunk("text", "Короткая подводка.")
                await reply_done.wait()
                yield chunk("done")

        plugin.host = Host()
        radio.speech = asyncio.create_task(radio._announce(dict(plugin._playback_state)))
        await started.wait()
        await plugin.on_event("tts_started", {})
        # Returning the main conversation to ready must not lift the radio hold.
        await plugin.on_state_changed({"new_state": "READY"})
        reply_done.set()
        await asyncio.sleep(.03)
        assert not radio.speech.done() and plugin._playback_volume == .1
        assert plugin._read_settings()["playback_volume"] == .7
        await plugin.on_event("tts_completed", {})
        await asyncio.wait_for(radio.speech, 1)
        assert plugin._playback_volume == .7 and plugin._duck_restore_volume is None
        assert plugin._playback_state["volume"] == .7 and not plugin._duck_reasons
        if service == "spotify":
            assert requests == [("volume", .1), ("volume", .7)]
        else:
            assert requests == []
    asyncio.run(run())


def test_manual_volume_change_during_radio_becomes_restored_target(tmp_path, monkeypatch):
    async def run():
        plugin = player(tmp_path, monkeypatch)
        radio = plugin._presenter
        radio.VOICE_QUIET_GRACE = .01
        started, reply_done = asyncio.Event(), asyncio.Event()
        class Host:
            async def send_chat_message(self, prompt, **kwargs):
                started.set()
                yield chunk("text", "Подводка")
                await reply_done.wait()
                yield chunk("done")
        plugin.host = Host()
        radio.speech = asyncio.create_task(radio._announce(dict(plugin._playback_state)))
        await started.wait()
        plugin._playback_control_sync("volume", 4)
        assert plugin._playback_volume == .1
        await plugin.on_event("tts_started", {})
        reply_done.set()
        await plugin.on_event("tts_interrupted", {})
        await asyncio.wait_for(radio.speech, 1)
        assert plugin._playback_volume == .4
        assert plugin._read_settings()["playback_volume"] == .4
    asyncio.run(run())


@pytest.mark.parametrize("ending", ["error", "cancel", "timeout"])
def test_failed_or_cancelled_radio_restores_volume_without_tts(tmp_path, monkeypatch, ending):
    async def run():
        plugin = player(tmp_path, monkeypatch)
        started = asyncio.Event()
        class Host:
            async def send_chat_message(self, prompt, **kwargs):
                started.set()
                if ending == "error":
                    yield chunk("error")
                elif ending == "timeout":
                    raise asyncio.TimeoutError()
                else:
                    await asyncio.Event().wait()
                    yield chunk("done")
        plugin.host = Host()
        radio = plugin._presenter
        radio.speech = asyncio.create_task(radio._announce(dict(plugin._playback_state)))
        await started.wait()
        if ending == "cancel":
            radio.cancel()
        await asyncio.gather(radio.speech, return_exceptions=True)
        assert plugin._playback_volume == .7 and not plugin._duck_reasons
        assert plugin._read_settings()["playback_volume"] == .7
    asyncio.run(run())


def test_cancelled_old_announcement_cannot_release_new_announcement(tmp_path, monkeypatch):
    async def run():
        plugin = player(tmp_path, monkeypatch)
        starts = asyncio.Queue()
        class Host:
            async def send_chat_message(self, prompt, **kwargs):
                starts.put_nowait(True)
                await asyncio.Event().wait()
                yield chunk("done")
        plugin.host = Host()
        radio = plugin._presenter
        old = asyncio.create_task(radio._announce(dict(plugin._playback_state)))
        await starts.get()
        new = asyncio.create_task(radio._announce(dict(plugin._playback_state)))
        await starts.get()
        old.cancel()
        await asyncio.gather(old, return_exceptions=True)
        assert plugin._playback_volume == .1 and len(plugin._duck_reasons) == 1
        new.cancel()
        await asyncio.gather(new, return_exceptions=True)
        assert plugin._playback_volume == .7 and not plugin._duck_reasons
    asyncio.run(run())


def test_tts_hold_survives_core_ready_and_missing_completion_expires(tmp_path, monkeypatch):
    async def run():
        plugin = player(tmp_path, monkeypatch)
        await plugin.on_event("tts_started", {})
        await plugin.on_state_changed({"new_state": "READY"})
        assert plugin._playback_volume == .1
        plugin._presenter.tts_changed_at = time.monotonic() - 100
        await plugin._presenter.tick()
        assert plugin._playback_volume == .7
    asyncio.run(run())


def test_quiet_or_muted_music_is_not_made_louder(tmp_path, monkeypatch):
    async def run():
        plugin = player(tmp_path, monkeypatch, volume=.03)
        await plugin.on_event("tts_started", {})
        assert plugin._playback_volume == .03
        await plugin.on_event("tts_completed", {})
        assert plugin._playback_volume == .03
    asyncio.run(run())


def test_late_tts_completion_ends_fallback_wait_promptly(tmp_path, monkeypatch):
    async def run():
        plugin = player(tmp_path, monkeypatch)
        radio = plugin._presenter
        radio.VOICE_START_GRACE = .01
        radio.VOICE_QUIET_GRACE = .01
        # Missing initial edge starts the conservative fallback; a late edge
        # must still restore on actual completion, rather than wait its 40 s.
        waiter = asyncio.create_task(radio._wait_for_voice("Подводка " * 100, 0))
        await asyncio.sleep(.15)
        assert not waiter.done()
        await plugin.on_event("tts_started", {})
        await plugin.on_event("tts_completed", {})
        await asyncio.wait_for(waiter, .5)
    asyncio.run(run())
