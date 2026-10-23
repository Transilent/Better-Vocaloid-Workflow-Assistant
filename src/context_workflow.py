"""Recoverable context evidence and advisory exports over existing scores.

This layer never infers lyrics, renders audio, or modifies project parameters.
"""


def canonical_kind(value):
    return 'vwa.' + value[5:] if isinstance(value, str) and value.startswith('bvwa.') else value


import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import sys

# The embedded interpreter does not add script directories automatically.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import atomic_json, cancelled, file_hash, preserve_changed_file
from artifact_cache import fingerprint
from context_evidence import build_evidence, verify_evidence_fingerprint
from context_plan import make_plan, validate_plan, plan_fingerprint

SCHEMA = 1
MAX_RECORD_BYTES = 8 * 1024**2
PROPOSAL_INPUT_PATH = 'context/proposal-input.json'
PROPOSAL_PATH = 'context/proposal.json'
PROPOSAL_INPUT_KIND = 'vwa.context-proposal-input'


def _path(job, name):
    from task_contract import resolve
    return resolve(job, {'location': {'kind': 'task', 'path': name}})


def _read(path):
    path = Path(path)
    if path.stat().st_size > MAX_RECORD_BYTES:
        raise ValueError('语境记录过大：' + path.name)
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('语境记录必须为JSON对象：' + path.name)
    return data


def _read_stable(path):
    path = Path(path)
    before = file_hash(path)
    data = _read(path)
    after = file_hash(path)
    if before != after:
        raise ValueError('读取语境输入期间文件发生变化，请重试：' + path.name)
    return data, after


def _check_record_capacity(data):
    # Match atomic_json's pretty UTF-8 output, including Windows text newlines.
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    if sys.platform == 'win32':
        text = text.replace('\n', '\r\n')
    # Reserve space for the preparing/ready commit metadata. Never write an
    # advisory record that this same workflow would refuse to read on resume.
    if len(text.encode('utf-8')) + 4096 > MAX_RECORD_BYTES:
        raise ValueError('提案超过语境记录容量；当前计划与输入未替换，请缩小待分析片段。')


def requested(request):
    from project_exports import context_requested
    return context_requested(request)


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('语境乐谱包含无效数值。')
    return value


def _notes(path):
    from project_exports import read_ustx
    data = read_ustx(path)
    if not isinstance(data, dict):
        raise ValueError('语境输入不是有效USTX。')
    resolution = _number(data.get('resolution', 480))
    if resolution <= 0:
        raise ValueError('USTX分辨率无效。')
    tempos = data.get('tempos') or [{'position': 0, 'bpm': data.get('bpm', 120)}]
    points, bpms, seconds = [], [], []
    for row in sorted(tempos, key=lambda row: _number(row['position'])):
        position, bpm = _number(row['position']), _number(row['bpm'])
        if bpm <= 0 or position < 0 or (points and position <= points[-1]):
            raise ValueError('USTX速度表无效。')
        if not points and position != 0:
            raise ValueError('USTX速度表需要从0开始。')
        seconds.append(seconds[-1] + (position - points[-1]) / resolution * 60 / bpms[-1] if points else 0.)
        points.append(position)
        bpms.append(bpm)

    def to_seconds(tick):
        import bisect
        index = max(0, bisect.bisect_right(points, tick) - 1)
        return seconds[index] + (tick - points[index]) / resolution * 60 / bpms[index]

    notes = []
    for part in data.get('voice_parts', []):
        offset = _number(part.get('position', 0))
        for note in part.get('notes', []):
            start = offset + _number(note['position'])
            end = start + _number(note['duration'])
            notes.append({'lyric': note.get('lyric', ''), 'pitch': _number(note['tone']),
                          'start': to_seconds(start), 'end': to_seconds(end)})
    return sorted(notes, key=lambda note: (note['start'], note['end'], note['pitch']))


