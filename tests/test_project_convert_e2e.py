"""L1 end-to-end tests running the real isolated LibreSVIP converter subprocess across formats."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]
SRC = APP / 'src' if (APP / 'src').is_dir() else APP
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import project_convert_service as svc


def _write_svp(path: Path, *, version: int = 122) -> Path:
    blick = 705_600_000 // 480
    doc = {
        'version': version,
        'time': {'meter': [{'index': 0, 'numerator': 4, 'denominator': 4}], 'tempo': [{'position': 0, 'bpm': 120}]},
        'library': [],
        'tracks': [
            {
                'name': '主唱',
                'mainRef': {'isInstrumental': False, 'blickOffset': 0},
                'mainGroup': {
                    'notes': [
                        {'onset': 0, 'duration': 480 * blick, 'pitch': 60, 'lyrics': '你'},
                        {'onset': 480 * blick, 'duration': 480 * blick, 'pitch': 62, 'lyrics': '好'},
                        {'onset': 960 * blick, 'duration': 480 * blick, 'pitch': 64, 'lyrics': '世'},
                        {'onset': 1440 * blick, 'duration': 480 * blick, 'pitch': 67, 'lyrics': '界'},
                    ],
                    'parameters': {
                        'pitchDelta': {'mode': 'linear', 'points': [0, 0, 240 * blick, 45, 480 * blick, 0]},
                    },
                },
            },
            {
                'name': '和声',
                'mainRef': {'isInstrumental': False, 'blickOffset': 0},
                'mainGroup': {
                    'notes': [
                        {'onset': 0, 'duration': 480 * blick, 'pitch': 64, 'lyrics': '你'},
                        {'onset': 480 * blick, 'duration': 480 * blick, 'pitch': 67, 'lyrics': '好'},
                    ],
                    'parameters': {},
                },
            },
        ],
    }
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    return path


class ProjectConvertE2ETests(unittest.TestCase):
    def test_core_format_matrix_split_and_lyrics_export(self):
        if not svc.export_runtime_available():
            self.skipTest('dependencies/export runtime not present')
        with tempfile.TemporaryDirectory(prefix='vwa-conv-e2e-') as tmp:
            root = Path(tmp)
            out_dir = root / 'out'
            svp = _write_svp(root / 'song.svp', version=122)

            # 1) SVP -> USTX -> VPR -> VSQX -> UFData -> MIDI (keep notes & verify)
            r_ustx = svc.convert_project_file(svp, 'ustx', out_dir, {'selected_tracks': [0, 1]})
            self.assertEqual(len(r_ustx['outputs']), 1)
            self.assertTrue(r_ustx['outputs'][0]['verified'])
            self.assertEqual(r_ustx['outputs'][0]['outputNotes'], 6)

            ustx_path = Path(r_ustx['outputs'][0]['path'])
            r_vpr = svc.convert_project_file(ustx_path, 'vpr', out_dir, {'selected_tracks': [0]})
            self.assertTrue(r_vpr['outputs'][0]['verified'])
            self.assertEqual(r_vpr['outputs'][0]['outputNotes'], 4)

            vpr_path = Path(r_vpr['outputs'][0]['path'])
            r_vsqx = svc.convert_project_file(vpr_path, 'vsqx', out_dir, {'selected_tracks': [0]})
            self.assertTrue(r_vsqx['outputs'][0]['verified'])
            self.assertEqual(r_vsqx['outputs'][0]['outputNotes'], 4)

            r_uf = svc.convert_project_file(svp, 'ufdata', out_dir, {'selected_tracks': [0], 'pitch_mode': 'strip'})
            self.assertTrue(r_uf['outputs'][0]['verified'])
            self.assertEqual(r_uf['outputs'][0]['outputNotes'], 4)

            r_mid = svc.convert_project_file(svp, 'mid', out_dir, {'selected_tracks': [0]})
            self.assertTrue(r_mid['outputs'][0]['verified'])
            self.assertEqual(r_mid['outputs'][0]['outputNotes'], 4)

            # 2) Multi-track split export (2 tracks -> 2 separate USTX files)
            r_split = svc.convert_project_file(
                svp, 'ustx', root / 'split', {'selected_tracks': [0, 1], 'split_tracks': True},
            )
            self.assertEqual(len(r_split['outputs']), 2)
            self.assertEqual([o['outputNotes'] for o in r_split['outputs']], [4, 2])
            self.assertTrue(all(o['verified'] for o in r_split['outputs']))

            # 3) Lyrics export: LRC & SRT
            r_lrc = svc.convert_project_file(svp, 'lrc', out_dir, {'selected_tracks': [0]})
            self.assertTrue(r_lrc['outputs'][0]['verified'])
            lrc_text = Path(r_lrc['outputs'][0]['path']).read_text(encoding='utf-8')
            self.assertIn('你', lrc_text)
            self.assertIn('[00:', lrc_text)

            r_srt = svc.convert_project_file(svp, 'srt', out_dir, {'selected_tracks': [0]})
            self.assertTrue(r_srt['outputs'][0]['verified'])
            srt_text = Path(r_srt['outputs'][0]['path']).read_text(encoding='utf-8')
            self.assertIn('-->', srt_text)
            self.assertIn('你', srt_text)

            # 4) SV2 high-fidelity reader bridge -> USTX & UFData
            sv2 = _write_svp(root / 'song_sv2.svp', version=189)
            r_sv2 = svc.convert_project_file(sv2, 'ustx', root / 'sv2_out', {'selected_tracks': [0]})
            self.assertEqual(r_sv2['readerEngine'], 'vwa_high_fidelity')
            self.assertTrue(r_sv2['outputs'][0]['verified'])
            self.assertEqual(r_sv2['outputs'][0]['outputNotes'], 4)
            # Parent process still never imported libresvip directly
            self.assertNotIn('libresvip', sys.modules)


if __name__ == '__main__':
    unittest.main(verbosity=2)
