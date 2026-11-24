"""The new chorus project keeps timing, each voice target and export independent."""
from dataclasses import replace
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

import mido
import numpy as np
import soundfile as sf
import soxr

from chorus_analysis import Features, Suggestion, apply_suggestions, feature_track, proposals
from chorus_audio import export_delivery, export_stems, monitor_tracks, render_key, render_track
from chorus_gain import apply_syllables, clear_syllables, gain_time, suggest_syllables, take_spans
from chorus_pitch_dsp import preserve_low_transients
from chorus_precision import (fit_timing, mark_note_edges, merge_speech_drafts, move_anchor, note_anchors, phone_anchors,
                              phoneme_cache, pitch_from_stroke, reference_pitch, update_phrase)
from chorus_project import SYLLABLE_GAIN, ChorusProject, Edit, Lyric, Phrase, Recording, Score
from chorus_speech import SpeechClip, same_text


def write_audio(path, rate, delay=0.):
    t = np.arange(3 * rate) / rate - delay
    shape = sum(scale * np.exp(-((t - at) / width) ** 2)
                for at, width, scale in [(.48, .10, 1.), (.76, .05, .44), (1.07, .10, .83),
                                         (1.48, .07, .66), (1.91, .09, .92)])
    pcm = (.33 * shape * (np.sin(2 * np.pi * 223.6 * t) + .2 * np.sin(2 * np.pi * 673 * t))).astype('float32')
    sf.write(path, pcm, rate, subtype='FLOAT')
    return pcm


def write_score(path):
    score = mido.MidiFile(ticks_per_beat=480)
    tempo = score.add_track()
    tempo.append(mido.MetaMessage('set_tempo', tempo=500000, time=0))
    for name, pitch in [('主唱', 57), ('上和声', 58)]:
        # Keep the fixture independent of the singing track's measured MIDI
        # so assigning the other lane would visibly change the target.
        track = score.add_track()
        track.append(mido.MetaMessage('track_name', name='Lead' if name == '主唱' else 'Harmony'))
        track.append(mido.Message('note_on', note=pitch, velocity=75, time=288))
        track.append(mido.Message('note_off', note=pitch, velocity=0, time=1850))
    score.save(path)


def check_syllable_gain(root):
    rate = 44100
    first_syllable = 8.
    clock = np.arange(11 * rate) / rate - first_syllable
    pcm = np.zeros(len(clock), dtype='float32')
    rng = np.random.default_rng(214)

    for first, last, level, frequency in (
            (.24, .34, .09, 3600), (.38, .83, .09, 204),
            (1.05, 1.25, .02, 204), (1.27, 1.36, .05, 2900),
            (1.42, 1.59, .018, 204), (1.75, 2.65, .09, 204)):
        interval = (clock >= first) & (clock < last)
        pcm[interval] = level * (np.sin(2 * np.pi * frequency * clock[interval])
                                 if frequency == 204 else rng.uniform(-1., 1., interval.sum()))

    path = root / 'syllables.wav'
    sf.write(path, np.stack([pcm, pcm * .8], axis=-1), rate, subtype='FLOAT')
    take = Recording.read(path)
    project = ChorusProject.fresh().add((take,))
    span = (first_syllable + .22, first_syllable + 1.68)
    clip = SpeechClip(take, first_syllable, first_syllable + 2., 'ja', 'sa a')
    phones = (('s', .22, .34), ('a', .36, .88), ('a', 1., 1.65))
    rows = suggest_syllables(clip, (span,), phones)
    assert [row.db for row in rows] == [0., 4.]
    assert not suggest_syllables(clip, (span,), (('a', .24, .34),))
    applied = apply_syllables(project, take.id, rows)
    edit, = applied.edits_for(take.id, SYLLABLE_GAIN)
    assert edit.points[0][1] == edit.points[-1][1] == 0.
    raw = render_track(project, take, 'processed')
    boosted = render_track(applied, take, 'processed')
    np.testing.assert_array_equal(render_track(applied, take, 'timing'), raw)
    mask = ((clock < .99) | ((clock >= 1.27) & (clock < 1.36)) | (clock >= 1.63))
    np.testing.assert_array_equal(boosted[mask], raw[mask])
    body = (clock >= 1.12) & (clock < 1.18)
    np.testing.assert_allclose(boosted[body], raw[body] * 10 ** (4. / 20.))
    assert render_key(applied, 'original') == render_key(project, 'original')
    assert render_key(applied, 'processed') != render_key(project, 'processed')

    alignment = Edit(take.id, 'timing', 8.90, 9.90, .20)
    trim = Edit(take.id, 'gain', 8.95, 9.70, 1.5, manual=True)
    moved = replace(applied, edits=(*applied.edits, alignment, trim))
    spans = take_spans(moved, take, (9.21, 9.85))
    np.testing.assert_allclose(spans, ((9.01, 9.65),), atol=1e-9)
    assert abs(gain_time(moved, take, (9.21, 9.85), 9.14) - 9.34) < 1e-9
    without = clear_syllables(moved, take.id, spans)
    assert without.edits == (alignment, trim)
    mapped = render_track(moved, take, 'processed')
    baseline = render_track(without, take, 'processed')
    shifted_body = slice(round(9.32 * rate), round(9.38 * rate))
    np.testing.assert_allclose(mapped[shifted_body], baseline[shifted_body] * 10 ** (4. / 20.), atol=1e-7)
    stored = root / 'syllable-gain.vwachorus'
    moved.save(stored)
    assert ChorusProject.open(stored) == moved
    print('PASS: syllable gain preserves consonants and breaths, follows timing, and coexists with phrase trim')


