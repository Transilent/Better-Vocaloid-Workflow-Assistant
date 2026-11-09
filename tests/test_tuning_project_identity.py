"""Acceptance tests for exact SVP candidate-origin sidecars."""

import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
TEST_TEMP = APP / "work/acceptance/test-temp"
TEST_TEMP.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(APP / "src"))

import tuning_project_identity as identity


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_project(root, name="candidate.svp"):
    path = root / name
    raw = b'{"track":["private"],"bytes":1}\x00'
    path.write_bytes(raw)
    return path, raw


def project(path, raw=None, *, declared=None):
    return {"path": str(path), "sha256": declared or sha(raw if raw is not None else path.read_bytes())}


def style_receipt(candidate, *, source_sha=None, output_sha=None):
    source_sha = source_sha or ("1" * 64)
    output_sha = output_sha or sha(candidate.read_bytes())
    return {
        "schema": 1,
        "kind": "vwa.style-candidate",
        "version": 7,
        "preset": "gentle",
        "label": "温柔",
        "category": "emotion",
        "strength": .6,
        "density_mode": "dense",
        "track_indices": [0],
        "status": "applied",
        "experimental": True,
        "source_content_sha256": source_sha,
        "output_content_sha256": "3" * 64,
        "source_sha256": source_sha,
        "output_sha256": output_sha,
        "producer_sha256": "4" * 64,
        "pitch_producer_sha256": None,
        "tracks": [],
        "pitch": {"enabled": False, "status": "not_selected"},
    }


def tuning_receipt(candidate, root, *, source_sha=None, output_sha=None):
    source_sha = source_sha or ("5" * 64)
    output_sha = output_sha or sha(candidate.read_bytes())
    return {
        "schema": 1,
        "kind": "vwa.tuning-result",
        "session": str(root / "session/session.json"),
        "signature": "6" * 64,
        "source_sha256": source_sha,
        "output": str(candidate),
        "output_sha256": output_sha,
        "output_bytes": candidate.stat().st_size,
        "options": {"track_indices": [0], "expression_density": "original"},
        "dependencies": {"files": {}, "code": {}},
        "report": {"status": "applied"},
        "status": "applied",
        "cached": False,
        "receipt": str(candidate) + ".tuning.json",
        "project_commit_count": 1,
        "serialized_content_sha256": "7" * 64,
        "commit_state": "receipted",
        "receipt_verified": True,
    }


