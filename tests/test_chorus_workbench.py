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

from chorus_analysis import Suggestion, apply_suggestions, feature_track, proposals
from chorus_audio import export_stems, monitor_tracks, render_key, render_track
from chorus_precision import (mark_note_edges, merge_speech_drafts, move_anchor, note_anchors, phone_anchors, phoneme_cache,
                              pitch_from_stroke, reference_pitch, update_phrase, warp)
from chorus_project import ChorusProject, Edit, Lyric, Phrase, Recording, Score
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
    knots, anchors, shift = warp(notes, *selection, 1., .60)
    assert len(anchors) == 3 and knots[1][0] == .50
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
    candidate = Suggestion(take.id, 'timing', *selection, shift, 'note evidence', points=knots)
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
        destination, receipt = export_stems(aligned, root, 'timing', (.3, 2.1), check, update)
        result, rate = sf.read(destination / receipt['tracks'][0]['file'], dtype='float32')
        assert rate == 16000 and receipt['origin_frame'] == 4800 and receipt['frames'] == 28800
        np.testing.assert_array_equal(result, after[4800:33600])
        assert Recording.read(target_path).sha == take.sha
        print('PASS: tempo-aware independent voice target, known offset, region undo, mixed-rate render and stem exactness')
        check_precision(root, project, guide, take)
        check_native_pitch_transitions(root)


if __name__ == '__main__':
    main()