def check_precision(root, project, guide, take):
    assert same_text(Lyric('', 'きっと、歌う', 'ja', True), Lyric('b', 'キット 歌う', 'ja', True))
    assert not same_text(Lyric('', '歌う', 'ja', True), Lyric('b', '歌う', 'ja'))
    selection = (.30, 2.30)
    project = update_phrase(project, selection, line=Lyric('', 'きっと', 'ja', True))
    project = update_phrase(project, selection, line=Lyric(take.id, 'きっと', 'ja', True))
    project = update_phrase(project, selection, reference=guide.id)
    edited, count = merge_speech_drafts(project, take.id, 'ja', project.phrases, [{'text': '別の歌詞'}])
    assert count == 0 and project == edited and project.phrases[0].id
    melody = ((.50, .75, 62), (1., 1.2, 61), (1.6, 1.98, 63))
    harmony = ((.62, .86, 55), (1.05, 1.28, 54), (1.75, 2.02, 56))
    notes = note_anchors(melody, harmony, Phrase(*selection), -.12)
    assert len(notes) == 3
    knots = ((.30, .42), *((ref, source) for _, ref, source in notes), (2.30, 2.45))
    assert knots[1][0] == .50
    manual = move_anchor(take.id, selection, knots, 1., 1.03)
    assert manual.manual and manual.points[2][0] == 1.03
    detected = np.full(300, np.nan, dtype=np.float32)
    detected[42:63] = 58.
    base = Edit(take.id, 'timing', *selection, .06, ((.30, .24), (2.30, 2.24)))
    marked = mark_note_edges(take, (detected, .01), .58, selection, base)
    np.testing.assert_allclose(marked.points[1:3], ((.48, .42), (.69, .63)), atol=1e-9)
    longer = move_anchor(take.id, selection, marked.points, .69, .755)
    assert longer.points[1] == marked.points[1] and longer.end == base.end
    assert abs(longer.output_time(.63) - .755) < 1e-9 and longer.source_time(.31) == base.source_time(.31)
    lock = Edit(take.id, 'timing', .80, 1.36, 0., manual=True)
    next_project = replace(project, edits=(lock,))
    candidate = Suggestion(take.id, 'timing', *selection, -.12, 'note evidence', points=knots)
    protected = apply_suggestions(next_project, [candidate])
    before, retained, after = protected.edits_for(take.id, 'timing')
    assert retained == lock and before.end == lock.start and after.start == lock.end
    assert before.points[-1][0] == lock.start
    assert apply_suggestions(protected, [candidate], manual=True).edits[0].manual

    midi = np.full(300, 57.7, dtype=np.float32)
    stroke = pitch_from_stroke(project, take, (midi, .01), [(.6, 58.), (.91, 57.5)], False)
    assert len(stroke) == 1 and len(stroke[0].points) >= 20
    flat = reference_pitch(project, take, guide, (.4, 1.), {take.id: (midi, .01), guide.id: (midi + 7, .01)},
                           'contour', 1.)
    assert flat and all(abs(pitch) < .01 for row in flat for _, pitch in row.points)
    absolute = reference_pitch(project, take, guide, (.4, 1.), {take.id: (midi, .01), guide.id: (midi + 7, .01)},
                               'absolute', 1.)
    assert all(abs(pitch - 700) < .01 for row in absolute for _, pitch in row.points)
    alternative_path = root / 'alt.wav'
    sf.write(alternative_path, np.full(4 * project.rate, .1, dtype=np.float32), project.rate, subtype='FLOAT')
    alt = Recording.read(alternative_path, 'take')
    comp = Edit(take.id, 'take', .5, 1.40, 0., source=alt.id, manual=True)
    gain = Edit(take.id, 'gain', .6, 1.20, -6., manual=True)
    stitched = replace(project.add((alt,)), edits=(comp, gain))
    assert stitched.frames == project.frames and alt not in monitor_tracks(stitched, '')
    assert render_key(stitched, 'original', '') != render_key(project, 'original', '')
    original = render_track(stitched, take, 'original')[:, 0]
    assert abs(original[project.rate] - .1) < 1e-6
    after = render_track(stitched, take, 'processed')[:, 0]
    assert abs(after[project.rate] - .1 * 10 ** (-6 / 20)) < 1e-6
    assert stitched.remove(alt.id).edits == (gain,)
    saved = root / 'extended.vwachorus'
    with_curve = replace(stitched, edits=(comp, gain, manual, *stroke), alignment_engine='tifa')
    with_curve.save(saved)
    assert ChorusProject.open(saved) == with_curve
    print('PASS: confirmed lyrics, mixed-voice note anchors, local lock, draw, preserved harmony, gain and takes')

    clips = SpeechClip(guide, .3, 2.3, 'ja', 'きっと'), SpeechClip(take, .3, 2.3, 'ja', 'きっと')
    pair = tuple(clip.key for clip in clips)
    phones = [['k', .05, .09], ['i', .09, .18], ['cl', .18, .23], ['t', .23, .34]]
    calls = []

    def measure(rows, _operation, _cache, _check, _progress, *, source):
        engine = source[0]
        calls.append(engine)
        return [{'route': engine, 'phones': [] if engine == 'tifa' and row.track == take else phones}
                for row in rows]

    with patch('chorus_precision.alignment_source', side_effect=lambda value: (
            'tifa' if value == 'auto' else value, root, ())), patch('chorus_precision.speech_batch', measure):
        known = {}
        auto, _ = phoneme_cache([clips], known, root, lambda: None, lambda *_: None, 'auto')
        assert phone_anchors(clips, auto[pair])[0] == 'hubertfa' and calls == ['tifa', 'hubertfa']
        strict, _ = phoneme_cache([clips], known, root, lambda: None, lambda *_: None, 'tifa')
        assert not phone_anchors(clips, strict[pair])[1] and calls[-1] == 'tifa'
        cached, _ = phoneme_cache([clips], known, root, lambda: None, lambda *_: None, 'hubertfa')
        assert phone_anchors(clips, cached[pair])[0] == 'hubertfa' and len(calls) == 3
    mixed = {clips[0].key: {'route': 'tifa', 'phones': phones},
             clips[1].key: {'route': 'hubertfa', 'phones': phones}}
    assert not phone_anchors(clips, mixed)[1]
    print('PASS: selected aligner persists, its cache is separate and fallback uses one model per pair')