def write_receipt(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path.read_bytes()


class CandidateOriginTests(unittest.TestCase):
    def test_supplied_hash_cannot_replace_current_project_bytes(self):
        with tempfile.TemporaryDirectory(prefix='candidate-origin-',dir=TEST_TEMP) as temporary:
            root=Path(temporary);candidate,raw=write_project(root)
            write_receipt(Path(str(candidate)+'.style.json'),style_receipt(candidate))
            good=identity.inspect_candidate_origin(project(candidate,raw),sha256=sha(raw))
            self.assertTrue(good['verified'])
            candidate.write_bytes(raw+b' ')
            bad=identity.inspect_candidate_origin(project(candidate,raw),sha256=sha(raw))
            self.assertFalse(bad['verified'])
            self.assertEqual(bad['candidate_sha256'],sha(candidate.read_bytes()))
            self.assertIn('调用方',bad['reason'])

    def test_real_style_and_tuning_shapes_verify_current_bytes(self):
        with tempfile.TemporaryDirectory(prefix="candidate-origin-", dir=TEST_TEMP) as temporary:
            root = Path(temporary)
            candidate, raw = write_project(root)
            info = project(candidate, raw)

            style_path = Path(str(candidate) + ".style.json")
            style_raw = write_receipt(style_path, style_receipt(candidate))
            style = identity.inspect_candidate_origin(info)
            self.assertEqual(style, {
                "kind": "style",
                "verified": True,
                "candidate_sha256": sha(raw),
                "source_sha256": "1" * 64,
                "receipt_sha256": sha(style_raw),
                "receipt_name": candidate.name + ".style.json",
            })

            style_path.unlink()
            tuning_path = Path(str(candidate) + ".tuning.json")
            tuning_raw = write_receipt(tuning_path, tuning_receipt(candidate, root))
            tuning = identity.inspect_candidate_origin(info)
            self.assertEqual(tuning, {
                "kind": "tuning",
                "verified": True,
                "candidate_sha256": sha(raw),
                "source_sha256": "5" * 64,
                "receipt_sha256": sha(tuning_raw),
                "receipt_name": candidate.name + ".tuning.json",
            })

    def test_project_without_exact_sidecars_returns_none(self):
        with tempfile.TemporaryDirectory(prefix="candidate-origin-", dir=TEST_TEMP) as temporary:
            root = Path(temporary)
            candidate, raw = write_project(root)
            replacement_style = root / (candidate.stem + ".style.json")
            write_receipt(replacement_style, style_receipt(candidate))
            self.assertIsNone(identity.inspect_candidate_origin(project(candidate, raw)))

    def test_changed_candidate_bytes_are_unverified_even_with_stale_inspect_result(self):
        with tempfile.TemporaryDirectory(prefix="candidate-origin-", dir=TEST_TEMP) as temporary:
            root = Path(temporary)
            candidate, raw = write_project(root)
            stale = project(candidate, raw)
            receipt_raw = write_receipt(Path(str(candidate) + ".style.json"),
                                        style_receipt(candidate))
            candidate.write_bytes(raw + b"changed")

            result = identity.inspect_candidate_origin(stale)
            self.assertEqual(result["kind"], "unverified_candidate")
            self.assertFalse(result["verified"])
            self.assertEqual(result["candidate_sha256"], sha(raw + b"changed"))
            self.assertEqual(result["receipt_sha256"], sha(receipt_raw))
            self.assertIn("不一致", result["reason"])

    def test_malformed_unverified_and_oversized_receipts_remain_advisory(self):
        with tempfile.TemporaryDirectory(prefix="candidate-origin-", dir=TEST_TEMP) as temporary:
            root = Path(temporary)
            candidate, raw = write_project(root)
            info = project(candidate, raw)
            sidecar = Path(str(candidate) + ".style.json")

            sidecar.write_bytes(b"{not-json")
            malformed = identity.inspect_candidate_origin(info)
            self.assertEqual(malformed["kind"], "unverified_candidate")
            self.assertFalse(malformed["verified"])
            self.assertEqual(malformed["receipt_sha256"], sha(b"{not-json"))
            self.assertIn("JSON", malformed["reason"])

            write_receipt(sidecar, style_receipt(candidate, output_sha="0" * 64))
            mismatch = identity.inspect_candidate_origin(info)
            self.assertEqual(mismatch["kind"], "unverified_candidate")
            self.assertFalse(mismatch["verified"])
            self.assertIn("不一致", mismatch["reason"])

            sidecar.unlink()
            tuning_path = Path(str(candidate) + ".tuning.json")
            payload = tuning_receipt(candidate, root)
            payload["receipt_verified"] = False
            write_receipt(tuning_path, payload)
            unverified = identity.inspect_candidate_origin(info)
            self.assertEqual(unverified["kind"], "unverified_candidate")
            self.assertFalse(unverified["verified"])
            self.assertIn("尚未通过验证", unverified["reason"])

            with patch.object(identity, "MAX_RECEIPT_BYTES", 64):
                oversized = identity.inspect_candidate_origin(info)
            self.assertEqual(oversized["kind"], "unverified_candidate")
            self.assertFalse(oversized["verified"])
            self.assertIsNone(oversized["receipt_sha256"])
            self.assertIn("上限", oversized["reason"])

    def test_tuning_sidecar_has_priority_over_style(self):
        with tempfile.TemporaryDirectory(prefix="candidate-origin-", dir=TEST_TEMP) as temporary:
            root = Path(temporary)
            candidate, raw = write_project(root)
            info = project(candidate, raw)
            style_path = Path(str(candidate) + ".style.json")
            tuning_path = Path(str(candidate) + ".tuning.json")
            write_receipt(style_path, style_receipt(candidate, source_sha="a" * 64))
            write_receipt(tuning_path, tuning_receipt(candidate, root, source_sha="b" * 64))

            preferred = identity.inspect_candidate_origin(info)
            self.assertEqual(preferred["kind"], "tuning")
            self.assertEqual(preferred["source_sha256"], "b" * 64)
            self.assertEqual(preferred["receipt_name"], candidate.name + ".tuning.json")

            tuning_path.write_text("{broken", encoding="utf-8")
            broken_preferred = identity.inspect_candidate_origin(info)
            self.assertEqual(broken_preferred["kind"], "unverified_candidate")
            self.assertEqual(broken_preferred["receipt_name"], candidate.name + ".tuning.json")

            tuning_path.unlink()
            fallback = identity.inspect_candidate_origin(info)
            self.assertEqual(fallback["kind"], "style")
            self.assertEqual(fallback["source_sha256"], "a" * 64)

    def test_directories_and_links_are_not_followed(self):
        with tempfile.TemporaryDirectory(prefix="candidate-origin-", dir=TEST_TEMP) as temporary:
            root = Path(temporary)
            candidate, raw = write_project(root)
            info = project(candidate, raw)
            sidecar = Path(str(candidate) + ".style.json")

            sidecar.mkdir()
            directory = identity.inspect_candidate_origin(info)
            self.assertEqual(directory["kind"], "unverified_candidate")
            self.assertFalse(directory["verified"])
            self.assertIn("普通文件", directory["reason"])
            sidecar.rmdir()

            elsewhere = root / "unrelated.json"
            write_receipt(elsewhere, style_receipt(candidate))
            try:
                os.symlink(elsewhere, sidecar)
            except (OSError, NotImplementedError):
                write_receipt(sidecar, style_receipt(candidate))
                native_sidecar = identity._native(str(sidecar))
                real_lstat = os.lstat
                real_attributes = real_lstat(native_sidecar)
                reparse_attributes = SimpleNamespace(
                    st_mode=real_attributes.st_mode,
                    st_file_attributes=0x400,
                )

                def fake_lstat(path):
                    if os.path.normcase(os.fspath(path)) == os.path.normcase(native_sidecar):
                        return reparse_attributes
                    return real_lstat(path)

                context = patch.object(identity.os, "lstat", side_effect=fake_lstat)
            else:
                context = nullcontext()
            with context:
                linked = identity.inspect_candidate_origin(info)
            self.assertEqual(linked["kind"], "unverified_candidate")
            self.assertFalse(linked["verified"])
            self.assertIsNone(linked["receipt_sha256"])
            self.assertTrue("链接" in linked["reason"] or "重解析点" in linked["reason"])

    def test_output_path_inside_receipt_is_not_opened(self):
        with tempfile.TemporaryDirectory(prefix="candidate-origin-", dir=TEST_TEMP) as temporary:
            root = Path(temporary)
            candidate, raw = write_project(root)
            info = project(candidate, raw)
            payload = style_receipt(candidate)
            payload["output"] = str(root / "must-not-be-opened.json")
            write_receipt(Path(str(candidate) + ".style.json"), payload)
            result = identity.inspect_candidate_origin(info)
            self.assertEqual(result["kind"], "style")
            self.assertTrue(result["verified"])
            self.assertFalse((root / "must-not-be-opened.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
