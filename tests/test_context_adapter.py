"""Boundary tests for the pure, explicit experimental context adapter."""
import copy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from context_adapter import adapt_svp
from context_evidence import build_evidence
from vocal_expression import SV_QUARTER, TempoMap


def build_case():
    tempo = [{'position': 0, 'bpm': 120},
             {'position': 2*SV_QUARTER, 'bpm': 90}]
    clock = TempoMap(tempo)
    # Three raw SVP notes; the last two belong to one selected word/group.
    spans = [(0., .75), (1., 2.2), (2.35, 3.8), (4., 6.5)]
    lyrics = ['你', '好', '-', '啊']
    local, notes = [], []
    for index, ((start, end), lyric) in enumerate(zip(spans, lyrics)):
        onset, finish = clock.to_position(start), clock.to_position(end)
        local.append({'onset': onset, 'duration': finish - onset,
                      'pitch': (60, 62, 62, 64)[index], 'lyrics': lyric,
                      'attributes': {'dF0Vbr': .25},
                      'pitchTakes': {'activeTakeId': 7, 'takes': [{'id': 7}]},
                      'timbreTakes': {'activeTakeId': 8, 'takes': [{'id': 8}]}})
        notes.append({'lyric': lyric, 'start': clock.to_seconds(onset),
                      'end': clock.to_seconds(finish),
                      'pitch': (60, 62, 62, 64)[index]})
    evidence = build_evidence('你好啊', 'zh', notes, voice='lead',
                              provenance={'score': 'synthetic-adapter-fixture'})
    x0, x6 = 0, clock.to_position(6.8)
    loudness = [x0, 0]
    # Dense original curve with no x at the selected boundaries.
    for step in range(1, 41):
        position = int(round(x6 * step / 41))
        loudness.extend([position, round((step % 5 - 2) * .22, 6)])
    loudness.extend([x6, .31])
    project = {
        'version': 154,
        'time': {'tempo': tempo, 'meter': []},
        'tracks': [
            {'name': 'Lead', 'groups': [], 'mainRef': {
                'isInstrumental': False, 'blickOffset': 0, 'pitchOffset': 0,
                'database': {'name': 'Test bank'},
                'pitchTakes': {'activeTakeId': 3, 'takes': [{'id': 3}]}},
             'mainGroup': {'notes': local, 'parameters': {
                 'loudness': {'mode': 'linear', 'points': loudness},
                 'tension': {'mode': 'linear', 'points': []},
                 'pitchDelta': {'mode': 'linear', 'points': [
                     x0, 0, int(x6*.5), 3, x6, 0]},
                 'futureControl': {'mode': 'cubic', 'points': [x0, 77, x6, 88]}}},
             'mixer': {'gainDecibel': -2, 'pan': .2}},
            {'name': 'Instrumental', 'groups': [], 'mainRef': {
                'isInstrumental': True, 'audio': {'filename': 'mix.wav'}},
             'mainGroup': {'notes': [], 'parameters': {}}},
        ],
        'projectMixer': {'gainDecibel': -.5},
    }
    word_note_ids = [evidence['notes'][1]['id'], evidence['notes'][2]['id']]
    application = {
        'schema_version': 1, 'kind': 'vwa.context-application',
        'evidence_ids': {'lead': evidence['evidence_id']},
        'track_bindings': {'lead': 0},
        'selections': [{
            'id': 'word-trial', 'voice': 'lead',
            'note_ids': word_note_ids, 'phase': 'onset',
            'recipe': 'more-force', 'amount': .5,
            'rationale': 'explicit synthetic test',
            'source_span': [1, 2], 'source_quote': '好'}],
    }
    profile = {
        'schema_version': 1, 'kind': 'vwa.context-adapter-profile',
        'id': 'fixture-profile', 'voicebank': 'Test bank',
        'allow_unbound_voicebank': False,
        'status': 'experimental_unrendered',
        'recipes': {'more-force': {'phase': 'onset', 'controls': {
            'loudness': 1.2, 'tension': -.03}}},
    }
    return project, evidence, application, profile