def collect_evidence(job, request):
    """Bind current score facts to verbatim lyrics; accept only verified timing."""
    from project_exports import voices_for
    job = Path(job).resolve()
    words = request.get('context_word_spans')
    if words is None:
        source = _path(job, 'context/word-spans.json')
        words = _read(source) if source.is_file() else {}
    if not isinstance(words, dict) or set(words) - set(voices_for(request)):
        raise ValueError('词范围需按当前声部分组。')
    result = {}
    for voice in voices_for(request):
        cancelled(job)
        folder = 'midi/' + ('' if voice == 'vocals' else voice + '/')
        score = _path(job, folder + voice + '.ustx')
        score_hash = file_hash(score)
        metadata_path = _path(job, folder + 'report.json')
        metadata = _read(metadata_path) if metadata_path.is_file() else {}
        quality = _path(job, folder + voice + '.quality.json')
        expected_score = metadata.get('native_sha256', {}).get('ustx')
        expected_quality = metadata.get('output_sha256', {}).get('quality.json')
        quality_hash = file_hash(quality) if quality.is_file() else None
        trusted = bool(expected_score == score_hash and quality_hash and expected_quality == quality_hash)
        syllables = _read(quality).get('syllables', []) if trusted else []
        text_key = 'backing_lyrics' if voice == 'backing' else 'lyrics'
        provenance = {'score_file': score.relative_to(job).as_posix(), 'score_sha256': score_hash,
                      'score_status': 'producer_verified' if expected_score == score_hash else 'current_unverified',
                      'quality_sha256': quality_hash, 'timing_status': 'verified' if trusted else 'unavailable_or_unverified',
                      'text_source': 'request.' + text_key, 'text_transform': 'none'}
        result[voice] = build_evidence(request.get(text_key, ''), request['language'], _notes(score),
                                      syllables, voice=voice, provenance=provenance,
                                      word_spans=words.get(voice, []))
        final_quality_hash = file_hash(quality) if quality.is_file() else None
        if file_hash(score) != score_hash or final_quality_hash != quality_hash:
            raise ValueError('建立语境证据期间源文件发生变化，请重试。')
    return result


def _plan_path(job, request):
    value = request.get('context_plan')
    if value is not None:
        from common import app_path
        if not isinstance(value, str) or not value.strip():
            raise ValueError('context_plan必须为计划文件路径。')
        path = Path(value)
        return app_path(path) if path.is_absolute() or value.startswith('@/') else _path(job, value)
    return _path(job, 'context/plan.json')


def _baseline(job, request):
    from project_exports import read_receipts, cached_output, project_signatures
    if 'svp' not in request.get('project_formats', []):
        return {'status': 'not_selected'}
    path = _path(job, 'exports/voices.svp')
    item = cached_output(job, path, project_signatures(job, request)['svp'], read_receipts(job))
    if not item:
        return {'status': 'missing_or_unverified'}
    return {'status': 'verified', 'file': item['file'], 'sha256': item['sha256'],
            'input_signature': item['input_signature']}


def _inputs(job, request):
    from context_planner import validate_proposal
    evidence = collect_evidence(job, request)
    plan = validate_plan(_read(_plan_path(job, request)), evidence)
    baseline = _baseline(job, request)
    proposal = None
    proposal_source = None
    proposal_path = _path(job, PROPOSAL_PATH)
    if proposal_path.is_file():
        proposal_source, source_digest = _proposal_source(job)
        if proposal_source is None:
            raise ValueError('提案缺少独立输入文件。')
        semantic = copy.deepcopy(proposal_source['semantic_response'])
        proposal = validate_proposal(
            _read_stable(proposal_path)[0], evidence,
            semantic_response=semantic, verify_code=True, require_ready=True)
        if proposal.get('commit', {}).get('proposal_input_sha256') != source_digest:
            raise ValueError('提案输入文件已变化，请重新 propose。')
        if proposal_source.get('goal') != proposal['analysis'].get('goal'):
            raise ValueError('提案goal与独立输入文件不一致。')
        if 'context_goal' in request and request['context_goal'] != proposal_source.get('goal'):
            raise ValueError('请求中的语境目标与已保存提案输入不一致，请重新 propose。')
    signature = _signature(evidence, plan, baseline, proposal, proposal_source)
    return evidence, plan, baseline, signature, proposal, proposal_source


def _signature(evidence, plan, baseline, proposal=None, proposal_source=None):
    from context_planner import proposal_fingerprint
    return fingerprint({
        'schema': SCHEMA,
        'plan': plan_fingerprint(plan, evidence),
        'proposal': proposal_fingerprint(proposal) if proposal is not None else None,
        'proposal_source': proposal_source,
        'baseline': baseline,
        'code': {name: file_hash(Path(__file__).with_name(name))
                 for name in ('context_evidence.py', 'context_plan.py', 'context_workflow.py',
                              'context_planner.py') if Path(__file__).with_name(name).is_file()},
    })


