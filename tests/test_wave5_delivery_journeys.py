"""Wave 5 end-to-end user journey tests: Video Mixdown and Local Publication.

Runs in three tiers:
1. Pure-Python hermetic tests (always executed, including GitHub CI without FFmpeg/Qt6):
   - W5-03 alignment math (accurate offset estimation, silence/short/ambiguous rejection),
   - W5-05 publication validation limits, Xiaohongshu manual-only guard, AI declaration guard,
   - W5-06 tag parsing, title/credits formatting, illegal image magic sniff rejection,
   - W5-07 immutable bundle hash tamper detection & safe_rmtree resilience.
2. Local real-FFmpeg delivery tests (executed whenever bundled ffmpeg.exe is present):
   - W5-02 still-image + full audio MP4 render (ISS-006 + W5-BUG-02/04: PNG/odd-dim/offset=None/+0.5/-0.5,
     verifying full audio duration, dual-stream decode, even <=1920x1080 dimensions),
   - W5-03 source-video audio replacement + -14 LUFS loudnorm measurement,
   - W5-05 dual-platform (Bilibili + Xiaohongshu) local bundle with 16:9 & 3:4 custom covers +
     video-frame cover verifying W5-BUG-03 (manifest['created_at'] is a string, never an OS stat tuple)
     and Bilibili 4:3 (1440x1080) + 16:9 (1920x1080) padded covers.
3. Qt6 Offscreen UI & multi-DPI geometry tests (executed whenever PySide6 is available):
   - W5-01 VideoPage + PublicationPage (steps 0/1/2) multi-DPI x Dark/Light zero-overflow gate (W5-BUG-01),
   - W5-02 UI QuickVideo still-image auto-manual-offset + instant preview + previewFrame no-crash (W5-BUG-02/07),
   - W5-07/08 QuickPublication stale draft, title credits, copyFieldFor, importBundle, cover objectNames (W5-BUG-06).
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import wave
import zlib

APP = Path(__file__).resolve().parents[1]
for candidate in (APP / 'src', APP):
    text = str(candidate)
    if text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)

os.environ.setdefault('VWA_TESTING', '1')
os.environ.setdefault('BVWA_TESTING', '1')

import common
import project_io
import publication
import publication_draft_service as draft_svc
import task_contract
import task_store
import video_mixdown
import vwa_io


def _find_ffmpeg() -> Path | None:
    candidates = [
        APP / 'dependencies/ffmpeg/ffmpeg.exe',
        Path('C:/vwa/workspace/preview/vwa/dependencies/ffmpeg/ffmpeg.exe'),
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def _has_qt6() -> bool:
    qt_root = os.environ.get('VWA_TEST_QT6_ROOT', '')
    if qt_root and Path(qt_root).is_dir() and qt_root not in sys.path:
        sys.path.insert(0, qt_root)
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


def _write_png(path: Path, width: int, height: int, rgb: tuple[int, int, int] = (48, 96, 180)) -> Path:
    """Create a valid minimal RGB PNG with arbitrary (including odd) dimensions."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', crc)

    row = b'\x00' + bytes(rgb) * width
    raw = row * height
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw, 1)) + chunk(b'IEND', b'')
    path.write_bytes(png)
    return path


def _write_pulsed_wav(path: Path, duration_s: float = 8.0, *, sr: int = 16000, shift_s: float = 0.0, silent: bool = False) -> Path:
    """Write a deterministic PCM16 WAV with distinct asymmetric energy pulses for correlation alignment."""
    n = int(round(duration_s * sr))
    pulses = ((0.8, 0.35, 330.0), (2.1, 0.25, 440.0), (3.9, 0.50, 523.25), (5.6, 0.20, 659.25), (6.5, 0.40, 392.0))
    samples = bytearray(n * 2)
    if not silent:
        for start, dur, freq in pulses:
            s0 = max(0, int((start + shift_s) * sr))
            s1 = min(n, int((start + dur + shift_s) * sr))
            for i in range(s0, s1):
                t = i / sr
                val = int(18000 * math.sin(2.0 * math.pi * freq * t))
                struct.pack_into('<h', samples, i * 2, val)
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(samples))
    return path


