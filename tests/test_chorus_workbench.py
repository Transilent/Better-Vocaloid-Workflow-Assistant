"""The new chorus project keeps timing, each voice target and export independent."""
from dataclasses import replace
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))

import mido
import numpy as np
import soundfile as sf
import soxr

from chorus_analysis import apply_suggestions, feature_track, proposals
from chorus_audio import export_stems, render_track
from chorus_project import ChorusProject, Edit, Recording, Score


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


if __name__ == '__main__':
    main()