def _payloads(evidence, plan, baseline, proposal=None, proposal_source=None):
    payloads = {'exports/context/' + voice + '.evidence.json': value for voice, value in evidence.items()}
    payloads['exports/context/plan.snapshot.json'] = plan
    if proposal is not None:
        for voice, value in proposal['analysis']['structures'].items():
            payloads['exports/context/' + voice + '.structure.json'] = value
    payloads['exports/context/preview.json'] = {
        'schema_version': SCHEMA, 'kind': 'vwa.context-preview', 'status': 'advisory_only',
        'evidence_ids': {voice: value['evidence_id'] for voice, value in evidence.items()},
        'selected_candidate': plan['selected_candidate'], 'candidates': plan['candidates'],
        'baseline': baseline, 'parameter_application': 'not_applied_advisory_only',
        'project_written': False, 'audio_rendered': False}
    if proposal is not None:
        payloads['exports/context/proposal-input.snapshot.json'] = proposal_source
        payloads['exports/context/proposal.snapshot.json'] = proposal
    return payloads


def _obsolete(receipts, payloads):
    return [name for name, item in receipts.items()
            if name.startswith('exports/context/') and name not in payloads
            and isinstance(item, dict) and item.get('context_status') == 'advisory_only'
            and item.get('active', True)]


def prepare_context(job, request, *, refresh_draft=False):
    """Create a draft only on explicit preparation, preserving any old plan."""
    job = Path(job).resolve()
    evidence = collect_evidence(job, request)
    path = _plan_path(job, request)
    if not path.exists() or refresh_draft:
        if request.get('context_plan'):
            raise ValueError('外部计划为只读输入；请先在任务内创建草稿。')
        draft = make_plan(evidence)
        if path.exists():
            preserve_changed_file(job, path, None, fingerprint(draft))
        atomic_json(path, draft)
    return export_context(job, request)


def export_context(job, request):
    """Commit advice independently after base project export; never write SVP."""
    from project_exports import read_receipts, cached_output, commit_receipt
    from task_contract import index_export
    job = Path(job).resolve()
    evidence, plan, baseline, signature, proposal, proposal_source = _inputs(job, request)
    payloads = _payloads(evidence, plan, baseline, proposal, proposal_source)
    receipts = read_receipts(job)
    outputs = []
    for name, data in payloads.items():
        cancelled(job)
        path = _path(job, name)
        item = cached_output(job, path, signature, receipts)
        if item:
            index_export(job, item, replace=True)
        else:
            if _inputs(job, request)[3] != signature:
                raise ValueError('语境导出期间输入发生变化，请重试。')
            saved = preserve_changed_file(job, path, receipts.get(name, {}).get('sha256'), fingerprint(data)) if path.exists() else None
            atomic_json(path, data)
            item = commit_receipt(job, path, signature, receipts, context_status='advisory_only')
            if saved:
                item['preserved_copy'] = saved
                atomic_json(job/'exports/receipts.json', receipts)
        outputs.append(item)
    if _inputs(job, request)[3] != signature:
        raise ValueError('语境导出期间输入发生变化，已完成文件保留，请重试。')
    obsolete = _obsolete(receipts, payloads)
    if obsolete:
        from task_contract import retire_exports
        retire_exports(job, obsolete)
        for name in obsolete:
            receipts[name]['active'] = False
        atomic_json(job/'exports/receipts.json', receipts)
    return {'status': 'advisory_only', 'outputs': outputs, 'selected_candidate': plan['selected_candidate'],
            'baseline': baseline, 'parameter_application': 'not_applied_advisory_only'}


