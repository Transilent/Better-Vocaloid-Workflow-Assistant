"""Independent, recoverable SVP context analysis and explicit audition copies.

Run from the generated application root. The original job, project and lyrics
are read-only. No synthesis, network service or automatic emotion-value mapping.
"""


def canonical_kind(value):
    return 'vwa.' + value[5:] if isinstance(value, str) and value.startswith('bvwa.') else value


import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from context_alignment import align_lyrics
from context_evidence import build_evidence, verify_evidence_fingerprint
from context_planner import propose_plan, validate_proposal
from vocal_expression import TempoMap

SCHEMA = 1
MAX_BYTES = 64 * 1024**2


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read_json(path):
    path = Path(path)
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError('Context project record exceeds 64 MiB.')
    value = json.loads(raw.rstrip(b'\x00').decode('utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError('Context project record must be an object.')
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')


def _code():
    names = ('context_project.py', 'context_alignment.py', 'context_reading.py',
             'context_evidence.py', 'context_planner.py', 'context_structure.py',
              'context_semantics.py', 'gesture_catalog.py', 'context_plan.py',
              'vocal_expression.py')
    root = Path(__file__).resolve().parent
    return {name: digest((root/name).read_bytes()) for name in names}


def _safe_file(root, name):
    if not isinstance(name, str) or Path(name).name != name or name in ('.', '..'):
        raise ValueError('Invalid context bundle filename.')
    return root/name


def _existing(destination, request_id):
    if not destination.exists():
        return None
    manifest = read_json(destination/'manifest.json')
    if (canonical_kind(manifest.get('kind')) != 'vwa.context-project-bundle'
            or type(manifest.get('schema_version')) is not int or manifest['schema_version'] != SCHEMA
            or manifest.get('request_id') != request_id or manifest.get('status') not in ('prepared', 'applied')):
        raise ValueError('Existing context output has different inputs; use a new directory.')
    required = ({'baseline.svp','inputs.json','alignment.json','evidence.json','readings.json','proposal.json'}
                if manifest['status'] == 'prepared' else
                {'A-before.svp','B-context-trial.svp','application.json','profile.json','receipt.json'})
    if not isinstance(manifest.get('files'), dict) or set(manifest['files']) != required:
        raise ValueError('Existing context output has an incomplete artifact manifest.')
    for name, expected in manifest.get('files', {}).items():
        if digest(_safe_file(destination, name).read_bytes()) != expected:
            raise ValueError('Existing context output was modified: ' + name)
    if manifest['status'] == 'prepared':
        inputs = read_json(destination/'inputs.json')
        if (digest(canonical(inputs)) != request_id
                or digest((destination/'baseline.svp').read_bytes()) != inputs.get('baseline_sha256')
                or manifest.get('source_sha256') != inputs.get('project_sha256')
                or manifest.get('code_sha256') != inputs.get('code_sha256')):
            raise ValueError('Prepared baseline or analysis inputs differ from their bound revision.')
    return manifest


def _commit(destination, request_id, payloads, **metadata):
    destination = Path(destination).resolve()
    if existing := _existing(destination, request_id):
        return existing
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.with_name('.'+destination.name+'.pending-'+uuid.uuid4().hex)
    stage.mkdir()
    files = {}
    for name, value in payloads.items():
        raw = value if isinstance(value, bytes) else _json(value)
        if len(raw) > MAX_BYTES:
            raise ValueError('Context output too large: ' + name)
        _safe_file(stage, name).write_bytes(raw)
        files[name] = digest(raw)
    manifest = {'schema_version': SCHEMA, 'kind': 'vwa.context-project-bundle',
                'request_id': request_id, 'files': files, **metadata}
    (stage/'manifest.json').write_bytes(_json(manifest))
    # Final directory is committed only after every complete artifact exists.
    # Interrupted pending directories remain recoverable and are never trusted.
    os.rename(stage, destination)
    return manifest


def extract_score(project):
    """Return flat-track absolute notes and bindings, preserving all note data."""
    clock = TempoMap(project['time']['tempo'])
    evidence_notes, bindings = {}, {}
    for index, track in enumerate(project.get('tracks', [])):
        if track.get('mainRef', {}).get('isInstrumental'):
            continue
        notes = track.get('mainGroup', {}).get('notes', [])
        if not notes:
            continue
        ref = track.get('mainRef', {})
        if (track.get('groups') or ref.get('blickAbsoluteBegin', 0) != 0
                or ref.get('blickAbsoluteEnd', -1) != -1):
            raise ValueError('Context audition currently requires flat, untrimmed note groups.')
        offset, pitch_offset = ref.get('blickOffset', 0), ref.get('pitchOffset', 0)
        if type(offset) is not int or type(pitch_offset) is not int:
            raise ValueError('Invalid context group offset.')
        voice = 'lead' if not bindings else 'backing' if len(bindings) == 1 else 'voice'+str(index)
        bindings[voice] = index
        values = []
        for note in notes:
            if any(type(note.get(key)) is not int for key in ('onset', 'duration', 'pitch')) or note['duration'] <= 0:
                raise ValueError('Invalid SVP note.')
            values.append({'lyric': note.get('lyrics', ''),
                           'pitch': note['pitch']+pitch_offset,
                           'start': clock.to_seconds(note['onset']+offset),
                           'end': clock.to_seconds(note['onset']+offset+note['duration'])})
        evidence_notes[voice] = sorted(values, key=lambda row: (row['start'], row['end'], row['pitch']))
    if not bindings:
        raise ValueError('No singing notes in the selected SVP.')
    return evidence_notes, bindings


def _relocate_media(project, original_folder):
    changes = []
    for track in project.get('tracks', []):
        for ref in [track.get('mainRef', {}), *track.get('groups', [])]:
            audio = ref.get('audio')
            if not isinstance(audio, dict) or not audio.get('filename'):
                continue
            path = Path(audio['filename'])
            resolved = path if path.is_absolute() else (original_folder/path).resolve()
            if not resolved.is_file():
                raise FileNotFoundError('Referenced accompaniment is missing: '+str(resolved))
            if not path.is_absolute():
                changes.append({'before': audio['filename'], 'after': str(resolved)})
                audio['filename'] = str(resolved)
    return changes


def prepare_bundle(project_path, lyrics, language, destination, *, reading_units=None, word_spans=None):
    """Explicit opt-in partial alignment, with optional local Japanese readings."""
    source = Path(project_path).resolve()
    before = source.read_bytes()
    project = read_json(source)
    if source.read_bytes() != before:
        raise ValueError('Source project changed while reading.')
    notes, bindings = extract_score(project)
    if (language not in ('zh', 'ja') or not isinstance(lyrics, dict)
            or set(lyrics)-set(bindings) or any(not isinstance(x, str) for x in lyrics.values())):
        raise ValueError('Lyrics must contain only current voice names and original text.')
    reading_units = copy.deepcopy(reading_units) if reading_units is not None else {}
    word_spans = copy.deepcopy(word_spans) if word_spans is not None else {}
    if (not isinstance(reading_units, dict) or not isinstance(word_spans, dict)
            or set(reading_units)-set(bindings) or set(word_spans)-set(bindings)):
        raise ValueError('Reading/word inputs must use current voice names.')
    media_changes = _relocate_media(project, source.parent)
    baseline_bytes = _json(project) if media_changes else before
    inputs = {'project_sha256': digest(before), 'baseline_sha256': digest(baseline_bytes),
              'lyrics': copy.deepcopy(lyrics),
              'language': language, 'explicit_reading_units': reading_units,
              'explicit_word_spans': word_spans, 'code_sha256': _code()}
    if language == 'ja' and any(text and voice not in reading_units for voice, text in lyrics.items()):
        from context_reading import local_reading_identity
        inputs['local_reading_runtime'] = local_reading_identity()
    request_id = digest(canonical(inputs))
    destination = Path(destination).resolve()
    if existing := _existing(destination, request_id):
        return existing
    evidence, alignment, readings = {}, {}, {}
    for voice, score in notes.items():
        text = lyrics.get(voice, '')
        units, words = reading_units.get(voice), word_spans.get(voice, [])
        if language == 'ja' and text and units is None:
            from context_reading import japanese_reading_units
            reading = japanese_reading_units(text)
            readings[voice] = reading
            units = reading['units']
            if voice not in word_spans:
                words = reading['words']
        alignment[voice] = align_lyrics(text, language, score, reading_units=units)
        evidence[voice] = build_evidence(text, language, score, voice=voice,
            word_spans=words, alignment=alignment[voice],
            provenance={'score_source_sha256': digest(before), 'text_transform': 'none',
                        'text_source': 'explicit_lyrics_input',
                        'score_source': str(source), 'timing_source': 'svp_tempo_and_note_offsets'})
    proposal = propose_plan(evidence)
    validate_proposal(proposal, evidence, verify_code=True)
    if source.read_bytes() != before:
        raise ValueError('Source project changed during analysis.')
    if _code() != inputs['code_sha256']:
        raise ValueError('Analysis code changed during preparation; retry with stable code.')
    return _commit(destination, request_id,
        {'baseline.svp': baseline_bytes, 'inputs.json': inputs, 'alignment.json': alignment,
         'evidence.json': evidence, 'readings.json': readings, 'proposal.json': proposal},
        status='prepared', language=language, track_bindings=bindings,
        source_project=str(source), source_sha256=digest(before),
        media_relocations=media_changes, code_sha256=_code(),
        audio_rendered=False, parameter_application='not_applied_advisory_only')


def densify_linear(project, *, step=.005):
    """Add samples while preserving the existing piecewise-linear function.

    A new cubic fit can move values between original knots. Context audition
    copies require unchanged continuous values outside intentional gestures.
    """
    import numpy as np
    from expression_calibration import CONTROLS
    if type(step) not in (int, float) or not math.isfinite(step) or not .001 <= step <= .02:
        raise ValueError('Invalid context density step.')
    clock = TempoMap(project['time']['tempo'])
    updates, rows = [], []
    for index, track in enumerate(project['tracks']):
        if track.get('mainRef', {}).get('isInstrumental'):
            continue
        offset = track.get('mainRef', {}).get('blickOffset', 0)
        controls = {}
        for name, curve in track.get('mainGroup', {}).get('parameters', {}).items():
            if name not in CONTROLS or curve.get('mode', 'cubic') != 'linear':
                continue
            points = curve.get('points', [])
            if not points:
                continue
            xs, ys = points[::2], points[1::2]
            if (len(points) % 2 or any(type(x) is not int for x in xs)
                    or any(a >= b for a,b in zip(xs,xs[1:]))
                    or any(type(y) not in (int,float) or not math.isfinite(y) for y in ys)):
                raise ValueError('Invalid linear curve for density: '+name)
            extra = []
            estimate = len(xs)
            for left,right,y0,y1 in zip(xs,xs[1:],ys,ys[1:]):
                if y0 == y1:
                    continue
                start,end = clock.to_seconds(left+offset),clock.to_seconds(right+offset)
                estimate += math.ceil((end-start)/step)+1
                if estimate > 2_000_000:
                    raise ValueError('Context curve density exceeds the point limit.')
                extra.extend(clock.to_position(float(t))-offset
                    for t in np.arange(math.ceil(start/step), math.floor(end/step)+1)*step
                    if start < t < end)
                extra.extend(p-offset for p in clock.positions if left < p-offset < right)
            grid = sorted(set(xs+extra))
            values = np.interp(grid, xs, ys).tolist()
            lookup = {x:y for x,y in zip(xs,ys)}
            values = [lookup.get(x,y) for x,y in zip(grid,values)]
            replacement = [v for pair in zip(grid,values) for v in pair]
            updates.append((curve,replacement))
            controls[name] = {'points_before':len(xs), 'points_after':len(grid),
                'added_points':len(grid)-len(xs), 'original_knots_preserved':True,
                'continuous_linear_function_preserved':True, 'range_unchanged':True}
        rows.append({'track_index':index,'controls':controls})
    for curve,points in updates:
        curve['points'] = points
    return {'step_seconds':step,'interpolation':'original_piecewise_linear',
            'pitch_and_notes_untouched':True,'tracks':rows}


def apply_bundle(bundle, application, profile, destination):
    """Write A/B and a reproducible receipt only after pure adaptation succeeds."""
    from context_adapter import adapt_svp
    root = Path(bundle).resolve()
    manifest = read_json(root/'manifest.json')
    if (canonical_kind(manifest.get('kind')) != 'vwa.context-project-bundle' or manifest.get('status') != 'prepared'
            or manifest.get('schema_version') != SCHEMA):
        raise ValueError('A prepared context analysis bundle is required.')
    if manifest.get('code_sha256') != _code():
        raise ValueError('Analysis code changed; prepare a fresh analysis bundle.')
    _existing(root, manifest['request_id'])
    evidence = read_json(root/'evidence.json')
    for row in evidence.values():
        verify_evidence_fingerprint(row)
    validate_proposal(read_json(root/'proposal.json'), evidence, verify_code=True)
    application, profile = copy.deepcopy(application), copy.deepcopy(profile)
    if application.get('track_bindings') != manifest['track_bindings']:
        raise ValueError('Application track binding differs from prepared analysis.')
    adapter_hash = digest(canonical({name: digest(Path(__file__).with_name(name).read_bytes())
        for name in ('context_adapter.py','calibration_refinement.py','expression_calibration.py','vocal_expression.py')}))
    request_id = digest(canonical({'analysis': digest((root/'manifest.json').read_bytes()),
        'application': application, 'profile': profile, 'adapter_code_sha256': adapter_hash}))
    destination = Path(destination).resolve()
    if existing := _existing(destination, request_id):
        return existing
    original = read_json(root/'baseline.svp')
    result, adaptation_receipt = adapt_svp(original, evidence, application, profile=profile)
    if canonical(result) == canonical(original):
        raise ValueError('The selected application produced no parameter differences.')
    # Keep intentional expression changes separate from shape-preserving density.
    # The latter retains every incoming knot and amplitude, including long notes.
    adapted_content_sha = digest(canonical(result))
    density = densify_linear(result, step=.005)
    result_bytes = _json(result)
    receipt = {'schema_version': 1, 'kind': 'vwa.context-project-application-receipt',
               'adapter': adaptation_receipt, 'expression_density': density,
               'adapted_content_sha256_before_density': adapted_content_sha,
               'final_content_sha256': digest(canonical(result))}
    # Re-read the serialized candidate, not just its in-memory object.
    if json.loads(result_bytes.decode('utf-8')) != result:
        raise ValueError('SVP serialization changed the candidate.')
    receipt.update({'source_bundle_sha256': digest((root/'manifest.json').read_bytes()),
        'adapter_code_sha256': adapter_hash, 'audio_rendered': False,
        'A_sha256': digest((root/'baseline.svp').read_bytes()), 'B_sha256': digest(result_bytes),
        'acoustic_evaluation': 'not_run', 'listening_evaluation': 'awaiting_user'})
    # Catch changes made to the analysis while the candidate was being built.
    _existing(root, manifest['request_id'])
    return _commit(destination, request_id,
        {'A-before.svp': (root/'baseline.svp').read_bytes(), 'B-context-trial.svp': result_bytes,
         'application.json': application, 'profile.json': profile, 'receipt.json': receipt},
        status='applied', source_analysis=str(root),
        audio_rendered=False, parameter_application='explicit_experimental_candidate',
        adapter_code_sha256=adapter_hash)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Create independent, explicit SVP context audition copies.')
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare')
    prepare.add_argument('--project', type=Path, required=True)
    prepare.add_argument('--lyrics', type=Path, required=True, help='JSON voice -> verbatim original text')
    prepare.add_argument('--language', choices=('zh', 'ja'), required=True)
    prepare.add_argument('--readings', type=Path)
    prepare.add_argument('--words', type=Path)
    prepare.add_argument('--output', type=Path, required=True)
    apply = commands.add_parser('apply')
    apply.add_argument('--bundle', type=Path, required=True)
    apply.add_argument('--application', type=Path, required=True)
    apply.add_argument('--profile', type=Path, required=True)
    apply.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        result = prepare_bundle(args.project, read_json(args.lyrics), args.language, args.output,
            reading_units=read_json(args.readings) if args.readings else None,
            word_spans=read_json(args.words) if args.words else None)
    else:
        result = apply_bundle(args.bundle, read_json(args.application), read_json(args.profile), args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
