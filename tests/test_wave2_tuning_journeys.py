"""Wave 2 end-to-end user journey tests: Tuning & Tuning Workshop.

Covers Codex M2-02 / M2-03 / M2-04 gaps and locks W2-BUG-01..04:
1. Pure-Python hermetic tier (always executed, including minimal GitHub CI):
   - W2-01: Multi-format ProjectNotes / build_view format badges (SV1 SVP non-empty, SV2,
     USTX, VPR, VSQX, UFData, ACEP), long-intro initial_viewport auto-alignment, extended
     \\\\?\\ path stripping, and optional real 9-format Codex fixture verification.
   - W2-02: VPR/VSQX phoneme table lookup via VWA_EXPORT_RUNTIME fallback (W2-BUG-02).
   - W2-03: Style region execution + .tuning.json receipt hash chain + unselected-region
     exact note/lyric/parameter zero-tamper guarantee + session resume (Codex M2-03).
   - W2-05: PresetLibrary full lifecycle: save, export, import preview, import commit,
     conflict copy handling, user delete, built-in delete protection (Codex M2-04).
2. Qt6 Offscreen interactive tier (executed whenever PySide6 is available):
   - W2-02b: QuickTuning 5-format edit (SVP/USTX/VPR/VSQX/UFData) lyric edit, batch lyric,
     pitch draw, undo/redo + SV2/ACEP2 read-only & SHA-256 protection (Codex M2-02).
   - W2-04: QuickWorkshop style authoring, local default save/use/restore, saveStyle,
     non-destructive trialStyle -> applyStyle onto loaded project (Codex M2-04).
   - W2-06: QuickCalibration preflight/error recovery/cancel + \\\\?\\ extended path safety
     on QDesktopServices.openUrl for QuickTuning & QuickCalibration (W2-BUG-04).
   - W2-07: TuningPage & TuningWorkshopPage (style/expression, style/pitch, calibration,
     presets) multi-DPI (100/125/130% + narrow 784px) x Dark/Light zero-overflow gate (W2-BUG-01).
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock
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
from common import atomic_json, file_hash
import preset_library
from preset_documents import PresetError
from preset_builtin_documents import example_documents
import tuning_engine as engine
import tuning_session_store as session_store
from tuning_timeline import ProjectNotes
import tuning_workflow as workflow
import ustx_tuning
import vpr_tuning
import vwa_io


CODEX_FIXTURES = Path('C:/vwa/work/codex-e2e-20261126/fixtures')


def _ensure_export_runtime() -> bool:
    for candidate in (
        APP / 'dependencies/export',
        Path(os.environ.get('VWA_EXPORT_RUNTIME', '')),
        Path('C:/vwa/workspace/preview/vwa/dependencies/export'),
    ):
        if str(candidate) and (candidate / 'libresvip/core/lyric_phoneme/japanese/vocaloid_xsampa.py').is_file():
            if not (APP / 'dependencies/export/libresvip/__init__.py').is_file():
                os.environ['VWA_EXPORT_RUNTIME'] = str(candidate)
            return True
    return False


def _has_qt6() -> bool:
    for qt_root in (
        os.environ.get('VWA_TEST_QT6_ROOT', ''),
        str(APP / 'runtime/qt6'),
        'C:/vwa/workspace/preview/vwa/runtime/qt6',
    ):
        if qt_root and Path(qt_root).is_dir() and qt_root not in sys.path:
            sys.path.insert(0, qt_root)
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


def _sv_quarter() -> int:
    return 705_600_000


def _make_svp(notes: list[tuple[int, int, int, str]], *, version: int = 153, name: str = 'Lead') -> dict:
    q = _sv_quarter()
    return {
        'version': version,
        'time': {
            'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}],
            'tempo': [{'position': 0, 'bpm': 120.0}],
        },
        'library': [],
        'tracks': [{
            'name': name,
            'dispColor': 'ff7db235',
            'dispOrder': 0,
            'renderEnabled': False,
            'mixer': {'gainDecibel': 0.0, 'pan': 0.0, 'mute': False, 'solo': False, 'display': True},
            'mainGroup': {
                'name': 'main',
                'uuid': '00000000-0000-4000-8000-000000000001',
                'parameters': {
                    'pitchDelta': {'mode': 'cubic', 'points': []},
                    'vibratoEnv': {'mode': 'cubic', 'points': []},
                    'loudness': {'mode': 'cubic', 'points': []},
                    'tension': {'mode': 'cubic', 'points': []},
                    'breathiness': {'mode': 'cubic', 'points': []},
                    'voicing': {'mode': 'cubic', 'points': []},
                    'gender': {'mode': 'cubic', 'points': []},
                    'toneShift': {'mode': 'cubic', 'points': []},
                },
                'vocalModes': {},
                'notes': [
                    {'onset': onset * q, 'duration': dur * q, 'pitch': pitch, 'lyrics': lyric}
                    for onset, dur, pitch, lyric in notes
                ],
            },
            'mainRef': {
                'groupID': '00000000-0000-4000-8000-000000000001',
                'blickOffset': 0,
                'pitchOffset': 0,
                'isInstrumental': False,
                'database': {'name': 'Eleanor Forte', 'languageOverride': 'japanese'},
                'voice': {},
            },
            'groups': [],
        }],
    }


def _make_ustx(notes: list[tuple[int, int, int, str]], *, name: str = 'Wave2Target') -> bytes:
    payload = {
        'name': name,
        'ustx_version': '0.10',
        'resolution': 480,
        'tempos': [{'position': 0, 'bpm': 120}],
        'time_signatures': [{'bar_position': 0, 'beat_per_bar': 4, 'beat_unit': 4}],
        'tracks': [{'track_name': 'Vocal'}],
        'wave_parts': [],
        'voice_parts': [{
            'track_no': 0,
            'position': 0,
            'duration': 4800,
            'curves': [],
            'notes': [
                {'position': pos, 'duration': dur, 'tone': tone, 'lyric': lyric}
                for pos, dur, tone, lyric in notes
            ],
        }],
    }
    return ustx_tuning.encode(payload, b'')


def _make_vpr(path: Path, *, title: str = 'Wave2 VPR') -> Path:
    seq = {
        'version': {'major': 5, 'minor': 1, 'revision': 0},
        'vender': 'Yamaha Corporation',
        'title': title,
        'masterTrack': {
            'samplingRate': 44100,
            'loop': {'isEnabled': False, 'begin': 0, 'end': 7680},
            'tempo': {'isFolded': False, 'height': 0.0, 'global': {'isEnabled': False, 'value': 12000},
                      'events': [{'pos': 0, 'value': 12000}]},
            'timeSig': {'isFolded': False, 'events': [{'bar': 0, 'numer': 4, 'denom': 4}]},
            'volume': {'isFolded': False, 'height': 0.0, 'events': [{'pos': 0, 'value': 0}]},
        },
        'voices': [{'compID': 'VY1', 'name': 'VY1'}],
        'tracks': [{
            'type': 0, 'name': 'Vocal', 'color': 0, 'busNo': 0,
            'isFolded': False, 'height': 0.0,
            'volume': {'isFolded': False, 'height': 0.0, 'events': [{'pos': 0, 'value': 0}]},
            'panpot': {'isFolded': False, 'height': 0.0, 'events': [{'pos': 0, 'value': 0}]},
            'isMuted': False, 'isSoloMode': False,
            'parts': [{
                'pos': 0, 'duration': 3840, 'styleName': 'No Effect',
                'voice': {'compID': 'VY1', 'langID': 0},
                'controllers': [],
                'notes': [
                    {'lyric': 'あ', 'phoneme': 'a', 'isProtected': False, 'pos': 0, 'duration': 480, 'number': 60, 'velocity': 64},
                    {'lyric': 'い', 'phoneme': 'i', 'isProtected': False, 'pos': 480, 'duration': 480, 'number': 62, 'velocity': 64},
                ],
            }],
        }],
    }
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('Project/sequence.json', json.dumps(seq, ensure_ascii=False))
    return path


def _make_vsqx(path: Path, *, title: str = 'Wave2 VSQX') -> Path:
    xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<vsq4 xmlns="http://www.yamaha.co.jp/vocaloid/schema/vsq4/"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <vender><![CDATA[Yamaha corporation]]></vender>
  <version><![CDATA[4.0.0.3]]></version>
  <vVoiceTable>
    <vVoice>
      <bs>0</bs><pc>0</pc><id><![CDATA[VY1V4]]></id><name><![CDATA[VY1V4]]></name>
      <vPrm><bre>0</bre><bri>0</bri><cle>0</cle><gen>0</gen><ope>0</ope></vPrm>
    </vVoice>
  </vVoiceTable>
  <mixer>
    <masterUnit><oDev>0</oDev><rLvl>0</rLvl><vol>0</vol></masterUnit>
    <vsUnit><tNo>0</tNo><iGin>0</iGin><sLvl>-898</sLvl><sEnable>0</sEnable><m>0</m><s>0</s><pan>64</pan><vol>0</vol></vsUnit>
  </mixer>
  <masterTrack>
    <seqName><![CDATA[{title}]]></seqName>
    <seqComment><![CDATA[]]></seqComment>
    <resolution>480</resolution>
    <preMeasure>1</preMeasure>
    <timeSig><m>0</m><nu>4</nu><de>4</de></timeSig>
    <tempo><t>0</t><v>12000</v></tempo>
  </masterTrack>
  <vsTrack>
    <tNo>0</tNo>
    <name><![CDATA[Lead]]></name>
    <comment><![CDATA[]]></comment>
    <vsPart>
      <t>1920</t><playTime>3840</playTime>
      <name><![CDATA[Part1]]></name><comment><![CDATA[]]></comment>
      <sPlug><id><![CDATA[ACA9C502-A04B-42b5-B2EB-5CEA36D16FCE]]></id><name><![CDATA[VOCALOID2 Compatible Style]]></name><version><![CDATA[3.0.0.1]]></version></sPlug>
      <pStyle><v id="accent">50</v><v id="bendDep">8</v><v id="bendLen">0</v><v id="decay">50</v><v id="fallPort">0</v><v id="opening">127</v><v id="risePort">0</v></pStyle>
      <singer><t>0</t><bs>0</bs><pc>0</pc></singer>
      <note><t>0</t><dur>480</dur><n>60</n><v>64</v><y><![CDATA[あ]]></y><p><![CDATA[a]]></p></note>
      <note><t>480</t><dur>480</dur><n>62</n><v>64</v><y><![CDATA[い]]></y><p><![CDATA[i]]></p></note>
    </vsPart>
  </vsTrack>
</vsq4>
"""
    path.write_text(xml, encoding='utf-8')
    return path