def outputs_intact(job, request):
    from project_exports import read_receipts, cached_output
    try:
        evidence, plan, baseline, signature, proposal, proposal_source = _inputs(Path(job).resolve(), request)
        receipts = read_receipts(Path(job))
        if _obsolete(receipts, _payloads(evidence, plan, baseline, proposal, proposal_source)):
            return False
        return all(cached_output(Path(job), _path(job, name), signature, receipts)
                   for name in _payloads(evidence, plan, baseline, proposal, proposal_source))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'project':
        from context_project import main as project_main
        return project_main(sys.argv[2:])
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Prepare or validate VWA context advice without model inference.',
        epilog='For independent SVP audition copies: context_workflow.py project --help')
    parser.add_argument('command', choices=('prepare', 'propose', 'export'))
    parser.add_argument('--job', required=True, type=Path)
    parser.add_argument('--plan', type=str)
    parser.add_argument('--words', type=Path, help='JSON object: voice -> explicit word spans')
    parser.add_argument('--refresh-draft', action='store_true', help='Back up old task plan, then create an empty draft or replace it with a proposal')
    parser.add_argument('--goal', help='Unparsed user goal retained with the proposal input')
    parser.add_argument('--semantic-response', type=Path, help='Explicit imported semantic response JSON')
    args = parser.parse_args()
    if args.refresh_draft and args.command not in ('prepare', 'propose'):
        parser.error('--refresh-draft requires prepare or propose')
    if args.goal is not None and args.command != 'propose':
        parser.error('--goal requires propose')
    if args.semantic_response is not None and args.command != 'propose':
        parser.error('--semantic-response requires propose')
    request = _read(args.job/'request.json')
    if args.plan:
        request = {**request, 'context_plan': args.plan}
    pending_words = None
    if args.words:
        words, _ = _read_stable(args.words)
        request = {**request, 'context_word_spans': words}
        if args.command == 'prepare':
            # Validate new spans before replacing the persistent manual input.
            prospective = collect_evidence(args.job, request)
            plan_path = _plan_path(args.job, request)
            if plan_path.is_file() and not args.refresh_draft:
                validate_plan(_read(plan_path), prospective)
            elif args.plan:
                raise ValueError('外部计划不存在。')
            path = _path(args.job, 'context/word-spans.json')
            if path.is_file():
                preserve_changed_file(args.job, path, None, None)
            atomic_json(path, words)
        elif args.command == 'propose':
            # Validate now, but only replace the persisted segmentation after
            # proposal guard/validation succeeds.
            prospective = collect_evidence(args.job, request)
            pending_words = words
    semantic = _read_stable(args.semantic_response)[0] if args.semantic_response else None
    if args.command == 'prepare':
        result = prepare_context(args.job, request, refresh_draft=args.refresh_draft)
    elif args.command == 'propose':
        result = propose_context(args.job, request, goal=args.goal,
                                 semantic_response=semantic,
                                 refresh_draft=args.refresh_draft)
        if pending_words is not None:
            path = _path(args.job, 'context/word-spans.json')
            if path.is_file():
                preserve_changed_file(args.job, path, None, None)
            atomic_json(path, pending_words)
            # Persist the same bytes the successful proposal already used.
    else:
        result = export_context(args.job, request)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _proposal_source_path(job):
    return _path(job, PROPOSAL_INPUT_PATH)


