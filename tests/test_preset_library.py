"""Contract tests for the bounded local preset library.

The suite uses real generated documents, temporary roots, process-local
threads, and synthetic fault injection. It does not spawn processes, touch the
default user library, load Qt, or invoke any preset application path.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
TEST_WORK = APP / "work"
SRC = APP / "src"
sys.path.insert(0, str(SRC))

import preset_library as library
from common import exclusive_sidecar, native_path
from preset_builtin_documents import builtin_documents, example_documents
from preset_documents import (
    MAX_DOCUMENT_BYTES,
    PresetError,
    encode_document,

    validate_document,
)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sparse_json(document: dict) -> bytes:
    """Valid JSON with BOM and whitespace that import must preserve exactly."""

    body = json.dumps(document, ensure_ascii=False, indent=3).encode("utf-8")
    return b"\xef\xbb\xbf  " + body + b"\r\n  "


class PresetLibraryTests(unittest.TestCase):
    def setUp(self):
        TEST_WORK.mkdir(parents=True, exist_ok=True)
        self._temporary = tempfile.TemporaryDirectory(
            prefix="preset-library-tests-", dir=native_path(TEST_WORK)
        )
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.root = self.base / "library"
        self.lib = library.PresetLibrary(self.root)
        self.examples = example_documents()
        self.by_type = {document["type"]: document for document in self.examples}
        self._source_number = 0

    def example(self, kind: str) -> dict:
        return copy.deepcopy(self.by_type[kind])

    def source_file(self, raw: bytes) -> Path:
        self._source_number += 1
        path = self.base / f"input-{self._source_number}.vwa-preset.json"
        path.write_bytes(raw)
        return path

    def import_raw(
        self,
        target: library.PresetLibrary,
        raw: bytes,
        expected_revision,
        *,
        as_copy=False,
        expected_sha256=None,
    ):
        return target.import_file(
            self.source_file(raw),
            expected_revision=expected_revision,
            expected_sha256=expected_sha256,
            as_copy=as_copy,
        )

    @staticmethod
    def find_entry(snapshot: dict, identifier: str, version: str) -> dict | None:
        for entry in snapshot["entries"]:
            if entry["id"] == identifier and entry["version"] == version:
                return entry
        return None

    @staticmethod
    def local_entries(snapshot: dict) -> list[dict]:
        return [entry for entry in snapshot["entries"] if not entry["builtin"]]

    @staticmethod
    def document_files(root: Path) -> list[Path]:
        directory = root / "documents"
        return sorted(directory.rglob("*.json")) if directory.exists() else []

    def test_snapshot_is_read_only_and_lists_generated_builtins(self):
        self.assertFalse(self.root.exists())
        snapshot = self.lib.snapshot()
        self.assertFalse(self.root.exists())
        self.assertIsNone(snapshot["revision"])

        generated = builtin_documents()
        self.assertEqual(len(generated), 10)
        self.assertEqual(
            [entry["id"] for entry in snapshot["entries"][:10]],
            [document["id"] for document in generated],
        )
        self.assertEqual(
            sum(entry["type"] == "mixdown" for entry in snapshot["entries"][:10]),
            6,
        )
        self.assertEqual(
            sum(entry["type"] == "preparation" for entry in snapshot["entries"][:10]),
            4,
        )
        for entry in snapshot["entries"]:
            self.assertTrue(entry["builtin"])
            self.assertFalse(entry["alias"])
            self.assertFalse(entry["favorite"])
            self.assertEqual(entry["version"], "1.0.0")
            self.assertLessEqual(entry["bytes"], MAX_DOCUMENT_BYTES)

        source = generated[0]
        loaded = self.lib.get_document(source["id"], source["version"])
        self.assertEqual(loaded, validate_document(source, allow_builtin=True))
        self.assertFalse(self.root.exists())

    def test_exact_import_and_export_preserve_authored_bytes(self):
        document = self.example("preparation")
        raw = sparse_json(document)
        source = self.source_file(raw)
        imported = self.lib.import_file(source, expected_revision=None)

        self.assertFalse(imported["already_present"])
        self.assertEqual(imported["entry"]["id"], document["id"])
        self.assertEqual(imported["entry"]["type"], "preparation")
        self.assertEqual(imported["entry"]["sha256"], digest(raw))
        self.assertEqual(imported["entry"]["name"], document["metadata"]["name"])
        self.assertEqual(source.read_bytes(), raw)

        index_raw = self.lib.index_path.read_bytes()
        self.assertEqual(imported["revision"], digest(index_raw))
        stored = self.document_files(self.root)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].read_bytes(), raw)
        self.assertEqual(stored[0].name, digest(raw) + ".json")
        self.assertRegex(stored[0].parent.parent.name, r"^[0-9a-f]{64}$")
        self.assertNotIn(document["id"], stored[0].as_posix())
        self.assertEqual(self.lib.get_document(document["id"], document["version"]), document)
        self.assertFalse(self.lib.previous_index_path.exists())

        destination = self.base / "roundtrip.vwa-preset.json"
        exported = self.lib.export_file(document["id"], document["version"], destination)
        self.assertEqual(exported, destination.absolute())
        self.assertEqual(destination.read_bytes(), raw)
        before = destination.read_bytes()
        with self.assertRaises(PresetError) as raised:
            self.lib.export_file(document["id"], document["version"], destination)
        self.assertEqual(raised.exception.code, "export_exists")
        self.assertEqual(destination.read_bytes(), before)

        builtin = builtin_documents()[0]
        builtin_destination = self.base / "builtin.vwa-preset.json"
        self.lib.export_file(builtin["id"], builtin["version"], builtin_destination)
        self.assertEqual(
            builtin_destination.read_bytes(),
            encode_document(builtin, allow_builtin=True),
        )

    def test_import_appends_all_four_types_and_preserves_aliases_and_favorites(self):
        revision = None
        originals = []
        for position, document in enumerate(self.examples):
            raw = encode_document(document)
            first = self.import_raw(self.lib, raw, revision)
            alias = document["type"] + " 既有别名"
            renamed = self.lib.rename(
                document["id"],
                document["version"],
                alias=alias,
                expected_revision=first["revision"],
            )
            favorite = position % 2 == 0
            favored = self.lib.set_favorite(
                document["id"],
                document["version"],
                favorite=favorite,
                expected_revision=renamed["revision"],
            )
            revision = favored["revision"]
            originals.append((document, raw, alias, favorite))

        appended = []
        for document, _raw, _alias, _favorite in originals:
            next_document = copy.deepcopy(document)
            next_document["id"] = "user.example.append_" + document["type"]
            next_document["version"] = "0.2.0"
            next_document["metadata"]["name"] += " 追加"
            next_raw = encode_document(next_document)
            result = self.import_raw(self.lib, next_raw, revision)
            revision = result["revision"]
            appended.append((next_document, next_raw))

        snapshot = self.lib.snapshot()
        self.assertEqual(len(self.local_entries(snapshot)), 8)
        self.assertEqual({entry["type"] for entry in self.local_entries(snapshot)},
                         {"preparation", "mixdown", "tuning", "style_recipe"})
        for document, raw, alias, favorite in originals:
            entry = self.find_entry(snapshot, document["id"], document["version"])
            self.assertEqual(entry["sha256"], digest(raw))
            self.assertEqual(entry["alias"], alias)
            self.assertEqual(entry["name"], alias)
            self.assertEqual(entry["favorite"], favorite)
        for document, raw in appended:
            entry = self.find_entry(snapshot, document["id"], document["version"])
            self.assertIsNotNone(entry)
            self.assertEqual(entry["sha256"], digest(raw))
            self.assertFalse(entry["favorite"])

        index_before = self.lib.index_path.read_bytes()
        for document, raw, _alias, _favorite in originals:
            repeated = self.import_raw(self.lib, raw, revision)
            self.assertTrue(repeated["already_present"])
            self.assertFalse(repeated["copied"])
            self.assertEqual(repeated["revision"], revision)
            self.assertEqual(repeated["source_sha256"], digest(raw))
        self.assertEqual(self.lib.index_path.read_bytes(), index_before)
        self.assertEqual(len(self.document_files(self.root)), 8)

    def test_conflicting_import_keeps_existing_metadata_and_offers_independent_copy(self):
        document = self.example("mixdown")
        raw = encode_document(document)
        first = self.import_raw(self.lib, raw, None)
        alias = "保留的混音别名"
        renamed = self.lib.rename(
            document["id"],
            document["version"],
            alias=alias,
            expected_revision=first["revision"],
        )
        favored = self.lib.set_favorite(
            document["id"],
            document["version"],
            favorite=True,
            expected_revision=renamed["revision"],
        )
        before_snapshot = self.lib.snapshot()
        before_index = self.lib.index_path.read_bytes()

        changed = copy.deepcopy(document)
        changed["payload"]["reverb_mix"] = 0.234
        changed_raw = encode_document(changed)
        changed_source = self.source_file(changed_raw)
        with self.assertRaises(PresetError) as raised:
            self.lib.import_file(
                changed_source,
                expected_revision=favored["revision"],
                expected_sha256=digest(changed_raw),
            )
        self.assertEqual(raised.exception.code, "preset_version_conflict")
        self.assertEqual(raised.exception.details["alternative"], "as_copy")
        self.assertTrue(raised.exception.details["preserve_existing"])
        self.assertIn("独立副本", str(raised.exception))
        self.assertEqual(self.lib.snapshot(), before_snapshot)
        self.assertEqual(self.lib.index_path.read_bytes(), before_index)
        self.assertEqual(changed_source.read_bytes(), changed_raw)
        self.assertEqual(len(self.document_files(self.root)), 1)

        copied = self.lib.import_file(
            changed_source,
            expected_revision=favored["revision"],
            expected_sha256=digest(changed_raw),
            as_copy=True,
        )
        self.assertTrue(copied["copied"])
        self.assertFalse(copied["already_present"])
        self.assertEqual(copied["source_sha256"], digest(changed_raw))
        self.assertNotEqual(copied["entry"]["id"], document["id"])
        self.assertTrue(copied["entry"]["id"].startswith("user."))
        self.assertEqual(copied["entry"]["version"], "0.1.0")
        self.assertEqual(changed_source.read_bytes(), changed_raw)
        after_snapshot = self.lib.snapshot()
        original_entry = self.find_entry(
            after_snapshot, document["id"], document["version"]
        )
        self.assertEqual(original_entry["alias"], alias)
        self.assertTrue(original_entry["favorite"])
        self.assertEqual(original_entry["sha256"], digest(raw))
        copied_document = self.lib.get_document(
            copied["entry"]["id"], copied["entry"]["version"]
        )
        validate_document(copied_document)
        self.assertEqual(copied_document["metadata"]["name"],
                         changed["metadata"]["name"])
        self.assertEqual(len(self.local_entries(after_snapshot)), 2)

    def test_as_copy_checks_preview_sha_and_revision_before_writing(self):
        document = self.example("tuning")
        raw = encode_document(document)
        source = self.source_file(raw)
        preview_lib = library.PresetLibrary(self.base / "as-copy-preview")
        with self.assertRaises(PresetError) as raised:
            preview_lib.import_file(
                source,
                expected_revision=None,
                expected_sha256="0" * 64,
                as_copy=True,
            )
        self.assertEqual(raised.exception.code, "file_changed")
        self.assertFalse(preview_lib.root.exists())
        self.assertEqual(source.read_bytes(), raw)

        copied = preview_lib.import_file(
            source,
            expected_revision=None,
            expected_sha256=digest(raw),
            as_copy=True,
        )
        self.assertTrue(copied["copied"])
        self.assertNotEqual(copied["entry"]["id"], document["id"])
        self.assertEqual(copied["entry"]["version"], "0.1.0")
        self.assertEqual(copied["source_sha256"], digest(raw))
        files_before = self.document_files(preview_lib.root)
        index_before = preview_lib.index_path.read_bytes()
        next_document = self.example("style_recipe")
        next_raw = encode_document(next_document)
        with self.assertRaises(PresetError) as raised:
            preview_lib.import_file(
                self.source_file(next_raw),
                expected_revision=None,
                expected_sha256=digest(next_raw),
                as_copy=True,
            )
        self.assertEqual(raised.exception.code, "library_stale")
        self.assertEqual(self.document_files(preview_lib.root), files_before)
        self.assertEqual(preview_lib.index_path.read_bytes(), index_before)

    def test_builtin_export_import_always_appends_an_independent_user_copy(self):
        builtin = copy.deepcopy(builtin_documents()[0])
        builtin_before = copy.deepcopy(builtin)
        raw = encode_document(builtin, allow_builtin=True)
        source = self.source_file(raw)
        first = self.lib.import_file(
            source,
            expected_revision=None,
            expected_sha256=digest(raw),
        )
        self.assertTrue(first["copied"])
        self.assertFalse(first["already_present"])
        self.assertEqual(first["source_sha256"], digest(raw))
        self.assertTrue(first["entry"]["id"].startswith("user."))
        self.assertEqual(first["entry"]["version"], "0.1.0")
        self.assertEqual(source.read_bytes(), raw)
        copied_document = self.lib.get_document(
            first["entry"]["id"], first["entry"]["version"]
        )
        validate_document(copied_document)
        self.assertEqual(copied_document["metadata"]["name"],
                         builtin["metadata"]["name"])
        self.assertEqual(copied_document["payload"], builtin["payload"])
        self.assertEqual(
            self.lib.get_document(builtin["id"], builtin["version"]),
            builtin_before,
        )
        snapshot = self.lib.snapshot()
        self.assertEqual(len(self.local_entries(snapshot)), 1)
        self.assertFalse(any(
            entry["id"] == builtin["id"]
            and entry["version"] == builtin["version"]
            and not entry["builtin"]
            for entry in snapshot["entries"]
        ))

        second = self.lib.import_file(
            source,
            expected_revision=first["revision"],
            expected_sha256=digest(raw),
        )
        self.assertTrue(second["copied"])
        self.assertNotEqual(second["entry"]["id"], first["entry"]["id"])
        self.assertEqual(len(self.local_entries(self.lib.snapshot())), 2)
        self.assertEqual(source.read_bytes(), raw)
        with self.assertRaises(PresetError) as raised:
            self.lib.put_document(
                builtin_before,
                expected_revision=second["revision"],
            )
        self.assertEqual(raised.exception.code, "preset_builtin_forbidden")
        self.assertEqual(len(self.local_entries(self.lib.snapshot())), 2)

    def test_import_is_idempotent_and_rejects_same_version_conflicts(self):
        document = self.example("mixdown")
        raw = encode_document(document)
        first = self.lib.import_file(self.source_file(raw), expected_revision=None)
        revision_one = first["revision"]
        index_one = self.lib.index_path.read_bytes()

        repeated = self.lib.import_file(
            self.source_file(raw), expected_revision=revision_one
        )
        self.assertTrue(repeated["already_present"])
        self.assertEqual(repeated["revision"], revision_one)
        self.assertEqual(self.lib.index_path.read_bytes(), index_one)

        changed = copy.deepcopy(document)
        changed["payload"]["reverb_mix"] = 0.234
        changed_raw = encode_document(changed)
        with self.assertRaises(PresetError) as raised:
            self.lib.import_file(
                self.source_file(changed_raw), expected_revision=revision_one
            )
        self.assertEqual(raised.exception.code, "preset_version_conflict")
        self.assertEqual(raised.exception.details["id"], document["id"])
        self.assertEqual(self.lib.index_path.read_bytes(), index_one)

        newer = copy.deepcopy(document)
        newer["version"] = "0.2.0"
        newer_raw = encode_document(newer)
        second = self.lib.import_file(
            self.source_file(newer_raw), expected_revision=revision_one
        )
        self.assertEqual(second["revision"], digest(self.lib.index_path.read_bytes()))
        self.assertEqual(self.lib.previous_index_path.read_bytes(), index_one)
        snapshot = self.lib.snapshot()
        self.assertIsNotNone(self.find_entry(snapshot, document["id"], "0.1.0"))
        self.assertIsNotNone(self.find_entry(snapshot, document["id"], "0.2.0"))
        self.assertEqual(len(self.local_entries(snapshot)), 2)

        put_document = self.example("style_recipe")
        put_document["id"] = "user.example.put_style"
        put_document["version"] = "0.1.1"
        put_raw = encode_document(put_document)
        put = self.lib.put_document(
            put_document,
            expected_revision=second["revision"],
        )
        put_files = [
            path
            for path in self.document_files(self.root)
            if path.name == digest(put_raw) + ".json"
        ]
        self.assertEqual(len(put_files), 1)
        self.assertEqual(put_files[0].read_bytes(), put_raw)
        self.assertEqual(put["entry"]["sha256"], digest(put_raw))

    def test_alias_favorite_and_delete_are_local_and_immutable(self):
        document = self.example("tuning")
        raw = sparse_json(document)
        first = self.lib.import_file(self.source_file(raw), expected_revision=None)
        stored = self.document_files(self.root)
        self.assertEqual(len(stored), 1)
        original_stored = stored[0].read_bytes()

        alias = "本地柔和 ✨"
        renamed = self.lib.rename(
            document["id"],
            document["version"],
            alias=alias,
            expected_revision=first["revision"],
        )
        self.assertEqual(renamed["entry"]["alias"], alias)
        self.assertEqual(renamed["entry"]["name"], alias)
        self.assertEqual(
            self.lib.get_document(document["id"], document["version"]),
            document,
        )

        favored = self.lib.set_favorite(
            document["id"],
            document["version"],
            favorite=True,
            expected_revision=renamed["revision"],
        )
        self.assertTrue(favored["entry"]["favorite"])
        self.assertEqual(stored[0].read_bytes(), original_stored)

        index_before_delete = self.lib.index_path.read_bytes()
        deleted = self.lib.delete(
            document["id"],
            document["version"],
            expected_revision=favored["revision"],
        )
        self.assertEqual(deleted["revision"], digest(self.lib.index_path.read_bytes()))
        self.assertEqual(self.lib.previous_index_path.read_bytes(), index_before_delete)
        snapshot = self.lib.snapshot()
        self.assertIsNone(self.find_entry(snapshot, document["id"], document["version"]))
        self.assertEqual(stored[0].read_bytes(), original_stored)
        with self.assertRaises(PresetError) as raised:
            self.lib.get_document(document["id"], document["version"])
        self.assertEqual(raised.exception.code, "preset_not_found")

        restored = self.lib.import_file(
            self.source_file(raw), expected_revision=deleted["revision"]
        )
        self.assertFalse(restored["already_present"])
        self.assertEqual(stored[0].read_bytes(), original_stored)
        self.assertIsNotNone(
            self.find_entry(self.lib.snapshot(), document["id"], document["version"])
        )
    def test_copy_creates_independent_uuid_document_and_builtins_are_read_only(self):
        source = builtin_documents()[0]
        source_before = copy.deepcopy(source)
        snapshot = self.lib.snapshot()
        self.assertIsNone(snapshot["revision"])
        self.assertEqual(len(snapshot["entries"]), 10)

        copied = self.lib.copy(
            source["id"],
            source["version"],
            name="内置副本",
            expected_revision=None,
        )
        copied_id = copied["entry"]["id"]
        self.assertRegex(
            copied_id,
            r"^user\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}$",
        )
        self.assertEqual(copied["entry"]["version"], "0.1.0")
        self.assertFalse(copied["already_present"])
        copied_document = self.lib.get_document(copied_id, "0.1.0")
        validate_document(copied_document)
        self.assertEqual(copied_document["metadata"]["name"], "内置副本")
        self.assertEqual(self.lib.get_document(source["id"], source["version"]), source_before)

        after_copy = self.lib.snapshot()
        self.assertEqual(after_copy["revision"], copied["revision"])
        self.assertEqual(len(self.local_entries(after_copy)), 1)
        self.assertEqual(len(after_copy["entries"]), 11)

        current_revision = after_copy["revision"]
        calls = (
            lambda: self.lib.rename(
                source["id"], source["version"], alias="x", expected_revision=current_revision
            ),
            lambda: self.lib.set_favorite(
                source["id"],
                source["version"],
                favorite=True,
                expected_revision=current_revision,
            ),
            lambda: self.lib.delete(
                source["id"], source["version"], expected_revision=current_revision
            ),
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaises(PresetError) as raised:
                    call()
                self.assertEqual(raised.exception.code, "preset_builtin_readonly")
        self.assertEqual(self.lib.snapshot()["revision"], current_revision)

    def test_revision_cas_is_mandatory_and_lock_contention_is_nonblocking(self):
        seed = self.example("mixdown")
        second = self.example("preparation")
        first = self.lib.import_file(
            self.source_file(encode_document(seed)), expected_revision=None
        )
        revision_one = first["revision"]
        index_one = self.lib.index_path.read_bytes()

        with self.assertRaises(TypeError):
            self.lib.put_document(seed)

        second_raw = encode_document(second)
        with self.assertRaises(PresetError) as raised:
            self.lib.put_document(second, expected_revision=None)
        self.assertEqual(raised.exception.code, "library_stale")
        self.assertEqual(self.lib.index_path.read_bytes(), index_one)
        self.assertEqual(len(self.document_files(self.root)), 1)

        renamed = self.lib.rename(
            seed["id"], seed["version"], alias="已更新", expected_revision=revision_one
        )
        with self.assertRaises(PresetError) as raised:
            self.lib.set_favorite(
                seed["id"],
                seed["version"],
                favorite=True,
                expected_revision=revision_one,
            )
        self.assertEqual(raised.exception.code, "library_stale")
        snapshot = self.lib.snapshot()
        entry = self.find_entry(snapshot, seed["id"], seed["version"])
        self.assertEqual(entry["alias"], "已更新")
        self.assertFalse(entry["favorite"])
        self.assertEqual(snapshot["revision"], renamed["revision"])

        entered = threading.Event()
        release = threading.Event()
        thread_errors: list[BaseException] = []

        def hold_lock():
            try:
                with exclusive_sidecar(self.lib.lock_path, create=True):
                    entered.set()
                    release.wait(5)
            except BaseException as error:  # Report thread failures to the test.
                thread_errors.append(error)
                entered.set()

        holder = threading.Thread(target=hold_lock, name="preset-lock-holder")
        holder.start()
        try:
            self.assertTrue(entered.wait(3), "lock holder did not acquire the sidecar")
            with self.assertRaises(PresetError) as raised:
                self.lib.put_document(second, expected_revision=renamed["revision"])
            self.assertEqual(raised.exception.code, "library_busy")
            self.assertTrue(raised.exception.details["retryable"])
        finally:
            release.set()
            holder.join(3)
        self.assertFalse(holder.is_alive())
        self.assertEqual(thread_errors, [])
        self.assertEqual(self.lib.snapshot()["revision"], renamed["revision"])
        self.assertIsNone(
            self.find_entry(self.lib.snapshot(), second["id"], second["version"])
        )
        self.assertGreater(len(second_raw), 0)

    def test_two_writers_using_one_revision_have_exactly_one_winner(self):
        seed = self.example("mixdown")
        first = self.lib.import_file(
            self.source_file(encode_document(seed)), expected_revision=None
        )
        writers = []
        for identifier in ("user.example.race_a", "user.example.race_b"):
            document = self.example("style_recipe")
            document["id"] = identifier
            document["version"] = "0.1.0"
            writers.append(document)

        barrier = threading.Barrier(3)
        outcomes: list[tuple[str, object, object]] = []

        def write(document):
            barrier.wait()
            try:
                result = self.lib.put_document(
                    document, expected_revision=first["revision"]
                )
                outcomes.append(("ok", document["id"], result))
            except PresetError as error:
                outcomes.append(("error", document["id"], error))
            except BaseException as error:
                outcomes.append(("raw", document["id"], error))

        threads = [threading.Thread(target=write, args=(document,)) for document in writers]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(outcomes), 2)
        successes = [outcome for outcome in outcomes if outcome[0] == "ok"]
        failures = [outcome for outcome in outcomes if outcome[0] == "error"]
        self.assertEqual(len(successes), 1, outcomes)
        self.assertEqual(len(failures), 1, outcomes)
        self.assertIn(failures[0][2].code, {"library_busy", "library_stale"})
        snapshot = self.lib.snapshot()
        local_ids = {entry["id"] for entry in self.local_entries(snapshot)}
        self.assertEqual(
            local_ids,
            {seed["id"], successes[0][1]},
        )
        self.assertEqual(snapshot["revision"], successes[0][2]["revision"])

    def test_document_backup_and_index_faults_never_advertise_new_state(self):
        seed = self.example("mixdown")
        first = self.lib.import_file(
            self.source_file(encode_document(seed)), expected_revision=None
        )
        revision_one = first["revision"]
        index_one = self.lib.index_path.read_bytes()
        candidates = [self.example("preparation"), self.example("tuning")]
        candidates[0]["id"] = "user.example.fault_preparation"
        candidates[1]["id"] = "user.example.fault_tuning"

        real_replace = library._atomic_replace

        def fail_index(path, raw, **kwargs):
            if Path(path) == self.lib.index_path:
                raise PresetError("injected_index_failure", "synthetic index fault")
            return real_replace(path, raw, **kwargs)

        with patch.object(library, "_atomic_replace", side_effect=fail_index):
            with self.assertRaises(PresetError) as raised:
                self.lib.put_document(candidates[0], expected_revision=revision_one)
        self.assertEqual(raised.exception.code, "injected_index_failure")
        self.assertEqual(self.lib.index_path.read_bytes(), index_one)
        snapshot = self.lib.snapshot()
        self.assertEqual(snapshot["revision"], revision_one)
        self.assertIsNone(self.find_entry(snapshot, candidates[0]["id"], "0.1.0"))
        self.assertEqual(len(self.document_files(self.root)), 2)

        def fail_previous(path, raw, **kwargs):
            if Path(path) == self.lib.previous_index_path:
                raise PresetError("injected_backup_failure", "synthetic backup fault")
            return real_replace(path, raw, **kwargs)

        with patch.object(library, "_atomic_replace", side_effect=fail_previous):
            with self.assertRaises(PresetError) as raised:
                self.lib.put_document(candidates[1], expected_revision=revision_one)
        self.assertEqual(raised.exception.code, "injected_backup_failure")
        self.assertEqual(self.lib.index_path.read_bytes(), index_one)
        self.assertEqual(len(self.document_files(self.root)), 3)

        third = self.example("style_recipe")
        third["id"] = "user.example.fault_document"
        with patch.object(
            library,
            "_publish_immutable",
            side_effect=PresetError("injected_document_failure", "synthetic document fault"),
        ):
            with self.assertRaises(PresetError) as raised:
                self.lib.put_document(third, expected_revision=revision_one)
        self.assertEqual(raised.exception.code, "injected_document_failure")
        self.assertEqual(self.lib.index_path.read_bytes(), index_one)
        self.assertEqual(len(self.document_files(self.root)), 3)
        self.assertEqual(self.lib.snapshot()["revision"], revision_one)
    def test_previous_recovery_restores_valid_index_and_archives_current_bytes(self):
        document = self.example("tuning")
        raw = sparse_json(document)
        first = self.lib.import_file(self.source_file(raw), expected_revision=None)
        index_one = self.lib.index_path.read_bytes()
        self.assertEqual(first["revision"], digest(index_one))

        renamed = self.lib.rename(
            document["id"],
            document["version"],
            alias="待恢复别名",
            expected_revision=first["revision"],
        )
        index_two = self.lib.index_path.read_bytes()
        self.assertEqual(renamed["revision"], digest(index_two))
        self.assertEqual(self.lib.previous_index_path.read_bytes(), index_one)

        recovered = self.lib.recover_previous()
        self.assertEqual(recovered["revision"], first["revision"])
        recovered_entry = self.find_entry(
            recovered, document["id"], document["version"]
        )
        self.assertEqual(recovered_entry["alias"], "")
        archived_two = (
            self.lib.history_path / f"index-{digest(index_two)}-previous.json"
        )
        self.assertEqual(archived_two.read_bytes(), index_two)
        self.assertEqual(self.lib.index_path.read_bytes(), index_one)

        corrupt = b'{"format":"vwa.preset-library","index_version":1'
        self.lib.index_path.write_bytes(corrupt)
        with self.assertRaises(PresetError) as raised:
            self.lib.snapshot()
        self.assertEqual(raised.exception.code, "library_corrupt")

        recovered_again = self.lib.recover_previous()
        self.assertEqual(recovered_again["revision"], first["revision"])
        archived_corrupt = (
            self.lib.history_path / f"index-{digest(corrupt)}-corrupt.json"
        )
        self.assertEqual(archived_corrupt.read_bytes(), corrupt)
        self.assertEqual(self.lib.index_path.read_bytes(), index_one)
        self.assertEqual(
            self.lib.get_document(document["id"], document["version"]),
            document,
        )

    def test_recovery_rejects_bad_referenced_document_without_promotion(self):
        document = self.example("tuning")
        first = self.lib.import_file(
            self.source_file(encode_document(document)), expected_revision=None
        )
        self.lib.rename(
            document["id"],
            document["version"],
            alias="当前版本",
            expected_revision=first["revision"],
        )
        current = self.lib.index_path.read_bytes()
        stored = self.document_files(self.root)
        self.assertEqual(len(stored), 1)
        stored[0].write_bytes(b'{"broken":')

        with self.assertRaises(PresetError) as raised:
            self.lib.recover_previous()
        self.assertEqual(raised.exception.code, "library_corrupt")
        self.assertEqual(raised.exception.details.get("source"), "previous")
        self.assertEqual(self.lib.index_path.read_bytes(), current)
        self.assertFalse(self.lib.history_path.exists())

    def test_strict_index_and_missing_document_fail_closed(self):
        strict_lib = library.PresetLibrary(self.base / "strict-index")
        document = self.example("mixdown")
        first = strict_lib.import_file(
            self.source_file(encode_document(document)), expected_revision=None
        )
        index_value = json.loads(strict_lib.index_path.read_text(encoding="utf-8"))
        index_value["entries"].append(copy.deepcopy(index_value["entries"][0]))
        duplicate = (
            json.dumps(
                index_value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        strict_lib.index_path.write_bytes(duplicate)
        with self.assertRaises(PresetError) as raised:
            strict_lib.snapshot()
        self.assertEqual(raised.exception.code, "library_corrupt")
        self.assertEqual(raised.exception.details["id"], document["id"])
        self.assertGreater(len(first["revision"]), 0)

        missing_lib = library.PresetLibrary(self.base / "missing-document")
        imported = missing_lib.import_file(
            self.source_file(encode_document(self.example("preparation"))),
            expected_revision=None,
        )
        files = self.document_files(missing_lib.root)
        self.assertEqual(len(files), 1)
        files[0].unlink()
        with self.assertRaises(PresetError) as raised:
            missing_lib.snapshot()
        self.assertEqual(raised.exception.code, "library_corrupt")
        self.assertEqual(raised.exception.details["id"], imported["entry"]["id"])
        self.assertEqual(
            raised.exception.details["version"],
            imported["entry"]["version"],
        )

    def test_bounds_alias_and_synthetic_reparse_checks(self):
        raw_a = encode_document(self.example("mixdown"))
        raw_b = encode_document(self.example("preparation"))

        count_lib = library.PresetLibrary(self.base / "bounded-count")
        with patch.object(library, "MAX_LIBRARY_ENTRIES", 1):
            seed = count_lib.import_file(self.source_file(raw_a), expected_revision=None)
            with self.assertRaises(PresetError) as raised:
                count_lib.import_file(
                    self.source_file(raw_b), expected_revision=seed["revision"]
                )
            self.assertEqual(raised.exception.code, "library_full")
            self.assertEqual(len(self.document_files(count_lib.root)), 1)

        storage_lib = library.PresetLibrary(self.base / "bounded-storage")
        storage_limit = len(raw_a) + 10
        with patch.object(library, "MAX_LIBRARY_BYTES", storage_limit):
            seed = storage_lib.import_file(
                self.source_file(raw_a), expected_revision=None
            )
            with self.assertRaises(PresetError) as raised:
                storage_lib.import_file(
                    self.source_file(raw_b), expected_revision=seed["revision"]
                )
            self.assertEqual(raised.exception.code, "library_full")
            self.assertEqual(len(self.document_files(storage_lib.root)), 1)

        with self.assertRaises(PresetError) as raised:
            count_lib.rename(
                seed["entry"]["id"],
                seed["entry"]["version"],
                alias="x" * (library.MAX_ALIAS_CHARACTERS + 1),
                expected_revision=seed["revision"],
            )
        self.assertEqual(raised.exception.code, "preset_invalid_alias")

        reparse_root = self.base / "reparse-root"
        reparse_root.mkdir()
        original_reparse = library._is_reparse

        def fake_root_reparse(path, info=None):
            return Path(path) == reparse_root or original_reparse(path, info)

        with patch.object(library, "_is_reparse", side_effect=fake_root_reparse):
            with self.assertRaises(PresetError) as raised:
                library.PresetLibrary(reparse_root).snapshot()
        self.assertEqual(raised.exception.code, "library_unsafe_path")
        self.assertFalse((reparse_root / library.LOCK_FILENAME).exists())

        export_lib = library.PresetLibrary(self.base / "export-library")
        export_doc = self.example("mixdown")
        export_revision = export_lib.import_file(
            self.source_file(encode_document(export_doc)), expected_revision=None
        )
        export_parent = self.base / "export-parent"
        export_parent.mkdir()

        def fake_export_parent_reparse(path, info=None):
            return Path(path) == export_parent or original_reparse(path, info)

        destination = export_parent / "blocked.json"
        with patch.object(library, "_is_reparse", side_effect=fake_export_parent_reparse):
            with self.assertRaises(PresetError) as raised:
                export_lib.export_file(
                    export_doc["id"], export_doc["version"], destination
                )
        self.assertEqual(raised.exception.code, "library_unsafe_path")
        self.assertFalse(destination.exists())
        self.assertGreater(len(export_revision["revision"]), 0)


    def test_import_preview_sha_mismatch_writes_nothing(self):
        document = self.example("preparation")
        raw = sparse_json(document)
        source = self.source_file(raw)
        with self.assertRaises(PresetError) as raised:
            self.lib.import_file(source, expected_revision=None, expected_sha256="0" * 64)
        self.assertEqual(raised.exception.code, "file_changed")
        self.assertEqual(raised.exception.details["actual_sha256"], digest(raw))
        self.assertFalse(self.root.exists())
        self.assertEqual(source.read_bytes(), raw)
        imported = self.lib.import_file(
            source, expected_revision=None, expected_sha256=digest(raw)
        )
        self.assertEqual(imported["entry"]["sha256"], digest(raw))

    def test_deleted_version_cannot_be_reinstalled_with_different_bytes(self):
        document = self.example("mixdown")
        raw_one = encode_document(document)
        first = self.lib.import_file(self.source_file(raw_one), expected_revision=None)
        deleted = self.lib.delete(
            document["id"], document["version"], expected_revision=first["revision"]
        )
        files_before = self.document_files(self.root)
        self.assertEqual(len(files_before), 1)
        changed = copy.deepcopy(document)
        changed["payload"]["reverb_mix"] = 0.312
        raw_two = encode_document(changed)
        with self.assertRaises(PresetError) as raised:
            self.lib.import_file(
                self.source_file(raw_two), expected_revision=deleted["revision"]
            )
        self.assertEqual(raised.exception.code, "preset_version_conflict")
        self.assertEqual(len(self.document_files(self.root)), 1)
        restored = self.lib.import_file(
            self.source_file(raw_one), expected_revision=deleted["revision"]
        )
        self.assertFalse(restored["already_present"])
        self.assertEqual(self.document_files(self.root)[0].read_bytes(), raw_one)
        self.assertIsNotNone(
            self.find_entry(self.lib.snapshot(), document["id"], document["version"])
        )

    def test_alias_controls_entry_name_without_changing_document(self):
        document = self.example("tuning")
        imported = self.lib.import_file(
            self.source_file(encode_document(document)), expected_revision=None
        )
        original_name = document["metadata"]["name"]
        renamed = self.lib.rename(
            document["id"],
            document["version"],
            alias="显示别名",
            expected_revision=imported["revision"],
        )
        self.assertEqual(renamed["entry"]["name"], "显示别名")
        entry = self.find_entry(
            self.lib.snapshot(), document["id"], document["version"]
        )
        self.assertEqual(entry["name"], "显示别名")
        self.assertEqual(
            self.lib.get_document(document["id"], document["version"])["metadata"]["name"],
            original_name,
        )

    def test_lstat_permission_and_strict_index_failures_are_typed(self):
        self.root.mkdir()
        original_lstat = library.os.lstat

        def denied_for_root(path, *args, **kwargs):
            candidate = Path(str(path).removeprefix("\\\\?\\"))
            if candidate == Path(str(self.root).removeprefix("\\\\?\\")):
                raise PermissionError("synthetic permission failure")
            return original_lstat(path, *args, **kwargs)

        with patch.object(library.os, "lstat", side_effect=denied_for_root):
            with self.assertRaises(PresetError) as raised:
                self.lib.snapshot()
        self.assertEqual(raised.exception.code, "library_io_error")
        with patch.object(library.json, "loads", side_effect=RecursionError("deep")):
            with self.assertRaises(PresetError) as raised:
                library._strict_json(b"{}", label="索引")
        self.assertEqual(raised.exception.code, "library_corrupt")
        bad_type = {
            "format": library.INDEX_FORMAT,
            "index_version": library.INDEX_VERSION,
            "entries": [{
                "id": "user.example.bad_type",
                "version": "0.1.0",
                "sha256": "0" * 64,
                "type": [],
                "bytes": 1,
                "alias": "",
                "favorite": False,
            }],
        }
        raw = (json.dumps(bad_type, sort_keys=True, separators=(",", ":")) + "\n").encode()
        with self.assertRaises(PresetError) as raised:
            library._decode_index(raw)
        self.assertEqual(raised.exception.code, "library_corrupt")
        self.assertFalse(library._valid_alias("\ud800"))

    def test_real_subprocess_writers_using_one_revision_have_one_winner(self):
        seed = self.example("mixdown")
        first = self.lib.import_file(
            self.source_file(encode_document(seed)), expected_revision=None
        )
        documents = []
        for identifier in ("user.example.process_a", "user.example.process_b"):
            document = self.example("style_recipe")
            document["id"] = identifier
            document["version"] = "0.1.0"
            documents.append(document)
        worker = r'''
import json, sys
config = json.loads(sys.argv[1])
sys.path.insert(0, config["src"])
from preset_library import PresetLibrary
from preset_documents import PresetError
try:
    result = PresetLibrary(config["root"]).put_document(
        config["document"], expected_revision=config["revision"]
    )
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
except PresetError as error:
    print(json.dumps({"ok": False, "code": error.code, "details": error.details}, ensure_ascii=False))
except BaseException as error:
    print(json.dumps({"ok": False, "code": "raw_error", "reason": repr(error)}))
'''
        processes = []
        for document in documents:
            config = {
                "root": str(self.root),
                "revision": first["revision"],
                "document": document,
                "src": str(SRC),
            }
            processes.append(subprocess.Popen(
                [sys.executable, "-c", worker, json.dumps(config, ensure_ascii=False)],
                cwd=str(APP), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8",
            ))
        outputs = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, stderr)
            outputs.append(json.loads(stdout.strip().splitlines()[-1]))
        successes = [row for row in outputs if row["ok"]]
        failures = [row for row in outputs if not row["ok"]]
        self.assertEqual(len(successes), 1, outputs)
        self.assertEqual(len(failures), 1, outputs)
        self.assertIn(failures[0]["code"], {"library_busy", "library_stale"})
        snapshot = self.lib.snapshot()
        self.assertEqual(len(self.local_entries(snapshot)), 2)
        self.assertEqual(snapshot["revision"], successes[0]["result"]["revision"])

if __name__ == "__main__":
    unittest.main(verbosity=2)
