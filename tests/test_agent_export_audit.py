"""Meaningful integration tests for the read-only Export audit worker."""

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / "src"))

import mido

import agent_exports
from agent_export_audit import audit_export
from common import native_path


LEGACY_RUNTIME = Path(
    r"C:\Users\DevWind\Documents\Codex\2026-10-08\or-1-2-3-midi4-midi"
    r"\outputs\Better-Vocaloid-Workflow-Assistant\dependencies\export"
)


def runtime_path():
    for value in (
        os.environ.get("VWA_MCP_EXPORT_RUNTIME"),
        str(LEGACY_RUNTIME),
        str(APP / "dependencies/export"),
    ):
        if value and (Path(value) / "libresvip/plugins/mid/midi_converter.py").is_file():
            return str(Path(value).resolve())
    return None


EXPORT_RUNTIME = runtime_path()


def digest(path):
    value = hashlib.sha256()
    with open(native_path(path), "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_bytes(path):
    with open(native_path(path), "rb") as stream:
        return stream.read()


def write_bytes(path, value):
    with open(native_path(path), "wb") as stream:
        stream.write(value)
    return Path(path)


def read_text(path, encoding="utf-8"):
    with open(native_path(path), "r", encoding=encoding) as stream:
        return stream.read()


def write_text(path, value, encoding="utf-8"):
    with open(native_path(path), "w", encoding=encoding) as stream:
        stream.write(value)
    return Path(path)


def unlink(path):
    Path(native_path(path)).unlink()


def json_ready(value):
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    json.loads(text)
    return text


def receipt_body(value):
    if isinstance(value, dict) and isinstance(value.get("receipt"), dict):
        return value["receipt"]
    return value


def sign_receipt(value):
    body = receipt_body(copy.deepcopy(value))
    body.pop("receipt_sha256", None)
    body["receipt_sha256"] = agent_exports._digest(body)
    return body


def write_midi(path, *, with_tempo_change=True):
    """Mainline fixture: 480 PPQN, 120->100 at tick 480, two notes to tick 960."""
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    master = mido.MidiTrack([
        mido.MetaMessage("track_name", name="Tempo", time=0),
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("time_signature", numerator=3, denominator=4, time=0),
    ])
    if with_tempo_change:
        master.append(mido.MetaMessage("set_tempo", tempo=600_000, time=480))
    midi.tracks.append(master)
    lead = mido.MidiTrack([
        mido.MetaMessage("track_name", name="Lead", time=0),
        mido.MetaMessage("lyrics", text="你", time=0),
        mido.Message("note_on", note=60, velocity=88, time=0),
        mido.Message("note_off", note=60, velocity=0, time=480),
        mido.MetaMessage("lyrics", text="好", time=0),
        mido.Message("note_on", note=64, velocity=88, time=0),
        mido.Message("note_off", note=64, velocity=0, time=480),
    ])
    midi.tracks.append(lead)
    with open(native_path(path), "wb") as stream:
        midi.save(file=stream)
    return Path(path)


def write_unison_midi(path):
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf8")
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500_000, time=0),
        mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0),
    ]))
    for title, lyric in (("甲", "同"), ("乙", "异")):
        midi.tracks.append(mido.MidiTrack([
            mido.MetaMessage("track_name", name=title, time=0),
            mido.MetaMessage("lyrics", text=lyric, time=0),
            mido.Message("note_on", note=60, velocity=80, time=0),
            mido.Message("note_off", note=60, velocity=0, time=480),
        ]))
    with open(native_path(path), "wb") as stream:
        midi.save(file=stream)
    return Path(path)


def replace_receipt_value(receipt, **updates):
    body = receipt_body(receipt)
    body.update(updates)
    body["receipt_sha256"] = agent_exports._digest(
        {key: value for key, value in body.items() if key != "receipt_sha256"}
    )
    return body


def audit_result(source, receipt, root):
    return audit_export(
        source,
        receipt,
        export_runtime=EXPORT_RUNTIME,
        output_root=root / "audit-cache",
    )