def _proposal_source(job):
    import hashlib
    path = _proposal_source_path(job)
    if not path.is_file():
        return None, None
    source, digest = _read_stable(path)
    expected = {'schema_version', 'kind', 'goal', 'semantic_response', 'source_sha256'}
    if set(source) != expected or source.get('schema_version') != SCHEMA or canonical_kind(source.get('kind')) != PROPOSAL_INPUT_KIND:
        raise ValueError('提案输入文件版本或字段无效。')
    if not isinstance(source.get('goal'), str):
        raise ValueError('提案输入goal必须为文本。')
    semantic = source.get('semantic_response')
    if semantic is not None and not isinstance(semantic, dict):
        raise ValueError('提案输入semantic_response必须为对象或null。')
    unsigned = {key: value for key, value in source.items() if key != 'source_sha256'}
    encoded = json.dumps(unsigned, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    if source.get('source_sha256') != hashlib.sha256(encoded).hexdigest():
        raise ValueError('提案输入source_sha256与当前文件不一致。')
    return source, digest


def _proposal_semantic_response(job):
    source, digest = _proposal_source(job)
    return (copy.deepcopy(source['semantic_response']) if source else None), digest


def _proposal_input(goal, semantic):
    import hashlib
    data = {'schema_version': SCHEMA, 'kind': PROPOSAL_INPUT_KIND,
            'goal': goal, 'semantic_response': copy.deepcopy(semantic),
            'source_sha256': None}
    encoded = json.dumps({key: value for key, value in data.items() if key != 'source_sha256'},
                         ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    data['source_sha256'] = hashlib.sha256(encoded).hexdigest()
    return data


def _request_goal(request):
    value = request.get('context_goal', request.get('goal', ''))
    if not isinstance(value, str):
        raise ValueError('context goal必须是文本。')
    return value


def _write_proposal_source(job, goal, semantic):
    path = _proposal_source_path(job)
    data = _proposal_input(goal, semantic)
    if path.is_file():
        current, _ = _read_stable(path)
        if current == data:
            return data, file_hash(path)
        preserve_changed_file(job, path, None, None)
    atomic_json(path, data)
    return data, file_hash(path)


def _commit_proposal_pair(job, request, proposal, *, plan_before_bytes,
                          previous_plan_sha256=None):
    """Write proposal first, then its strict plan, and mark ready last."""
    from context_planner import validate_proposal, proposal_fingerprint
    evidence = collect_evidence(job, request)
    semantic, source_digest = _proposal_semantic_response(job)
    checked = validate_proposal(
        proposal, evidence, semantic_response=semantic, verify_code=True)
    proposal_path = _path(job, PROPOSAL_PATH)
    plan_path = _path(job, 'context/plan.json')
    if (plan_path.read_bytes() if plan_path.is_file() else None) != plan_before_bytes:
        raise ValueError('提交提案前任务计划已变化；新计划保留，提案不能覆盖它。')
    preparing = copy.deepcopy(proposal)
    preparing['commit'] = {
        'state': 'preparing',
        'proposal_fingerprint': None,
        'proposal_input_sha256': source_digest,
        'previous_plan_sha256': previous_plan_sha256,
        'previous_plan_bytes_sha256': (hashlib.sha256(plan_before_bytes).hexdigest()
                                       if plan_before_bytes is not None else None),
        'expected_plan_sha256': fingerprint(checked['plan']),
    }
    if proposal_path.is_file():
        preserve_changed_file(job, proposal_path, None, fingerprint(preparing))
    atomic_json(proposal_path, preparing)
    # Source/score freshness is rechecked immediately before replacing the plan.
    current = collect_evidence(job, request)
    _, current_source = _proposal_semantic_response(job)
    if current_source != source_digest:
        raise ValueError('提交提案期间提案输入发生变化；预提交记录已保留。')
    if {voice: value['evidence_id'] for voice, value in current.items()} != {
            voice: value['evidence_id'] for voice, value in evidence.items()}:
        raise ValueError('提交提案期间证据发生变化；原计划未替换。')
    # Last plan lease: a user edit after the preparing record must not be
    # backed up and overwritten in the default path.
    if (plan_path.read_bytes() if plan_path.is_file() else None) != plan_before_bytes:
        raise ValueError('提交提案期间任务计划发生变化；新计划保留，预提交提案未使用。')
    plan = checked['plan']
    if plan_path.is_file():
        preserve_changed_file(job, plan_path, None, fingerprint(plan))
    atomic_json(plan_path, plan)
    ready = copy.deepcopy(checked)
    ready['commit'] = {
        'state': 'ready',
        'proposal_fingerprint': proposal_fingerprint(checked),
        'proposal_input_sha256': source_digest,
        'plan_sha256': fingerprint(checked['plan']),
    }
    atomic_json(proposal_path, ready)
    return ready


def propose_context(job, request, *, goal=None, semantic_response=None,
                    refresh_draft=False):
    """Build proposals while distinguishing generated plans from manual edits."""
    from context_planner import propose_plan, validate_proposal, proposal_fingerprint
    job = Path(job).resolve()
    evidence = collect_evidence(job, request)
    for value in evidence.values():
        verify_evidence_fingerprint(value)

    previous_source, previous_digest = _proposal_source(job)
    if goal is None:
        selected_goal = (previous_source['goal'] if previous_source is not None
                         else _request_goal(request))
    else:
        selected_goal = goal
    if not isinstance(selected_goal, str):
        raise ValueError('goal必须为文本。')
    if semantic_response is None and previous_source is not None:
        semantic_response = copy.deepcopy(previous_source['semantic_response'])

    path = _plan_path(job, request)
    external = bool(request.get('context_plan'))
    if external and not path.is_file():
        raise ValueError('外部计划不存在。')
    plan_before_bytes = path.read_bytes() if path.is_file() else None
    existing = _read_stable(path)[0] if path.is_file() else None
    if external:
        if existing is None:
            raise ValueError('外部计划不存在。')
        # Validate read-only external input before persisting any proposal state.
        validate_plan(existing, evidence)
    proposal_path = _path(job, PROPOSAL_PATH)
    previous_proposal = _read_stable(proposal_path)[0] if proposal_path.is_file() else None
    previous_state = (previous_proposal or {}).get('commit', {}).get('state')
    previous_commit = (previous_proposal or {}).get('commit') or {}
    current_plan_hash = fingerprint(existing) if existing is not None else None
    current_plan_bytes_hash = (hashlib.sha256(plan_before_bytes).hexdigest()
                               if plan_before_bytes is not None else None)
    generated_plan = bool(
        previous_proposal and (
            (previous_state == 'ready'
             and existing == previous_proposal.get('plan'))
            or (previous_state == 'preparing'
                and (existing == previous_proposal.get('plan')
                     or current_plan_bytes_hash
                         == previous_commit.get('previous_plan_bytes_sha256')))))
    if not external and existing and existing.get('candidates') and not generated_plan and not refresh_draft:
        raise ValueError('任务已有手工编辑的非空计划；请保留它，或用 '
                         '--refresh-draft 在明确备份后替换。')

    # Build and fully validate in memory before replacing any persisted input.
    proposal = propose_plan(
        evidence, goal=selected_goal, semantic_response=semantic_response)
    checked = validate_proposal(
        proposal, evidence, semantic_response=semantic_response, verify_code=True)
    _check_record_capacity(checked)
    persisted_goal = checked['analysis']['goal']
    persisted_response = copy.deepcopy(checked['semantics']['response'])
    candidate_source = _proposal_input(persisted_goal, persisted_response)
    final_evidence = collect_evidence(job, request)
    if {voice: value['evidence_id'] for voice, value in final_evidence.items()} != {
            voice: value['evidence_id'] for voice, value in evidence.items()}:
        raise ValueError('提案生成期间证据发生变化，请重试。')

    same_retry = bool(
        previous_state == 'ready' and previous_source == candidate_source
        and previous_proposal.get('plan') == checked.get('plan')
        and existing == checked.get('plan')
        and previous_proposal.get('provenance', {}).get('code_sha256')
            == checked.get('provenance', {}).get('code_sha256'))
    if same_retry and not refresh_draft:
        exported = export_context(job, request)
        exported.update(proposal_status='ready',
                        proposal=proposal_path.relative_to(job).as_posix(),
                        plan_commit='reused_generated_plan')
        return exported

    # A plan edited during planning is a new manual input and requires explicit
    # refresh; check before persisting proposal source or artifacts.
    if not external and (path.read_bytes() if path.is_file() else None) != plan_before_bytes:
        raise ValueError('生成提案期间任务计划发生变化；已保留新计划，请重试并用 --refresh-draft 明确替换。')
    source, source_digest = _write_proposal_source(
        job, persisted_goal, persisted_response)
    semantic, persisted_digest = _proposal_semantic_response(job)
    if persisted_digest != source_digest or semantic != checked['semantics']['response']:
        raise ValueError('写入提案输入后语义响应不一致，请重试。')

    if external:
        ready = copy.deepcopy(checked)
        ready['commit'] = {
            'state': 'ready',
            'proposal_fingerprint': proposal_fingerprint(checked),
            'proposal_input_sha256': source_digest,
            'plan_commit': 'external_plan_preserved',
            'plan_sha256': fingerprint(existing),
        }
        if proposal_path.is_file():
            preserve_changed_file(job, proposal_path, None, fingerprint(ready))
        atomic_json(proposal_path, ready)
        exported = export_context(job, request)
        exported.update(proposal_status='ready',
                        proposal=proposal_path.relative_to(job).as_posix(),
                        plan_commit='external_plan_preserved')
        return exported

    ready = _commit_proposal_pair(
        job, request, checked, plan_before_bytes=plan_before_bytes,
        previous_plan_sha256=current_plan_hash)
    exported = export_context(job, request)
    exported.update(proposal_status='ready',
                    proposal=proposal_path.relative_to(job).as_posix(),
                    plan_commit='task_plan_committed')
    return exported


if __name__ == '__main__':
    main()