def same_slot_case():
    tempo = [{'position': 0, 'bpm': 120}]
    clock = TempoMap(tempo)
    finish = clock.to_position(.5)
    evidence = build_evidence('你好', 'zh', [
        {'lyric': '你', 'start': 0., 'end': clock.to_seconds(finish), 'pitch': 60},
        {'lyric': '好', 'start': 0., 'end': clock.to_seconds(finish), 'pitch': 60},
    ], voice='lead', provenance={'score': 'same-slot-fixture'})
    notes = [
        {'onset': 0, 'duration': finish, 'pitch': 60, 'lyrics': lyric}
        for lyric in ('你', '好')]
    project = {
        'version': 154,
        'time': {'tempo': tempo, 'meter': []},
        'tracks': [{
            'name': 'Lead', 'groups': [],
            'mainRef': {'isInstrumental': False, 'blickOffset': 0,
                        'pitchOffset': 0, 'database': {'name': 'Test bank'}},
            'mainGroup': {'notes': notes, 'parameters': {
                'loudness': {'mode': 'linear',
                             'points': [0, 0, finish, 0]}}},
        }],
    }
    selections = []
    for index, note_id in enumerate(row['id'] for row in evidence['notes']):
        quote = '你好'[index]
        selections.append({
            'id': 'slot-' + str(index), 'voice': 'lead', 'note_ids': [note_id],
            'phase': 'onset', 'recipe': 'slot-force', 'amount': .5,
            'rationale': 'same-slot stable binding test',
            'source_span': [index, index + 1], 'source_quote': quote})
    application = {
        'schema_version': 1, 'kind': 'vwa.context-application',
        'evidence_ids': {'lead': evidence['evidence_id']},
        'track_bindings': {'lead': 0}, 'selections': selections[:1]}
    overlap = copy.deepcopy(application)
    overlap['selections'] = copy.deepcopy(selections)
    profile = {
        'schema_version': 1, 'kind': 'vwa.context-adapter-profile',
        'id': 'slot-profile', 'voicebank': 'Test bank',
        'allow_unbound_voicebank': False,
        'status': 'experimental_unrendered',
        'recipes': {'slot-force': {'phase': 'onset',
                                   'controls': {'loudness': .5}}}}
    return project, evidence, application, overlap, profile


def reject(call, needle=None):
    try:
        call()
    except ValueError as error:
        if needle is not None:
            assert needle in str(error), (needle, str(error))
        return error
    raise AssertionError('Expected ValueError')


def pair(points):
    return dict(zip(points[::2], points[1::2]))


