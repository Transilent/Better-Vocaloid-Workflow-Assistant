"""L0 unit & native inspection tests for the standalone Project Format Converter service."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import mido

APP = Path(__file__).resolve().parents[1]
SRC = APP / 'src' if (APP / 'src').is_dir() else APP
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import project_convert_service as svc


def _sample_svp(path: Path, *, version: int = 122, two_tracks: bool = True) -> Path:
    blick = 705_600_000 // 480
    tracks = [
        {
            'name': '主唱 Lead',
            'mainRef': {'isInstrumental': False, 'blickOffset': 0},
            'mainGroup': {
                'notes': [
                    {'onset': 0, 'duration': 480 * blick, 'pitch': 60, 'lyrics': '梦'},
                    {'onset': 480 * blick, 'duration': 480 * blick, 'pitch': 62, 'lyrics': '灯'},
                    {'onset': 960 * blick, 'duration': 480 * blick, 'pitch': 64, 'lyrics': '笼'},
                ],
                'parameters': {
                    'pitchDelta': {'mode': 'linear', 'points': [0, 0, 240 * blick, 35, 480 * blick, 0]},
                },
            },
        },
    ]
    if two_tracks:
        tracks.append({
            'name': '和声 Harmony',
            'mainRef': {'isInstrumental': False, 'blickOffset': 0},
            'mainGroup': {
                'notes': [
                    {'onset': 0, 'duration': 480 * blick, 'pitch': 64, 'lyrics': '梦'},
                    {'onset': 480 * blick, 'duration': 480 * blick, 'pitch': 67, 'lyrics': '灯'},
                ],
                'parameters': {},
            },
        })
    tracks.append({
        'name': '伴奏 Inst',
        'mainRef': {'isInstrumental': True, 'audio': {'filename': 'inst.wav'}},
        'mainGroup': {'notes': []},
    })
    doc = {
        'version': version,
        'time': {'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}], 'tempo': [{'position': 0, 'bpm': 128}]},
        'library': [],
        'tracks': tracks,
    }
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    return path


def _sample_ufdata(path: Path) -> Path:
    doc = {
        'formatVersion': 1,
        'project': {
            'name': 'UF Sample',
            'tracks': [
                {
                    'name': 'Lead',
                    'notes': [
                        {'key': 60, 'tickOn': 0, 'tickOff': 480, 'lyric': 'あ'},
                        {'key': 62, 'tickOn': 480, 'tickOff': 960, 'lyric': 'い'},
                    ],
                    'pitch': {'ticks': [0, 240, 480], 'values': [60.0, 60.5, 60.0], 'isAbsolute': True},
                }
            ],
            'timeSignatures': [{'measurePosition': 0, 'numerator': 4, 'denominator': 4}],
            'tempos': [{'tickPosition': 0, 'bpm': 120.0}],
            'measurePrefix': 0,
        },
    }
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    return path


def _sample_vsqx(path: Path) -> Path:
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
        '<vsq4 xmlns="http://www.yamaha.co.jp/vocaloid/schema/vsq4/">\n'
        '  <masterTrack>\n'
        '    <resolution>480</resolution>\n'
        '    <preMeasure>1</preMeasure>\n'
        '    <timeSig><m>0</m><nu>4</nu><de>4</de></timeSig>\n'
        '    <tempo><t>0</t><v>13200</v></tempo>\n'
        '  </masterTrack>\n'
        '  <vsTrack>\n'
        '    <tNo>0</tNo>\n'
        '    <name><![CDATA[Lead]]></name>\n'
        '    <vsPart>\n'
        '      <t>1920</t><playTime>3840</playTime>\n'
        '      <singer><t>0</t><bs>0</bs><pc>0</pc></singer>\n'
        '      <cc><t>0</t><v id="S">2</v></cc>\n'
        '      <cc><t>240</t><v id="P">1024</v></cc>\n'
        '      <note><t>0</t><dur>480</dur><n>60</n><v>64</v><y><![CDATA[あ]]></y><p><![CDATA[a]]></p></note>\n'
        '      <note><t>480</t><dur>480</dur><n>62</n><v>64</v><y><![CDATA[い]]></y><p><![CDATA[i]]></p></note>\n'
        '    </vsPart>\n'
        '  </vsTrack>\n'
        '</vsq4>\n'
    )
    path.write_text(xml, encoding='utf-8')
    return path


def _sample_midi(path: Path) -> Path:
    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage('track_name', name='Melody', time=0))
    track.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(116), time=0))
    track.append(mido.MetaMessage('lyrics', text='la', time=0))
    track.append(mido.Message('note_on', note=60, velocity=90, time=0))
    track.append(mido.Message('note_off', note=60, velocity=0, time=480))
    track.append(mido.MetaMessage('lyrics', text='ra', time=0))
    track.append(mido.Message('note_on', note=64, velocity=90, time=0))
    track.append(mido.Message('note_off', note=64, velocity=0, time=480))
    mid.save(str(path))
    return path


class ProjectConvertServiceTests(unittest.TestCase):
    def test_catalog_groups_and_acep_read_only_policy(self):
        cat = svc.catalog()
        core_out = {row['id'] for row in cat['outputCoreFormats']}
        lyrics_out = {row['id'] for row in cat['outputLyricsFormats']}
        ext_out = {row['id'] for row in cat['outputExtendedFormats']}
        readable = {row['id'] for row in cat['inputFormats']}

        self.assertEqual(core_out, {'svp', 'ustx', 'vpr', 'vsqx', 'ufdata', 'mid'})
        self.assertEqual(lyrics_out, {'lrc', 'srt'})
        self.assertEqual(ext_out, {'ust', 'svip', 'ccs', 'ds', 'dspx', 'musicxml'})
        # ACEP is strictly high-fidelity read-only (ACE export uses UFData)
        self.assertIn('acep', readable)
        self.assertNotIn('acep', core_out | lyrics_out | ext_out)
        # Importing service must never pull LibreSVIP or PySide6 into main process
        self.assertNotIn('libresvip', sys.modules)
        self.assertNotIn('PySide6', sys.modules)

    def test_native_inspection_svp_sv2_ufdata_and_midi(self):
        with tempfile.TemporaryDirectory(prefix='vwa-conv-l0-') as tmp:
            root = Path(tmp)
            svp = _sample_svp(root / 'demo.svp', version=122)
            info = svc.inspect_project_file(svp)
            self.assertEqual(info['format'], 'svp')
            self.assertEqual(info['engineSource'], 'native')
            self.assertFalse(info['sv2'])
            self.assertEqual(info['vocalTrackCount'], 2)
            self.assertEqual(info['instrumentalTrackCount'], 1)
            self.assertEqual(info['totalNotes'], 5)
            self.assertTrue(info['tracks'][0]['hasPitch'])
            self.assertTrue(info['tracks'][0]['hasLyrics'])

            sv2 = _sample_svp(root / 'demo_sv2.svp', version=189, two_tracks=False)
            info2 = svc.inspect_project_file(sv2)
            self.assertTrue(info2['sv2'])
            self.assertIn('SV2', info2['formatVersion'])
            self.assertTrue(any('Synthesizer V 2' in s for s in info2['sourceNotes']))

            uf = _sample_ufdata(root / 'demo.ufdata')
            info_uf = svc.inspect_project_file(uf)
            self.assertEqual(info_uf['format'], 'ufdata')
            self.assertEqual(info_uf['totalNotes'], 2)
            self.assertTrue(info_uf['tracks'][0]['hasPitch'])

            vsqx = _sample_vsqx(root / 'demo.vsqx')
            info_vsqx = svc.inspect_project_file(vsqx)
            self.assertEqual(info_vsqx['format'], 'vsqx')
            self.assertEqual(info_vsqx['totalNotes'], 2)
            self.assertTrue(info_vsqx['tracks'][0]['hasPitch'])
            self.assertTrue(info_vsqx['tracks'][0]['hasLyrics'])

            mid = _sample_midi(root / 'demo.mid')
            info_mid = svc.inspect_project_file(mid)
            self.assertEqual(info_mid['format'], 'mid')
            self.assertEqual(info_mid['totalNotes'], 2)
            self.assertTrue(info_mid['tracks'][0]['hasLyrics'])

    def test_precheck_warnings_and_non_clobbering_plan(self):
        with tempfile.TemporaryDirectory(prefix='vwa-plan-') as tmp:
            root = Path(tmp)
            svp = _sample_svp(root / '测试工程.svp', version=122)
            info = svc.inspect_project_file(svp)

            # Multi-track to LRC without split should warn
            w_lrc = svc.build_precheck_warnings(info, 'lrc', {'selected_tracks': [0, 1], 'split_tracks': False})
            self.assertTrue(any('单轨歌词' in w for w in w_lrc), w_lrc)

            # Target UFData should advise about instrumental omission & ACE compatibility
            w_uf = svc.build_precheck_warnings(info, 'ufdata', {'selected_tracks': [0], 'include_instrumental': True})
            self.assertTrue(any('伴奏' in w for w in w_uf), w_uf)
            self.assertTrue(any('ACE Studio' in w for w in w_uf), w_uf)

            # Output planning never clobbers source or existing files and sanitizes illegal chars
            same = svc.plan_output_paths(svp, 'svp', root)
            self.assertNotEqual(Path(same[0]['path']).resolve(), svp.resolve())
            self.assertTrue(same[0]['fileName'].endswith('_2.svp'))

            split = svc.plan_output_paths(
                svp, 'ustx', root, vocal_track_names=['Lead:A/B', 'Harmony*1'], split_tracks=True,
            )
            self.assertEqual(len(split), 2)
            self.assertNotIn(':', split[0]['fileName'])
            self.assertNotIn('/', split[0]['fileName'])
            self.assertNotIn('*', split[1]['fileName'])

            with self.assertRaises(ValueError):
                svc.plan_output_paths(svp, 'acep', root)


if __name__ == '__main__':
    unittest.main(verbosity=2)
