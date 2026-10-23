"""Compare extracted notes with fixed, aligned human-score references.

No score data are passed to the transcriber. Human notation can differ from
audio articulation; report both strict and relaxed tolerances, not a universal
transcription accuracy claim.
"""
import math
from pathlib import Path
import sys

APP=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(APP),str(Path(__file__).resolve().parent)]


def notes(values):
    result=[]
    for value in values:
        start=float(value.get('onset',value.get('start',float('nan'))))
        end=float(value.get('offset',value.get('end',float('nan'))))
        pitch=float(value['pitch'])
        if not all(math.isfinite(x) for x in (start,end,pitch)) or end<=start or not 0<=pitch<=127:
            raise ValueError('Invalid benchmark note')
        result.append({'onset':start,'offset':end,'pitch':pitch,'lyric':value.get('lyric',value.get('lyrics',''))})
    return sorted(result,key=lambda n:(n['onset'],n['offset'],n['pitch']))


def match(reference,predicted,*,onset=.05,pitch=.5,offset=False):
    """Maximum-cardinality one-to-one matching; timing breaks remaining ties."""
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    if not reference or not predicted:return []
    if len(reference)*len(predicted)>25_000_000:raise ValueError('Benchmark matrix too large')
    r=np.asarray([[n['onset'],n['offset'],n['pitch']] for n in reference])
    p=np.asarray([[n['onset'],n['offset'],n['pitch']] for n in predicted])
    starts=abs(r[:,None,0]-p[None,:,0]);ends=abs(r[:,None,1]-p[None,:,1]);keys=abs(r[:,None,2]-p[None,:,2])
    valid=(starts<=onset+1e-9)&(keys<=pitch+1e-9)
    if offset:valid&=ends<=np.maximum(.05,.2*(r[:,1]-r[:,0]))[:,None]+1e-9
    # Each valid edge costs <=2. A larger-than-total invalid cost makes the
    # assignment prioritize count over a greedy nearest-onset choice.
    cost=np.where(valid,starts/max(onset,1e-12)+.01*np.minimum(ends,10),4*(min(len(r),len(p))+1))
    ri,pi=linear_sum_assignment(cost)
    return [(int(a),int(b)) for a,b in zip(ri,pi) if valid[a,b]]


def evaluate(reference,predicted):
    import numpy as np
    reference,predicted=notes(reference),notes(predicted)
    metrics={}
    for name,tolerance,with_offset in (('onset_50ms',.05,False),('onset_100ms',.1,False),
                                       ('onset_offset_50ms',.05,True),('onset_offset_100ms',.1,True)):
        pairs=match(reference,predicted,onset=tolerance,offset=with_offset)
        precision=len(pairs)/len(predicted) if predicted else 0.
        recall=len(pairs)/len(reference) if reference else 0.
        metrics[name]={'true_positive':len(pairs),'reference_notes':len(reference),'predicted_notes':len(predicted),
                      'precision':precision,'recall':recall,'f1':2*precision*recall/(precision+recall) if precision+recall else 0.}
    pairs=match(reference,predicted,onset=.1)
    metrics['matched_timing']={'onset_median_ms':float(np.median([(predicted[j]['onset']-reference[i]['onset'])*1000 for i,j in pairs])) if pairs else None,
        'offset_abs_median_ms':float(np.median([abs(predicted[j]['offset']-reference[i]['offset'])*1000 for i,j in pairs])) if pairs else None}
    matched_r={i for i,j in pairs};matched_p={j for i,j in pairs}
    metrics['missing']=[n for i,n in enumerate(reference) if i not in matched_r]
    metrics['extra']=[n for i,n in enumerate(predicted) if i not in matched_p]
    pitchless=match(reference,predicted,onset=.1,pitch=127)
    metrics['onset_matched_wrong_pitch']=[{'reference':reference[i],'predicted':predicted[j]}
        for i,j in pitchless if abs(reference[i]['pitch']-predicted[j]['pitch'])>.5]
    return metrics


def read_midi(path):
    """Read note timing through all tempo changes; preserve simultaneous notes."""
    import mido
    result=[];pending={};time=0.;tempo=500000
    midi=mido.MidiFile(str(path))
    if midi.type==2:raise ValueError('Independent MIDI timelines are unsupported')
    for message in mido.merge_tracks(midi.tracks):
        time+=mido.tick2second(message.time,midi.ticks_per_beat,tempo)
        if message.type=='set_tempo':tempo=message.tempo
        if message.type=='note_on' and message.velocity:
            pending.setdefault((message.channel,message.note),[]).append(time)
        elif message.type=='note_off' or message.type=='note_on' and not message.velocity:
            key=(message.channel,message.note)
            if pending.get(key):
                start=pending[key].pop(0)
                if time>start:result.append({'onset':start,'offset':time,'pitch':message.note})
    if any(pending.values()):raise ValueError('Unclosed MIDI note')
    return notes(result)


if __name__=='__main__':
    import argparse,json
    from common import atomic_json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference');parser.add_argument('midi');parser.add_argument('output')
    args=parser.parse_args();reference=json.loads(Path(args.reference).read_text(encoding='utf-8'))
    result=evaluate(reference['notes'],read_midi(args.midi))
    result['reference_provenance']={k:v for k,v in reference.items() if k!='notes'}
    atomic_json(Path(args.output),result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('missing','extra','onset_matched_wrong_pitch')},ensure_ascii=False,indent=2))