@unittest.skipUnless(EXPORT_RUNTIME, "readonly LibreSVIP export runtime is unavailable")
class ExportAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = APP / "work"
        cls.work.mkdir(exist_ok=True)
        cls._temporary = tempfile.TemporaryDirectory(
            prefix="agent-export-audit-fixtures-", dir=cls.work
        )
        cls.root = Path(cls._temporary.name)
        cls.source = write_midi(cls.root / "中文 审计.mid")
        cls.plan = agent_exports.plan_export(
            cls.source, ["svp", "ustx", "vsqx"], {"language": "zh"}
        )
        cls.receipt = agent_exports.apply_export(
            cls.source, cls.plan, cls.root / "export", export_runtime=EXPORT_RUNTIME
        )
        cls.audit = audit_result(cls.source, cls.receipt, cls.root)

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def test_three_formats_are_really_parsed_and_receipt_claims_are_independent(self):
        audit = self.audit
        self.assertEqual(
            [candidate["format"] for candidate in audit["candidates"]],
            ["svp", "ustx", "vsqx"],
        )
        self.assertEqual(audit["source_sha256"], digest(self.source))
        self.assertEqual(audit["source"]["note_count"], 2)
        self.assertFalse(audit["inference_performed"])
        self.assertFalse(audit["acoustic_or_phonetic_verified"])
        self.assertIn(audit["status"], {"verified", "differences", "unknown"})
        for candidate in audit["candidates"]:
            self.assertTrue(candidate["parsed"])
            self.assertEqual(candidate["source_sha256"], audit["source_sha256"])
            self.assertEqual(candidate["candidate_sha256"], digest(candidate["candidate_path"]))
            self.assertEqual(candidate["counts"]["source_note_count"], 2)
            self.assertEqual(candidate["counts"]["candidate_note_count"], 2)
            self.assertEqual(candidate["timing"]["comparison"],
                             "seconds_from_each_side_actual_tempo_map")
            self.assertEqual(candidate["timing"]["base_tolerance_ms"], 2.0)
            self.assertIsInstance(candidate["blockers"], list)
            self.assertIsInstance(candidate["warnings"], list)
        top_blocks = {(item["code"], item.get("format")) for item in audit["blockers"]}
        top_warns = {(item["code"], item.get("format")) for item in audit["warnings"]}
        for candidate in audit["candidates"]:
            for item in candidate["blockers"]:
                self.assertIn((item["code"], item.get("format")), top_blocks)
            for item in candidate["warnings"]:
                self.assertIn((item["code"], item.get("format")), top_warns)
        json_ready(audit)
        unsigned = {key: value for key, value in audit.items() if key != "audit_sha256"}
        self.assertEqual(audit["audit_sha256"], agent_exports._digest(unsigned))

    def test_real_tempo_change_can_change_seconds_even_when_ticks_match(self):
        audit = self.audit
        for candidate in audit["candidates"]:
            self.assertEqual(candidate["note_audit"]["missing_count"], 0)
            self.assertEqual(candidate["note_audit"]["extra_count"], 0)
            self.assertEqual(candidate["note_audit"]["pitch_mismatch_count"], 0)
            self.assertEqual(candidate["note_audit"]["lyric_mismatch_count"], 0)
            pairs = candidate["note_audit"]["matched_count"]
            self.assertEqual(pairs, 2)
        # Source note 2 starts at 0.5s because the change begins at that exact
        # point.  Actual converter tempo tables may normalize or retain a
        # redundant anchor at zero; either result must be judged by the parsed
        # effective map and candidate seconds, not by raw tick tuples alone.
        for candidate in audit["candidates"]:
            timing = candidate["timing"]
            self.assertIn("max_onset_error_ms", timing)
            self.assertIn("max_allowed_error_ms", timing)
            if not timing["within_tolerance"]:
                self.assertIn("timing_seconds_mismatch",
                              {item["code"] for item in candidate["blockers"]})
            if not candidate["tempo"]["effective_map_equivalent"]:
                self.assertIn("tempo_content_mismatch",
                              {item["code"] for item in candidate["blockers"]})
        self.assertEqual(audit['status'],'verified')
        for candidate in audit['candidates']:
            self.assertTrue(candidate['timing']['within_tolerance'])
            self.assertLess(candidate['timing']['max_timing_error_ms'],.001)

    def test_tempo_corruption_is_detected_even_when_notes_and_ticks_match(self):
        source=write_midi(self.root/'seconds-drift.mid')
        plan=agent_exports.plan_export(source,['svp'],{'language':'zh'})
        receipt=agent_exports.apply_export(source,plan,self.root/'seconds-drift-export',export_runtime=EXPORT_RUNTIME)
        candidate=receipt_body(receipt)['candidates'][0]
        path=Path(candidate['candidate_path'])
        document=json.loads(read_bytes(path).rstrip(b'\0').decode('utf-8-sig'))
        document['time']['tempo'][0]['bpm']=100.0
        body=json.dumps(document,ensure_ascii=False).encode('utf-8')
        new_sha=hashlib.sha256(body).hexdigest()
        new_path=agent_exports.candidate_path_for(Path(receipt['output_root']),plan['plan_sha256'],'svp',new_sha)
        write_bytes(new_path,body)
        candidate.update(candidate_path=str(new_path),candidate_sha256=new_sha,bytes=len(body))
        audit=audit_result(source,sign_receipt(receipt),self.root/'seconds-drift-cache')
        item=audit['candidates'][0]
        self.assertEqual(item['note_audit']['missing_count'],0)
        self.assertEqual(item['note_audit']['extra_count'],0)
        self.assertEqual(item['note_audit']['pitch_mismatch_count'],0)
        self.assertGreater(item['timing']['max_onset_error_ms'],99)
        self.assertIn('timing_seconds_mismatch',{issue['code'] for issue in item['blockers']})

    def test_native_tempo_positions_match_editor_playback_and_count_in(self):
        for candidate in receipt_body(self.receipt)['candidates']:
            path=Path(candidate['candidate_path'])
            if candidate['format']=='svp':
                data=json.loads(read_bytes(path).rstrip(b'\0').decode('utf-8-sig'))
                self.assertEqual([row['position'] for row in data['time']['tempo']],[0,705600000])
                self.assertEqual([row['bpm'] for row in data['time']['tempo']],[120,100])
            elif candidate['format']=='ustx':
                with agent_exports._runtime_session(Path(EXPORT_RUNTIME),self.root/'native-cache'):
                    import yaml
                    data=yaml.safe_load(read_text(path,encoding='utf-8-sig'))
                self.assertEqual(data['tempos'],[{'position':0,'bpm':120},{'position':480,'bpm':100}])
            else:
                import xml.etree.ElementTree as ET
                root=ET.fromstring(read_bytes(path))
                master=next(row for row in root if row.tag.endswith('masterTrack'))
                tempos=[row for row in master if row.tag.endswith('tempo')]
                self.assertEqual([[int(child.text) for child in row] for row in tempos],
                                 [[0,12000],[1920,10000]])  # 3/4 count-in: 1440 + 480

    def test_nonstandard_meter_ppqn_and_meter_change_keep_physical_time(self):
        source=write_midi(self.root/'meter-960.mid')
        midi=mido.MidiFile(filename=native_path(source),charset='utf8')
        midi.ticks_per_beat=960
        for message in midi.tracks[0]:
            if message.type=='time_signature':message.numerator=3
        midi.tracks[0].append(mido.MetaMessage('time_signature',numerator=6,denominator=8,time=5280))
        with open(native_path(source),'wb') as stream:midi.save(file=stream)
        plan=agent_exports.plan_export(source,['svp','ustx','vsqx'])
        receipt=agent_exports.apply_export(source,plan,self.root/'meter-960-export',export_runtime=EXPORT_RUNTIME)
        audit=audit_result(source,receipt,self.root/'meter-960-cache')
        self.assertEqual(audit['status'],'verified',audit['blockers'])
        for candidate in audit['candidates']:
            self.assertTrue(candidate['timing']['within_tolerance'])

    def test_duplicate_position_tempo_anchor_is_warning_not_hard_failure(self):
        source = write_midi(self.root / "anchor.mid", with_tempo_change=False)
        plan = agent_exports.plan_export(source, ["ustx"], {"language": "zh"})
        receipt = agent_exports.apply_export(
            source, plan, self.root / "anchor-export", export_runtime=EXPORT_RUNTIME
        )
        candidate = receipt_body(receipt)["candidates"][0]
        path = Path(candidate["candidate_path"])
        with agent_exports._runtime_session(
            Path(EXPORT_RUNTIME), self.root / "anchor-yaml-cache"
        ):
            import yaml
            document = yaml.safe_load(read_text(path, encoding="utf-8-sig"))
            document["tempos"].insert(0, copy.deepcopy(document["tempos"][0]))
            body = yaml.safe_dump(
                document, allow_unicode=True, sort_keys=False
            ).encode("utf-8")
        write_bytes(path, body)
        new_sha = digest(path)
        new_path = agent_exports.candidate_path_for(
            Path(receipt["output_root"]), plan["plan_sha256"], "ustx", new_sha
        )
        write_bytes(new_path, body)
        unlink(path)
        candidate["candidate_sha256"] = new_sha
        candidate["candidate_path"] = str(new_path)
        candidate["bytes"] = len(body)
        candidate["tempo_verified"] = False
        signed = sign_receipt(receipt)
        audit = audit_result(source, signed, self.root / "anchor-cache")
        item = audit["candidates"][0]
        codes = {issue["code"] for issue in item["blockers"]}
        warnings = {issue["code"] for issue in item["warnings"]}
        self.assertEqual(item["tempo"]["duplicate_candidate_positions"], [0])
        self.assertTrue(item["tempo"]["effective_map_equivalent"])
        self.assertTrue(item["tempo"]["structure_equivalent"])
        self.assertNotIn("tempo_content_mismatch", codes)
        self.assertIn("tempo_table_structure_normalized", warnings)
        self.assertEqual(item["status"], "verified")

    def test_lyric_tamper_is_detected_from_actual_candidate_text(self):
        source = write_midi(self.root / "lyric.mid")
        plan = agent_exports.plan_export(source, ["svp"], {"language": "zh"})
        receipt = agent_exports.apply_export(
            source, plan, self.root / "lyric-export", export_runtime=EXPORT_RUNTIME
        )
        candidate = receipt_body(receipt)["candidates"][0]
        path = Path(candidate["candidate_path"])
        document = json.loads(read_bytes(path).rstrip(b"\0").decode("utf-8-sig"))
        document["tracks"][0]["mainGroup"]["notes"][0]["lyrics"] = "篡改"
        write_text(path, json.dumps(document, ensure_ascii=False), encoding="utf-8")
        new_sha = digest(path)
        new_path = agent_exports.candidate_path_for(
            Path(receipt["output_root"]), plan["plan_sha256"], "svp", new_sha
        )
        write_bytes(new_path, read_bytes(path))
        unlink(path)
        candidate["candidate_sha256"] = new_sha
        candidate["candidate_path"] = str(new_path)
        signed = sign_receipt(receipt)
        audit = audit_result(source, signed, self.root / "lyric-cache")
        item = audit["candidates"][0]
        self.assertIn("lyric_mismatch", {issue["code"] for issue in item["blockers"]})
        self.assertGreaterEqual(item["note_audit"]["lyric_mismatch_count"], 1)

    def test_pitch_tamper_and_hash_tamper_are_rejected(self):
        source = write_midi(self.root / "pitch.mid")
        plan = agent_exports.plan_export(source, ["svp"], {"language": "zh"})
        receipt = agent_exports.apply_export(
            source, plan, self.root / "pitch-export", export_runtime=EXPORT_RUNTIME
        )
        candidate = receipt_body(receipt)["candidates"][0]
        path = Path(candidate["candidate_path"])
        document = json.loads(read_bytes(path).rstrip(b"\0").decode("utf-8-sig"))
        document["tracks"][0]["mainGroup"]["notes"][1]["pitch"] = 65
        write_text(path, json.dumps(document, ensure_ascii=False), encoding="utf-8")
        new_sha = digest(path)
        new_path = agent_exports.candidate_path_for(
            Path(receipt["output_root"]), plan["plan_sha256"], "svp", new_sha
        )
        write_bytes(new_path, read_bytes(path))
        unlink(path)
        candidate["candidate_sha256"] = new_sha
        candidate["candidate_path"] = str(new_path)
        signed = sign_receipt(receipt)
        audit = audit_result(source, signed, self.root / "pitch-cache")
        item = audit["candidates"][0]
        self.assertIn("pitch_mismatch", {issue["code"] for issue in item["blockers"]})
        self.assertGreaterEqual(item["note_audit"]["pitch_mismatch_count"], 1)

        bad = sign_receipt(copy.deepcopy(receipt))
        bad["candidates"][0]["candidate_sha256"] = "0" * 64
        bad["receipt_sha256"] = agent_exports._digest(
            {key: value for key, value in bad.items() if key != "receipt_sha256"}
        )
        audit_bad = audit_result(source, bad, self.root / "pitch-cache-2")
        self.assertIn("candidate_path_mismatch",
                      {issue["code"] for issue in audit_bad["blockers"]})

    def test_unison_identity_is_kept_ambiguous_instead_of_invented(self):
        source = write_unison_midi(self.root / "unison.mid")
        plan = agent_exports.plan_export(source, ["svp"], {"language": "zh"})
        receipt = agent_exports.apply_export(
            source, plan, self.root / "unison-export", export_runtime=EXPORT_RUNTIME
        )
        audit = audit_result(source, receipt, self.root / "unison-cache")
        item = audit["candidates"][0]
        associations = item["tracks"]["association"].values()
        self.assertTrue(any(entry["ambiguous"] for entry in associations))
        self.assertIn(
            "track_association_ambiguous",
            {issue["code"] for issue in item["warnings"]},
        )
        self.assertEqual(item["note_audit"]["lyric_mismatch_count"], 0)

    def test_source_and_candidate_hash_changes_are_unknown_not_success(self):
        source = write_midi(self.root / "source-hash.mid", with_tempo_change=False)
        plan = agent_exports.plan_export(source, ["svp"], {"language": "zh"})
        receipt = agent_exports.apply_export(
            source, plan, self.root / "source-hash-export", export_runtime=EXPORT_RUNTIME
        )
        midi = mido.MidiFile(filename=native_path(source), charset="utf8")
        for track in midi.tracks:
            for message in track:
                if message.type == "note_on" and message.velocity:
                    message.velocity = 77
                    break
        with open(native_path(source), "wb") as stream:
            midi.save(file=stream)
        source_audit = audit_result(source, receipt, self.root / "source-hash-cache")
        self.assertEqual(source_audit["status"], "unknown")
        self.assertIn(
            "source_sha256_mismatch",
            {issue["code"] for issue in source_audit["blockers"]},
        )

        source2 = write_midi(self.root / "candidate-hash.mid", with_tempo_change=False)
        plan2 = agent_exports.plan_export(source2, ["svp"], {"language": "zh"})
        receipt2 = agent_exports.apply_export(
            source2, plan2, self.root / "candidate-hash-export",
            export_runtime=EXPORT_RUNTIME,
        )
        candidate_path = Path(receipt2["candidates"][0]["candidate_path"])
        changed = read_bytes(candidate_path) + b" "
        write_bytes(candidate_path, changed)
        candidate_audit = audit_result(
            source2, receipt2, self.root / "candidate-hash-cache"
        )
        self.assertEqual(candidate_audit["status"], "unknown")
        self.assertIn(
            "candidate_sha256_mismatch",
            {issue["code"] for issue in candidate_audit["blockers"]},
        )

    def test_source_receipt_and_stable_repeat_are_verified(self):
        repeat = audit_result(self.source, self.receipt, self.root)
        self.assertEqual(repeat["audit_sha256"], self.audit["audit_sha256"])
        self.assertEqual(repeat["candidates"], self.audit["candidates"])
        # The isolated app also contains a copy of the export runtime. Pin
        # automatic discovery to the runtime already loaded explicitly above;
        # mixing two distinct package roots is correctly rejected.
        with patch.dict(os.environ, {"VWA_MCP_EXPORT_RUNTIME": str(EXPORT_RUNTIME)}):
            discovered = audit_export(
                self.source, self.receipt, output_root=self.root / "discovered-cache"
            )
        self.assertEqual(discovered["audit_sha256"], repeat["audit_sha256"])
        changed = copy.deepcopy(receipt_body(self.receipt))
        changed["plan_sha256"] = "1" * 64
        audit = audit_result(self.source, changed, self.root)
        self.assertIn("receipt_sha256_mismatch",
                      {item["code"] for item in audit["blockers"]})
        self.assertEqual(audit["source_sha256"], self.audit["source_sha256"])

    def test_cancellation_is_explicit_and_non_destructive(self):
        with self.assertRaises(InterruptedError):
            audit_export(
                self.source, self.receipt, export_runtime=EXPORT_RUNTIME,
                output_root=self.root / "cancel-cache", cancel=lambda: True,
            )
        self.assertTrue(self.source.is_file())


if __name__ == "__main__":
    unittest.main()