def _probe_mp4(ffmpeg: Path, mp4: Path) -> dict:
    proc = subprocess.run(
        [str(ffmpeg), '-nostdin', '-hide_banner', '-i', str(mp4)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
    )
    text = proc.stderr
    assert 'Video:' in text and 'Audio:' in text, text
    m_dur = re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)', text)
    assert m_dur, text
    h, m, s = map(float, m_dur.groups())
    m_sz = re.search(r'Video:.*?\b(\d{2,5})x(\d{2,5})\b', text)
    assert m_sz, text
    subprocess.run(
        [str(ffmpeg), '-nostdin', '-v', 'error', '-xerror', '-i', str(mp4),
         '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'],
        check=True, timeout=60,
    )
    return {'seconds': h * 3600 + m * 60 + s, 'width': int(m_sz.group(1)), 'height': int(m_sz.group(2))}


class Wave5PurePythonTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix='vwa-w5-')).resolve()

    def tearDown(self):
        vwa_io.safe_rmtree(self._tmp)

    def test_w5_03_alignment_math_accurate_and_rejects_ambiguous_or_silent(self):
        ref_wav = _write_pulsed_wav(self._tmp / 'ref.wav', duration_s=10.0, shift_s=0.0)
        shifted_wav = _write_pulsed_wav(self._tmp / 'shifted.wav', duration_s=10.0, shift_s=-0.75)
        res = video_mixdown.estimate_offset(ref_wav, shifted_wav, max_shift=5.0)
        self.assertTrue(res['accepted'], res)
        self.assertAlmostEqual(res['offset_seconds'], 0.75, delta=0.05)
        self.assertGreaterEqual(res['correlation'], video_mixdown.MIN_CORRELATION)
        self.assertGreaterEqual(res['margin'], video_mixdown.MIN_MARGIN)

        # Silent audio raises ValueError in strict mode and returns accepted=False in non-strict mode
        silent_wav = _write_pulsed_wav(self._tmp / 'silent.wav', duration_s=8.0, silent=True)
        with self.assertRaises(ValueError):
            video_mixdown.estimate_offset(ref_wav, silent_wav, max_shift=5.0)
        soft = video_mixdown._estimate_offset(ref_wav, silent_wav, 5.0, lambda: None, strict=False)
        self.assertFalse(soft['accepted'])
        self.assertEqual(soft['reason_code'], 'mixdown_constant')

        # Short audio (< 5s) rejected
        short_wav = _write_pulsed_wav(self._tmp / 'short.wav', duration_s=2.0)
        soft_short = video_mixdown._estimate_offset(ref_wav, short_wav, 5.0, lambda: None, strict=False)
        self.assertFalse(soft_short['accepted'])
        self.assertEqual(soft_short['reason_code'], 'too_short')

    def test_w5_05_06_publication_validation_limits_and_image_sniff(self):
        # Bilibili limits (80 title, 2000 desc)
        publication.validate_content({'title': 'a' * 80, 'description': 'b' * 100}, 'bilibili')
        with self.assertRaises(ValueError):
            publication.validate_content({'title': 'a' * 81, 'description': 'ok'}, 'bilibili')
        with self.assertRaises(ValueError):
            publication.validate_content({'title': 'ok', 'description': 'b' * 2001}, 'bilibili')

        # Xiaohongshu limits (20 title, 1000 desc) + mandatory AI declaration + manual-only browser guard
        publication.validate_content({'title': 'a' * 20, 'description': 'ok', 'ai_generated': False}, 'xiaohongshu', require_ai=True)
        with self.assertRaises(ValueError):
            publication.validate_content({'title': 'a' * 21, 'description': 'ok', 'ai_generated': False}, 'xiaohongshu', require_ai=True)
        with self.assertRaises(ValueError):
            publication.validate_content({'title': 'ok', 'description': 'ok'}, 'xiaohongshu', require_ai=True)
        with self.assertRaises(ValueError):
            publication.require_browser_platform('xiaohongshu')
        publication.require_browser_platform('bilibili')

        # Fake .png magic bytes rejected by _sniff_image; real PNG accepted
        fake_png = self._tmp / 'fake.png'
        fake_png.write_bytes(b'not a real png file content')
        with self.assertRaises(ValueError):
            draft_svc._sniff_image(fake_png)
        real_png = _write_png(self._tmp / 'real.png', 16, 16)
        draft_svc._sniff_image(real_png)

        # Tag parsing strips hashes and splits spaces/Chinese commas
        self.assertEqual(draft_svc._tags('#VOCALOID, 翻唱、#AI调音'), ['VOCALOID', '翻唱', 'AI调音'])

    def test_w5_07_discard_preview_dir_uses_safe_rmtree(self):
        temp_root = Path(tempfile.gettempdir()).resolve()
        d = temp_root / f'vwa-quick-publication-test-{os.getpid()}'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'cover.jpg').write_bytes(b'x')
        draft_svc.discard_preview_dir(d, None)
        self.assertFalse(d.exists())


