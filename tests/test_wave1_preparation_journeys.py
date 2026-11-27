"""Wave 1 end-to-end user journey tests: Preparation Workflow, Batch Queue & Subtitles.

Runs in three tiers:
1. Pure-Python hermetic tests (always executed, including minimal GitHub CI):
   - W1-01: PrepQueueStore FIFO enqueue (CJK/#%&[] paths), reorder, remove, 32-item cap, persistence, corrupt recovery.
   - W1-02: PreparationDraft options/BPM/formats, custom preset lifecycle + built-in protection, draft settings
     persistence (never leaks song path/lyrics), imported stems validation.
2. Real export runtime tests (executed whenever dependencies/export is present):
   - W1-03: Codex ISS-007 tri-lingual (JA/ZH/EN) full project_exports.export_projects() E2E inside CJK/#%&[]
     special-character job path: Japanese VSQX bs=0 + VY1V4 + X-SAMPA phonemes (zero Chinese `l a`),
     English SVP/USTX/VSQX continuations & CYBER_DIVA, Chinese exact note/receipt round-trip.
3. Qt6 Offscreen UI tests (executed whenever PySide6 is available):
   - W1-04: QuickSubtitles + SubtitleDialog full lifecycle (LRC + multi-track MIDI, track switch, add/edit/remove,
     out-of-order rejection, W1-BUG-05 invalid-offset error clear on reset, SRT/VTT UTF-8 monotonic export +
     no-overwrite guard, restoreSource draft switching, `subtitles` alias route).
   - W1-05: Batch queue UI (PreparationQueueCard) + state-machine guards (W1-BUG-01..04): cancel pauses queue,
     single-song failure advances to next valid song while skipping deleted files, previous song lyrics never leak
     to next queued song, setPreparationMusicPath clears stale error notices.
   - W1-06: PreparationSourcesDialog (`check`/`download` panes offline-safe) + PreparationPage (default/advanced/queue)
     and SubtitleDialog multi-DPI x Dark/Light zero-overflow gate.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import unittest
from unittest import mock
import wave
import xml.etree.ElementTree as ET

APP = Path(__file__).resolve().parents[1]
for candidate in (APP / 'src', APP):
    text = str(candidate)
    if text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)

os.environ.setdefault('VWA_TESTING', '1')
os.environ.setdefault('BVWA_TESTING', '1')

import common
import mido
import preparation_commands as prep
from preparation_queue import MAX_QUEUE_ITEMS, PrepQueueStore, queue_path
import preset_library
import project_exports as pe
import subtitles
import vwa_io


def _ensure_export_runtime() -> bool:
    import project_convert_service as svc
    if svc.export_runtime_available():
        return True
    preview_export = Path('C:/vwa/workspace/preview/vwa/dependencies/export')
    if (preview_export / 'libresvip/__init__.py').is_file():
        os.environ['VWA_EXPORT_RUNTIME'] = str(preview_export)
        return svc.export_runtime_available()
    return False


def _has_qt6() -> bool:
    qt_root = os.environ.get('VWA_TEST_QT6_ROOT', '')
    if qt_root and Path(qt_root).is_dir() and qt_root not in sys.path:
        sys.path.insert(0, qt_root)
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


def _has_soundfile() -> bool:
    try:
        import soundfile  # noqa: F401
        return True
    except ImportError:
        return False


def _write_wav(path: Path, duration_s: float = 0.5, sr: int = 44100) -> Path:
    n = max(1, int(round(duration_s * sr)))
    data = bytearray(n * 2)
    for i in range(n):
        val = int(8000 * math.sin(2.0 * math.pi * 440.0 * i / sr))
        struct.pack_into('<h', data, i * 2, val)
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(data))
    return path


def _write_lyric_midi(path: Path) -> Path:
    mid = mido.MidiFile(ticks_per_beat=480, charset='utf-8')
    t0 = mido.MidiTrack()
    t0.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(120), time=0))
    mid.tracks.append(t0)
    for name, words in (('Lead', ['你', '好', '世', '界']), ('Backing', ['la', 'la', 'oh'])):
        tr = mido.MidiTrack()
        tr.append(mido.MetaMessage('track_name', name=name, time=0))
        for idx, word in enumerate(words):
            tr.append(mido.MetaMessage('lyrics', text=word, time=0 if idx == 0 else 480))
            tr.append(mido.Message('note_on', note=60 + idx, velocity=90, time=0))
            tr.append(mido.Message('note_off', note=60 + idx, velocity=0, time=480))
        mid.tracks.append(tr)
    mid.save(str(path))
    return path


def _write_ustx_voice(path: Path, lyrics: list[str]) -> Path:
    notes_yaml = []
    for i, lyr in enumerate(lyrics):
        pos = i * 480
        notes_yaml.append(
            f'    - position: {pos}\n'
            f'      duration: 480\n'
            f'      tone: {60 + (i % 7)}\n'
            f'      lyric: "{lyr}"\n'
            f'      pitch:\n'
            f'        data:\n'
            f'        - {{x: -40, y: 0, shape: io}}\n'
            f'        - {{x: 40, y: 0, shape: io}}\n'
            f'        snap_first: true\n'
        )
    text = (
        'name: TestVoice\n'
        'comment: ""\n'
        'output_dir: Vocal\n'
        'cache_dir: UCache\n'
        'ustx_version: "0.6"\n'
        'resolution: 480\n'
        'bpm: 120.0\n'
        'beat_per_bar: 4\n'
        'beat_unit: 4\n'
        'tempos:\n'
        '- position: 0\n'
        '  bpm: 120.0\n'
        'time_signatures:\n'
        '- bar_position: 0\n'
        '  beat_per_bar: 4\n'
        '  beat_unit: 4\n'
        'tracks:\n'
        '- track_name: Lead\n'
        '  track_color: Blue\n'
        '  mute: false\n'
        '  solo: false\n'
        '  volume: 0.0\n'
        'voice_parts:\n'
        '- name: Lead\n'
        '  track_no: 0\n'
        '  position: 0\n'
        '  notes:\n'
        + ''.join(notes_yaml)
        + 'wave_parts: []\n'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


class Wave1PurePythonTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix='vwa-w1-l0-')).resolve()

    def tearDown(self):
        vwa_io.safe_rmtree(self._tmp)

    def test_w1_01_prep_queue_store_persistence_reorder_cap_and_corrupt_recovery(self):
        q = PrepQueueStore(self._tmp)
        s1 = _write_wav(self._tmp / '中文 曲目 [v1] #1 & 100%.wav', 0.1)
        s2 = _write_wav(self._tmp / '日本語 テスト (demo).wav', 0.1)
        s3 = _write_wav(self._tmp / 'english_track.wav', 0.1)

        e1 = q.enqueue(s1, title='中文曲', options={'language': 'zh'})
        e2 = q.enqueue(s2, title='日文曲', options={'language': 'ja'})
        e3 = q.enqueue(s3, title='英文曲', options={'language': 'en'})
        self.assertEqual([r['title'] for r in q.list_items()], ['中文曲', '日文曲', '英文曲'])

        # Reorder and remove
        self.assertTrue(q.move(e3['id'], -2))
        self.assertEqual([r['title'] for r in q.list_items()], ['英文曲', '中文曲', '日文曲'])
        self.assertTrue(q.remove(e2['id']))
        self.assertEqual([r['title'] for r in q.list_items()], ['英文曲', '中文曲'])

        # Persistence across fresh store instance
        q2 = PrepQueueStore(self._tmp)
        self.assertEqual([r['title'] for r in q2.list_items()], ['英文曲', '中文曲'])
        popped = q2.dequeue()
        self.assertEqual(popped['options']['language'], 'en')

        # 32-item cap enforced
        q2.clear()
        for i in range(MAX_QUEUE_ITEMS):
            q2.enqueue(s1, title=f'Song {i}')
        with self.assertRaises(ValueError):
            q2.enqueue(s1, title='Overflow')

        # Corrupt JSON recovers gracefully as empty list
        queue_path(self._tmp).write_text('{broken json', encoding='utf-8')
        self.assertEqual(q2.list_items(), [])

    def test_w1_02_draft_options_presets_persistence_and_imported_stems(self):
        draft = prep.PreparationDraft()
        draft = draft.edit('language', 'ja').edit('bpm', '132.5').edit('recognize_lyrics', False)
        draft = draft.with_export_format('project_formats', 'vsqx', True)
        self.assertEqual(draft.preferences()['language'], 'ja')
        self.assertEqual(draft.preferences()['bpm'], 132.5)
        self.assertIn('vsqx', draft.preferences()['project_formats'])

        # Invalid BPM rejected on preferences()
        bad_bpm = draft.edit('bpm', 'not-a-number')
        with self.assertRaises(ValueError):
            bad_bpm.preferences()

        # Custom preset append, apply, remove & built-in name guard
        lib = preset_library.PresetLibrary(self._tmp / 'presets')
        with self.assertRaises(ValueError):
            prep.append_preset(lib, draft, '内置-非法')
        doc = prep.append_preset(lib, draft, '日文快捷准备')
        catalog = prep.preset_catalog(lib.snapshot(), {})
        user_row = next(r for r in catalog if r['name'] == '日文快捷准备')
        self.assertTrue(user_row['removable'])
        loaded = lib.get_document(user_row['id'], user_row['version'])
        applied = prep.apply_preset(prep.PreparationDraft(), loaded['payload'])
        self.assertEqual(applied.view['language'], 'ja')
        self.assertFalse(applied.view['recognize_lyrics'])
        self.assertIn('vsqx', applied.view['project_formats'])
        # Song-specific BPM is intentionally not carried in portable presets
        self.assertEqual(applied.view['bpm'], '')
        prep.remove_preset(lib, user_row['id'], user_row['version'])
        self.assertFalse(any(r['name'] == '日文快捷准备' for r in prep.preset_catalog(lib.snapshot(), {})))

        # Draft settings persistence must NOT leak song path or lyrics
        song = _write_wav(self._tmp / 'secret_song.wav', 0.1)
        dirty_draft = prep.PreparationDraft(music_path=str(song)).edit('language', 'ja').edit('lyrics', '私密歌词')
        with mock.patch.object(prep, 'ROOT', self._tmp), mock.patch.object(common, 'ROOT', self._tmp):
            (self._tmp / 'cache').mkdir(exist_ok=True)
            prep.save_draft_settings(dirty_draft)
            restored = prep.restore_draft_settings(prep.PreparationDraft())
        self.assertEqual(restored.view['language'], 'ja')
        self.assertEqual(restored.music_path, '')
        self.assertEqual(restored.view['lyrics'], '')

        # Imported stems validation
        imp = prep.PreparationDraft(voice_mode='import')
        with self.assertRaises(ValueError):
            prep.validate_imported_stems(imp)
        not_wav = self._tmp / 'fake.mp3'
        not_wav.write_bytes(b'1234')
        with self.assertRaises(ValueError):
            prep.validate_imported_stems(imp.set_stem('lead', not_wav).set_stem('backing', song).set_stem('instrumental', song))

        if _has_soundfile():
            w1 = _write_wav(self._tmp / 'lead.wav', 0.2)
            w2 = _write_wav(self._tmp / 'backing.wav', 0.2)
            w3 = _write_wav(self._tmp / 'inst.wav', 0.2)
            # Duplicate stem rejected
            with self.assertRaises(ValueError):
                prep.validate_imported_stems(imp.set_stem('lead', w1).set_stem('backing', w1).set_stem('instrumental', w3))
            valid_stems = prep.validate_imported_stems(imp.set_stem('lead', w1).set_stem('backing', w2).set_stem('instrumental', w3))
            self.assertEqual(set(valid_stems), {'lead', 'backing', 'instrumental'})


@unittest.skipUnless(_ensure_export_runtime(), 'dependencies/export runtime unavailable in minimal CI')
class Wave1RealExportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix='vwa-w1-exp-')).resolve()

    def tearDown(self):
        vwa_io.safe_rmtree(self._tmp)

    def test_w1_03_iss007_trilingual_vsqx_svp_ustx_export_and_special_path(self):
        # Special CJK + symbols job directory path
        for lang, lyrics, expected_bs, expected_singer in (
            ('ja', ['こ', 'の', 'ま', 'し', 'つ', 'ko', 'shi', 'tsu', 'wa', 'wo', 'n'], '0', 'VY1V4'),
            ('zh', ['你', '好', '世', '界', '梦', '灯', '笼'], '4', None),
            ('en', ['hel', '+', 'lo', '-', 'world', '+~', 'shine'], '1', 'CYBER_DIVA'),
        ):
            job = self._tmp / f'任务_{lang} [v2] #1 & 100%'
            job.mkdir(parents=True, exist_ok=True)
            _write_ustx_voice(job / 'midi/vocals.ustx', lyrics)
            request = {
                'language': lang,
                'voice_mode': 'single',
                'project_formats': ['vsqx', 'svp', 'ustx'],
                'audio_formats': [],
                'include_instrumental': False,
                'export_pitch': False,
            }
            with mock.patch.object(pe, 'library', side_effect=self._bundled_library):
                result = pe.export_projects(job, request)
            self.assertEqual(len(result['outputs']), 3, f'{lang}: {result}')

            # Inspect exported VSQX XML directly (ISS-007 & W1-BUG-06)
            vsqx_path = job / 'exports/voices.vsqx'
            self.assertTrue(vsqx_path.is_file())
            root = ET.parse(vsqx_path).getroot()
            voices = root.findall('./{*}vVoiceTable/{*}vVoice')
            singers = root.findall('./{*}vsTrack/{*}vsPart/{*}singer')
            self.assertTrue(voices and singers)
            if expected_bs is not None:
                for el in voices + singers:
                    self.assertEqual(el.findtext('{*}bs'), expected_bs, f'{lang} VSQX wrong bs')
            if expected_singer is not None:
                self.assertEqual(voices[0].findtext('{*}name'), expected_singer)

            if lang == 'ja':
                notes_map = {n.findtext('{*}y'): n.findtext('{*}p') for n in root.findall('.//{*}note')}
                self.assertEqual(notes_map.get('ko'), 'k o')
                self.assertEqual(notes_map.get('shi'), 'S i')
                self.assertEqual(notes_map.get('tsu'), 'ts M')
                self.assertEqual(notes_map.get('wo'), 'w o')
                self.assertNotIn('l a', notes_map.values())

            if lang == 'en':
                svp_doc = json.loads((job / 'exports/voices.svp').read_bytes().removesuffix(b'\x00').decode('utf-8-sig'))
                svp_lyrics = [n['lyrics'] for n in svp_doc['tracks'][0]['mainGroup']['notes']]
                self.assertIn('+', svp_lyrics)
                self.assertIn('-', svp_lyrics)

    @staticmethod
    def _bundled_library():
        runtime = os.environ.get('VWA_EXPORT_RUNTIME', '').strip()
        if runtime and runtime not in sys.path:
            sys.path.insert(0, runtime)
        from libresvip.model.base import InstrumentalTrack, Project, SingingTrack
        from libresvip.plugins.svp.synthv_studio_converter import SynthVStudioConverter
        from libresvip.plugins.ustx.ustx_converter import OpenUtauConverter
        from libresvip.plugins.vsqx.vsqx_converter import VsqxConverter
        return (
            {'ustx': OpenUtauConverter, 'vsqx': VsqxConverter, 'svp': SynthVStudioConverter},
            Project,
            SingingTrack,
            InstrumentalTrack,
        )


@unittest.skipUnless(_has_qt6(), 'Qt6/PySide6 unavailable in pure-Python CI')
class Wave1QtOffscreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PySide6.QtCore import QCoreApplication, QPointF, QUrl
        from PySide6.QtGui import QGuiApplication
        import quick_app

        cls.QCoreApplication = QCoreApplication
        cls.QPointF = QPointF
        cls.QUrl = QUrl
        cls.app = QGuiApplication.instance() or QGuiApplication(sys.argv)
        cls.engine, cls.win, cls.owner, cls.modes = quick_app.create_workspace(cls.app)
        cls._tmp = Path(tempfile.mkdtemp(prefix='vwa-w1-ui-')).resolve()
        cls._pump(25)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, 'owner', None) is not None:
            cls.owner.ux.clearPrepQueue()
            cls.owner.close()
            cls._pump(10)
        vwa_io.safe_rmtree(cls._tmp)

    @classmethod
    def _pump(cls, rounds: int = 15):
        for _ in range(rounds):
            cls.QCoreApplication.processEvents()

    def _walk(self, item):
        if item is None:
            return
        yield item
        for child in item.childItems():
            yield from self._walk(child)

    def _find(self, object_name: str):
        for item in self._walk(self.win.contentItem()):
            if item.objectName() == object_name:
                return item
        from PySide6.QtCore import QObject
        return self.win.findChild(QObject, object_name)

    def _overflows(self, viewport_w: int):
        bad = []
        for item in self._walk(self.win.contentItem()):
            if item.isVisible() and item.width() > 0:
                pt = item.mapToScene(self.QPointF(0, 0))
                if pt.x() + item.width() > viewport_w + 2:
                    bad.append((item.metaObject().className(), item.objectName(), round(pt.x(), 1), round(pt.x() + item.width(), 1)))
        return bad

    def test_w1_04_subtitles_full_lifecycle_offset_error_clear_and_alias_route(self):
        sub = self.owner.subtitles
        lrc_file = self._tmp / 'timed.lrc'
        lrc_file.write_text('[00:01.00]第一句\n[00:04.50]第二句\n[00:09.00]第三句\n', encoding='utf-8')
        mid_file = _write_lyric_midi(self._tmp / 'duet.mid')

        # Load LRC -> edit cue -> invalid offset -> reset offset clears error (W1-BUG-05)
        self.assertTrue(sub.loadLocal(self.QUrl.fromLocalFile(str(lrc_file))))
        self.assertEqual(sub.view['cueCount'], 3)
        self.assertFalse(sub.view['sourcePath'].startswith('\\\\?\\'))
        self.assertTrue(sub.updateCue(0, 1.0, 4.0, '第一句（改）'))
        self.assertTrue(sub.addCue())
        self.assertEqual(sub.view['cueCount'], 4)
        self.assertTrue(sub.removeCue(3))

        # Out-of-order start disables export cleanly
        self.assertTrue(sub.updateCue(1, 10.0, 12.0, '乱序'))
        self.assertFalse(sub.view['canExport'])
        self.assertTrue(sub.updateCue(1, 4.5, 8.5, '第二句'))
        self.assertTrue(sub.view['canExport'])

        # W1-BUG-05: invalid offset triggers error, returning to 0.0 clears stale error!
        self.assertFalse(sub.setOffset(float('nan')))
        self.assertTrue(bool(sub.view['error']))
        self.assertTrue(sub.setOffset(0.0))
        self.assertEqual(sub.view['error'], '')

        # Shift by +0.5s and export both SRT and WebVTT; verify UTF-8, monotonic timestamps, no-overwrite
        self.assertTrue(sub.setOffset(0.5))
        srt_out = self._tmp / 'out.srt'
        vtt_out = self._tmp / 'out.vtt'
        self.assertTrue(sub.exportTo(self.QUrl.fromLocalFile(str(srt_out)), 'srt'))
        self.assertTrue(sub.exportTo(self.QUrl.fromLocalFile(str(vtt_out)), 'vtt'))
        srt_text = srt_out.read_text(encoding='utf-8')
        vtt_text = vtt_out.read_text(encoding='utf-8')
        self.assertIn('第一句（改）', srt_text)
        self.assertTrue(vtt_text.startswith('WEBVTT'))
        starts = [
            int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0
            for h, m, s, ms in re.findall(r'(\d{2}):(\d{2}):(\d{2}),(\d{3}) -->', srt_text)
        ]
        self.assertEqual(starts, sorted(starts))
        self.assertAlmostEqual(starts[0], 1.5, places=2)
        # Never overwrite existing SRT
        orig_sha = common.file_hash(srt_out)
        self.assertFalse(sub.exportTo(self.QUrl.fromLocalFile(str(srt_out)), 'srt'))
        self.assertEqual(common.file_hash(srt_out), orig_sha)

        # Load multi-track MIDI, switch tracks, then restore LRC draft with zero loss
        self.assertTrue(sub.loadLocal(self.QUrl.fromLocalFile(str(mid_file))))
        self.assertEqual(len(sub.midiTracks), 2)
        second_track_id = int(sub.midiTracks[1]['id'])
        self.assertTrue(sub.selectMidiTrack(second_track_id))
        self.assertTrue(sub.restoreSource('lrc'))
        self.assertEqual(sub.view['sourceKind'], 'lrc')
        self.assertEqual(sub.cues[0]['text'], '第一句（改）')

        # `subtitles` alias route navigates to processing and opens SubtitleDialog
        self.owner.navigate('subtitles')
        self._pump(25)
        self.assertEqual(self.owner.state.route, 'processing')

    def test_w1_05_batch_queue_ui_cancel_pause_skip_missing_and_lyric_isolation(self):
        self.owner.navigate('processing')
        self._pump(25)
        ux = self.owner.ux
        ux.clearPrepQueue()

        s1 = _write_wav(self._tmp / 'q1.wav', 0.2)
        s2 = _write_wav(self._tmp / 'q2_deleted.wav', 0.2)
        s3 = _write_wav(self._tmp / 'q3.wav', 0.2)

        # W1-BUG-04: setPreparationMusicPath error cleared upon valid audio path
        self.owner.setPreparationMusicPath(str(self._tmp / 'does_not_exist.wav'))
        self._pump(10)
        self.assertTrue(bool(self.owner.notice))
        self.owner.setPreparationMusicPath(str(s1))
        self._pump(10)
        self.assertEqual(self.owner.notice, '')

        # W1-BUG-01: Queue card visible & supports reorder/remove/clear
        ux.enqueuePreparation(self.QUrl.fromLocalFile(str(s1)))
        ux.enqueuePreparation(self.QUrl.fromLocalFile(str(s2)))
        ux.enqueuePreparation(self.QUrl.fromLocalFile(str(s3)))
        self._pump(20)
        self.assertEqual(len(ux.prepQueue), 3)
        qcard = self._find('prepQueueCard')
        self.assertIsNotNone(qcard)
        self.assertTrue(qcard.isVisible())
        self.assertIsNotNone(self._find('prepQueueStart'))
        self.assertIsNotNone(self._find('prepQueueClear'))

        # Reorder s3 above s2 then move back
        id_s3 = ux.prepQueue[2]['id']
        ux.moveQueuedPreparation(id_s3, -1)
        self.assertEqual(ux.prepQueue[1]['id'], id_s3)
        ux.moveQueuedPreparation(id_s3, 1)
        self.assertEqual(ux.prepQueue[2]['id'], id_s3)

        # W1-BUG-02: Paste lyrics onto current draft, delete s2 before its turn, then drive queue transitions
        self.owner.setPreparationField('lyrics', '上一首遗留歌词')
        started_songs: list[tuple[str, str]] = []

        def fake_start():
            started_songs.append((self.owner.preparation['music_path'], self.owner.preparation['lyrics']))

        with mock.patch.object(self.owner, 'startPreparation', side_effect=fake_start):
            # Start queue -> picks s1 and clears stale lyrics!
            self.assertTrue(ux.startPrepQueue())
            self.assertEqual(len(started_songs), 1)
            self.assertEqual(Path(started_songs[0][0]).resolve(), s1.resolve())
            self.assertEqual(started_songs[0][1], '')  # Stale lyrics cleared!

            # Delete s2 on disk before next advance
            s2.unlink()

            # Simulate user CANCELLING s1 -> queue MUST pause and NOT auto-start s3!
            ux._was_prep_busy = True
            self.owner.state.preparation_task['state'] = 'cancelled'
            ux._watch_preparation_queue()
            self.assertEqual(len(started_songs), 1)
            self.assertEqual(len(ux.prepQueue), 2)

            # Simulate s1 FAILED (or finished) -> queue skips missing s2 and auto-starts s3!
            ux._was_prep_busy = True
            self.owner.state.preparation_task['state'] = 'failed'
            ux._watch_preparation_queue()
            self.assertEqual(len(started_songs), 2)
            self.assertEqual(Path(started_songs[1][0]).resolve(), s3.resolve())
            self.assertEqual(ux.prepQueue, [])

        ux.clearPrepQueue()
        self._pump(15)

    def test_w1_06_sources_dialog_and_multidpi_zero_overflow(self):
        self.owner.navigate('processing')
        self._pump(20)
        # Keep 1 item in queue + expand advanced options to stress-test full layout height/width
        s = _write_wav(self._tmp / 'layout_q.wav', 0.1)
        self.owner.ux.enqueuePreparation(self.QUrl.fromLocalFile(str(s)))
        self.owner.setPreparationField('advanced_expanded', True)
        self._pump(20)

        for theme in ('dark', 'light'):
            self.owner.support.setSetting('theme', theme)
            for (w, h, scale) in ((1280, 720, 100), (1366, 768, 125), (1440, 900, 130)):
                self.win.setWidth(w)
                self.win.setHeight(h)
                self.owner.support.setSetting('ui_scale', scale)
                self._pump(25)
                self.assertEqual(
                    self._overflows(int(self.win.width())), [],
                    f'PreparationPage overflow at {w}x{h}@{scale}% ({theme})',
                )

        # Open SubtitleDialog and check zero overflow at 130%
        self.owner.openSubtitleTool()
        self._pump(25)
        self.assertEqual(self._overflows(int(self.win.width())), [], 'SubtitleDialog overflow at 1440x900@130%')
        self.owner.ux.clearPrepQueue()
        self._pump(10)


if __name__ == '__main__':
    unittest.main(verbosity=2)
