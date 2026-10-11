from types import SimpleNamespace
import pytest
from src.plugin import YandexMusicClient, MusicError
from yandex_music.exceptions import NetworkError, UnauthorizedError

def test_wave_retries_transient_limits_without_changing_queue(monkeypatch):
    client=YandexMusicClient("test-only")
    calls=[]
    delays=[]
    monkeypatch.setattr("src.plugin.time.sleep",delays.append)
    def tracks(station,**kwargs):
        calls.append((station,kwargs))
        if len(calls)<3: raise NetworkError("429 Concurrency limit exceeded")
        raw=SimpleNamespace(id="123",title="Song",artists=[],albums=[],cover_uri="")
        return SimpleNamespace(sequence=[SimpleNamespace(track=raw)],batch_id="batch")
    client._stream_client=SimpleNamespace(rotor_station_tracks=tracks)
    result,batch=client.radio_batch("user:onyourwave","456")
    assert result[0].track_id=="123" and batch=="batch"
    assert delays==[1,2] and len(calls)==3
    assert all(kwargs=={"queue":"456","timeout":5} for _,kwargs in calls)
    assert not client._radio_request_lock.locked()
    client.close()

def test_wave_limits_are_bounded_and_authentication_is_not_retried(monkeypatch):
    monkeypatch.setattr("src.plugin.time.sleep",lambda _:None)
    client=YandexMusicClient("test-only")
    calls=[]
    def limited(*args,**kwargs): calls.append(1);raise NetworkError("429 Concurrency limit exceeded")
    client._stream_client=SimpleNamespace(rotor_station_tracks=limited)
    with pytest.raises(MusicError,match="временно ограничил"):client.radio_batch("user:onyourwave")
    assert len(calls)==3
    def unauthorized(*args,**kwargs):calls.append(1);raise UnauthorizedError("test")
    client._stream_client.rotor_station_tracks=unauthorized
    with pytest.raises(MusicError):client.radio_batch("user:onyourwave")
    assert len(calls)==4
    client._radio_request_lock.acquire()
    with pytest.raises(MusicError,match="уже загружается"):client.radio_batch("user:onyourwave")
    client._radio_request_lock.release()
    client.close()
