import asyncio

from src.plugin import AstraMusic, YandexMusicClient, VkMusicClient


def test_provider_search_metadata_retains_track_duration():
    yandex = YandexMusicClient._track({'id': 42, 'title': 'Song', 'durationMs': 185000})
    vk = VkMusicClient._track({'id': 42, 'owner_id': 1, 'duration': 185})
    assert yandex.as_dict()['duration_seconds'] == 185
    assert vk.as_dict()['duration_seconds'] == 185


def test_stream_status_excludes_signed_urls_and_has_end_revision(tmp_path, monkeypatch):
    monkeypatch.setenv('ASTRA_MUSIC_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ASTRA_MUSIC_NATIVE_PLAYER', '0')
    plugin = AstraMusic()
    plugin._playback_state.update(revision=7, title='Song', status='playing', url='SECRET-CDN')
    plugin._stream_finished_revision = 6
    state = asyncio.run(plugin._stream_music_status())
    assert state['finished_revision'] == 6
    assert state['revision'] == 7
    assert 'SECRET-CDN' not in str(state)


def test_stream_control_allowlist_does_not_allow_internal_ended(tmp_path, monkeypatch):
    monkeypatch.setenv('ASTRA_MUSIC_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ASTRA_MUSIC_NATIVE_PLAYER', '0')
    plugin = AstraMusic()
    calls = []
    async def control(**params):
        calls.append(params)
        return {'success': True}
    monkeypatch.setattr(plugin, 'ui_playback_control', control)
    assert asyncio.run(plugin._stream_music_control('ended')).get('error')
    assert not calls
    assert asyncio.run(plugin._stream_music_control('next'))['success']
    assert calls == [{'action': 'next', 'value': 0}]


def test_end_revision_published_only_when_internal_end_succeeds(tmp_path, monkeypatch):
    monkeypatch.setenv('ASTRA_MUSIC_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ASTRA_MUSIC_NATIVE_PLAYER', '0')
    plugin = AstraMusic()
    plugin._playback_state.update(revision=7)
    async def guarded(fn, action, value):
        return {'success': True}
    monkeypatch.setattr(plugin, '_guarded', guarded)
    asyncio.run(plugin.ui_playback_control(action='ended', value=10))
    assert plugin._stream_finished_revision == 7
    plugin._playback_state.update(revision=8)
    async def error(fn, action, value):
        return {'error': 'failed'}
    monkeypatch.setattr(plugin, '_guarded', error)
    asyncio.run(plugin.ui_playback_control(action='ended'))
    assert plugin._stream_finished_revision == 7


def test_stream_level_is_user_target_while_voice_ducks_and_pause_preserves_it(tmp_path, monkeypatch):
    monkeypatch.setenv('ASTRA_MUSIC_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ASTRA_MUSIC_NATIVE_PLAYER', '0')
    plugin = AstraMusic()
    plugin._playback_state.update(revision=7, track_id='song', url='fake', status='playing')
    plugin._playback_revision = 7
    plugin._playback_volume = .4
    plugin._playback_state['volume'] = .4
    plugin._set_music_ducked(True, 'core')
    state = asyncio.run(plugin._stream_music_status())
    assert state['requested_volume'] == .4 and state['volume'] == .1 and state['ducked']
    asyncio.run(plugin._stream_music_control('pause'))
    assert asyncio.run(plugin._stream_music_status())['requested_volume'] == .4
    plugin._set_music_ducked(False, 'core')
    assert asyncio.run(plugin._stream_music_status())['volume'] == .4


def test_stream_position_has_real_report_timestamp_and_sample_age(tmp_path, monkeypatch):
    monkeypatch.setenv('ASTRA_MUSIC_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ASTRA_MUSIC_NATIVE_PLAYER', '0')
    plugin = AstraMusic()
    plugin._playback_revision = 7
    plugin._playback_state.update(revision=7)
    asyncio.run(plugin.ui_playback_report(revision=7, status='progress', position_seconds=12, duration_seconds=100))
    state = asyncio.run(plugin._stream_music_status())
    assert state['position_seconds'] == 12
    assert state['position_updated_at'] > 0 and 0 <= state['position_sample_age'] < 2
