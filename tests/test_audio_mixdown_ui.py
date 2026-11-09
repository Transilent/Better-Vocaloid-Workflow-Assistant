"""No-device acceptance for the single resident realtime mixdown player.

This file used to exercise the retired per-segment renderer, crossfade player,
fallback player and resident audition service.  Those scenarios are obsolete.
The remaining coverage focuses on the current single RealtimePreviewPlayer,
multi-track request construction, EQ projection, preset application/undo and
safe export publication.
"""
from contextlib import contextmanager
import copy
import hashlib
import os
import shutil
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'src'))
sys.path.insert(0, str(APP / 'tests'))

import numpy as np
import soundfile as sf
from PyQt5.QtWidgets import QApplication, QDialog, QWidget

import audio_mixdown_ui as ui
import mixdown_preset_ui as preset_ui
import mixdown_realtime_player as realtime
import mixdown_export
from mixdown_preset_documents import make_document
from mixdown_realtime_player import RealtimePreviewPlayer
from preset_library import PresetLibrary
from test_mixdown_realtime_player import Service


app = QApplication.instance() or QApplication([])


def settle(rounds=4):
    for _ in range(rounds):
        app.processEvents()


def wait_for(predicate, player=None, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if player is not None:
            player.poll()
        app.processEvents()
        if predicate():
            return True
        time.sleep(.002)
    if player is not None:
        player.poll()
    app.processEvents()
    return bool(predicate())


def remove_tree(path):
    path = Path(path)
    def retry(function, value, _info):
        try:
            os.chmod(value, 0o700)
            function(value)
        except OSError:
            pass
    for _ in range(10):
        try:
            shutil.rmtree(path, onerror=retry)
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(.05)
    shutil.rmtree(path, ignore_errors=True)


def write_wav(path, frequency, rate=8000, seconds=.1):
    frames = np.arange(max(1, int(rate * seconds)), dtype=np.float32) / rate
    sf.write(str(path), .08 * np.sin(2 * np.pi * frequency * frames),
             rate, subtype='PCM_24')
    return Path(path)


def source_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fake_render(request, check, progress=None):
    fake_render.requests.append(request)
    if check and check():
        raise ui._Cancelled('test cancellation')
    vocals = request['vocals']
    assert 1 <= len(vocals) <= 3
    required = {'path', 'gain_db', 'offset_seconds', 'mute', 'solo', 'pan',
                'reverb_send'}
    assert all(required <= set(row) for row in vocals)
    output = Path(request['output_path'])
    output.parent.mkdir(parents=True, exist_ok=True)
    write_wav(output, 330, rate=8000, seconds=.1)
    sources = [row['path'] for row in vocals] + [request['instrumental_path']]
    if progress:
        progress({'event': 'progress', 'phase': 'mix', 'fraction': .5})
    return {
        'output_path': str(output.resolve()),
        'duration_seconds': .1,
        'peak_dbfs': -12.0,
        'meters': {
            'tracks': [
                {'role': 'vocal', 'index': index, 'peak_dbfs': -18.0 - index,
                 'mute': row['mute'], 'solo': row['solo'], 'pan': row['pan'],
                 'audible': not row['mute'] and not row['solo']}
                for index, row in enumerate(vocals)
            ] + [{'role': 'backing', 'index': 0, 'peak_dbfs': -14.0,
                  'mute': request['instrumental_mute'],
                  'solo': request['instrumental_solo'],
                  'pan': request['instrumental_pan'], 'audible': True}],
            'bus_peak_dbfs': -10.0,
        },
        'source_sha256': {str(Path(path).resolve()): source_hash(path)
                          for path in sources},
        'output_sha256': source_hash(output),
    }


fake_render.requests = []


def fixture_player_factory(dll_path, *, cache_dir, parent=None,
                           service_factory=None, service_options=None):
    del dll_path, service_factory, service_options
    return RealtimePreviewPlayer(
        'fixture.dll', cache_dir=cache_dir, parent=parent,
        service_factory=Service,
    )


@contextmanager
def fixture_dialog(*, preset_library=None):
    owner = QWidget()
    created = {}

    def factory(*args, **kwargs):
        player = fixture_player_factory(*args, **kwargs)
        created['player'] = player
        return player

    with patch.object(ui.AudioMixdownDialog, '_prepare_component_support'), \
            patch.object(ui.AudioMixdownDialog, '_start_component_support_check',
                         return_value=False), \
            patch.object(realtime, 'native_output_path',
                         return_value=str(APP / 'fixture-native-output.dll')), \
            patch.object(realtime, 'RealtimePreviewPlayer', side_effect=factory):
        dialog = ui.AudioMixdownDialog(
            owner, None, owner, embedded=True,
            preset_library=preset_library,
        )
    player = created['player']
    dialog._support_ready = True
    dialog._support_error = ''
    dialog.support_panel.hide()
    dialog._preview_debounce.stop()
    try:
        yield owner, dialog, player, player._service
    finally:
        dialog._preview_debounce.stop()
        dialog._audition_poll_timer.stop()
        dialog._audition_closing = True
        dialog._audition_cleanup_complete = True
        cache = dialog._preview_cache_directory
        dialog._preview_cache_directory = None
        try:
            player.close()
        finally:
            ui._discard_preview_directory(cache)
            try:
                dialog.deleteLater()
                owner.deleteLater()
            except RuntimeError:
                pass
            settle(2)


def configure_sources(dialog, root, vocal_count=1):
    vocals = [write_wav(root / ('vocal-%d.wav' % (index + 1)),
                        180 + 60 * index) for index in range(vocal_count)]
    while len(dialog.active_vocal_tracks()) < vocal_count:
        dialog.add_vocal_track()
    for track, path in zip(dialog.active_vocal_tracks(), vocals):
        track['path'].setText(str(path))
    backing = write_wav(root / 'backing.wav', 110)
    dialog.fields['instrumental'].setText(str(backing))
    dialog._preview_debounce.stop()
    return vocals, backing


def prepare_session(dialog, player, service):
    assert dialog._start_native_preview()
    assert wait_for(
        lambda: any(command[0] == 'open' for command in service.commands),
        player=player,
    )
    generation = player._generation
    service.events.put({
        'type': 'live_ready', 'request_id': generation,
        'sample_rate': 48000, 'channels': 2,
        'duration_frames': 480000,
    })
    player.poll()
    settle()
    assert player.ready and player.duration() == 10000
    return generation


def send_live_state(player, service, generation, frame, *, state='playing', **extra):
    snapshot = {
        'state': state,
        'sample_rate': 48000,
        'duration_frames': 480000,
        'native': {'media_frame': frame, 'sample_rate': 48000,
                   'queued_frames': 1024, 'underrun_frames': 0},
    }
    snapshot.update(extra)
    service.events.put({
        'type': 'live_state', 'request_id': generation,
        'snapshot': snapshot,
    })
    player.poll()
    settle()


work = APP / 'work'
work.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory(
        prefix='audio-mix-realtime-', dir=work, ignore_cleanup_errors=True) as folder:
    root = Path(folder)

    # The dialog owns exactly one native player.  Hiding the page keeps the
    # same transport/session alive; explicit close is the only lifecycle end.
    with fixture_dialog() as (owner, dialog, player, service):
        configure_sources(dialog, root, 2)
        generation = prepare_session(dialog, player, service)
        dialog.show()
        settle()
        dialog.hide()
        settle()
        assert player.is_alive and not service.closed
        assert player._generation == generation
        assert not any(command[0] in ('release',) for command in service.commands)
        assert not any(command[0] == 'open' and command[1] != generation
                       for command in service.commands)
    root = Path(folder) / 'source-two'
    root.mkdir()

    # A/B, parameter refresh and seek stay on the same player/generation.  No
    # segment WAV render or player replacement may occur.
    with fixture_dialog() as (owner, dialog, player, service):
        configure_sources(dialog, root, 1)
        generation = prepare_session(dialog, player, service)
        dialog.eq_enabled.setChecked(True)
        dialog.compression_enabled.setChecked(True)
        dialog.reverb_enabled.setChecked(True)
        dialog.low_cut.setValue(90)
        dialog.presence.setValue(3.5)
        dialog.compression.setValue(45)
        dialog.reverb.setValue(30)
        dialog._preview_debounce.stop()
        with patch.object(mixdown_export, 'render_once',
                          side_effect=AssertionError('preview rendered a WAV')):
            assert dialog.play_preview('before')
            assert wait_for(
                lambda: any(
                    command[0] == 'parameters'
                    and command[2]['eq_low_cut_hz'] == 0.0
                    and command[2]['eq_presence_db'] == 0.0
                    and command[2]['compression_strength'] == 0.0
                    and command[2]['reverb_mix'] == 0.0
                    for command in service.commands),
                player=player,
            ), service.commands
            assert dialog.play_preview('after')
            assert wait_for(
                lambda: sum(
                    command[0] == 'parameters'
                    and command[2]['compression_strength'] > 0.0
                    for command in service.commands) >= 1,
                player=player,
            ), service.commands
            send_live_state(player, service, generation, 0, state='playing')
            dialog._seek_to_song_position(4200, commit=True)
            assert wait_for(
                lambda: any(command[0] == 'seek' for command in service.commands),
                player=player,
            )
        assert player._generation == generation
        assert sum(command[0] == 'open' for command in service.commands) == 1
        assert not any(command[0] == 'release' for command in service.commands)
        seek_command = next(command for command in reversed(service.commands)
                            if command[0] == 'seek')
        assert seek_command[2][0] == 4200
        assert seek_command[2][1] is True
    root = Path(folder) / 'source-three'
    root.mkdir()

    # Three vocal tracks and their offsets/M/S/pan/reverb sends enter the one
    # canonical request.  Export publishes into its own container only after
    # source fingerprints remain unchanged; an existing target is untouched.
    with fixture_dialog() as (owner, dialog, player, service):
        vocals, backing = configure_sources(dialog, root, 3)
        dialog.vocal_tracks[0]['gain_db'].setValue(-4.0)
        dialog.vocal_tracks[0]['offset'].setValue(.11)
        dialog.vocal_tracks[0]['pan'].setValue(-25)
        dialog.vocal_tracks[0]['mute'].setChecked(True)
        dialog.vocal_tracks[0]['reverb_send'] = .25
        dialog.vocal_tracks[1]['solo'].setChecked(True)
        dialog.vocal_tracks[1]['pan'].setValue(30)
        dialog.vocal_tracks[2]['offset'].setValue(.22)
        dialog.instrumental_pan.setValue(10)
        dialog.instrumental_mute.setChecked(True)
        request = dialog._request(root / 'request-check.wav')
        assert len(request['vocals']) == 3
        assert [row['offset_seconds'] for row in request['vocals']] == [.11, 0.0, .22]
        assert [row['mute'] for row in request['vocals']] == [True, False, False]
        assert [row['solo'] for row in request['vocals']] == [False, True, False]
        assert [round(row['pan'], 6) for row in request['vocals']] == [-.25, .30, 0.0]
        assert abs(request['vocals'][0]['reverb_send'] - .25) < 1e-9
        assert request['instrumental_mute'] is True
        frozen_input = copy.deepcopy(request)
        plan = mixdown_export.plan_export(
            frozen_input, source_job=Path(folder) / 'source-job')
        frozen_input['vocals'][0]['offset_seconds'] = 9.0
        assert plan.request['vocals'][0]['offset_seconds'] == .11
        assert plan.parameters['vocals'][0]['offset_seconds'] == .11
        assert Path(plan.request['output_path']) != plan.destination
        assert plan.temporary.name.endswith('.part.wav')
        assert not plan.destination.exists()

        before = {path: path.read_bytes() for path in (*vocals, backing)}
        blocked = root / 'blocked.wav'
        blocked.write_bytes(b'never overwrite')
        dialog.fields['output'].setText(str(blocked))
        dialog.export()
        settle()
        assert not dialog.busy()
        assert '无法开始导出' in dialog.status.text()
        assert blocked.read_bytes() == b'never overwrite'

        output = root / 'mix.wav'
        dialog.fields['output'].setText(str(output))
        player_commands = len(service.commands)
        # Exercise the export transaction synchronously.  Transport/QThread
        # lifecycle has its own coverage; this no-device test owns the safety
        # boundary: temporary container, source fingerprints and atomic publish.
        def run_export_now(kind, function, with_progress=False):
            del with_progress
            result = function(lambda: False, lambda event: None)
            dialog.worker = None
            dialog._busy_kind = None
            dialog.succeeded(kind, result)
            return True

        fake_render.requests.clear()
        with patch.object(mixdown_export, 'render_once', side_effect=fake_render), \
                patch.object(dialog, '_launch', side_effect=run_export_now):
            dialog.export()
        assert output.is_file() and output.stat().st_size > 44
        assert len(fake_render.requests) == 1
        rendered_path = Path(fake_render.requests[0]['output_path'])
        assert rendered_path != output.resolve()
        assert rendered_path.name.startswith('.' + output.stem + '.')
        assert rendered_path.name.endswith('.part.wav')
        assert all(path.read_bytes() == before[path] for path in before)
        assert len(service.commands) == player_commands
        receipt = dialog._export_receipt
        assert receipt['schema'] == 'vwa.audio_mixdown.receipt/v2'
        assert len(receipt['inputs']['vocals']) == 3
        assert all(len(row['sha256']) == 64 for row in receipt['inputs']['vocals'])
        assert len(receipt['inputs']['instrumental_path']['sha256']) == 64
        assert len(receipt['output']['sha256']) == 64
        assert [row['offset_seconds'] for row in
                receipt['parameters']['vocals']] == [.11, 0.0, .22]
    root = Path(folder) / 'source-eq'
    root.mkdir()

    # EQ remains operational and is projected to the backing sample rate.
    with fixture_dialog() as (owner, dialog, player, service):
        del player, service
        backing = write_wav(root / 'metadata-32000.wav', 110, 32000, .1)
        vocal = write_wav(root / 'metadata-vocal-64000.wav', 220, 64000, .1)
        dialog.fields['vocal'].setText(str(vocal))
        dialog.fields['instrumental'].setText(str(backing))
        dialog._preview_debounce.stop()
        assert dialog.eq_curve.sample_rate() == 32000.0
        assert dialog.eq_band_controls.sample_rate() == 32000.0
        assert abs(dialog.eq_curve.frequency_limit_hz() - 15200.0) < 1e-9
        bands = dialog.eq_band_controls.bands()
        assert len(bands) == 4
        bands[0]['freq_hz'] = 75.0
        bands[2]['gain_db'] = 4.0
        bands[3]['freq_hz'] = 20000.0
        dialog.eq_enabled.setChecked(True)
        assert dialog._apply_eq_bands(bands)
        request = dialog._request(root / 'eq-check.wav')
        assert len(request['eq_bands']) == 4
        assert abs(request['eq_bands'][3]['freq_hz'] - 15200.0) < 1e-9
        assert abs(request['eq_low_cut_hz'] - 75.0) < 1e-9
        assert abs(request['eq_presence_db'] - 4.0) < 1e-9
    root = Path(folder) / 'source-preset'
    root.mkdir()

    # Saving twice appends independent identities.  Applying a preset with the
    # default lock preserves track mix, and one undo restores the exact stamp.
    library = PresetLibrary(Path(folder) / 'preset-library')
    with fixture_dialog(preset_library=library) as (owner, dialog, player, service):
        del player, service
        dialog.add_vocal_track()
        dialog.vocal_tracks[0]['gain_db'].setValue(-7.0)
        dialog.vocal_tracks[0]['pan'].setValue(-33)
        dialog.vocal_tracks[0]['offset'].setValue(.12)
        dialog.vocal_tracks[0]['reverb_send'] = .22
        dialog.vocal_tracks[1]['pan'].setValue(24)
        dialog.compression.setValue(63)
        payload = copy.deepcopy(dialog._mix_preset_state()['processing'])

        first = make_document(payload, 'Same preset name')
        second = make_document(payload, 'Same preset name')

        class SaveDialog:
            document = first

            def __init__(self, *args, **kwargs):
                del args, kwargs

            def exec_(self):
                return QDialog.Accepted

        with patch.object(preset_ui, 'MixPresetSaveDialog', SaveDialog):
            assert dialog.save_mix_preset()
            SaveDialog.document = second
            assert dialog.save_mix_preset()
        local = [entry for entry in library.snapshot()['entries']
                 if not entry.get('builtin')]
        assert len(local) == 2
        assert {entry['id'] for entry in local} == {first['id'], second['id']}

        before = dialog._mix_preset_stamp()
        assert dialog.apply_mix_preset('opera_bright', lock_mix=True)
        after = dialog._mix_preset_stamp()
        assert after['paths'] == before['paths']
        assert after['balance'] == before['balance']
        assert after['state']['tracks'] == before['state']['tracks']
        assert after['state']['processing'] != before['state']['processing']
        assert dialog.undo_mix_preset()
        assert dialog._mix_preset_stamp() == before

        before = dialog._mix_preset_stamp()
        assert dialog.apply_mix_preset('opera_bright', lock_mix=False)
        after = dialog._mix_preset_stamp()
        assert after['state']['tracks'] != before['state']['tracks']
        assert dialog.undo_mix_preset()
        assert dialog._mix_preset_stamp() == before

remove_tree(root.parent)

print('PASS: one realtime player survives hide, A/B/seek do not render, three-track/EQ requests and preset undo/export safety verified')
