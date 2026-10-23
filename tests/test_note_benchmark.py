"""One-to-one note matching, timing tolerances and real MIDI tempo handling."""
import math
from pathlib import Path
import sys,tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tools/research')]
from note_benchmark import notes,match,evaluate,read_midi
import mido

def n(a,b,p=60):return {'onset':a,'offset':b,'pitch':p}

# Greedy nearest matching can lose a valid match. Cardinality takes priority.
reference=[n(0,.4),n(.06,.5)];predicted=[n(.03,.4),n(-.03,.3)]
assert len(match(reference,predicted))==2
r=evaluate([n(0,1)],[n(0,1),n(0,1)])
assert r['onset_50ms']['recall']==1 and r['onset_50ms']['precision']==.5
assert r['onset_50ms']['true_positive']==1
r=evaluate([n(0,1)],[n(.075,1.1)])
assert r['onset_50ms']['f1']==0 and r['onset_100ms']['f1']==1
assert r['onset_offset_100ms']['f1']==1
r=evaluate([n(0,1)],[n(0,.7)])
assert r['onset_50ms']['f1']==1 and r['onset_offset_50ms']['f1']==0
r=evaluate([n(0,1)],[n(0,1,61)])
assert r['onset_50ms']['f1']==0 and len(r['onset_matched_wrong_pitch'])==1
assert not evaluate([],[])['missing']
for bad in (n(0,0),n(1,0),n(0,1,float('nan')),n(0,1,128)):
    try:notes([bad]);raise AssertionError('Invalid note accepted')
    except ValueError:pass

with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='note-benchmark-') as directory:
    path=Path(directory)/'tempo.mid';m=mido.MidiFile(ticks_per_beat=480)
    m.tracks.append(mido.MidiTrack([
        mido.MetaMessage('set_tempo',tempo=500000,time=0),
        mido.MetaMessage('set_tempo',tempo=1000000,time=480)]))
    m.tracks.append(mido.MidiTrack([
        mido.Message('note_on',note=60,velocity=80,time=0),
        mido.Message('note_off',note=60,time=960)]));m.save(path)
    r=read_midi(path);assert len(r)==1 and abs(r[0]['offset']-1.5)<1e-9
    m.tracks[1].pop();m.save(path)
    try:read_midi(path);raise AssertionError('Unclosed note accepted')
    except ValueError:pass
print('PASS: maximum-cardinality matches, duplicates, pitch/timing errors and tempo-aware MIDI')