def main():
    project, evidence, application, profile = build_case()
    original = copy.deepcopy(project)
    before_evidence = copy.deepcopy(evidence)
    output, receipt = adapt_svp(project, {'lead': evidence}, application,
                                profile=profile)
    assert project == original and evidence == before_evidence
    assert output is not project and output != project
    old_group = original['tracks'][0]['mainGroup']
    group = output['tracks'][0]['mainGroup']
    assert group['notes'] == old_group['notes']
    assert output['tracks'][0]['mainRef'] == original['tracks'][0]['mainRef']
    assert output['tracks'][0]['mixer'] == original['tracks'][0]['mixer']
    assert output['tracks'][1] == original['tracks'][1]
    assert output['projectMixer'] == original['projectMixer']
    assert group['parameters']['pitchDelta'] == old_group['parameters']['pitchDelta']
    assert group['parameters']['futureControl'] == old_group['parameters']['futureControl']

    # One selection with two notes yields exactly one window per control, not
    # a fresh onset at the sustain note. Raw x positions all survive.
    old_loud = pair(old_group['parameters']['loudness']['points'])
    new_loud = group['parameters']['loudness']['points']
    assert len(new_loud) > 2*len(old_loud) and len(new_loud) % 2 == 0
    assert all(x in new_loud[::2] for x in old_loud)
    loud_effects = [row for row in receipt['effects'] if row['control'] == 'loudness']
    assert len(loud_effects) == 1
    effect = loud_effects[0]
    assert effect['selection_id'] == 'word-trial'
    assert effect['note_ids'] == application['selections'][0]['note_ids']
    assert effect['group_count'] == 1
    assert effect['window_start_seconds'] == effect['start_seconds']
    assert .15 <= effect['window_duration_seconds'] <= .160001
    assert effect['window_end_seconds'] < effect['end_seconds']
    assert effect['original_x_preserved'] is True
    assert effect['selected_values_changed'] is True
    # Both empty and non-empty controls preserve values precisely outside the
    # selected exclusive interval; the selected interval's baseline need not
    # remain unchanged, and is recorded explicitly.
    old_tension = pair(old_group['parameters']['tension']['points'])
    new_tension = pair(group['parameters']['tension']['points'])
    new_lookup = pair(new_loud)
    clock = TempoMap(project['time']['tempo'])
    group_start = evidence['notes'][1]['start']
    group_end = evidence['notes'][2]['end']
    for x, value in old_loud.items():
        seconds = clock.to_seconds(x)
        if seconds <= group_start or seconds >= group_end:
            assert new_lookup[x] == value
    assert new_tension and min(new_tension.values()) < 0
    assert all(abs(value) <= 1 for value in new_tension.values())
    assert old_group['parameters']['tension']['points'] == []
    # The empty control becomes neutral outside the one onset lobe; it does
    # not restart at the second note or stay displaced through the long note.
    for x, value in new_tension.items():
        seconds = clock.to_seconds(x)
        if (seconds <= effect['window_start_seconds'] + 1e-12
                or seconds >= effect['window_end_seconds'] - 1e-12):
            assert abs(value) <= 1e-12
    # The 5 ms sampling is audio-seconds based, including the tempo change.
    assert .155 <= effect['window_end_seconds'] - effect['window_start_seconds'] <= .160001

    # The conservative project loudness upper bound cannot be overshot.
    boundary = copy.deepcopy(project)
    points = boundary['tracks'][0]['mainGroup']['parameters']['loudness']['points']
    for index in range(1, len(points), 2):
        points[index] = 12.
    boundary_app = copy.deepcopy(application)
    boundary_profile = copy.deepcopy(profile)
    boundary_app['selections'][0]['recipe'] = 'up'
    boundary_profile['recipes']['up'] = {'phase': 'onset',
        'controls': {'loudness': 1.}}
    reject(lambda: adapt_svp(boundary, {'lead': evidence}, boundary_app,
                             profile=boundary_profile), '越界')

    # Known wrong non-empty bank is rejected even with allow_unbound true,
    # whereas truly empty/unbound is permitted by that flag.
    wrong = copy.deepcopy(project)
    wrong['tracks'][0]['mainRef']['database']['name'] = 'Wrong bank'
    allow_unbound = copy.deepcopy(profile)
    allow_unbound['allow_unbound_voicebank'] = True
    reject(lambda: adapt_svp(wrong, {'lead': evidence}, application,
                             profile=allow_unbound), '不一致')
    unbound = copy.deepcopy(project)
    unbound['tracks'][0]['mainRef']['database'] = {'name': ''}
    adapt_svp(unbound, {'lead': evidence}, application, profile=allow_unbound)
    reject(lambda: adapt_svp(unbound, {'lead': evidence}, application,
                             profile=profile), '未绑定')

    # Empty automation accepts a retained non-linear mode because it has no
    # spline to replace. A selected non-empty cubic curve is still rejected.
    empty_mode = copy.deepcopy(project)
    empty_mode['tracks'][0]['mainGroup']['parameters']['tension'] = {
        'mode': 'cubic', 'points': []}
    adapt_svp(empty_mode, {'lead': evidence}, application, profile=profile)

    # Release uses a single trailing window; sustain uses one middle window.
    release_app = copy.deepcopy(application)
    release_app['selections'][0].update(
        phase='release', recipe='release-force', note_ids=[evidence['notes'][3]['id']],
        source_span=[2, 3], source_quote='啊')
    release_profile = copy.deepcopy(profile)
    release_profile['recipes']['release-force'] = {'phase': 'release',
        'controls': {'loudness': .8}}
    _, release_receipt = adapt_svp(project, {'lead': evidence}, release_app,
                                   profile=release_profile)
    release = release_receipt['effects'][0]
    assert abs((release['end_seconds'] - release['window_start_seconds']) - .2) < .006
    assert release['window_end_seconds'] == release['end_seconds']
    sustain_app = copy.deepcopy(application)
    sustain_app['selections'][0].update(
        phase='sustain', recipe='sustain-force', note_ids=[evidence['notes'][3]['id']],
        source_span=[2, 3], source_quote='啊')
    sustain_profile = copy.deepcopy(profile)
    sustain_profile['recipes']['sustain-force'] = {'phase': 'sustain',
        'controls': {'loudness': .8}}
    _, sustain_receipt = adapt_svp(project, {'lead': evidence}, sustain_app,
                                   profile=sustain_profile)
    sustain = sustain_receipt['effects'][0]
    assert sustain['window_start_seconds'] > sustain['start_seconds']
    assert sustain['window_end_seconds'] < sustain['end_seconds']

    # Stable same-time binding reaches the shared-window rejection. Swapping
    # distinct lyrics fails earlier as stale score binding, not an ID sort.
    tie_project, tie_evidence, tie_app, tie_overlap, tie_profile = same_slot_case()
    reject(lambda: adapt_svp(tie_project, {'lead': tie_evidence}, tie_app,
                             profile=tie_profile), '未选音符')
    swapped = copy.deepcopy(tie_project)
    swapped['tracks'][0]['mainGroup']['notes'].reverse()
    reject(lambda: adapt_svp(swapped, {'lead': tie_evidence}, tie_app,
                             profile=tie_profile), 'lyric')
    # Distinct selections with overlapping audio intervals conflict even when
    # their evidence note IDs differ.
    reject(lambda: adapt_svp(tie_project, {'lead': tie_evidence}, tie_overlap,
                             profile=tie_profile), '重叠')

    # A current-fingerprint evidence with unresolved note text is still not
    # permission to guess: selected unknown mappings are rejected.
    unknown_evidence = build_evidence('你好呀', 'zh', [
        {'lyric': row['lyric'], 'start': row['start'], 'end': row['end'],
         'pitch': row['pitch']} for row in evidence['notes']], voice='lead')
    unknown_app = copy.deepcopy(application)
    unknown_app['evidence_ids']['lead'] = unknown_evidence['evidence_id']
    unknown_app['selections'][0]['note_ids'] = [unknown_evidence['notes'][0]['id']]
    reject(lambda: adapt_svp(project, {'lead': unknown_evidence}, unknown_app,
                             profile=profile), '未知')

    # Stale evidence, unknown channel, nonlinear selected curve and source error.
    stale = copy.deepcopy(evidence); stale['notes'][0]['pitch'] += 1
    reject(lambda: adapt_svp(project, {'lead': stale}, application,
                             profile=profile), 'evidence')
    unknown_control = copy.deepcopy(profile)
    unknown_control['recipes']['more-force']['controls']['gender'] = .1
    reject(lambda: adapt_svp(project, {'lead': evidence}, application,
                             profile=unknown_control), '不支持的通道')
    nonlinear = copy.deepcopy(application)
    nonlinear['selections'][0]['recipe'] = 'nonlinear'
    nonlinear_profile = copy.deepcopy(profile)
    nonlinear_profile['recipes']['nonlinear'] = {'phase': 'onset',
        'controls': {'tension': .02}}
    nonlinear_project = copy.deepcopy(project)
    nonlinear_project['tracks'][0]['mainGroup']['parameters']['tension'] = {
        'mode': 'cubic', 'points': [0, .1, 100000, .2]}
    reject(lambda: adapt_svp(nonlinear_project, {'lead': evidence}, nonlinear,
                             profile=nonlinear_profile), 'linear')
    wrong_source = copy.deepcopy(application)
    wrong_source['selections'][0]['source_quote'] = '錯'
    reject(lambda: adapt_svp(project, {'lead': evidence}, wrong_source,
                             profile=profile), '逐字一致')
    over_limit = copy.deepcopy(profile)
    over_limit['recipes']['more-force']['controls']['loudness'] = 1.51
    reject(lambda: adapt_svp(project, {'lead': evidence}, application,
                             profile=over_limit), '单次变化')

    # Real gap >150 ms is not silently bridged or split.
    gap = copy.deepcopy(application)
    gap['selections'][0]['note_ids'] = [
        evidence['notes'][0]['id'], evidence['notes'][1]['id']]
    gap['selections'][0].update(source_span=[0, 2], source_quote='你好')
    reject(lambda: adapt_svp(project, {'lead': evidence}, gap,
                             profile=profile), '150 ms')

    # Duplicate IDs, NaN and unsupported linked/clipped tracks are strict.
    duplicate = copy.deepcopy(application)
    duplicate['selections'].append(copy.deepcopy(duplicate['selections'][0]))
    reject(lambda: adapt_svp(project, {'lead': evidence}, duplicate,
                             profile=profile), '唯一')
    nan_profile = copy.deepcopy(profile)
    nan_profile['recipes']['more-force']['controls']['loudness'] = float('nan')
    reject(lambda: adapt_svp(project, {'lead': evidence}, application,
                             profile=nan_profile), '有限数字')
    linked = copy.deepcopy(project); linked['tracks'][0]['groups'] = [{'id': 'x'}]
    reject(lambda: adapt_svp(linked, {'lead': evidence}, application,
                             profile=profile), 'flat')
    clipped = copy.deepcopy(project)
    clipped['tracks'][0]['mainRef']['blickAbsoluteEnd'] = 10
    reject(lambda: adapt_svp(clipped, {'lead': evidence}, application,
                             profile=profile), 'flat')

    print('PASS: pure context adapter; grouped word window, audio-second '
          'envelope, original-x retention, empty-neutral curve, retakes/'
          'score/unknown channels preserved, strict binding and limits')


if __name__ == '__main__':
    main()