def _make_ufdata(path: Path, *, name: str = 'Wave2 UFData') -> Path:
    doc = {
        'formatVersion': 1,
        'project': {
            'name': name,
            'tracks': [{
                'name': 'Lead',
                'notes': [
                    {'key': 60, 'tickOn': 0, 'tickOff': 480, 'lyric': 'la', 'phoneme': ''},
                    {'key': 62, 'tickOn': 480, 'tickOff': 960, 'lyric': 'ti', 'phoneme': ''},
                ],
                'pitch': {'ticks': [], 'values': [], 'isAbsolute': True},
            }],
            'timeSignatures': [{'measurePosition': 0, 'numerator': 4, 'denominator': 4}],
            'tempos': [{'tickPosition': 0, 'bpm': 120.0}],
            'measurePrefix': 0,
        },
    }
    atomic_json(path, doc)
    return path


class Wave2PurePythonJourneysTests(unittest.TestCase):
    def setUp(self):
        (APP / 'work').mkdir(parents=True, exist_ok=True)
        self._tmp_ctx = vwa_io.resilient_tempdir(APP / 'work', prefix='w2-')
        self.work = Path(self._tmp_ctx.__enter__())
        self.addCleanup(self._tmp_ctx.__exit__, None, None, None)

    def _build_view_for(self, project_obj: ProjectNotes) -> dict:
        active = project_obj.editable[0] if project_obj.editable else next(iter(project_obj.tracks))
        left, span = session_store.initial_viewport(project_obj, active)
        opts = copy.deepcopy(engine.DEFAULTS)
        opts['style_detail'] = {}
        return session_store.build_view(
            busy=False, project_obj=project_obj, candidate=None, preset=None, regions=[],
            targets=(), legacy=None, draft_conflict=None, draft_review=None, session=None,
            job='', session_parent=self.work, lyric_match_scope=[], lyric_match_index=-1,
            active_track=active, options=opts,
            indices_fn=lambda segments=False: [0], source_mode_reason_fn=lambda m, i: '',
            score_block='', pitch_block='', selected_region='',
            style_brush={'preset': 'natural', 'strength': 1.0, 'pitch': False, 'detail': {},
                         'technique': 'none', 'technique_strength': 0.0, 'technique_pitch': False,
                         'style_recipe': None},
            display_source='', mode='single', selected_tracks=(active,), score_tool='style',
            selected_note_view=None, selected_notes=(), lyric_context=[], query='',
            start=0.0, end=0.0, left=left, span=span, state='ready', message='',
            region_undo=[], region_redo=[],
            view_preferences={'snap': True, 'auto_apply_selection': False, 'parameter_open': False, 'beat_division': 4},
            origin=None, confirmed=False, plan=None, candidate_stale=False, saved_copy='',
            summary='', drafts_root=self.work, draft_description_fn=lambda d: '',
            preset_review=None, file_url_fn=lambda p: p,
        )

    def test_w2_01_formats_badges_long_intro_viewport_and_extended_path(self):
        """W2-01: SV1/SV2/USTX/VPR/VSQX/UFData badges non-empty + long-intro viewport + \\\\?\\ strip."""
        # Long-intro SV1 project: first vocal note starts at 48 beats = 24.0s (> 18s default span), song lasts to 46s
        sv1_long = self.work / 'sv1_long_intro.svp'
        atomic_json(sv1_long, _make_svp([(48, 2, 60, 'あ'), (50, 2, 62, 'い'), (90, 2, 64, 'う')]))
        sv1_short = self.work / 'sv1_short_intro.svp'
        atomic_json(sv1_short, _make_svp([(1, 2, 60, 'あ'), (3, 2, 62, 'い')]))
        sv2_path = self.work / 'sv2_sample.svp'
        atomic_json(sv2_path, _make_svp([(0, 2, 60, 'あ'), (2, 2, 62, 'い')], version=201))
        ustx_path = self.work / 'sample.ustx'
        ustx_path.write_bytes(_make_ustx([(0, 480, 60, 'la'), (480, 480, 62, 'ti')]))
        vpr_path = _make_vpr(self.work / 'sample.vpr')
        vsqx_path = _make_vsqx(self.work / 'sample.vsqx')
        ufdata_path = _make_ufdata(self.work / 'sample.ufdata')

        # SV1 long-intro badge & viewport alignment (W2-BUG-03)
        proj_long = ProjectNotes.load(sv1_long)
        view_long = self._build_view_for(proj_long)
        self.assertTrue(view_long['projectFormat'].startswith('SVP'), view_long['projectFormat'])
        self.assertGreaterEqual(view_long['viewportStart'], 22.0)
        self.assertLessEqual(view_long['viewportStart'], 24.0)

        # Short-intro stays anchored at project.minimum (0.0)
        proj_short = ProjectNotes.load(sv1_short)
        view_short = self._build_view_for(proj_short)
        self.assertEqual(view_short['viewportStart'], 0.0)
        self.assertEqual(view_short['projectFormat'], 'SVP · 153')

        # All other formats report non-empty projectFormat badges
        for path, prefix in (
            (sv2_path, 'SV2'),
            (ustx_path, 'USTX'),
            (vpr_path, 'VPR'),
            (vsqx_path, 'VSQX'),
            (ufdata_path, 'UFDATA'),
        ):
            before_sha = file_hash(path)
            proj = ProjectNotes.load(path)
            view = self._build_view_for(proj)
            self.assertTrue(view['projectFormat'].startswith(prefix), (path.name, view['projectFormat']))
            self.assertEqual(file_hash(path), before_sha)

        # Extended-path helper (W2-BUG-04)
        self.assertEqual(session_store.local_file_path(r'\\?\C:\vwa\work\test.svp'), r'C:\vwa\work\test.svp')
        self.assertEqual(session_store.local_file_path(r'\\?\UNC\srv\share\a.svp'), r'\\srv\share\a.svp')

        # Optional real 9-format Codex fixture audit when present on local machine
        if CODEX_FIXTURES.is_dir():
            real_cases = [
                ('SV1-Installation.svp', 'SVP'),
                ('SV2-Hoshi-no-Kakera.svp', 'SV2'),
                ('SV2-Rolling-Girl.svp', 'SV2'),
                ('USTX-Welcome-to-the-New-World.ustx', 'USTX'),
                ('VPR-Yamaha-J-POP-Vocals.vpr', 'VPR'),
                ('VSQX-Welcome-to-the-New-World.vsqx', 'VSQX'),
                ('UFData-D@rling.ufdata', 'UFDATA'),
                ('ACE-JSON-ACEP.acep', 'ACEP'),
                ('ACE-ACEP2.acep', 'ACEP'),
            ]
            for filename, prefix in real_cases:
                fpath = CODEX_FIXTURES / filename
                if not fpath.is_file():
                    continue
                before_sha = file_hash(fpath)
                proj = ProjectNotes.load(fpath)
                view = self._build_view_for(proj)
                self.assertTrue(view['projectFormat'].startswith(prefix), (filename, view['projectFormat']))
                active = proj.editable[0] if proj.editable else next(iter(proj.tracks))
                notes = proj._windows.get(active, ((), ()))[0]
                if notes:
                    first = notes[0]['start']
                    self.assertLessEqual(view['viewportStart'], first + 0.1)
                    self.assertGreaterEqual(view['viewportStart'] + view['viewportSpan'], first)
                self.assertEqual(file_hash(fpath), before_sha)

    def test_w2_02_vpr_phoneme_table_honors_export_runtime_env(self):
        """W2-02: vpr_tuning._score_phone_table falls back cleanly to VWA_EXPORT_RUNTIME (W2-BUG-02)."""
        empty_root = self.work / 'empty-app-root'
        empty_root.mkdir()
        fake_runtime = self.work / 'external-export-runtime'
        ja_file = fake_runtime / 'libresvip/core/lyric_phoneme/japanese/vocaloid_xsampa.py'
        ja_file.parent.mkdir(parents=True)
        ja_file.write_text("HIRAGANA_TO_XSAMPA = {'あ': 'a', 'ら': '4 a'}\n", encoding='utf-8')

        vpr_tuning._score_phone_table.cache_clear()
        with mock.patch.dict(os.environ, {'VWA_EXPORT_RUNTIME': str(fake_runtime)}):
            table = vpr_tuning._score_phone_table(empty_root, 'japanese', 'HIRAGANA_TO_XSAMPA')
            self.assertEqual(table['ら'], '4 a')

        # Also verify against real external export runtime if available
        if _ensure_export_runtime():
            vpr_tuning._score_phone_table.cache_clear()
            with mock.patch.object(vpr_tuning, 'ROOT', empty_root):
                self.assertTrue(vpr_tuning.score_phoneme(0, 'ら'))

    def test_w2_03_style_region_candidate_unselected_zero_tamper_and_resume(self):
        """W2-03 (Codex M2-03): Selected region tuned, unselected notes/lyrics/params preserved, receipt verified."""
        svp = self.work / 'two_phrases.svp'
        # Phrase A: beats 0..4 (0.0..2.0s), Phrase B: beats 10..14 (5.0..7.0s)
        original_doc = _make_svp([
            (0, 1, 60, 'あ'), (1, 1, 62, 'い'), (2, 1, 64, 'う'), (3, 1, 65, 'え'),
            (10, 1, 67, 'か'), (11, 1, 69, 'き'), (12, 1, 71, 'く'), (13, 1, 72, 'け'),
        ])
        atomic_json(svp, original_doc)
        before_sha = file_hash(svp)

        session_dir = self.work / 'session-m203'
        session = workflow.create_session(svp, session_dir)
        opts = copy.deepcopy(engine.DEFAULTS)
        opts['track_indices'] = [0]
        opts['style_regions'] = [{
            'track_index': 0,
            'start': 0.0,
            'end': 2.1,
            'preset': 'gentle',
            'strength': 1.0,
            'pitch': False,
        }]
        receipt = workflow.run_session(session, opts)
        self.assertTrue(receipt['receipt_verified'], receipt)
        self.assertEqual(file_hash(svp), before_sha)

        # Re-verify hash chain from disk
        reverified = workflow.verified_result(session)
        self.assertEqual(reverified['output_sha256'], receipt['output_sha256'])
        self.assertTrue(Path(reverified['receipt']).is_file())

        # Parse exported candidate and assert exact preservation of notes & unselected region
        exported = json.loads(Path(reverified['output']).read_text(encoding='utf-8'))
        orig_notes = original_doc['tracks'][0]['mainGroup']['notes']
        out_notes = exported['tracks'][0]['mainGroup']['notes']
        self.assertEqual(len(out_notes), len(orig_notes))
        for i in range(len(orig_notes)):
            for field in ('onset', 'duration', 'pitch', 'lyrics'):
                self.assertEqual(out_notes[i][field], orig_notes[i][field], (i, field))

        # Selected region (0..2.1s -> blicks 0..4.2*Q) must have parameter points,
        # while unselected phrase B (5.0..7.0s -> blicks >= 10*Q) must have NO non-zero loudness modification!
        q = _sv_quarter()
        loudness_pts = exported['tracks'][0]['mainGroup']['parameters']['loudness']['points']
        pairs = [(loudness_pts[i], loudness_pts[i + 1]) for i in range(0, len(loudness_pts), 2)]
        selected_nonzero = [v for t, v in pairs if t < int(4.5 * q) and abs(v) > 1e-3]
        unselected_nonzero = [v for t, v in pairs if t >= int(9.5 * q) and abs(v) > 1e-3]
        self.assertTrue(selected_nonzero, 'Expected loudness curve edits in selected region')
        self.assertFalse(unselected_nonzero, f'Unselected region was unexpectedly modified: {unselected_nonzero[:5]}')

        # Resume session from immutable input snapshot
        from tuning_timeline import TimelineDraftStore
        from style_drafts import DraftStore
        import threading
        resumed = session_store.resume_project(
            session,
            TimelineDraftStore(self.work / 'drafts-v2'),
            DraftStore(self.work / 'drafts-v1'),
            threading.Event(),
            default_options_fn=lambda: copy.deepcopy(engine.DEFAULTS),
        )
        self.assertEqual(resumed[0].sha256, before_sha)
        self.assertIsNotNone(resumed[4])
        self.assertTrue(resumed[4]['receipt_verified'])

    def test_w2_05_preset_library_export_import_commit_conflict_and_builtin_guard(self):
        """W2-05 (Codex M2-04 presets): Save -> Export -> Inspect -> Commit Import -> Copy Conflict -> Built-in Guard."""
        lib_a = preset_library.PresetLibrary(self.work / 'lib-a')
        lib_b = preset_library.PresetLibrary(self.work / 'lib-b')

        snap_a = lib_a.snapshot()
        builtins = [row for row in snap_a['entries'] if row['builtin']]
        self.assertGreaterEqual(len(builtins), 1)

        # Built-in delete protection
        with self.assertRaises(PresetError):
            lib_a.delete(builtins[0]['id'], builtins[0]['version'], expected_revision=snap_a['revision'])

        # Save a custom style recipe preset into lib_a + toggle favorite + rename
        examples = {doc['type']: copy.deepcopy(doc) for doc in example_documents()}
        style_doc = examples.get('style_recipe') or next(iter(examples.values()))
        style_doc['id'] = 'user.wave2.custom-style'
        style_doc['version'] = '1.0.0'
        style_doc['metadata']['name'] = 'Wave2 Custom Style'
        put_res = lib_a.put_document(style_doc, expected_revision=snap_a['revision'])
        self.assertEqual(put_res['entry']['id'], 'user.wave2.custom-style')
        lib_a.set_favorite('user.wave2.custom-style', '1.0.0', favorite=True, expected_revision=lib_a.snapshot()['revision'])
        self.assertTrue(any(e['id'] == 'user.wave2.custom-style' and e.get('favorite') for e in lib_a.snapshot()['entries']))

        # Export from lib_a
        export_path = self.work / 'wave2-style.vwa-preset.json'
        lib_a.export_file('user.wave2.custom-style', '1.0.0', export_path)
        self.assertTrue(export_path.is_file())

        # Preview / inspect import in lib_b -> commit import
        snap_b0 = lib_b.snapshot()
        imp1 = lib_b.import_file(export_path, expected_revision=snap_b0['revision'])
        self.assertEqual(imp1['entry']['id'], 'user.wave2.custom-style')

        # Identical SHA-256 re-import is idempotent; conflicting content with same ID+version
        # without as_copy must refuse silent overwrite; as_copy=True creates a distinct copy!
        snap_b1 = lib_b.snapshot()
        conflict_doc = copy.deepcopy(style_doc)
        conflict_doc['metadata']['name'] = 'Wave2 Conflicting Content'
        conflict_path = self.work / 'wave2-style-conflict.vwa-preset.json'
        atomic_json(conflict_path, conflict_doc)
        with self.assertRaises(PresetError):
            lib_b.import_file(conflict_path, expected_revision=snap_b1['revision'], as_copy=False)
        imp_copy = lib_b.import_file(conflict_path, expected_revision=snap_b1['revision'], as_copy=True)
        self.assertNotEqual(
            (imp_copy['entry']['id'], imp_copy['entry']['version']),
            ('user.wave2.custom-style', '1.0.0'),
        )
        lib_b.rename(imp_copy['entry']['id'], imp_copy['entry']['version'], alias='Wave2 Renamed Copy',
                     expected_revision=lib_b.snapshot()['revision'])
        self.assertTrue(any(e.get('alias') == 'Wave2 Renamed Copy' or e.get('name') == 'Wave2 Renamed Copy'
                            for e in lib_b.snapshot()['entries']))

        # User preset can be deleted cleanly
        snap_b2 = lib_b.snapshot()
        lib_b.delete('user.wave2.custom-style', '1.0.0', expected_revision=snap_b2['revision'])
        snap_b3 = lib_b.snapshot()
        self.assertFalse(any(e['id'] == 'user.wave2.custom-style' and e['version'] == '1.0.0' for e in snap_b3['entries']))
        self.assertTrue(any(e['id'] == imp_copy['entry']['id'] for e in snap_b3['entries']))


