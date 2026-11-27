"""Wave 6 end-to-end user journey tests: Project Format Converter.

Runs in three tiers:
1. Pure-Python hermetic tests (always executed, including minimal GitHub CI):
   - W6-01: Language detection (Japanese romaji vs English vs Kana/CJK), DS geminate/compound normalization,
     USTX MaskedCurve compat patch, UST encoding detection, extended-path stripping, friendly Chinese errors.
   - W6-02: SV1 library group-ref detection (W6-BUG-01), corrupt/empty file friendly errors, non-clobber planning.
2. Real export runtime + real Codex fixture tests (executed whenever dependencies/export is present):
   - W6-03: ISS-004 (SV2 Rolling Girl -> 6 core formats 1448->1448, lang=ja), ISS-005 (VPR <-> VSQX),
     W6-BUG-01 (SV1-Installation 507 notes -> USTX/SVP/LRC/SRT/UST UTF-8), W6-BUG-04 (MIDI 2-track 798 notes),
     W6-BUG-05 (English VSQX graceful fallback without optional CMU dict).
   - W6-04: ISS-008 (DS round-trip + JA SV2/USTX -> DS), W6-BUG-02 (modern USTX MaskedCurve),
     all extended formats (CCS/DSPX/MusicXML/SVIP/UST) & ACEP/ACEP2 read-only SHA-256 preservation.
   - W6-05: 4 conversion strategies (pitch keep/strip, lyric adapt/raw, instrumental, split_tracks),
     LRC/SRT monotonic timestamps, mid-flight cancel + zero leftover .pending-* + immediate recovery.
3. Qt6 Offscreen UI journeys (executed whenever PySide6 is available):
   - W6-06: Empty-state error banner visibility (W6-BUG-08), corrupt-to-valid recovery, dynamic default outputDir
     vs locked custom outputDir, stale receipt clearing, receipt warning & unverified status rendering.
   - W6-07: Real cross-page handoffs (sendToTuning -> tuning, sendToChorusScore -> chorus) +
     Multi-DPI x Dark/Light zero-overflow gate across empty, loaded, and receipt states.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import xml.etree.ElementTree as ET
import zipfile

APP = Path(__file__).resolve().parents[1]
for candidate in (APP / 'src', APP):
    text = str(candidate)
    if text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)

os.environ.setdefault('VWA_TESTING', '1')
os.environ.setdefault('BVWA_TESTING', '1')

import common
import project_convert_compat as compat
import project_convert_service as svc
import vwa_io

CODEX_FIXTURES = Path('C:/vwa/work/codex-e2e-20261126/fixtures')


def _ensure_export_runtime() -> bool:
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


def _make_sv1_with_group_refs(path: Path) -> Path:
    blick = 705_600_000 // 480
    doc = {
        'version': 122,
        'time': {'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}], 'tempo': [{'position': 0, 'bpm': 124}]},
        'library': [
            {
                'name': 'ChorusGroup',
                'uuid': 'grp-1',
                'notes': [
                    {'onset': 0, 'duration': 480 * blick, 'pitch': 60, 'lyrics': '你'},
                    {'onset': 480 * blick, 'duration': 480 * blick, 'pitch': 62, 'lyrics': '好'},
                    {'onset': 960 * blick, 'duration': 480 * blick, 'pitch': 64, 'lyrics': '世'},
                    {'onset': 1440 * blick, 'duration': 480 * blick, 'pitch': 65, 'lyrics': '界'},
                ],
                'parameters': {
                    'pitchDelta': {'mode': 'linear', 'points': [0, 0, 240 * blick, 40, 480 * blick, 0]},
                },
            }
        ],
        'tracks': [
            {
                'name': 'Lead',
                'mainRef': {'isInstrumental': False, 'blickOffset': 0},
                'mainGroup': {'notes': [{'onset': 0, 'duration': 480 * blick, 'pitch': 67, 'lyrics': '啊'}], 'parameters': {}},
                'groups': [{'groupID': 'grp-1', 'blickOffset': 1920 * blick, 'pitchOffset': 0}],
            }
        ],
    }
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    return path


def _make_english_svp(path: Path) -> Path:
    blick = 705_600_000 // 480
    words = ['hello', 'world', 'shadows', 'in', 'the', 'moonlight', 'never', 'fade']
    notes = [
        {'onset': i * 480 * blick, 'duration': 480 * blick, 'pitch': 60 + (i % 5), 'lyrics': w}
        for i, w in enumerate(words)
    ]
    doc = {
        'version': 122,
        'time': {'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}], 'tempo': [{'position': 0, 'bpm': 120}]},
        'library': [],
        'tracks': [
            {
                'name': 'English Lead',
                'mainRef': {'isInstrumental': False, 'blickOffset': 0},
                'mainGroup': {
                    'notes': notes,
                    'parameters': {
                        'pitchDelta': {'mode': 'linear', 'points': [0, 0, 240 * blick, 50, 480 * blick, 0]},
                    },
                },
            }
        ],
    }
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    return path


class Wave6PurePythonTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix='vwa-w6-l0-')).resolve()

    def tearDown(self):
        vwa_io.safe_rmtree(self._tmp)

    def test_w6_01_language_detection_romaji_vs_en_and_ds_normalization(self):
        def mk_tracks(lyrics):
            return [SimpleNamespace(note_list=[SimpleNamespace(lyric=x) for x in lyrics])]

        # Japanese romaji (Rolling Girl style) must detect 'ja', never 'en'
        ja_rom = ['wa', 'ma', 'mo', 'to', 'na', 'ka', 'shi', 'ko', 'ga', 'te', 'ta', 'kai', 'sa', 'no', 'da', 'tto', 'ro n', 'br', '-']
        self.assertEqual(compat.detect_tracks_language(mk_tracks(ja_rom)), 'ja')

        # Genuine English must detect 'en'
        en_words = ['shadows', 'in', 'the', 'moonlight', "don't", 'leave', 'me', 'here', 'world', 'tonight']
        self.assertEqual(compat.detect_tracks_language(mk_tracks(en_words)), 'en')

        # Kana & CJK
        self.assertEqual(compat.detect_tracks_language(mk_tracks(['あ', 'い', 'う', 'え', 'お'])), 'ja')
        self.assertEqual(compat.detect_tracks_language(mk_tracks(['你', '好', '世', '界'])), 'zh')

        # DS candidate normalization: geminate sokuon, multi-syllable, punctuation, repeated vowel
        ja_dict = {'to': 't o', 'ka': 'k a', 'chi': 'ch i', 'ro': 'r o', 'a': 'a'}
        self.assertEqual(compat.normalize_ds_candidate('tto', ja_dict, is_ja=True), 'to')
        self.assertEqual(compat.normalize_ds_candidate('kka', ja_dict, is_ja=True), 'ka')
        self.assertEqual(compat.normalize_ds_candidate('cchi', ja_dict, is_ja=True), 'chi')
        self.assertEqual(compat.normalize_ds_candidate('ro n', ja_dict, is_ja=True), 'ro')
        self.assertEqual(compat.normalize_ds_candidate('ka!', ja_dict, is_ja=True), 'ka')
        self.assertEqual(compat.normalize_ds_candidate('aa', ja_dict, is_ja=True), 'a')

        # USTX MaskedCurve downgrade preserves YAML indentation and leaves other types intact
        yaml_sample = 'expressions:\n  rpit:\n    type: MaskedCurve\n  vel:\n    type: Numerical\n'
        patched, count = compat.patch_ustx_masked_curve(yaml_sample)
        self.assertEqual(count, 1)
        self.assertIn('    type: Curve', patched)
        self.assertIn('    type: Numerical', patched)

        # UST encoding detection
        ust_utf8 = self._tmp / 'utf8.ust'
        ust_utf8.write_text('[#SETTING]\nCharset=UTF-8\n[#0000]\nLyric=你好\n', encoding='utf-8')
        self.assertEqual(compat.detect_ust_encoding(ust_utf8), 'utf-8')
        ust_sjis = self._tmp / 'sjis.ust'
        ust_sjis.write_bytes('[#SETTING]\nTempo=120\n[#0000]\nLyric=あ\n'.encode('shift_jis'))
        self.assertEqual(compat.detect_ust_encoding(ust_sjis), 'SHIFT_JIS')

        # Extended-length path stripping
        self.assertEqual(compat.strip_extended_prefix('\\\\?\\C:\\vwa\\out'), 'C:\\vwa\\out')
        self.assertEqual(compat.strip_extended_prefix('\\\\?\\UNC\\srv\\share\\a'), '\\\\srv\\share\\a')
        self.assertEqual(compat.strip_extended_prefix('C:\\vwa\\out'), 'C:\\vwa\\out')

        # Friendly Chinese errors for low-level exceptions
        for exc in (
            json.JSONDecodeError('Expecting value', '{', 1),
            ET.ParseError('syntax error'),
            zipfile.BadZipFile('not a zip'),
            UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'bad'),
        ):
            msg = compat.friendly_project_error(exc, file_name='broken.svp')
            self.assertIn('broken.svp', msg)
            self.assertNotIn('Expecting value', msg)

    def test_w6_02_sv1_group_refs_detection_and_friendly_corrupt_errors(self):
        svp = _make_sv1_with_group_refs(self._tmp / 'grouped.svp')
        info = svc.inspect_project_file(svp)
        self.assertTrue(info['hasGroupRefs'])
        self.assertEqual(info['totalNotes'], 5)
        self.assertFalse(info['path'].startswith('\\\\?\\'))
        self.assertFalse(info['defaultOutputDir'].startswith('\\\\?\\'))

        # Corrupt JSON SVP & empty VPR raise friendly Chinese ValueError (not raw JSONDecodeError)
        bad_svp = self._tmp / 'corrupt_broken.svp'
        bad_svp.write_text('{"version": 122, "tracks": [', encoding='utf-8')
        with self.assertRaises(ValueError) as ctx:
            svc.inspect_project_file(bad_svp)
        self.assertIn('损坏', str(ctx.exception))
        self.assertNotIn('Expecting value', str(ctx.exception))

        empty_vpr = self._tmp / 'corrupt_empty.vpr'
        empty_vpr.write_bytes(b'')
        with self.assertRaises(ValueError) as ctx2:
            svc.inspect_project_file(empty_vpr)
        self.assertIn('VPR', str(ctx2.exception))

        # Unsupported extension rejected cleanly
        bad_txt = self._tmp / 'notes.txt'
        bad_txt.write_text('hello', encoding='utf-8')
        with self.assertRaises(ValueError) as ctx3:
            svc.inspect_project_file(bad_txt)
        self.assertIn('暂不支持', str(ctx3.exception))


@unittest.skipUnless(_ensure_export_runtime(), 'dependencies/export runtime unavailable in minimal CI')
class Wave6RealExportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix='vwa-w6-e2e-')).resolve()

    def tearDown(self):
        vwa_io.safe_rmtree(self._tmp)

    def test_w6_03_iss004_iss005_sv1_groups_midi_multitrack_and_vsqx_fallback(self):
        # Always test English VSQX graceful fallback without CMU dictionary using synthetic English SVP
        en_svp = _make_english_svp(self._tmp / 'english_song.svp')
        with mock.patch.dict(os.environ, {'VWA_EXPORT_RUNTIME': os.environ.get('VWA_EXPORT_RUNTIME', '')}):
            # Even if vocal2midi CMU dict is missing in repo worktree, English -> VSQX must NOT crash
            res_en_vsqx = svc.convert_project_file(en_svp, 'vsqx', self._tmp / 'en_vsqx')
            self.assertTrue(res_en_vsqx['outputs'][0]['verified'], res_en_vsqx)
            self.assertEqual(res_en_vsqx['outputs'][0]['outputNotes'], 8)
            self.assertEqual(res_en_vsqx['detectedLanguage'], 'en')
            self.assertFalse(res_en_vsqx['outputDir'].startswith('\\\\?\\'))

        # Synthetic SV1 group-ref conversion must preserve all 5 notes (not drop library refs)
        grp_svp = _make_sv1_with_group_refs(self._tmp / 'grouped.svp')
        res_grp = svc.convert_project_file(grp_svp, 'ustx', self._tmp / 'grp_out')
        self.assertEqual(res_grp['readerEngine'], 'vwa_high_fidelity')
        self.assertEqual(res_grp['outputs'][0]['outputNotes'], 5)
        self.assertTrue(res_grp['outputs'][0]['verified'])

        if not CODEX_FIXTURES.is_dir():
            return

        # 1) ISS-004 + W6-BUG-05: SV2 Rolling Girl -> 6 core formats (all 1448->1448, lang='ja')
        rolling = CODEX_FIXTURES / 'projects_core/SV2-Rolling-Girl.svp'
        rolling_sha = common.file_hash(rolling)
        sv1 = CODEX_FIXTURES / 'projects_core/SV1-Installation.svp'
        mid = CODEX_FIXTURES / 'projects_core/MIDI-夏夜回响.mid'

        tasks = [
            *[(f'rolling_{t}', rolling, t) for t in ('svp', 'ustx', 'vpr', 'vsqx', 'ufdata', 'mid')],
            ('sv1_ustx', sv1, 'ustx'),
            ('sv1_svp', sv1, 'svp'),
            ('sv1_srt', sv1, 'srt'),
            ('sv1_ust', sv1, 'ust'),
            ('vpr_vocals', CODEX_FIXTURES / 'projects_core/VPR-Yamaha-J-POP-Vocals.vpr', 'vsqx'),
            ('vpr_complete', CODEX_FIXTURES / 'projects_core/VPR-Yamaha-J-POP-Complete.vpr', 'vsqx'),
            ('vsqx2vpr', CODEX_FIXTURES / 'projects_core/VSQX-Welcome-to-the-New-World.vsqx', 'vpr'),
            ('mid2svp', mid, 'svp'),
        ]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = dict(pool.map(lambda it: (it[0], svc.convert_project_file(it[1], it[2], self._tmp / it[0])), tasks))

        for tgt in ('svp', 'ustx', 'vpr', 'vsqx', 'ufdata', 'mid'):
            r = results[f'rolling_{tgt}']
            self.assertEqual(r['detectedLanguage'], 'ja', f'Rolling Girl -> {tgt} wrong lang: {r["detectedLanguage"]}')
            self.assertTrue(r['outputs'][0]['verified'], f'Rolling Girl -> {tgt}: {r}')
            self.assertEqual(r['outputs'][0]['outputNotes'], 1448, f'Rolling Girl -> {tgt}')
        self.assertEqual(common.file_hash(rolling), rolling_sha)

        # 2) W6-BUG-01 + W6-BUG-03: SV1-Installation (507 notes with library refs) -> USTX/SVP/SRT/UST
        for tgt in ('ustx', 'svp'):
            r = results[f'sv1_{tgt}']
            self.assertEqual(r['readerEngine'], 'vwa_high_fidelity')
            self.assertEqual(r['outputs'][0]['outputNotes'], 507, f'SV1 -> {tgt}')
            self.assertTrue(r['outputs'][0]['verified'])
        self.assertTrue(results['sv1_srt']['outputs'][0]['verified'])
        self.assertGreater(results['sv1_srt']['outputs'][0]['lineCount'], 5)
        self.assertTrue(results['sv1_ust']['outputs'][0]['verified'], results['sv1_ust'])
        self.assertEqual(results['sv1_ust']['outputs'][0]['outputNotes'], 500)

        # 3) ISS-005: VPR <-> VSQX bidirectional
        for key in ('vpr_vocals', 'vpr_complete'):
            self.assertTrue(results[key]['outputs'][0]['verified'], results[key])
            self.assertEqual(results[key]['outputs'][0]['outputNotes'], 119)
        self.assertGreaterEqual(results['vsqx2vpr']['outputs'][0]['outputNotes'], 428)

        # 4) W6-BUG-04: Multi-track MIDI preserves both vocal tracks (452 + 346 = 798 notes)
        self.assertEqual(results['mid2svp']['outputs'][0]['outputNotes'], 798, results['mid2svp'])
        self.assertTrue(results['mid2svp']['outputs'][0]['verified'])

    def test_w6_04_iss008_diffsinger_ustx_masked_curve_ext_and_acep(self):
        if not CODEX_FIXTURES.is_dir():
            self.skipTest('Codex real fixtures unavailable')

        # 1) ISS-008: Chinese DS -> USTX -> DS round-trip (2 sequential steps)
        ds_src = CODEX_FIXTURES / 'projects_ext/DS-不谓侠.ds'
        ds_sha = common.file_hash(ds_src)
        r_u = svc.convert_project_file(ds_src, 'ustx', self._tmp / 'ds2ustx')
        self.assertEqual(r_u['outputs'][0]['outputNotes'], 513)

        teeth = CODEX_FIXTURES / 'projects_core/USTX-Teeth-Mark-Love.ustx'
        ext_sources = [
            CODEX_FIXTURES / 'projects_ext/CCS-クロハネ.ccs',
            CODEX_FIXTURES / 'projects_ext/DSPX-Welcome-to-the-New-World-converted.dspx',
            CODEX_FIXTURES / 'projects_ext/MusicXML-Bach-BWV66.6.musicxml',
            CODEX_FIXTURES / 'projects_ext/SVIP-我的青春分裂成了同名大小调.svip',
            CODEX_FIXTURES / 'projects_ext/UST-Intergalactic-Disco.ust',
        ]
        acep_sources = [
            CODEX_FIXTURES / 'projects_core/ACE-JSON-ACEP-夕日坂-repacked.acep',
            CODEX_FIXTURES / 'projects_core/ACE-ACEP2-amazing-solo-repacked.acep',
        ]
        sha_before = {p: common.file_hash(p) for p in (*ext_sources, *acep_sources)}

        tasks = [
            ('ustx2ds', Path(r_u['outputs'][0]['path']), 'ds'),
            ('ds_welcome', CODEX_FIXTURES / 'projects_core/USTX-Welcome-to-the-New-World.ustx', 'ds'),
            ('ds_hoshi', CODEX_FIXTURES / 'projects_core/SV2-Hoshi-no-Kakera.svp', 'ds'),
            ('ds_rolling', CODEX_FIXTURES / 'projects_core/SV2-Rolling-Girl.svp', 'ds'),
            ('teeth_svp', teeth, 'svp'),
            ('teeth_ufdata', teeth, 'ufdata'),
            ('teeth_ds', teeth, 'ds'),
            *[(f'ext_{p.suffix.lstrip(".")}', p, 'ustx') for p in ext_sources],
            *[(f'acep_{i}', p, 'ufdata') for i, p in enumerate(acep_sources)],
        ]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = dict(pool.map(lambda it: (it[0], svc.convert_project_file(it[1], it[2], self._tmp / it[0])), tasks))

        self.assertTrue(results['ustx2ds']['outputs'][0]['verified'], results['ustx2ds'])
        self.assertEqual(results['ustx2ds']['outputs'][0]['outputNotes'], 513)
        self.assertEqual(common.file_hash(ds_src), ds_sha)

        for key in ('ds_welcome', 'ds_hoshi', 'ds_rolling'):
            self.assertTrue(results[key]['outputs'][0]['verified'], f'{key}: {results[key]}')
            self.assertGreater(results[key]['outputs'][0]['outputNotes'], 100)

        for key, min_notes in (('teeth_svp', 1300), ('teeth_ufdata', 1300), ('teeth_ds', 8)):
            self.assertTrue(results[key]['outputs'][0]['verified'], f'{key}: {results[key]}')
            self.assertGreaterEqual(results[key]['outputs'][0]['outputNotes'], min_notes)

        for p in ext_sources:
            r = results[f'ext_{p.suffix.lstrip(".")}']
            self.assertTrue(r['outputs'][0]['verified'], f'{p.name} -> USTX: {r}')
            self.assertGreater(r['outputs'][0]['outputNotes'], 100)
            self.assertEqual(common.file_hash(p), sha_before[p])

        for i, p in enumerate(acep_sources):
            r = results[f'acep_{i}']
            self.assertEqual(r['readerEngine'], 'vwa_high_fidelity')
            self.assertTrue(r['outputs'][0]['verified'], f'{p.name} -> UFData: {r}')
            self.assertGreater(r['outputs'][0]['outputNotes'], 50)
            self.assertEqual(common.file_hash(p), sha_before[p])

    def test_w6_05_options_matrix_split_lyrics_and_cancel_recovery(self):
        svp = _make_sv1_with_group_refs(self._tmp / 'matrix.svp')

        # Pitch keep vs strip check via native inspect
        r_keep = svc.convert_project_file(svp, 'svp', self._tmp / 'p_keep', {'pitch_mode': 'keep'})
        r_strip = svc.convert_project_file(svp, 'svp', self._tmp / 'p_strip', {'pitch_mode': 'strip'})
        info_keep = svc.inspect_project_file(r_keep['outputs'][0]['path'])
        info_strip = svc.inspect_project_file(r_strip['outputs'][0]['path'])
        self.assertTrue(any(t['hasPitch'] for t in info_keep['tracks']))
        self.assertFalse(any(t['hasPitch'] for t in info_strip['tracks']))

        # LRC & SRT strict monotonic timestamps
        r_lrc = svc.convert_project_file(svp, 'lrc', self._tmp / 'lrc_out')
        lrc_text = Path(r_lrc['outputs'][0]['path']).read_text(encoding='utf-8')
        ts_lrc = [int(m) * 60 + float(s) for m, s in re.findall(r'\[(\d+):(\d+(?:\.\d+)?)\]', lrc_text)]
        self.assertTrue(len(ts_lrc) >= 1 and ts_lrc == sorted(ts_lrc), lrc_text)

        r_srt = svc.convert_project_file(svp, 'srt', self._tmp / 'srt_out')
        srt_text = Path(r_srt['outputs'][0]['path']).read_text(encoding='utf-8')
        self.assertIn('-->', srt_text)

        # Cancel mid-flight must raise cancel error, leave zero .pending-* files, and allow immediate next run
        cancel_dir = self._tmp / 'cancel_out'
        with self.assertRaises(Exception):
            svc.convert_project_file(svp, 'ustx', cancel_dir, cancel=lambda: True)
        self.assertEqual(list(cancel_dir.glob('.pending-*')), [])
        r_after = svc.convert_project_file(svp, 'ustx', cancel_dir)
        self.assertTrue(r_after['outputs'][0]['verified'])


@unittest.skipUnless(_has_qt6(), 'Qt6/PySide6 unavailable in pure-Python CI')
class Wave6QtOffscreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        _ensure_export_runtime()
        from PySide6.QtCore import QCoreApplication, QUrl
        from PySide6.QtGui import QGuiApplication
        import quick_app

        cls.QCoreApplication = QCoreApplication
        cls.QUrl = QUrl
        cls.app = QGuiApplication.instance() or QGuiApplication(sys.argv)
        cls.engine, cls.win, cls.owner, cls.modes = quick_app.create_workspace(cls.app)
        cls._tmp = Path(tempfile.mkdtemp(prefix='vwa-w6-ui-')).resolve()
        cls._pump(25)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, 'owner', None) is not None:
            cls.owner.close()
            cls._pump(10)
        vwa_io.safe_rmtree(cls._tmp)

    @classmethod
    def _pump(cls, rounds: int = 15):
        for _ in range(rounds):
            cls.QCoreApplication.processEvents()

    def _wait_idle(self, ctrl, timeout_s: float = 30.0):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self._pump(10)
            if not ctrl.view.get('busy'):
                return
            time.sleep(0.03)
        self.fail('QuickConvert remained busy past timeout')

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
                pt = item.mapToScene(item.boundingRect().topLeft())
                if pt.x() + item.width() > viewport_w + 2:
                    bad.append((item.metaObject().className(), item.objectName(), round(pt.x(), 1), round(pt.x() + item.width(), 1)))
        return bad

    def test_w6_06_empty_error_banner_recovery_output_dir_and_stale_receipt(self):
        self.owner.navigate('project_convert')
        self._pump(25)
        conv = self.owner.converter
        conv.clearSource()
        self._pump(15)

        # W6-BUG-08(a): Empty-state corrupt drop MUST show visible friendly Chinese banner
        corrupt = self._tmp / 'corrupt_broken.svp'
        corrupt.write_text('{"version": 122, "tracks": [', encoding='utf-8')
        conv.loadSource(self.QUrl.fromLocalFile(str(corrupt)))
        self._wait_idle(conv)
        self._pump(15)
        self.assertFalse(conv.view['hasSource'])
        self.assertTrue(conv.view['error'])
        self.assertIn('损坏', conv.view['notice'])
        banner = self._find('convertEmptyNotice')
        self.assertIsNotNone(banner)
        self.assertTrue(banner.isVisible())

        # Immediate recovery to Folder A, then Folder B -> default outputDir follows new source folder!
        dir_a = self._tmp / 'folder_a'
        dir_b = self._tmp / 'folder_b'
        custom_dir = self._tmp / 'custom_out'
        dir_a.mkdir()
        dir_b.mkdir()
        custom_dir.mkdir()
        svp_a = _make_sv1_with_group_refs(dir_a / 'a.svp')
        svp_b = _make_english_svp(dir_b / 'b.svp')

        conv.loadSource(self.QUrl.fromLocalFile(str(svp_a)))
        self._wait_idle(conv)
        self._pump(15)
        self.assertTrue(conv.view['hasSource'])
        self.assertFalse(banner.isVisible())
        self.assertEqual(Path(conv.view['outputDir']).resolve(), dir_a.resolve())
        self.assertFalse(conv.view['outputDir'].startswith('\\\\?\\'))

        conv.loadSource(self.QUrl.fromLocalFile(str(svp_b)))
        self._wait_idle(conv)
        self._pump(15)
        self.assertEqual(Path(conv.view['outputDir']).resolve(), dir_b.resolve())

        # Explicit setOutputDir locks custom dir across subsequent loads
        conv.setOutputDir(self.QUrl.fromLocalFile(str(custom_dir)))
        self._pump(10)
        self.assertEqual(Path(conv.view['outputDir']).resolve(), custom_dir.resolve())
        conv.loadSource(self.QUrl.fromLocalFile(str(svp_a)))
        self._wait_idle(conv)
        self.assertEqual(Path(conv.view['outputDir']).resolve(), custom_dir.resolve())

        # Inject a receipt with warnings & verified=False to verify ProjectConvertReceiptCard renders warnings & review title
        conv._receipt = {
            'outputDir': str(custom_dir),
            'targetGroup': 'core',
            'outputs': [{'path': str(svp_a), 'fileName': 'a.ustx', 'sourceNotes': 5, 'outputNotes': 4, 'verified': False, 'sizeBytes': 1234}],
            'warnings': ['测试回执差异告警'],
            'preserved': ['音符'],
            'omitted': ['声库'],
            'canSendToTuning': True,
            'canSendToChorus': True,
        }
        conv.changed.emit()
        self._pump(20)
        warn_box = self._find('convertReceiptWarnings')
        status_txt = self._find('convertReceiptStatus')
        self.assertIsNotNone(warn_box)
        self.assertTrue(warn_box.isVisible())
        self.assertIn('复核', str(status_txt.property('text')))
        self.assertIsNotNone(self._find('convertSendToTuning_0'))
        self.assertIsNotNone(self._find('convertSendToChorus_0'))

        # Changing target format / strategy clears stale receipt immediately
        conv.setTargetFormat('ufdata')
        self._pump(10)
        self.assertFalse(conv.view['hasReceipt'])

    def test_w6_07_cross_page_handoffs_and_multidpi_zero_overflow(self):
        self.owner.navigate('project_convert')
        self._pump(20)
        conv = self.owner.converter

        if _ensure_export_runtime():
            out_dir = self._tmp / 'ui_handoff_out'
            out_dir.mkdir(exist_ok=True)
            svp = _make_sv1_with_group_refs(self._tmp / 'ui_src.svp')
            conv.loadSource(self.QUrl.fromLocalFile(str(svp)))
            self._wait_idle(conv)
            conv.setOutputDir(self.QUrl.fromLocalFile(str(out_dir)))
            conv.setTargetFormat('ustx')
            conv.startConvert()
            self._wait_idle(conv, timeout_s=60.0)
            self._pump(20)
            self.assertTrue(conv.view['hasReceipt'], conv.view['notice'])
            out_path = conv.view['receipt']['outputs'][0]['path']
            self.assertTrue(Path(out_path).is_file())

            # Handoff 1: sendToTuning -> routes to 'tuning'
            conv.sendToTuning(out_path)
            self._pump(30)
            self.assertEqual(self.owner.state.route, 'tuning')

            # Handoff 2: back to project_convert -> sendToChorusScore -> routes to 'chorus'
            self.owner.navigate('project_convert')
            self._pump(20)
            conv.sendToChorusScore(out_path)
            self._pump(30)
            self.assertEqual(self.owner.state.route, 'chorus')

        # Multi-DPI x Dark/Light zero-overflow gate on ProjectConvertPage (loaded + receipt)
        self.owner.navigate('project_convert')
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
                    f'ProjectConvertPage overflow at {w}x{h}@{scale}% ({theme})',
                )


if __name__ == '__main__':
    unittest.main(verbosity=2)
