"""Safety and portability contracts for SVP media handling."""

import hashlib
import struct
import json
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

from agent_media import (MediaError, apply_svp_metadata, copy_media_exclusive,
                         file_sha256, inspect_svp_media, relocate_svp_media)


def write_wav(path, seconds=0.1, value=b"\0\0"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(44100)
        stream.writeframes(value * int(44100 * seconds) * 2)
    return path


def duration(path):
    with wave.open(str(path), "rb") as stream:
        return stream.getnframes() / stream.getframerate()


def write_svp(path, media_name, *, duration_value=None, trailing_nul=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    project = {
        "renderConfig": {"filename": "untitled", "numChannels": 1},
        "time": {"tempo": [{"position": 0, "bpm": 120}], "meter": []},
        "tracks": [
            {"name": "Vocal", "mainRef": {"isInstrumental": False},
             "mainGroup": {"notes": [{"onset": 0, "duration": 480, "pitch": 60, "lyrics": "你"}]}},
            {"name": "伴奏", "mainRef": {"isInstrumental": True, "audio": {
                "filename": media_name,
                "duration": duration(path.parent / media_name) if duration_value is None else duration_value,
            }}, "mainGroup": {"notes": []}},
        ],
        "version": 100,
        "uuid": "fixture",
    }
    data = json.dumps(project, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(data + (b"\0" if trailing_nul else b""))
    return path


class AgentMediaTests(unittest.TestCase):
    def setUp(self):
        self.work = APP / "work"
        self.work.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="agent-media-", dir=self.work)
        self.root = Path(self.temporary.name)

    def tearDown(self):
        from common import native_path
        target=self.root.resolve();boundary=self.work.resolve()
        assert target.is_relative_to(boundary) and target!=boundary
        if Path(native_path(target)).exists():shutil.rmtree(native_path(target))
        self.temporary.cleanup()

    def test_float_wav_import_is_distinct_from_pcm_reference_analysis(self):
        from agent_control import Control
        from agent_media import audio_info
        source=self.root/'float-instrumental.wav'
        payload=struct.pack('<f',.25)*800
        header=b'RIFF'+struct.pack('<I',36+len(payload))+b'WAVEfmt '+struct.pack('<IHHIIHH',16,3,1,8000,32000,4,32)
        source.write_bytes(header+b'data'+struct.pack('<I',len(payload))+payload)
        control=object.__new__(Control);control.assets=[self.root];control.jobs=[];control.state=self.root/'state'
        for name in ('inputs','locks','artifacts'):(control.state/name).mkdir(parents=True,exist_ok=True)
        control.call_worker=lambda action,args:audio_info(args['path'])
        original=source.read_bytes();record=control.import_asset(source)
        self.assertEqual(record['kind'],'instrumental')
        self.assertEqual(record['sha256'],hashlib.sha256(original).hexdigest())
        self.assertEqual(Path(record['path']).read_bytes(),original)
        self.assertEqual(record['origin']['audio']['audio_format'],3)
        self.assertEqual(source.read_bytes(),original)

    def test_copy_to_deep_windows_output_uses_native_io_for_temporary_file(self):
        from common import native_path
        source=write_wav(self.root/'short/source.wav')
        root=self.root
        for index in range(6):root=root/('nested-'+str(index)+'x'*24)
        Path(native_path(root)).mkdir(parents=True)
        target=root/('c'*64+'.wav')
        result=copy_media_exclusive(source,target,allowed_roots=[self.root])
        self.assertEqual(result['sha256'],hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertTrue(Path(native_path(target)).is_file())

    def test_inspect_verifies_relative_media_sha_duration_and_vocals(self):
        audio = write_wav(self.root / "source" / "伴奏_instrumental.wav")
        svp = write_svp(self.root / "source" / "song.svp", audio.name)
        evidence = inspect_svp_media(svp)
        self.assertTrue(evidence["included"])
        self.assertEqual(evidence["count"], 1)
        self.assertEqual(evidence["sha256"], hashlib.sha256(audio.read_bytes()).hexdigest())
        self.assertAlmostEqual(evidence["duration_seconds"], duration(audio), places=12)
        self.assertEqual(evidence["relative_paths"], [audio.name])
        self.assertEqual(evidence["vocals"][0]["name"], "Vocal")
        self.assertTrue(evidence["readback_verified"])

    def test_duration_or_hash_claims_cannot_be_trusted(self):
        audio = write_wav(self.root / "source" / "media.wav")
        svp = write_svp(self.root / "source" / "wrong-duration.svp", audio.name,
                        duration_value=1.0)
        with self.assertRaises(MediaError) as raised:
            inspect_svp_media(svp)
        self.assertEqual(raised.exception.code, "media_duration_mismatch")

    def test_relocate_copies_beside_candidate_and_never_overwrites_unknown_media(self):
        source = self.root / "source"
        candidate = self.root / "candidate"
        audio = write_wav(source / "伴奏.wav", value=b"\x01\x00")
        source_svp = write_svp(source / "song.svp", audio.name)
        candidate_without_media = {
            "renderConfig": {"filename": "candidate"},
            "time": {"tempo": [{"position": 0, "bpm": 120}], "meter": []},
            "tracks": [
                {"name": "Vocal", "mainRef": {"isInstrumental": False},
                 "mainGroup": {"notes": [{"onset": 0, "duration": 480, "pitch": 60, "lyrics": "你"}]}},
                {"name": "伴奏", "mainRef": {"isInstrumental": True, "audio": {
                    "filename": "伴奏.wav", "duration": duration(audio)}}},
            ],
            "version": 100,
        }
        candidate.mkdir(parents=True)
        candidate_svp = candidate / "candidate.svp"
        candidate_svp.write_text(json.dumps(candidate_without_media, ensure_ascii=False), encoding="utf-8")
        vocal_before = json.dumps(candidate_without_media["tracks"][0], sort_keys=True)
        conflict = candidate / audio.name
        conflict.write_bytes(b"unknown")
        with self.assertRaises(MediaError) as raised:
            relocate_svp_media(source_svp, candidate_svp)
        self.assertEqual(raised.exception.code, "media_target_conflict")
        self.assertEqual(conflict.read_bytes(), b"unknown")

        conflict.unlink()
        evidence = relocate_svp_media(source_svp, candidate_svp)
        self.assertTrue(evidence["included"])
        self.assertEqual(evidence["count"], 1)
        self.assertEqual(evidence["sha256"], file_sha256(audio)["sha256"])
        self.assertTrue((candidate / audio.name).is_file())
        project = json.loads(candidate_svp.read_bytes().rstrip(b"\0"))
        self.assertEqual(json.dumps(project["tracks"][0], sort_keys=True), vocal_before)
        self.assertEqual(project["tracks"][1]["mainRef"]["audio"]["filename"], audio.name)

    def test_paths_outside_roots_and_parent_traversal_are_rejected(self):
        outside = write_wav(self.root / "outside.wav")
        source = self.root / "source"
        source.mkdir()
        intro = source / "song.svp"
        project = {
            "renderConfig": {}, "time": {}, "version": 100,
            "tracks": [{"name": "伴奏", "mainRef": {"isInstrumental": True, "audio": {
                "filename": "../outside.wav", "duration": duration(outside)}}}],
        }
        intro.write_text(json.dumps(project), encoding="utf-8")
        with self.assertRaises(MediaError) as raised:
            inspect_svp_media(intro)
        self.assertIn(raised.exception.code, {"media_path_outside_roots", "media_reference_invalid"})

        source_svp = source / "source.svp"
        source_svp.write_text(json.dumps(project), encoding="utf-8")
        with self.assertRaises(MediaError):
            relocate_svp_media(source_svp, source_svp, allowed_roots=[source])

    def test_no_instrumental_is_an_explicit_noop(self):
        svp = self.root / "no-media.svp"
        svp.write_text(json.dumps({
            "renderConfig": {}, "time": {}, "version": 100,
            "tracks": [{"name": "Vocal", "mainRef": {"isInstrumental": False},
                        "mainGroup": {"notes": []}}],
        }), encoding="utf-8")
        result = relocate_svp_media(svp, svp)
        self.assertFalse(result["included"])
        self.assertEqual(result["reason"], "source_has_no_supported_media")
        self.assertEqual(result["moved"], [])

    def test_metadata_changes_names_but_not_note_bytes(self):
        audio = write_wav(self.root / "media.wav")
        svp = write_svp(self.root / "song.svp", audio.name)
        before = json.loads(svp.read_bytes().rstrip(b"\0"))
        metadata = apply_svp_metadata(svp, track_names=["主唱"], project_name="试听包")
        after = json.loads(svp.read_bytes().rstrip(b"\0"))
        self.assertEqual(metadata["track_names"], ["主唱"])
        self.assertEqual(after["tracks"][0]["name"], "主唱")
        self.assertEqual(after["renderConfig"]["filename"], "试听包")
        self.assertEqual(after["tracks"][0]["mainGroup"], before["tracks"][0]["mainGroup"])
        self.assertEqual(after["tracks"][1]["mainRef"], before["tracks"][1]["mainRef"])

    def test_copy_target_outside_allowed_roots_is_rejected_before_write(self):
        source_root = self.root / "allowed"
        audio = write_wav(source_root / "伴奏.wav")
        target = self.root / "outside" / "伴奏.wav"
        with self.assertRaises(MediaError) as raised:
            copy_media_exclusive(audio, target, allowed_roots=[source_root])
        self.assertEqual(raised.exception.code, "media_path_outside_roots")
        self.assertFalse(target.exists())

    def test_file_sha_is_bounded_and_rejects_changed_media(self):
        target = write_wav(self.root / "large.wav", seconds=1.0, value=b"\x02\x00")
        evidence = file_sha256(target)
        self.assertEqual(evidence["sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertEqual(evidence["bytes"], target.stat().st_size)


if __name__ == "__main__":
    unittest.main()