def check_translation_first(root):
    rate = 16000
    t = np.arange(4 * rate) / rate
    tone = (.2 * np.sin(2 * np.pi * 235. * t)).astype(np.float32)
    tone[(t < .70) | (t > 3.24)] = 0.
    path = root / 'translation-first.wav'
    sf.write(path, tone, rate, subtype='FLOAT')
    voice = Recording.read(path)
    quiet = lambda a, b: not np.any(tone[max(0, round(a * rate)):round(b * rate)])
    clock = np.linspace(.85, 3.02, 14)
    anchors = [('a', at + .05 + (.23 if i == 5 else 0.), at) for i, at in enumerate(clock)]
    fit = fit_timing(anchors, .36, 3.66, 1., .6, quiet)
    assert not fit.points and not fit.local and not fit.review and abs(fit.movement - .05) < 1.e-7
    assert len(fit.anchors) == 13 and fit.residual_ms == 0
    project = ChorusProject.fresh().add((voice,))
    with patch('chorus_audio._pedalboard', side_effect=AssertionError('translation needs no stretch')):
        moved = render_track(replace(project, edits=(Edit(voice.id, 'timing', .36, 3.66, fit.movement),)),
                             voice, 'timing')[:, 0]
    np.testing.assert_array_equal(moved, np.pad(tone, (800, 0))[:len(tone)])
    half = fit_timing(anchors, .36, 3.66, .5, .6, quiet)
    assert abs(half.movement - .025) < 1.e-7 and not half.local
    plateau = fit_timing(anchors, .70, 3.24, 1., .6, quiet)
    assert plateau.points[0] == (.70, .70) and plateau.points[-1] == (3.24, 3.24)
    assert not plateau.local and abs(plateau.points[1][0] - plateau.points[1][1] - .05) < 1.e-7
    assert abs(plateau.points[2][0] - plateau.points[2][1] - .05) < 1.e-7
    variable = [('a', at + delta, at) for at, delta in zip(clock, np.linspace(-.12, .12, len(clock)))]
    localized = fit_timing(variable, .36, 3.66, 1., .6, quiet)
    assert localized.local and localized.residual_ms < 18 and len(localized.points) > 4
    rates = [((b[1] - a[1]) / (b[0] - a[0])) for a, b in zip(localized.points, localized.points[1:])]
    assert all(.6 <= rate <= 2.6 for rate in rates)
    repeated = Phrase(1., 2., lyrics=(Lyric('', '確認', 'ja', True), Lyric(voice.id, '新しい案')))
    assert repeated.resolved_lyric(voice.id).text == '確認'
    previous = Edit(voice.id, 'timing', .5, 1.4, .21)
    locked = Edit(voice.id, 'timing', 1.4, 2., .11, manual=True)
    reset = Suggestion(voice.id, 'timing', .4, 2.2, 0., '', restore_timing=True)
    assert apply_suggestions(replace(project, edits=(previous, locked)), (reset,)).edits == (locked,)
    warped = Suggestion(voice.id, 'timing', .36, 3.66, 0., '', points=localized.points,
                        anchors=localized.anchors, timing_residual_ms=localized.residual_ms)
    with patch('chorus_analysis._timing_candidates', return_value=[warped]):
        _, rows = proposals(replace(project, edits=(previous,)), (voice,), (.36, 3.66), 'timing', 1., .6,
                            {}, {}, lambda: None, lambda *_: None)
    assert rows == (warped,) and not rows[0].restore_timing
    print('PASS: translation preserves PCM, intermittent outliers stay put and real local drift earns a warp')


