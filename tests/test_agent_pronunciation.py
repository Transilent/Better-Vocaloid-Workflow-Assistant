"""Acceptance tests for native pronunciation reading and explicit phoneme patches."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

import mido

from agent_pronunciation import (
    PLAN_KIND,
    READ_KIND,
    RECEIPT_KIND,
    _canonical_digest,
    apply_pronunciation_patch,
    plan_pronunciation_patch,
    read_pronunciation,
)
from common import native_path


def digest(path):
    return hashlib.sha256(Path(native_path(path)).read_bytes()).hexdigest()


def json_ready(value):
    json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def write_svp(path, *, library=None, extra_groups=None, overlap=False):
    notes = [
        {
            "onset": 0,
            "duration": 705_600_000,
            "pitch": 60,
            "lyrics": "歌",
            "phonemes": "k a",
            "attributes": {"dF0Vbr": 0.1},
        },
        {
            "onset": 705_600_000 if not overlap else 352_800_000,
            "duration": 705_600_000,
            "pitch": 62,
            "lyrics": "詞",
            "phonemes": "",
        },
        {
            "onset": 1_411_200_000,
            "duration": 352_800_000,
            "pitch": 64,
            "lyrics": "続",
        },
    ]
    data = {
        "version": 2,
        "time": {
            "meter": [{"index": 0, "numerator": 4, "denominator": 4}],
            "tempo": [
                {"position": 0, "bpm": 120.0},
                {"position": 1_411_200_000, "bpm": 90.0},
            ],
        },
        "library": [] if library is None else library,
        "tracks": [
            {
                "name": "Lead 日本語",
                "groups": [] if extra_groups is None else extra_groups,
                "mainRef": {
                    "groupID": "ref-lead",
                    "blickOffset": 0,
                    "isInstrumental": False,
                },
                "mainGroup": {
                    "uuid": "group-lead",
                    "name": "main",
                    "parameters": {
                        "loudness": {"mode": "linear", "points": [0, -1.5, 100, 0.0]}
                    },
                    "notes": notes,
                },
                "trackOnlyMetadata": {"keep": ["日本語", 7]},
            },
            {
                "name": "Harmony 多轨",
                "groups": [],
                "mainRef": {
                    "groupID": "ref-harmony",
                    "blickOffset": 0,
                    "isInstrumental": False,
                },
                "mainGroup": {
                    "uuid": "group-harmony",
                    "name": "main",
                    "notes": [{
                        "onset": 0,
                        "duration": 705_600_000,
                        "pitch": 67,
                        "lyrics": "和",
                        "phonemes": "w a",
                    }],
                },
            },
        ],
        "projectExtension": {"preserve": True},
    }
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\0"
    Path(path).write_bytes(raw)
    return Path(path)


def add(parent, namespace, local, text=None, attributes=None):
    element = ET.SubElement(parent, f"{{{namespace}}}{local}")
    if text is not None:
        element.text = str(text)
    if attributes:
        element.attrib.update(attributes)
    return element


def write_vsqx(path, *, version=4, polyphonic=False):
    namespace = f"http://www.yamaha.co.jp/vocaloid/schema/vsq{version}/"
    ET.register_namespace("", namespace)
    root = ET.Element(f"{{{namespace}}}vsq{version}")
    add(root, namespace, "vender", "Yamaha corporation")
    add(root, namespace, "version", "4.0.0.3" if version == 4 else "3.0.0.0")
    master = add(root, namespace, "masterTrack")
    add(master, namespace, "seqName", "音素テスト")
    add(master, namespace, "comment", "keep-master-comment")
    add(master, namespace, "resolution", 480)
    add(master, namespace, "preMeasure", 1)
    sig = add(master, namespace, "timeSig")
    if version == 4:
        add(sig, namespace, "m", 0)
        add(sig, namespace, "nu", 4)
        add(sig, namespace, "de", 4)
    else:
        add(sig, namespace, "posMes", 0)
        add(sig, namespace, "nume", 4)
        add(sig, namespace, "denomi", 4)
    for tick, bpm in ((0, 12000), (1920, 9000)):
        tempo = add(master, namespace, "tempo")
        add(tempo, namespace, "t" if version == 4 else "posTick", tick)
        add(tempo, namespace, "v" if version == 4 else "bpm", bpm)

    def note(part, onset, duration, pitch, lyric, phonemes=None, lock=None):
        element = add(part, namespace, "note")
        if version == 4:
            add(element, namespace, "t", onset)
            add(element, namespace, "dur", duration)
            add(element, namespace, "n", pitch)
            add(element, namespace, "v", 64)
            add(element, namespace, "y", lyric)
            if phonemes is not None:
                attrs = {} if lock is None else {"lock": str(lock)}
                add(element, namespace, "p", phonemes, attrs)
            style = add(element, namespace, "nStyle")
            add(style, namespace, "v", 50, {"id": "accent"})
        else:
            add(element, namespace, "posTick", onset)
            add(element, namespace, "durTick", duration)
            add(element, namespace, "noteNum", pitch)
            add(element, namespace, "v", 64)
            add(element, namespace, "y", lyric)
            if phonemes is not None:
                attrs = {} if lock is None else {"lock": str(lock)}
                add(element, namespace, "phnms", phonemes, attrs)
            style = add(element, namespace, "noteStyle")
            add(style, namespace, "v", 50, {"id": "accent"})
        return element

    for track_index, track_name in enumerate(("Lead 日本語", "Harmony")):
        track = add(root, namespace, "vsTrack")
        add(track, namespace, "tNo" if version == 4 else "vsTrackNo", track_index)
        add(track, namespace, "name" if version == 4 else "trackName", track_name)
        add(track, namespace, "comment", "keep-track-comment")
        part_name = "vsPart" if version == 4 else "musicalPart"
        part = add(track, namespace, part_name)
        add(part, namespace, "t" if version == 4 else "posTick", 1920)
        add(part, namespace, "playTime" if version == 4 else "playTime", 3840)
        add(part, namespace, "name" if version == 4 else "partName", f"part-{track_index}")
        add(part, namespace, "comment", "keep-part-comment")
        curve = add(part, namespace, "cc")
        add(curve, namespace, "t", 2000)
        add(curve, namespace, "v", -300, {"id": "DYN"})
        if track_index == 0:
            note(part, 0, 480, 60, "歌", "a", 0)
            note(part, 480 if not polyphonic else 240, 480, 62, "詞", None, None)
        else:
            note(part, 0, 480, 67, "和", "w a", 1)
    extension = add(root, namespace, "projectExtension")
    add(extension, namespace, "keep", "音素以外を保持")
    root.append(ET.Comment("keep-top-level-comment"))
    raw = ET.tostring(root, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    Path(path).write_bytes(raw)
    return Path(path)


def write_midi(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Tempo", time=0),
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("end_of_track", time=960),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Vocal", time=0),
        mido.MetaMessage("lyrics", text="歌", time=0),
        mido.Message("note_on", note=60, velocity=80, time=0),
        mido.Message("note_off", note=60, velocity=0, time=480),
        mido.MetaMessage("lyrics", text="詞", time=0),
        mido.Message("note_on", note=62, velocity=80, time=0),
        mido.Message("note_off", note=62, velocity=0, time=480),
        mido.MetaMessage("end_of_track", time=0),
    ]))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(native_path(path), "wb") as stream:
        midi.save(file=stream)
    return path


class PronunciationTests(unittest.TestCase):
    def temporary(self, prefix):
        work = APP / "work"
        work.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(prefix=prefix, dir=work)

    def test_midi_uses_existing_note_id_unknown_and_pagination(self):
        with self.temporary("pron-midi-") as temporary:
            source = write_midi(Path(temporary) / "歌詞.mid")
            before = source.read_bytes()
            first = read_pronunciation(source, limit=1, offset=0)
            second = read_pronunciation(str(source), limit=1, offset=1)
            json_ready(first)
            json_ready(second)
            self.assertEqual(first["kind"], READ_KIND)
            self.assertEqual(first["format"], "midi")
            self.assertEqual(first["source_sha256"], digest(source))
            self.assertEqual(first["pagination"], {
                "limit": 1, "offset": 0, "total": 2, "returned": 1,
                "has_more": True, "next_offset": 1,
            })
            self.assertTrue(first["notes"][0]["note_ref"].startswith("note-"))
            self.assertTrue(second["notes"][0]["note_ref"].startswith("note-"))
            self.assertEqual(first["phoneme_summary"]["unknown"], 2)
            for row in (first["notes"][0], second["notes"][0]):
                self.assertIsNone(row["phonemes"])
                self.assertEqual(row["phoneme_state"], "unknown")
                self.assertFalse(row["phoneme_override"])
                self.assertFalse(row["editable"])
            self.assertFalse(first["write_supported"])
            self.assertEqual(source.read_bytes(), before)
            with self.assertRaises(ValueError):
                plan_pronunciation_patch(
                    source, first["source_sha256"],
                    [{"note_ref": first["notes"][0]["note_ref"], "phonemes": "a"}],
                )

    def test_svp_facts_patch_clear_set_semantics_and_retry(self):
        with self.temporary("pron-svp-") as temporary:
            source = write_svp(Path(temporary) / "入力 音素.svp")
            before = source.read_bytes()
            read = read_pronunciation(source, limit=2, offset=0)
            json_ready(read)
            self.assertEqual(read["format"], "svp")
            self.assertEqual(read["note_count"], 4)
            self.assertEqual(read["pagination"]["next_offset"], 2)
            self.assertEqual(read["phoneme_summary"], {
                "unknown": 2, "derived_unverified": 0, "explicit": 2,
            })
            self.assertEqual(read["notes"][0]["lyric"], "歌")
            self.assertEqual(read["notes"][0]["phoneme_state"], "explicit")
            self.assertEqual(read["notes"][1]["phoneme_state"], "unknown")
            self.assertGreater(read["notes"][1]["onset_seconds"], read["notes"][0]["onset_seconds"])

            edits = [
                {"note_ref": read["notes"][0]["note_ref"], "phonemes": ""},
                {"note_ref": read["notes"][1]["note_ref"], "phonemes": "k i"},
            ]
            plan = plan_pronunciation_patch(source, read["source_sha256"], edits)
            json_ready(plan)
            self.assertEqual(plan["kind"], PLAN_KIND)
            self.assertEqual(plan["edits"], edits)
            self.assertEqual(plan["lock_changes"], [])
            self.assertFalse(plan["inference_performed"])
            self.assertFalse(plan["g2p_performed"])

            receipt = apply_pronunciation_patch(source, plan, Path(temporary) / "輸出")
            self.assertEqual(receipt["kind"], RECEIPT_KIND)
            self.assertTrue(receipt["source_unchanged"])
            self.assertEqual(digest(source), plan["source_sha256"])
            self.assertEqual(source.read_bytes(), before)
            candidate = Path(receipt["candidate_path"])
            self.assertEqual(digest(candidate), receipt["candidate_sha256"])
            receipt_bytes = Path(receipt["receipt_path"]).read_bytes()
            value = json.loads(receipt_bytes)
            self.assertEqual(value["candidate_path"], str(candidate))
            unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
            self.assertEqual(value["receipt_sha256"], _canonical_digest(unsigned))
            candidate_json = json.loads(candidate.read_bytes().rstrip(b"\0"))
            self.assertEqual(candidate_json["projectExtension"], {"preserve": True})
            self.assertEqual(candidate_json["tracks"][0]["mainGroup"]["parameters"], read_json(source)["tracks"][0]["mainGroup"]["parameters"])
            self.assertTrue(candidate.read_bytes().endswith(b"\0"))
            self.assertEqual(read_pronunciation(candidate, limit=2)["phoneme_summary"], {
                "unknown": 2, "derived_unverified": 0, "explicit": 2,
            })
            notes = read_pronunciation(candidate, limit=3)["notes"]
            self.assertEqual(notes[0]["phonemes"], None)
            self.assertEqual(notes[1]["phonemes"], "k i")
            self.assertEqual(apply_pronunciation_patch(source, plan, Path(temporary) / "輸出"), receipt)

    def test_vsqx_v4_facts_lock_changes_preserve_metadata_and_collision(self):
        with self.temporary("pron-vsqx4-") as temporary:
            source = write_vsqx(Path(temporary) / "入力.vsqx", version=4)
            before = source.read_bytes()
            read = read_pronunciation(source, limit=10)
            json_ready(read)
            self.assertEqual(read["format"], "vsqx")
            self.assertEqual(read["format_version"], 4)
            self.assertEqual(read["phoneme_summary"], {
                "unknown": 1, "derived_unverified": 1, "explicit": 1,
            })
            lead = [row for row in read["notes"] if row["track_index"] == 0]
            self.assertEqual(lead[0]["phoneme_state"], "derived_unverified")
            self.assertTrue(lead[0]["phonemes"] == "a")
            self.assertEqual(lead[1]["phoneme_state"], "unknown")
            self.assertEqual(lead[0]["lyric"], "歌")
            plan = plan_pronunciation_patch(
                source,
                read["source_sha256"],
                [
                    {"note_ref": lead[0]["note_ref"], "phonemes": "g a"},
                    {"note_ref": lead[1]["note_ref"], "phonemes": "sh i"},
                ],
            )
            self.assertTrue(all(row["after"] is True for row in plan["lock_changes"]))
            self.assertTrue(all(row["changed"] for row in plan["lock_changes"]))
            receipt = apply_pronunciation_patch(source, plan, Path(temporary) / "候補")
            self.assertEqual(digest(source), plan["source_sha256"])
            self.assertEqual(source.read_bytes(), before)
            candidate_bytes = Path(receipt["candidate_path"]).read_bytes()
            self.assertIn("keep-top-level-comment".encode(), candidate_bytes)
            self.assertIn(b'lock="1"', candidate_bytes)
            tree = ET.parse(receipt["candidate_path"])
            values = [node.text for node in tree.getroot().iter() if node.tag.rsplit("}", 1)[-1] == "p"]
            self.assertEqual(values, ["g a", "sh i", "w a"])
            tempo_values = [node.text for node in tree.getroot().iter() if node.tag.rsplit("}", 1)[-1] == "v" and node.text in {"12000", "9000"}]
            self.assertEqual(tempo_values, ["12000", "9000"])
            candidate_read = read_pronunciation(receipt["candidate_path"], limit=10)
            candidate_lead = [row for row in candidate_read["notes"] if row["track_index"] == 0]
            self.assertEqual([row["phonemes"] for row in candidate_lead], ["g a", "sh i"])
            self.assertTrue(all(row["phoneme_locked"] for row in candidate_lead))
            self.assertEqual(apply_pronunciation_patch(source, plan, Path(temporary) / "候補"), receipt)
            Path(receipt["candidate_path"]).write_bytes(candidate_bytes + b"collision")
            with self.assertRaises(ValueError):
                apply_pronunciation_patch(source, plan, Path(temporary) / "候補")

    def test_vsqx_v3_is_read_and_patched_with_native_tags(self):
        with self.temporary("pron-vsqx3-") as temporary:
            source = write_vsqx(Path(temporary) / "old.vsqx", version=3)
            read = read_pronunciation(source)
            self.assertEqual(read["format_version"], 3)
            note = next(row for row in read["notes"] if row["track_index"] == 0)
            plan = plan_pronunciation_patch(
                source, read["source_sha256"],
                [{"note_ref": note["note_ref"], "phonemes": "k a"}],
            )
            receipt = apply_pronunciation_patch(source, plan, Path(temporary) / "out")
            tree = ET.parse(receipt["candidate_path"])
            values = [node.text for node in tree.getroot().iter() if node.tag.rsplit("}", 1)[-1] == "phnms"]
            self.assertEqual(values[0], "k a")
            self.assertEqual(tree.getroot()[0].tag, "{http://www.yamaha.co.jp/vocaloid/schema/vsq3/}vender")

    def test_invalid_edits_source_and_plan_are_rejected(self):
        with self.temporary("pron-invalid-") as temporary:
            source = write_vsqx(Path(temporary) / "invalid.vsqx", version=4)
            read = read_pronunciation(source)
            target = next(row for row in read["notes"] if row["track_index"] == 0)
            expected = read["source_sha256"]
            bad_sets = [
                [{"note_ref": target["note_ref"], "phonemes": "a", "controls": {}}],
                [{"note_ref": "note-" + "0" * 16 + "-old", "phonemes": "a"}],
                [{"note_ref": target["note_ref"], "phonemes": ""}],
                [{"note_ref": target["note_ref"], "phonemes": "a" * 5000}],
                [
                    {"note_ref": target["note_ref"], "phonemes": "a"},
                    {"note_ref": target["note_ref"], "phonemes": "b"},
                ],
            ]
            for edits in bad_sets:
                with self.assertRaises(ValueError):
                    plan_pronunciation_patch(source, expected, edits)
            with self.assertRaises(ValueError):
                plan_pronunciation_patch(source, "0" * 64, [{"note_ref": target["note_ref"], "phonemes": "a"}])
            valid_plan = plan_pronunciation_patch(
                source, expected, [{"note_ref": target["note_ref"], "phonemes": "a"}]
            )
            tampered = copy.deepcopy(valid_plan)
            tampered["diff"][0]["after"]["phonemes"] = "b"
            with self.assertRaises(ValueError):
                apply_pronunciation_patch(source, tampered, Path(temporary) / "bad-plan")
            recomputed = copy.deepcopy(tampered)
            unsigned = {key: value for key, value in recomputed.items() if key != "plan_sha256"}
            recomputed["plan_sha256"] = _canonical_digest(unsigned)
            with self.assertRaises(ValueError):
                apply_pronunciation_patch(source, recomputed, Path(temporary) / "bad-regenerate")
            source.write_bytes(source.read_bytes() + b" ")
            with self.assertRaises(ValueError):
                apply_pronunciation_patch(source, valid_plan, Path(temporary) / "changed-source")

    def test_svp_instrumental_without_main_group_is_read_without_fake_notes(self):
        with self.temporary("pron-instrumental-") as temporary:
            source = write_svp(Path(temporary) / "instrumental.svp")
            document = read_json(source)
            document["tracks"][1].pop("mainGroup", None)
            document["tracks"][1]["mainRef"] = {
                "isInstrumental": True, "blickOffset": 0, "audio": {"filename": "伴奏.wav"}
            }
            source.write_bytes(json.dumps(document, ensure_ascii=False).encode("utf-8") + b"\0")
            result = read_pronunciation(source)
            self.assertEqual(result["note_count"], 3)
            self.assertTrue(result["tracks"][1]["instrumental"])
            self.assertEqual(result["tracks"][1]["note_count"], 0)

    def test_svp_complex_refs_polyphony_and_ustx_are_rejected(self):
        with self.temporary("pron-refs-") as temporary:
            library = write_svp(Path(temporary) / "library.svp", library=[{"uuid": "g"}])
            with self.assertRaises(ValueError):
                read_pronunciation(library)
            groups = write_svp(Path(temporary) / "groups.svp", extra_groups=[{"groupID": "other"}])
            with self.assertRaises(ValueError):
                read_pronunciation(groups)
            poly = write_svp(Path(temporary) / "poly.svp", overlap=True)
            with self.assertRaises(ValueError):
                read_pronunciation(poly)
            vsqx_poly = write_vsqx(Path(temporary) / "poly.vsqx", polyphonic=True)
            with self.assertRaises(ValueError):
                read_pronunciation(vsqx_poly)
            ustx = Path(temporary) / "unsupported.ustx"
            ustx.write_text("name: fixture\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "USTX"):
                read_pronunciation(ustx)

    def test_vsqx_rejects_dtd_utf16_nul_and_foreign_xml_before_trust(self):
        with self.temporary("pron-xml-safety-") as temporary:
            dtd = Path(temporary) / "dtd.vsqx"
            dtd.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<!DOCTYPE vsq4 [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                '<vsq4 xmlns="http://www.yamaha.co.jp/vocaloid/schema/vsq4/"/>',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "DTD|ENTITY"):
                read_pronunciation(dtd)
            utf16 = Path(temporary) / "utf16.vsqx"
            utf16.write_bytes(
                write_vsqx(Path(temporary) / "base.vsqx").read_text(encoding="utf-8").encode("utf-16")
            )
            with self.assertRaisesRegex(ValueError, "UTF-8|NUL"):
                read_pronunciation(utf16)
            nul = Path(temporary) / "nul.vsqx"
            nul.write_bytes(write_vsqx(Path(temporary) / "base2.vsqx").read_bytes() + b"\0")
            with self.assertRaisesRegex(ValueError, "NUL"):
                read_pronunciation(nul)
            base = write_vsqx(Path(temporary) / "bom-base.vsqx")
            bom = Path(temporary) / "bom.vsqx"
            bom.write_bytes(b"\xef\xbb\xbf" + base.read_bytes())
            self.assertEqual(read_pronunciation(bom)["format"], "vsqx")
            fake_encoding = Path(temporary) / "encoding.vsqx"
            fake_encoding.write_text(
                '<?xml version="1.0" encoding="UTF-16"?>'
                '<vsq4 xmlns="http://www.yamaha.co.jp/vocaloid/schema/vsq4/"/>',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "UTF-8"):
                read_pronunciation(fake_encoding)
            foreign = Path(temporary) / "foreign.vsqx"
            foreign.write_text('<vsq4 xmlns="urn:not-yamaha"/>', encoding="utf-8")
            with self.assertRaises(ValueError):
                read_pronunciation(foreign)

    def test_plan_candidate_hash_regenerates_across_process_boundary(self):
        with self.temporary("pron-cross-process-") as temporary:
            source = write_vsqx(Path(temporary) / "cross.vsqx", version=4)
            plan_file = Path(temporary) / "plan.json"
            script = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from agent_pronunciation import read_pronunciation, plan_pronunciation_patch
source = sys.argv[2]
read = read_pronunciation(source, limit=500)
note = next(row for row in read["notes"] if row["track_index"] == 0)
plan = plan_pronunciation_patch(source, read["source_sha256"], [{"note_ref": note["note_ref"], "phonemes": "m a"}])
with open(sys.argv[3], "w", encoding="utf-8") as stream:
    json.dump(plan, stream, ensure_ascii=False)
'''
            env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
            result = subprocess.run(
                [sys.executable, "-B", "-c", script, str(APP / "src"), str(source), str(plan_file)],
                cwd=APP, env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            plan = json.loads(plan_file.read_text(encoding="utf-8"))
            receipt = apply_pronunciation_patch(source, plan, Path(temporary) / "out")
            self.assertEqual(digest(receipt["candidate_path"]), plan["expected_candidate_sha256"])

    def test_pagination_validation_and_finite_schema(self):
        with self.temporary("pron-page-") as temporary:
            source = write_svp(Path(temporary) / "page.svp")
            for limit, offset in ((True, 0), (0, 0), (501, 0), (1, -1), (1, True)):
                with self.assertRaises(ValueError):
                    read_pronunciation(source, limit=limit, offset=offset)
            read = read_pronunciation(source, limit=500, offset=99)
            self.assertEqual(read["notes"], [])
            self.assertEqual(read["pagination"]["returned"], 0)
            self.assertFalse(read["pagination"]["has_more"])
            self.assertIsNone(read["pagination"]["next_offset"])
            self.assertTrue(all(not row['note_refs'] for row in read['tracks']))
            first=read_pronunciation(source,limit=1)
            self.assertEqual(sum(len(row['note_refs']) for row in first['tracks']),1)
            self.assertGreater(sum(row['note_refs_total'] for row in first['tracks']),1)
            document=read_json(source)
            document['time']['tempo']=[{'position':i*705600000,'bpm':120+5*(i%2)} for i in range(1030)]
            Path(native_path(source)).write_bytes(json.dumps(document,ensure_ascii=False).encode('utf-8')+b'\0')
            limited=read_pronunciation(source,limit=1)
            self.assertEqual(limited['timing']['tempo_events_total'],1030)
            self.assertEqual(len(limited['timing']['tempo_events']),1024)
            self.assertTrue(limited['timing']['tempo_events_truncated'])
            json_ready(read)


def read_json(path):
    return json.loads(Path(path).read_bytes().rstrip(b"\0"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
