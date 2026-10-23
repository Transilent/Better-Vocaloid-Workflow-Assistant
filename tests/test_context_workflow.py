"""Context hooks reuse verified scores and preserve all existing SVP bytes."""
import copy
import builtins
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from common import atomic_json, file_hash
from v2m_runtime import configure
configure(APP/'dependencies/vocal2midi2')
from inference.API.ustx_api import save_ustx
from project_exports import export_job, project_signatures, commit_receipt, read_receipts, outputs_intact
from context_workflow import collect_evidence, prepare_context, export_context
import context_workflow


def raises(kind, call):
    try:
        call()
    except kind:
        return
    raise AssertionError('Expected ' + kind.__name__)


with tempfile.TemporaryDirectory(prefix='context-hooks-', dir=APP/'work') as folder:
    job = Path(folder)
    native = job/'midi/vocals.ustx'
    native.parent.mkdir()
    notes = [SimpleNamespace(onset=.5, offset=1.5, pitch=60, lyric='你'),
             SimpleNamespace(onset=1.5, offset=2., pitch=60, lyric='-'),
             SimpleNamespace(onset=2., offset=3., pitch=62, lyric='好')]
    save_ustx(notes, native, tempo=120)
    quality = native.with_name('vocals.quality.json')
    atomic_json(quality, {'syllables': [{'start': .5, 'end': 2., 'lyric': 'ni'},
                                      {'start': 2., 'end': 3., 'lyric': 'hao'}]})
    atomic_json(native.parent/'report.json', {
        'native_sha256': {'ustx': file_hash(native)},
        'output_sha256': {'quality.json': file_hash(quality)}})
    request = {'voice_mode': 'single', 'language': 'zh', 'lyrics': '你，好',
               'project_formats': ['svp', 'ustx'], 'audio_formats': [],
               'include_instrumental': False, 'export_pitch': True}
    atomic_json(job/'request.json', request)
    request_bytes = (job/'request.json').read_bytes()
    export_job(job, request)
    baseline = job/'exports/voices.svp'
    # Synthetic fixture: register rich tuning as the current baseline. The
    # context path must not open/rewrite any of it, including unknown channels.
    data = json.loads(baseline.read_bytes().removesuffix(b'\x00'))
    group = data['tracks'][0]['mainGroup']
    group.setdefault('parameters', {}).update({
        'tension': {'mode': 'cubic', 'points': [0, .1, 50000, -.21, 100000, .33]},
        'loudness': {'mode': 'linear', 'points': [0, -3, 55000, 2, 100000, -1]},
        'futureControl': {'mode': 'linear', 'points': [0, .6, 100000, .9]}})
    group['notes'][0].setdefault('attributes', {})['dF0Vbr'] = .3
    baseline.write_bytes(json.dumps(data, ensure_ascii=False).encode('utf-8') + b'\x00')
    commit_receipt(job, baseline, project_signatures(job, request)['svp'], read_receipts(job))
    protected = baseline.read_bytes()
    score_bytes = native.read_bytes()
    signatures = project_signatures(job, request)
    real_import = builtins.__import__
    def without_context(name, *args, **kwargs):
        if name in ('context_workflow', 'context_evidence', 'context_plan'):
            raise ImportError('optional context module unavailable')
        return real_import(name, *args, **kwargs)
    with patch('builtins.__import__', side_effect=without_context):
        assert outputs_intact(job, request)
        export_job(job, request)
    active = {**request, 'context_enabled': True}
    assert not outputs_intact(job, active)
    with patch('project_exports.library', side_effect=AssertionError('Context reran conversion')):
        result = export_job(job, active)
    assert baseline.read_bytes() == protected and native.read_bytes() == score_bytes
    assert (job/'request.json').read_bytes() == request_bytes
    assert result['context']['status'] == 'advisory_only'
    assert result['context']['parameter_application'] == 'not_applied_advisory_only'
    assert outputs_intact(job, active)
    assert project_signatures(job, active) == signatures, 'Context invalidated base project formats'
    evidence = collect_evidence(job, active)['vocals']
    assert evidence['provenance']['timing_status'] == 'verified'
    assert len(evidence['notes']) == 3 and len(evidence['syllables']) == 2
    assert evidence['notes'][0]['text_unit_ids'] == evidence['notes'][1]['text_unit_ids']
    assert evidence['notes'][0]['syllable_ids'] == evidence['notes'][1]['syllable_ids']
    saved_quality = quality.read_bytes()
    real_builder = context_workflow.build_evidence
    def disappearing_quality(*args, **kwargs):
        value = real_builder(*args, **kwargs)
        quality.unlink()
        return value
    with patch('context_workflow.build_evidence', side_effect=disappearing_quality):
        raises(ValueError, lambda: collect_evidence(job, active))
    quality.write_bytes(saved_quality)
    with_words = collect_evidence(job, {**active, 'context_word_spans': {
        'vocals': [{'span': [2, 3], 'source': 'manual'}]}})['vocals']
    assert with_words['words'][0]['text'] == '好'
    assert with_words['words'][0]['note_ids'] == [with_words['notes'][2]['id']]
    plan_path = job/'context/plan.json'
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    plan['candidates'] = [
        {'id': 'gentle', 'intent': '温柔地回应', 'gestures': [
            {'voice': 'vocals', 'note_ids': [evidence['notes'][0]['id']],
             'phase': 'onset', 'direction': 'soft_onset'}]},
        {'id': 'firm', 'intent': '坚定地回应', 'gestures': [
            {'voice': 'vocals', 'note_ids': [evidence['notes'][2]['id']],
             'phase': 'sustain', 'direction': 'supported_sustain'}]}]
    plan['selected_candidate'] = 'gentle'
    atomic_json(plan_path, plan)
    assert not outputs_intact(job, active) and outputs_intact(job, request)
    selected = export_context(job, active)
    assert selected['selected_candidate'] == 'gentle'
    preview_path = job/'exports/context/preview.json'
    preview = json.loads(preview_path.read_text(encoding='utf-8'))
    assert not preview['project_written'] and not preview['audio_rendered']
    assert len(preview['candidates']) == 2
    assert preview['baseline']['sha256'] == file_hash(baseline)
    assert baseline.read_bytes() == protected
    atomic_json(APP/'work/test-results/context-hooks-demo.json',
                {'synthetic_fixture': True, 'model_run': False, 'audio_rendered': False,
                 'evidence': evidence, 'plan': plan, 'preview': preview})
    stamps = {row['file']: (job/row['file']).stat().st_mtime_ns for row in selected['outputs']}
    export_context(job, active)
    assert all((job/name).stat().st_mtime_ns == stamp for name, stamp in stamps.items())
    # A context implementation change invalidates only its own products.
    real_hash = context_workflow.file_hash
    def changed_code(path):
        return 'f' * 64 if Path(path).name == 'context_plan.py' else real_hash(path)
    with patch('context_workflow.file_hash', side_effect=changed_code):
        assert not context_workflow.outputs_intact(job, active)
        assert project_signatures(job, active) == signatures
    # Invalid and stale plans leave all prior products and source files intact.
    before_preview = preview_path.read_bytes()
    invalid = copy.deepcopy(plan)
    invalid['candidates'][0]['gestures'][0]['note_ids'] = ['missing-note']
    atomic_json(plan_path, invalid)
    raises(ValueError, lambda: export_context(job, active))
    assert preview_path.read_bytes() == before_preview and baseline.read_bytes() == protected
    atomic_json(plan_path, plan)
    raises(ValueError, lambda: export_context(job, {**active, 'lyrics': '新的歌词'}))
    assert baseline.read_bytes() == protected
    raises(ValueError, lambda: export_context(job, {**active, 'context_plan': '../outside.json'}))
    (job/'cancel.flag').write_text('cancel', encoding='utf-8')
    raises(InterruptedError, lambda: export_context(job, active))
    (job/'cancel.flag').unlink()
    # Simulated write failure preserves committed base files; the next call
    # resumes the context subset rather than repeating project conversion.
    plan['selected_candidate'] = 'firm'
    atomic_json(plan_path, plan)
    real_write = context_workflow.atomic_json
    def fail_preview(path, value):
        if Path(path) == preview_path:
            raise OSError('injected context output failure')
        return real_write(path, value)
    with patch('context_workflow.atomic_json', side_effect=fail_preview):
        raises(OSError, lambda: export_context(job, active))
    assert baseline.read_bytes() == protected and not outputs_intact(job, active)
    with patch('project_exports.library', side_effect=AssertionError('Recovery reran conversion')):
        export_job(job, active)
    assert outputs_intact(job, active) and baseline.read_bytes() == protected
    # Preserve a hand-edited advisory output when regenerating it.
    preview_path.write_bytes(b'my private annotation')
    repaired = export_context(job, active)
    preserved = next(row for row in repaired['outputs'] if row['file'].endswith('/preview.json'))['preserved_copy']
    assert (job/preserved).read_bytes() == b'my private annotation'
    # Timing is not accepted from a changed/unreceipted quality file.
    quality.write_text('{"syllables":[{"start":0,"end":3,"lyric":"invented"}]}', encoding='utf-8')
    untrusted = collect_evidence(job, active)['vocals']
    assert not untrusted['syllables'] and untrusted['provenance']['timing_status'] != 'verified'
    raises(ValueError, lambda: prepare_context(job, active))
    old_plan = plan_path.read_bytes()
    prepare_context(job, active, refresh_draft=True)
    assert any(path.read_bytes() == old_plan for path in (job/'backups').iterdir())
    assert not json.loads(plan_path.read_text(encoding='utf-8'))['candidates']
    # CLI consumes existing files only and keeps the original request intact.
    words_path = job/'words-input.json'
    atomic_json(words_path, {'vocals': [{'span': [0, 1], 'source': 'manual'}]})
    command = [sys.executable, '-B', str(APP/'context_workflow.py'), 'prepare',
               '--job', str(job), '--words', str(words_path), '--refresh-draft']
    cli = subprocess.run(command, cwd=APP, capture_output=True, timeout=30)
    assert cli.returncode == 0, (cli.stdout+cli.stderr).decode('utf-8', errors='replace')
    stored_words = (job/'context/word-spans.json').read_bytes()
    for tail in (['export', '--refresh-draft'], ['prepare', '--refresh-draft', '--plan', str(plan_path)]):
        bad_cli = subprocess.run([sys.executable, '-B', str(APP/'context_workflow.py'),
                                  *tail, '--job', str(job), '--words', str(words_path)],
                                 cwd=APP, capture_output=True, timeout=30)
        assert bad_cli.returncode != 0
        assert (job/'context/word-spans.json').read_bytes() == stored_words
    cli = subprocess.run([sys.executable, '-B', str(APP/'context_workflow.py'), 'export', '--job', str(job)],
                         cwd=APP, capture_output=True, timeout=30)
    assert cli.returncode == 0, (cli.stdout+cli.stderr).decode('utf-8', errors='replace')
    assert (job/'request.json').read_bytes() == request_bytes
    assert baseline.read_bytes() == protected and native.read_bytes() == score_bytes
    # Switching voice selection retires obsolete context records, never files.
    old_evidence_path = job/'exports/context/vocals.evidence.json'
    old_evidence_bytes = old_evidence_path.read_bytes()
    for voice in ('lead', 'backing'):
        folder = job/'midi'/voice
        folder.mkdir()
        (folder/(voice+'.ustx')).write_bytes(score_bytes)
    dual = {**active, 'voice_mode': 'dual', 'project_formats': [],
            'backing_lyrics': '你，好', 'context_word_spans': {}}
    prepare_context(job, dual, refresh_draft=True)
    assert context_workflow.outputs_intact(job, dual)
    old_receipt = read_receipts(job)['exports/context/vocals.evidence.json']
    assert old_receipt['active'] is False and old_evidence_path.read_bytes() == old_evidence_bytes
    import task_contract
    task_contract.index_export(job, old_receipt)
    assert not any(row['path'] == str(old_evidence_path) for row in task_contract.inspect(job))
    prepare_context(job, active, refresh_draft=True)
    assert context_workflow.outputs_intact(job, active)
    assert read_receipts(job)['exports/context/lead.evidence.json']['active'] is False
    assert read_receipts(job)['exports/context/vocals.evidence.json'].get('active', True)
    assert baseline.read_bytes() == protected and native.read_bytes() == score_bytes

    import yaml
    tempo_score = job/'tempo.ustx'
    tempo_score.write_text(yaml.safe_dump({'resolution': 480, 'tempos': [
        {'position': 0, 'bpm': 120}, {'position': 960, 'bpm': 60}],
        'voice_parts': [{'position': 480, 'notes': [
            {'position': 0, 'duration': 960, 'tone': 60, 'lyric': 'no'}]}]}), encoding='utf-8')
    timed = context_workflow._notes(tempo_score)[0]
    assert timed['lyric'] == 'no' and timed['start'] == .5 and timed['end'] == 2.

print('PASS: context advice, verified timing, cache isolation, byte-exact parameter protection, CLI and recovery')