def check_native_pitch_transitions(root):
    from mixdown_runtime import component_manifest, optional_packages

    app, spec, _ = component_manifest()
    if optional_packages(app, spec) is None:
        return
    from scipy.signal import butter, sosfiltfilt

    rate = 44100
    clock = np.arange(round(2.8 * rate)) / rate
    frequency = 240 + 23 * np.sin(12 * clock) + 3.7 * np.sin(33 * clock)
    phase = 2 * np.pi * np.cumsum(frequency) / rate
    syllables = ((.19, .075), (.34, .08), (.76, .165), (1.33, .21), (1.80, .06), (2.1, .11), (2.4, .09))
    envelope = sum(np.exp(-((clock - at) / width) ** 2) for at, width in syllables)
    pcm = .18 * envelope * (np.sin(phase) + .31 * np.sin(2 * phase) + .17 * np.sin(3 * phase))
    path = root / 'variable-pitch.wav'
    sf.write(path, pcm, rate, subtype='FLOAT')
    voice = Recording.read(path)
    offsets = (-30, 20, -23, 48, -41, 19, -34, 55, -41, 56, -29, -29, 13, 41, -63)
    edits = tuple(Edit(voice.id, 'pitch', .13 + .157 * i, .254 + .157 * i, cents)
                  for i, cents in enumerate(offsets))
    project = replace(ChorusProject.fresh().add((voice,)), edits=edits)
    rendered = render_track(project, voice, 'pitch')[:, 0]
    assert len(rendered) == len(pcm) and np.max(np.abs(rendered - pcm)) > .01
    sub = sosfiltfilt(butter(5, 65, 'lowpass', fs=rate, output='sos'), rendered)
    # A 20 ms window over the old dynamic stretcher can exceed -41 dBFS.
    frame_count = round(.020 * rate)
    frames = sub[:len(sub) // frame_count * frame_count].reshape(-1, frame_count)
    assert np.max(np.sqrt(np.mean(frames * frames, axis=1))) < .002
    print('PASS: no low-frequency pulses on repeated native pitch transitions')

    clock = np.arange(rate * 2) / rate
    dry = (.14 * np.sin(2 * np.pi * 228 * clock)).astype(np.float32)
    at = lambda time: round(time * rate)
    dry[at(.78):at(.82)] = 0.
    sf.write(path, dry, rate, subtype='FLOAT')
    voice = Recording.read(path)
    edits = (Edit(voice.id, 'pitch', .12, .49, 30.),
             Edit(voice.id, 'pitch', .53, .78, -34.),
             Edit(voice.id, 'pitch', .82, 1.3, 26.))
    project = replace(ChorusProject.fresh().add((voice,)), edits=edits)
    rendered = render_track(project, voice, 'pitch')[:, 0]
    sustained = slice(at(.49), at(.53))
    pause = slice(at(.78), at(.82))
    assert np.max(np.abs(rendered[sustained] - dry[sustained])) > .03
    np.testing.assert_array_equal(rendered[pause], dry[pause])
    np.testing.assert_array_equal(rendered[at(1.3):], dry[at(1.3):])
    lock = Edit(voice.id, 'pitch', .49, .53, 0., manual=True)
    guarded = render_track(replace(project, edits=(*edits, lock)), voice, 'pitch')[:, 0]
    np.testing.assert_array_equal(guarded[sustained], dry[sustained])
    print('PASS: voiced correction gaps are continuous, with rests and manual keep-original preserved')


def check_ensemble(root, guide, take):
    from chorus_ensemble import _preferred_guide

    local_guide = replace(guide, phrase_reference_only=True)
    vocal_project = ChorusProject.fresh().add((local_guide, take))
    assert vocal_project.guide == take.id and _preferred_guide(vocal_project) == take
    assert _preferred_guide(replace(vocal_project, guide='')) is None
    assert _preferred_guide(replace(vocal_project, guide=local_guide.id)) == local_guide
    shifted_ref = vocal_project.update_track(local_guide.id, offset=-1.)
    assert shifted_ref.start == 0. and shifted_ref.end == take.duration
    assert shifted_ref.update_track(local_guide.id, offset=1.).end == take.duration
    local_path = root / 'scoped-reference.vwachorus'
    vocal_project.save(local_path)
    assert ChorusProject.open(local_path) == vocal_project
    peer_path, other_path = root / 'peer.wav', root / 'other-lyrics.wav'
    write_audio(peer_path, 16000, -.12)
    write_audio(other_path, 16000, .07)
    peer, other = Recording.read(peer_path), Recording.read(other_path)
    project = replace(ChorusProject.fresh().add((take, peer, other, guide)), guide=guide.id)
    clocks = {take.id: ((.38, 1.35), (1.78, 2.54)), peer.id: ((.38, 1.35),),
              other.id: ((.38, 1.35),), guide.id: ((0, 3.0),)}
    features = {}
    for track in project.tracks:
        active = np.zeros(300, dtype=bool)
        for first, last in clocks[track.id]:
            active[round(first * 100):round(last * 100)] = True
        features[track.id] = Features(.1 * active, np.zeros((300, 4)), active, [])
    reads = []

    def recognize(clips, operation, _cache, _check, _progress):
        reads.append(operation)
        return [{'route': 'RomajiASR', 'text': ('to se ya ni ho' if row.track.id == other.id
                                                   else 'ka ki ku ke ko sa')} for row in clips]

    def align(clips, operation, _cache, _check, _progress, *, source):
        reads.append(operation)
        result = []
        for clip in clips:
            origin = clip.frames[0] / clip.track.rate
            first = .50 if clip.end < 2.10 else 1.89
            shift = .13 if clip.track.id == take.id else 0.
            phones = [[label, first + shift + i * .057 - origin, first + shift + i * .057 + .04 - origin]
                      for i, label in enumerate(('k', 'a', 'k', 'i', 'k', 'u', 'k', 'e', 'k', 'o', 's', 'a'))]
            result.append({'route': 'TiFA', 'phones': phones})
        return result

    state = {}
    with (patch('chorus_ensemble.speech_batch', recognize),
          patch('chorus_precision.alignment_source', return_value=('tifa', root, ())),
          patch('chorus_precision.speech_batch', align)):
        phrases, found = proposals(project, (take, peer, other), (0.,3.), 'timing', 1., .60, features, {},
                                   lambda: None, lambda *_: None, speech=(state,root), ensemble_language='ja')
        repeated = replace(project, phrases=phrases)
        _, cached = proposals(repeated, (take, peer, other), (0.,3.), 'timing', 1., .60, features, {},
                              lambda: None, lambda *_: None, speech=(state,root), ensemble_language='ja')
    assert len(phrases)==2 and all(not line.confirmed for phrase in phrases for line in phrase.lyrics)
    assert reads==['asr','phones'] and found==cached
    ensemble = [row for row in found if row.track==take.id and row.can_apply]
    assert len(ensemble)==2 and '同词合唱' in ensemble[0].detail and '导唱参照' in ensemble[1].detail, found
    assert any(row.track==other.id and row.review and not row.can_apply for row in found)
    lock = Edit(take.id,'timing',1.99,2.20,0.,manual=True)
    committed = apply_suggestions(replace(project,edits=(lock,)), [row for row in found if not row.review])
    assert lock in committed.edits
    print('PASS: same-lyric ensemble voting, solo guide, draft and cached model evidence, timing lock')


def check_natural_pitch_and_score_matching(root):
    from chorus_audio import _shift_pitch, compiled_pitch_semitones
    from chorus_notes import (effective_note_offset, merge_vocal_notes, reset_vocal_notes,
                              segment_vocal_notes, shift_vocal_notes, split_vocal_note)
    from chorus_pitch_dsp import render_continuous_pitch, signalsmith_available
    from chorus_project import ScoreLane, ScoreNote
    from chorus_score_match import match_vocal_to_score, recommend_score_lanes

    # 1. Vibrato-tolerant vocal note segmentation & split/merge/shift persistence
    step = .01
    midi = np.full(360, np.nan, dtype=np.float32)
    t1 = np.arange(80) * step
    # Note 1: 0.20..1.00s at 60.0 with +/-0.45 semitone 6 Hz vibrato -> must stay ONE note!
    midi[20:100] = 60.0 + .45 * np.sin(2 * np.pi * 6.0 * t1)
    # Note 2: 1.00..1.70s legato step up to 62.0 -> continuous linked second note
    midi[100:170] = 62.0
    # Breath/rest 1.70..2.10s -> unlinked
    # Note 3: 2.10..2.90s at 64.0
    midi[210:290] = 64.0
    notes = segment_vocal_notes(midi, step)
    assert len(notes) == 3, notes
    assert abs(notes[0].center_midi - 60.0) < .15 and notes[0].linked_next and not notes[0].linked_prev
    assert abs(notes[1].center_midi - 62.0) < .15 and notes[1].linked_prev and not notes[1].linked_next
    assert abs(notes[2].center_midi - 64.0) < .15 and not notes[2].linked_prev

    # Build a synthetic vocal WAV matching those 3 notes (with breath rest between note 2 and 3)
    rate = 44100
    clock = np.arange(round(3.6 * rate)) / rate
    pcm = np.zeros((len(clock), 2), dtype=np.float32)
    for (a, b), hz in (((.20, 1.00), 261.63), ((1.00, 1.70), 293.66), ((2.10, 2.90), 329.63)):
        m = (clock >= a) & (clock < b)
        ph = 2 * np.pi * hz * (clock[m] - a)
        sig = (.20 * np.sin(ph) + .08 * np.sin(2 * ph)).astype(np.float32)
        pcm[m, 0] = sig
        pcm[m, 1] = sig * .92
    wav_path = root / 'natural-notes.wav'
    sf.write(wav_path, pcm, rate, subtype='FLOAT')
    take = Recording.read(wav_path)
    base_edits = (
        Edit(take.id, 'pitch', notes[0].start, notes[0].end, 35.0, manual=False),
        Edit(take.id, 'pitch', notes[2].start, notes[2].end, -40.0, manual=False),
    )
    proj = replace(ChorusProject.fresh('NaturalPitch').add((take,)), edits=base_edits, pitch_engine='auto')

    # 2a. Auto == manual sample-exactness on both 'auto' and 'vwa'
    for eng in (('auto', 'vwa') if signalsmith_available() else ('vwa',)):
        p_auto = replace(proj, pitch_engine=eng)
        p_man = replace(p_auto, edits=(replace(base_edits[0], manual=True), base_edits[1]))
        out_auto = render_track(p_auto, take, 'pitch')
        out_man = render_track(p_man, take, 'pitch')
        np.testing.assert_array_equal(out_auto, out_man)

        # 2b. Non-continuous neighbor isolation: shifting note 3 to +180¢ leaves note 1 bit-for-bit exact!
        p_far = shift_vocal_notes(p_auto, take.id, [(notes[2].start, notes[2].end)], 220.0)
        out_far = render_track(p_far, take, 'pitch')
        s0, e0 = round(notes[0].start * rate), round(notes[0].end * rate)
        np.testing.assert_array_equal(out_auto[s0:e0], out_far[s0:e0])
        # Rest/breath between note 2 and note 3 stays bit-for-bit dry!
        np.testing.assert_array_equal(out_far[round(1.75 * rate):round(2.05 * rate)],
                                      pcm[round(1.75 * rate):round(2.05 * rate)])

        # 2c. Splitting note 1 at midpoint preserves audio bit-for-bit!
        mid_t = (notes[0].start + notes[0].end) / 2.0
        p_split = split_vocal_note(p_auto, take.id, notes[0].start, notes[0].end, mid_t)
        split_notes = segment_vocal_notes(midi, step, note_edits=p_split.note_edits_for(take.id))
        assert len(split_notes) == 4 and abs(split_notes[0].end - mid_t) < 1e-6
        out_split = render_track(p_split, take, 'pitch')
        np.testing.assert_array_equal(out_auto, out_split)

        # 2d. Dragging only the second half of the split note changes that half, preserves first half offset!
        p_drag = shift_vocal_notes(p_split, take.id, [(split_notes[1].start, split_notes[1].end)], 100.0)
        off0, _, _, _ = effective_note_offset(split_notes[0], p_drag.edits_for(take.id, 'pitch'))
        off1, _, _, _ = effective_note_offset(split_notes[1], p_drag.edits_for(take.id, 'pitch'))
        assert abs(off0 - 35.0) < .5 and abs(off1 - 135.0) < .5, (off0, off1)
        out_drag = render_track(p_drag, take, 'pitch')
        assert not np.array_equal(out_split[round(mid_t * rate):e0], out_drag[round(mid_t * rate):e0])
        # Compiled display curve matches the two halves cleanly!
        curve_st = compiled_pitch_semitones(p_drag.edits_for(take.id, 'pitch'),
                                            np.array([notes[0].start + .12, notes[0].end - .12]),
                                            duration=take.duration, voiced_curve=(midi, step))
        assert abs(curve_st[0] - .35) < .03 and abs(curve_st[1] - 1.35) < .03, curve_st

    # 2e. Merging the split halves back + save/reopen round-trip
    p_merged = merge_vocal_notes(p_drag, take.id, [(split_notes[0].start, split_notes[0].end),
                                                   (split_notes[1].start, split_notes[1].end)])
    merged_notes = segment_vocal_notes(midi, step, note_edits=p_merged.note_edits_for(take.id))
    assert len(merged_notes) == 3
    p_reset = reset_vocal_notes(p_merged, take.id, [(merged_notes[0].start, merged_notes[0].end)], lock_original=True)
    saved = root / 'natural-roundtrip.vwachorus'
    p_reset.save(saved)
    assert ChorusProject.open(saved) == p_reset

    # 3. Ordered multi-part score matching: skipped score note, octave error, polyphonic chord, draft provenance
    score_lane_lead = (
        ScoreNote(.18, .98, 60, 'a'),
        ScoreNote(1.00, 1.30, 61, 'i'),   # missed/skipped by singer
        ScoreNote(1.32, 1.72, 62, 'u'),   # sung at 1.00..1.70
        ScoreNote(2.08, 2.88, 76, 'e'),   # +12 semitones octave mismatch vs singer's 64!
    )
    matches = match_vocal_to_score(notes, score_lane_lead)
    assert matches[0].target_pitch == 60 and not matches[0].review
    assert matches[1].target_pitch == 62
    assert matches[2].target_pitch == 76 and matches[2].review and '八度' in matches[2].reason

    poly_lane = (ScoreNote(.20, 1.00, 60, 'a'), ScoreNote(.22, .98, 64, 'a'))
    poly_matches = match_vocal_to_score([notes[0]], poly_lane)
    assert poly_matches[0].review and '重叠' in poly_matches[0].reason

    draft_matches = match_vocal_to_score([notes[0]], (ScoreNote(.18, .98, 60, 'a'),), draft=True)
    assert draft_matches[0].review and '草稿' in draft_matches[0].reason

    # 4. Score lane recommendation picks correct register for Lead vs Upper Harmony
    midi_score = mido.MidiFile(ticks_per_beat=480)
    t_meta = midi_score.add_track()
    t_meta.append(mido.MetaMessage('set_tempo', tempo=500000, time=0))
    for name, pitch in (('Lead', 60), ('Upper Harmony', 67)):
        tr_m = midi_score.add_track()
        tr_m.append(mido.MetaMessage('track_name', name=name))
        tr_m.append(mido.Message('note_on', note=pitch, velocity=80, time=192))
        tr_m.append(mido.Message('note_off', note=pitch, velocity=0, time=1920))
    draft_mid = root / 'prep-draft.mid'
    midi_score.save(draft_mid)
    draft_score = Score.read(draft_mid, source_kind='preparation_draft', source_label='Demo · prep-draft.mid')
    assert not draft_score.confirmed and draft_score.source_kind == 'preparation_draft'
    high_midi = midi + 7.0
    p_rec = replace(ChorusProject.fresh('Rec').add((replace(take, part='上和声'),)), score=draft_score)
    recs = recommend_score_lanes(p_rec, {take.id: (high_midi, step)})
    assert recs[take.id][0]['name'] == 'Upper Harmony', recs
    draft_path = root / 'draft-score.vwachorus'
    p_rec.save(draft_path)
    reopened_draft = ChorusProject.open(draft_path)
    assert reopened_draft == p_rec and not reopened_draft.score.confirmed
    print('PASS: vocal note split/merge/drag, unified sample-exact rendering, ordered score match & draft score')


def main():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        guide_path, target_path, score_path = (root / name for name in ('guide.wav', 'take.wav', 'score.mid'))
        write_audio(guide_path, 16000)
        dry_take = write_audio(target_path, 24000, .12)
        write_score(score_path)
        guide = Recording.read(guide_path, 'guide')
        take = Recording.read(target_path)
        score = Score.read(score_path)
        assert [(lane.name, lane.notes[0].pitch) for lane in score.lanes] == [('Lead', 57), ('Harmony', 58)]
        take = replace(take, part='上和声', score_lane=score.lanes[1].id)
        project = replace(ChorusProject.fresh('Fixture').add((guide, take)), score=score)
        assert project.rate == 16000 and project.reference(take) == guide
        check = lambda: None
        update = lambda *_: None
        features = {track.id: feature_track(track, check) for track in project.tracks}
        midi = np.full(300, np.nan, dtype='float32')
        midi[49:191] = 57.6
        phrases, rows = proposals(project, [take], (0., 3.), 'both', 1., .60, features,
                                 {take.id: (midi, .01)}, check, update)
        timing = [row for row in rows if row.kind == 'timing' and row.can_apply]
        tuning = [row for row in rows if row.kind == 'pitch' and row.can_apply]
        assert phrases and timing and tuning
        assert any(abs(row.value + .12) <= .02 for row in timing), timing
        assert tuning[0].from_score and abs(tuning[0].value - 40.) < 1e-3, tuning
        separated = np.full(300, np.nan, dtype=np.float32)
        separated[15:25] = 56.63
        separated[80:112] = 57.65
        _, stable = proposals(replace(project, score=None), [take], (0., 3.), 'pitch', 1., .60, features,
                              {take.id: (separated, .01)}, check, update, pitch_strength=.5)
        assert len(stable) == 1 and abs(stable[0].value - 17.5) < .001, stable
        committed = apply_suggestions(project, timing + tuning)
        assert len(committed.edits) == len(timing + tuning)
        active = committed.edits_for(take.id, 'pitch')[0]
        center = (active.start + active.end) / 2
        trimmed = committed.replace_region(take.id, 'pitch', center - .01, center + .01, ())
        assert len(trimmed.edits_for(take.id, 'pitch')) == 2
        assert trimmed.edits_for(take.id, 'timing') == committed.edits_for(take.id, 'timing')
        manual = Edit(take.id, 'timing', .3, 2.3, -.12)
        aligned = replace(project, edits=(manual,))
        dry = render_track(project, take, 'original')[:, 0]
        after = render_track(aligned, take, 'timing')[:, 0]
        np.testing.assert_allclose(dry, soxr.resample(dry_take, 24000, 16000), atol=1e-6)
        np.testing.assert_array_equal(dry[:4500], after[:4500])
        np.testing.assert_array_equal(after[8000:11000], dry[9920:12920])
        saved = root / 'project.vwachorus'
        aligned.save(saved)
        assert ChorusProject.open(saved) == aligned
        destination, receipt = export_stems(aligned, root, 'timing', (.3, 2.1), check, update, include_mix=True)
        result, rate = sf.read(destination / receipt['tracks'][0]['file'], dtype='float32')
        assert rate == 16000 and receipt['origin_frame'] == 4800 and receipt['frames'] == 28800
        np.testing.assert_array_equal(result, after[4800:33600])
        combined, mix_rate = sf.read(destination / receipt['vocal_mix']['file'], dtype='float32')
        assert mix_rate == rate and combined.shape == (28800, 2)
        np.testing.assert_array_equal(combined, np.repeat(result[:, None], 2, axis=1))
        isolated, report = export_delivery(aligned, root, 'timing', (.3, 2.1), root, check, update, include_mix=True)
        assert isolated != destination and report['frames'] == receipt['frames']
        np.testing.assert_array_equal(sf.read(isolated / report['vocal_mix']['file'],dtype='float32')[0],combined)
        assert not list(root.glob('.vwa-delivery-*'))
        assert Recording.read(target_path).sha == take.sha
        print('PASS: tempo-aware independent voice target, known offset, region undo, mixed-rate render and stem exactness')
        check_precision(root, project, guide, take)
        check_syllable_gain(root)
        check_translation_first(root)
        check_ensemble(root, guide, take)
        clock = np.arange(16000) / 16000
        dry = (.10 * np.sin(2 * np.pi * 240 * clock)).astype('float32')[:, None]
        artifact = dry + (.029 * np.exp(-((clock - .5) / .008) ** 2)).astype('float32')[:, None]
        untouched = artifact.copy()
        preserve_low_transients(dry, artifact, 16000)
        assert np.max(np.abs(artifact[7900:8100] - dry[7900:8100])) < .012
        np.testing.assert_array_equal(artifact[:6500], untouched[:6500])
        np.testing.assert_array_equal(artifact[9500:], untouched[9500:])
        check_native_pitch_transitions(root)
        check_natural_pitch_and_score_matching(root)


if __name__ == '__main__':
    main()