@unittest.skipUnless(_has_qt6(), 'PySide6 not available in minimal CI environment')
class Wave2Qt6InteractiveJourneysTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
        from PySide6.QtGui import QGuiApplication

        cls.app = QGuiApplication.instance() or QGuiApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.warnings: list[str] = []
        cls._prev_handler = qInstallMessageHandler(
            lambda kind, _ctx, text: cls.warnings.append(text)
            if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg)
            else None
        )

    def setUp(self):
        (APP / 'work').mkdir(parents=True, exist_ok=True)
        self._tmp_ctx = vwa_io.resilient_tempdir(APP / 'work', prefix='w2-qt-')
        self.work = Path(self._tmp_ctx.__enter__())
        self.addCleanup(self._tmp_ctx.__exit__, None, None, None)

    def _wait(self, label: str, predicate, timeout: float = 12.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.015)
        self.fail(f'Timeout waiting for {label}; qt_warnings={self.warnings[-5:]}')

    def test_w2_02b_04_06_quick_tuning_workshop_and_calibration_journeys(self):
        """W2-02b + W2-04 + W2-06: 5-format edits, Workshop style authoring->apply, Calibration & \\\\?\\ URL safety."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from quick_app import create_workspace

        engine_qml, window, bridge, _modes = create_workspace(self.app, viewport=(1280, 720), scale=100)
        try:
            tune = bridge.tuning
            workshop = bridge.workshop
            cal = bridge.calibration
            bridge.navigate('tuning')
            self.app.processEvents()

            # 1) Multi-format load + lyric edit + batch lyric + pitch draw + undo/redo (W2-02b)
            _ensure_export_runtime()
            salt = self.work.name
            svp_path = self.work / 'edit_sv1.svp'
            atomic_json(svp_path, _make_svp([(0, 1, 60, 'あ'), (1, 1, 62, 'い'), (2, 1, 64, 'う')], name=f'Lead-{salt}'))
            ustx_path = self.work / 'edit.ustx'
            ustx_path.write_bytes(_make_ustx([(0, 480, 60, 'la'), (480, 480, 62, 'ti'), (960, 480, 64, 'do')], name=f'Ustx-{salt}'))
            vpr_path = _make_vpr(self.work / 'edit.vpr', title=f'Vpr-{salt}')
            vsqx_path = _make_vsqx(self.work / 'edit.vsqx', title=f'Vsqx-{salt}')
            ufdata_path = _make_ufdata(self.work / 'edit.ufdata', name=f'Uf-{salt}')
            sv2_path = self.work / 'readonly_sv2.svp'
            atomic_json(sv2_path, _make_svp([(0, 1, 60, 'あ'), (1, 1, 62, 'い')], version=201, name=f'Sv2-{salt}'))

            has_export = _ensure_export_runtime()
            format_targets = [
                (svp_path, 'か', 'き', 'く', True),
                (ustx_path, 'ma', 'mi', 'mu', True),
                (ufdata_path, 'na', 'ni', 'nu', False),
            ]
            if has_export:
                format_targets.extend([
                    (vpr_path, 'ら', 'り', 'る', True),
                    (vsqx_path, 'ら', 'り', 'る', True),
                ])

            for fpath, new_lyric, batch_a, batch_b, can_draw in format_targets:
                before_sha = file_hash(fpath)
                tune.chooseProject(QUrl.fromLocalFile(str(fpath)))
                self._wait(f'load {fpath.name}', lambda p=fpath: tune.project is not None and not tune.view['busy'] and Path(tune.view['source']).name == p.name)
                self.assertTrue(tune.view['projectFormat'], (fpath.name, tune.view['projectFormat']))
                notes = list(tune.notes)
                self.assertGreaterEqual(len(notes), 2, fpath.name)
                n0, n1 = notes[0], notes[1]
                ok = tune.editNoteLyric(n0['id'], new_lyric)
                self.assertTrue(ok, (fpath.name, tune.view['message']))
                self.assertEqual(tune.view['noteEditCount'], 1)

                # Batch lyric preview + apply across 2 selected notes
                tune.selectNotes([n0['id'], n1['id']])
                preview = tune.previewLyricBatch(f'{batch_a} {batch_b}')
                self.assertTrue(preview.get('canApply'), (fpath.name, preview))
                self.assertTrue(tune.applyLyricBatch(), (fpath.name, preview))

                # Pitch draw on supported formats
                if can_draw and not n0.get('drawReason'):
                    mid = (n0['start'] + n0['end']) / 2.0
                    drawn = tune.drawPitchLine(n0['id'], n0['start'] + 0.05, 15.0, mid, 35.0)
                    self.assertTrue(drawn, (fpath.name, tune.view['message']))
                    self.assertGreaterEqual(tune.view['pitchStrokeCount'], 1)

                # Undo + Redo
                self.assertTrue(tune.view['canUndoRegions'], fpath.name)
                tune.undoRegions()
                self.assertTrue(tune.view['canRedoRegions'], fpath.name)
                tune.redoRegions()
                self.assertEqual(file_hash(fpath), before_sha)

            # SV2 read-only score protection
            sv2_sha = file_hash(sv2_path)
            tune.chooseProject(QUrl.fromLocalFile(str(sv2_path)))
            self._wait('load sv2', lambda: tune.project is not None and not tune.view['busy'] and 'SV2' in tune.view['projectFormat'])
            sv2_note = list(tune.notes)[0]
            self.assertFalse(tune.editNoteLyric(sv2_note['id'], 'か'))
            self.assertEqual(file_hash(sv2_path), sv2_sha)

            # 2) Workshop Style Authoring + Local Default + trialStyle -> applyStyle (W2-04)
            tune.chooseProject(QUrl.fromLocalFile(str(ustx_path)))
            self._wait('reload ustx for workshop', lambda: tune.project is not None and not tune.view['busy'] and Path(tune.view['source']).name == ustx_path.name)
            tune.setSessionLocation(QUrl.fromLocalFile(str(self.work / 'sessions')))
            tune.selectSpan(0.0, 1.0)
            bridge.navigate('tuning_workshop')
            workshop.setPane('style')
            workshop.setAuthorMethod('design')
            self.app.processEvents()

            templates = workshop.view['design']['templates']
            self.assertTrue(templates)
            # Pick a factory template (e.g. 'gentle', not 'none'/'library') so local default lifecycle is enabled
            factory_tpl = next((row['id'] for row in templates if row['id'] not in ('none', 'blank', 'library')), 'gentle')
            workshop.selectTemplate(factory_tpl)
            self.assertTrue(workshop.view['design'].get('isFactoryTemplate'), factory_tpl)
            workshop.selectDesignPanel('expression')
            workshop.selectDesignChannel('loudness')
            workshop.editDesign('loudness.note', 'onset', 2.5)
            workshop.selectDesignPanel('pitch')
            workshop.editDesign('onset', 'cents', -25.0)

            # Local default save -> useStyleDefault (applies to tuning) -> restoreStyleDefault
            workshop.saveStyleDefault()
            self._wait('save local default', lambda: not workshop.view['busy'] and workshop.view['design'].get('hasLocalDefault'))
            workshop.useStyleDefault()
            self._wait('use local default applied', lambda: not workshop.view['busy'] and tune.view['hasStyleRecipe'])
            bridge.navigate('tuning_workshop')
            workshop.setPane('style')
            workshop.restoreStyleDefault()
            self._wait('restore factory default', lambda: not workshop.view['busy'] and not workshop.view['design'].get('hasLocalDefault'))

            # Edit & trialStyle (non-destructive) -> applyStyle onto target project
            workshop.editDesign('loudness.note', 'onset', 3.0)
            workshop.edit('name', 'Wave2 Authored Style')
            workshop.trialStyle()
            self._wait('trial style ready', lambda: not workshop.view['busy'] and bool(workshop.view.get('trial')))
            self.assertEqual(len(tune.regions), 0, 'trialStyle must be non-destructive')
            workshop.applyStyle()
            self._wait('apply style to tuning region', lambda: not workshop.view['busy'] and len(tune.regions) == 1)

            # 3) W2-06: Calibration preflight/error recovery + \\?\ openUrl safety (W2-BUG-04)
            workshop.setPane('calibration')
            self.app.processEvents()
            bad_svp = self.work / 'bad.svp'
            bad_svp.write_text('{not json', encoding='utf-8')
            cal.chooseProject(QUrl.fromLocalFile(str(bad_svp)))
            self._wait('calibration invalid project error', lambda: not cal.worker.kind and bool(cal.view['message']))
            cal.chooseProject(QUrl.fromLocalFile(str(svp_path)))
            self._wait('calibration valid project recovery', lambda: not cal.worker.kind and len(cal.view['tracks']) == 1)
            cal.cancel()  # Safe no-op when idle

            opened_urls: list[QUrl] = []
            with mock.patch.object(QDesktopServices, 'openUrl', side_effect=lambda u: (opened_urls.append(u), True)[1]):
                cal.project = r'\\?\\'[:-1] + str(svp_path)
                cal.openFile('source')
                self.assertEqual(len(opened_urls), 1)
                self.assertNotIn('%3F', opened_urls[0].toString())
                self.assertEqual(Path(opened_urls[0].toLocalFile()), svp_path)

                # Verify QuickTuning.openCandidate also strips \\?\ prefix
                cand_file = self.work / 'candidate.ustx'
                cand_file.write_bytes(ustx_path.read_bytes())
                tune.candidate = {'output': r'\\?\\'[:-1] + str(cand_file), 'receipt_verified': True}
                with mock.patch.object(tune, '_verify_candidate', return_value=tune.candidate):
                    tune.openCandidate()
                    self._wait('openCandidate finishes', lambda: not tune.worker.kind and len(opened_urls) == 2)
                self.assertNotIn('%3F', opened_urls[1].toString())
                self.assertEqual(Path(opened_urls[1].toLocalFile()), cand_file)
        finally:
            from PySide6.QtCore import QCoreApplication, QEvent
            window.close()
            engine_qml.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()
            bridge.close()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()

    def test_w2_07_qml_offscreen_multi_dpi_narrow_and_theme_zero_overflow(self):
        """W2-07 (locks W2-BUG-01): Zero QML errors & zero horizontal overflow across DPIs, narrow 784px, Dark/Light."""
        from PySide6.QtCore import QPointF, QUrl
        from PySide6.QtQuick import QQuickItem
        from quick_app import create_workspace

        svp_path = self.work / 'visual_sv1.svp'
        atomic_json(svp_path, _make_svp([(0, 1, 60, 'あ'), (1, 1, 62, 'い')]))

        def collect_overflows(root_item: QQuickItem, max_w: float) -> list[tuple]:
            over = []
            def walk(item: QQuickItem):
                if isinstance(item, QQuickItem) and item.isVisible() and item.width() > 0 and item.height() > 0:
                    pos = item.mapToScene(QPointF(0, 0))
                    if pos.x() + item.width() > max_w + 2.0 and not item.clip():
                        over.append((item.objectName() or item.metaObject().className(),
                                     round(pos.x(), 1), round(pos.x() + item.width(), 1), max_w))
                    if not item.clip():
                        for child in item.childItems():
                            walk(child)
                elif isinstance(item, QQuickItem) and item.isVisible():
                    for child in item.childItems():
                        walk(child)
            walk(root_item)
            return over

        for vw, vh, scale, force_w, dark in (
            (1280, 720, 100, None, True),
            (1366, 768, 125, None, False),
            (1440, 900, 130, 784, True),
            (1440, 900, 130, 784, False),
        ):
            for _ in range(5):
                self.app.processEvents()
            before_warn = len(self.warnings)
            engine_qml, window, bridge, _modes = create_workspace(self.app, viewport=(vw, vh), scale=scale)
            try:
                if hasattr(bridge, 'setThemeMode'):
                    bridge.setThemeMode('dark' if dark else 'light')
                if force_w is not None:
                    window.setMinimumWidth(force_w)
                    window.setWidth(force_w)
                for _ in range(10):
                    self.app.processEvents()

                # Load SV1 project on tuning page so tuningProjectFormat badge is visible
                bridge.navigate('tuning')
                bridge.tuning.chooseProject(QUrl.fromLocalFile(str(svp_path)))
                self._wait(f'load sv1 visual {vw}@{scale}', lambda: bridge.tuning.project is not None and not bridge.tuning.view['busy'])
                for _ in range(10):
                    self.app.processEvents()

                # Check tuning page + all 4 workshop sub-views
                views = [
                    ('tuning', None, None),
                    ('tuning_workshop', 'style', 'expression'),
                    ('tuning_workshop', 'style', 'pitch'),
                    ('tuning_workshop', 'calibration', None),
                    ('tuning_workshop', 'presets', None),
                ]
                for target, pane, sub in views:
                    bridge.navigate(target)
                    if pane:
                        bridge.workshop.setPane(pane)
                    if sub:
                        bridge.workshop.selectDesignPanel(sub)
                    for _ in range(15):
                        self.app.processEvents()
                    over = collect_overflows(window.contentItem(), float(window.width()))
                    self.assertEqual(
                        over, [],
                        f'Overflow on {target}/{pane}/{sub} at {vw}x{vh}@{scale}% (w={window.width()}, dark={dark}): {over[:5]}',
                    )

                new_warnings = [w for w in self.warnings[before_warn:] if 'QML' in w or 'TypeError' in w or 'ReferenceError' in w]
                self.assertEqual(new_warnings, [], f'QML warnings at {vw}x{vh}@{scale}%: {new_warnings}')
            finally:
                from PySide6.QtCore import QCoreApplication, QEvent
                window.close()
                engine_qml.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()
                bridge.close()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main(verbosity=2)