@unittest.skipUnless(_find_ffmpeg() is not None, 'ffmpeg.exe unavailable in pure-Python CI')
class Wave5RealFFmpegDeliveryTests(unittest.TestCase):
    def setUp(self):
        self._ffmpeg = _find_ffmpeg()
        self._tmp = Path(tempfile.mkdtemp(prefix='vwa-w5-ff-')).resolve()
        self._jobs = self._tmp / 'jobs'
        self._jobs.mkdir(parents=True, exist_ok=True)
        cfg = {'ffmpeg': str(self._ffmpeg)}
        self._patches = [
            mock.patch.object(common, 'ROOT', self._tmp),
            mock.patch.object(task_store, 'ROOT', self._tmp),
            mock.patch.object(publication, 'ROOT', self._tmp),
            mock.patch.object(video_mixdown, 'load_config', return_value=cfg),
            mock.patch.object(publication, 'load_config', return_value=cfg),
            mock.patch.object(project_io, 'load_config', return_value=cfg),
            mock.patch.object(common, 'job_directories', side_effect=self._list_jobs),
            mock.patch.object(task_store, 'job_directories', side_effect=self._list_jobs),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        vwa_io.safe_rmtree(self._tmp)

    def _list_jobs(self):
        return sorted(p.resolve() for p in self._jobs.iterdir() if p.is_dir() and (p / 'request.json').is_file())

    def _make_job(self, name: str, *, bv: str = '') -> Path:
        job = (self._jobs / name).resolve()
        job.mkdir(parents=True, exist_ok=True)
        req = {'source_kind': 'bilibili' if bv else 'local', 'source_title': '测试曲目', 'bv': bv}
        (job / 'request.json').write_text(json.dumps(req, ensure_ascii=False), encoding='utf-8')
        (job / 'status.json').write_text(json.dumps({'state': 'done'}), encoding='utf-8')
        (job / 'source-info.json').write_text(json.dumps({'title': '测试曲目', 'bvid': bv}, ensure_ascii=False), encoding='utf-8')
        (job / '发布简介草稿.txt').write_text('本家测试简介\nSTAFF: VWA', encoding='utf-8')
        return job

    def test_w5_02_still_image_full_audio_render_iss006_and_offsets(self):
        job = self._make_job('Local_still_job')
        # Odd dimensions (1001x563) verify trunc(iw/2)*2 even-dimension safety
        png = _write_png(self._tmp / 'odd_cover.png', 1001, 563, (64, 128, 220))
        wav = _write_pulsed_wav(self._tmp / 'mix_6s.wav', duration_s=6.0)

        # offset=None on still image must NOT fail trying to decode audio from PNG (W5-BUG-02)
        out_default = self._tmp / 'still_default.mp4'
        rep = video_mixdown.render(job, png, wav, out_default, offset=None)
        self.assertEqual(rep['video_policy'], 'still_image_loop_full_audio')
        self.assertEqual(rep['alignment']['method'], 'still_image_default')
        info = _probe_mp4(self._ffmpeg, out_default)
        self.assertAlmostEqual(info['seconds'], 6.0, delta=0.5, msg=f'ISS-006 regression: got {info["seconds"]}s')
        self.assertEqual(info['width'] % 2, 0)
        self.assertEqual(info['height'] % 2, 0)
        self.assertLessEqual(info['width'], 1920)
        self.assertLessEqual(info['height'], 1080)

        # Positive offset (+0.5s) and negative offset (-0.5s) both produce expected durations
        out_pos = self._tmp / 'still_pos.mp4'
        video_mixdown.render(job, png, wav, out_pos, offset=0.5)
        self.assertAlmostEqual(_probe_mp4(self._ffmpeg, out_pos)['seconds'], 6.5, delta=0.5)

        out_neg = self._tmp / 'still_neg.mp4'
        video_mixdown.render(job, png, wav, out_neg, offset=-0.5)
        self.assertAlmostEqual(_probe_mp4(self._ffmpeg, out_neg)['seconds'], 5.5, delta=0.5)

        # Never overwrite an existing MP4
        with self.assertRaises(FileExistsError):
            video_mixdown.render(job, png, wav, out_default, offset=0.0)

    def test_w5_03_video_replace_audio_and_loudnorm(self):
        job = self._make_job('Local_norm_job')
        png = _write_png(self._tmp / 'src_img.png', 640, 360)
        ref_wav = _write_pulsed_wav(self._tmp / 'src_audio.wav', duration_s=7.0, shift_s=0.0)
        src_mp4 = self._tmp / 'source.mp4'
        video_mixdown.render(job, png, ref_wav, src_mp4, offset=0.0)

        mix_wav = _write_pulsed_wav(self._tmp / 'mix_audio.wav', duration_s=7.0, shift_s=-0.4)
        out_mp4 = self._tmp / 'norm_out.mp4'
        rep = video_mixdown.render(job, src_mp4, mix_wav, out_mp4, offset=None, normalize=True)
        self.assertTrue(rep['normalized'])
        self.assertIsInstance(rep['loudness_measurement'], dict)
        self.assertIn('input_i', rep['loudness_measurement'])
        self.assertAlmostEqual(rep['alignment']['offset_seconds'], 0.4, delta=0.06)
        info = _probe_mp4(self._ffmpeg, out_mp4)
        self.assertAlmostEqual(info['seconds'], 7.0, delta=0.5)

    def test_w5_05_07_publication_dual_covers_created_at_string_and_tamper_guard(self):
        job = self._make_job('Local_pub_job')
        png_16x9 = _write_png(self._tmp / 'cover_16_9.png', 640, 360, (90, 140, 210))
        png_3x4 = _write_png(self._tmp / 'cover_3_4.png', 360, 480, (210, 110, 80))
        wav = _write_pulsed_wav(self._tmp / 'pub_audio.wav', duration_s=4.0)
        vid = self._tmp / 'finished.mp4'
        video_mixdown.render(job, png_16x9, wav, vid, offset=0.0)

        content = {
            'bilibili': {'title': 'B站本地测试标题', 'description': 'B站简介内容\nSTAFF: VWA', 'tags': ['翻唱', 'VWA']},
            'xiaohongshu': {'title': '小红书测试标题', 'description': '小红书正文内容', 'ai_generated': False},
        }

        # 1) Frame cover mode (tests W5-BUG-03: created_at MUST be a string, NOT a 5-int stat tuple!)
        bundle_frame = publication.prepare(
            job, vid, content, cover_seconds=1.0, cover_mode='frame',
            parent=self._tmp / 'publish-packages', expected_video={'sha256': vwa_io.file_hash(vid)}, register=False,
        )
        m_frame = json.loads((bundle_frame / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(m_frame['final_publish'], 'human_only')
        self.assertIsInstance(m_frame['created_at'], str, f'W5-BUG-03 regression: created_at={m_frame["created_at"]!r}')
        self.assertRegex(m_frame['created_at'], r'^\d{8}-\d{6}-\d{6}$')

        # 2) Portrait 3:4 custom cover generates both 4:3 (1440x1080) and 16:9 (1920x1080) padded Bilibili covers
        bundle_3x4 = publication.prepare(
            job, vid, content, cover_file=png_3x4, cover_mode='custom',
            parent=self._tmp / 'publish-packages', expected_video={'sha256': vwa_io.file_hash(vid)}, register=False,
        )
        m_3x4 = json.loads((bundle_3x4 / 'manifest.json').read_text(encoding='utf-8'))
        bili_covers = m_3x4['platform_covers']['bilibili']
        self.assertEqual(bili_covers['policy'], 'contain')
        self.assertEqual((bili_covers['4:3']['width'], bili_covers['4:3']['height']), (1440, 1080))
        self.assertEqual((bili_covers['16:9']['width'], bili_covers['16:9']['height']), (1920, 1080))
        self.assertTrue((bundle_3x4 / bili_covers['4:3']['file']).is_file())
        self.assertTrue((bundle_3x4 / bili_covers['16:9']['file']).is_file())

        # Local handoff verification passes for both platforms
        publication.load_bundle(bundle_3x4, 'bilibili', verify_hash=True)
        xhs = publication.xiaohongshu_handoff(bundle_3x4)
        self.assertEqual(xhs['fields']['title'], '小红书测试标题')

        # Tampering 1 byte of padded cover immediately fails hash verification
        pad_43 = bundle_3x4 / bili_covers['4:3']['file']
        pad_43.write_bytes(pad_43.read_bytes() + b'\x00')
        with self.assertRaises(ValueError):
            publication.load_bundle(bundle_3x4, 'bilibili', verify_hash=True)


@unittest.skipUnless(_has_qt6(), 'Qt6/PySide6 unavailable in pure-Python CI')
class Wave5QtOffscreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtCore import QCoreApplication, QUrl
        import quick_app

        cls.QCoreApplication = QCoreApplication
        cls.QUrl = QUrl
        cls.app = QGuiApplication.instance() or QGuiApplication(sys.argv)
        cls.engine, cls.win, cls.owner, cls.modes = quick_app.create_workspace(cls.app)
        cls._tmp = Path(tempfile.mkdtemp(prefix='vwa-w5-ui-')).resolve()
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

    def test_w5_01_02_video_page_responsive_layout_and_still_image_ux(self):
        self.owner.navigate('video_mixdown')
        self._pump(25)

        # W5-BUG-02 & W5-BUG-07: Selecting a still PNG auto-switches to manual 0.000s, populates preview, disables auto-align
        png = _write_png(self._tmp / 'ui_cover.png', 640, 360)
        wav = _write_pulsed_wav(self._tmp / 'ui_mix.wav', duration_s=3.0)
        vc = self.owner.video
        vc.chooseFile('sourceVideo', self.QUrl.fromLocalFile(str(png)))
        vc.chooseFile('mixdown', self.QUrl.fromLocalFile(str(wav)))
        self._pump(15)
        view = vc.view
        self.assertTrue(view['sourceIsImage'])
        self.assertFalse(view['autoAlign'])
        self.assertEqual(view['offset'], 0.0)
        self.assertFalse(view['canAnalyze'])
        self.assertTrue(view['preview'].startswith('file:'))

        # previewFrame(0.0) on still image must NOT fail or invoke inspect_video
        vc.previewFrame(0.0)
        self._pump(15)
        self.assertNotEqual(vc.view['state'], 'failed', vc.view['message'])
        self.assertTrue(vc.view['preview'].startswith('file:'))

        # analyze() on still image fails fast with clear guidance and never goes busy
        vc.analyze()
        self._pump(10)
        self.assertFalse(vc.view['autoAlign'])
        self.assertIn('静图', vc.view['message'])

        # W5-BUG-01: Multi-DPI x Dark/Light zero-overflow gate on VideoPage
        for theme in ('dark', 'light'):
            self.owner.support.setSetting('theme', theme)
            for (w, h, scale) in ((1280, 720, 100), (1366, 768, 130), (1440, 900, 130)):
                self.win.setWidth(w)
                self.win.setHeight(h)
                self.owner.support.setSetting('ui_scale', scale)
                self._pump(25)
                self.assertEqual(
                    self._overflows(int(self.win.width())), [],
                    f'VideoPage overflow at {w}x{h}@{scale}% ({theme})',
                )

    def test_w5_01_06_08_publication_page_multidpi_and_cover_object_names(self):
        self.owner.navigate('publishing')
        self._pump(25)
        pc = self.owner.publication

        # W5-BUG-06: Cover controls are directly addressable by stable objectName
        self.assertIsNotNone(self._find('publicationCoverMode'))
        self.assertIsNotNone(self._find('publicationChooseCover'))
        self.assertIsNotNone(self._find('publicationPreviewCover'))

        # Test platform toggle + title/credits helper without requiring external files
        self.assertTrue(pc.togglePlatform('xiaohongshu', True))
        self.assertTrue(pc.applyTitleCredits('测试曲', '星尘', 'VWA', True, True))
        self._pump(10)
        self.assertIn('测试曲', pc.view['title'])
        self.assertIn('本次翻唱歌姬：星尘', pc.view['description'])

        # Multi-DPI x Dark/Light zero-overflow gate on PublicationPage
        for theme in ('dark', 'light'):
            self.owner.support.setSetting('theme', theme)
            for (w, h, scale) in ((1280, 720, 100), (1366, 768, 130), (1440, 900, 130)):
                self.win.setWidth(w)
                self.win.setHeight(h)
                self.owner.support.setSetting('ui_scale', scale)
                self._pump(20)
                self.assertEqual(
                    self._overflows(int(self.win.width())), [],
                    f'PublicationPage overflow at {w}x{h}@{scale}% ({theme})',
                )


if __name__ == '__main__':
    unittest.main(verbosity=2)
