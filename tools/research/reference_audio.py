"""Small paired-audio experiments; results never activate a runtime profile.

Exact SVP knots are supervision. Relative gestures are evaluated by held-out
song, with no assertion that an original singer's timbre identifies a different
voicebank's absolute control settings.
"""
import collections
import math
from pathlib import Path
import sys

APP=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(APP),str(Path(__file__).resolve().parent)]

import numpy as np

FEATURES=('bias','spectral_tilt','noise_ratio','periodicity','level','pitch',
          'tilt_change','note_phase','log_duration')


def score_tracks(data):
    """Audio-time notes, including repeated audible instances of linked groups."""
    from vocal_expression import TempoMap
    clock=TempoMap(data['time']['tempo']);library={g['uuid']:g for g in data.get('library',[])}
    for ti,track in enumerate(data['tracks']):
        if track.get('mainRef',{}).get('isInstrumental') or track.get('mixer',{}).get('mute'):continue
        refs=[(track.get('mainGroup',{}),track.get('mainRef',{}))]
        refs +=[(library[r['groupID']],r) for r in track.get('groups',[]) if r.get('groupID') in library]
        notes=[]
        for group,ref in refs:
            if ref.get('muted'):continue
            offset=ref.get('blickOffset',0)
            for n in group.get('notes',[]):
                if n.get('attributes',{}).get('muted'):continue
                notes.append((clock.to_seconds(n['onset']+offset),
                    clock.to_seconds(n['onset']+n['duration']+offset),n['pitch']+ref.get('pitchOffset',0)))
        if notes:yield ti,track.get('name',''),notes


def choose_track(candidates):
    """Prefer complete, untransposed melody matches over short harmony matches."""
    candidates=[c for c in candidates if c.get('parameterized',True)]
    if not candidates:return None
    def rank(c):
        return (c['pitch_agreement']*c['score_voiced_coverage']*
                min(1.,c['compared_seconds']/60.)/(1.+.08*abs(c['transpose_semitones'])))
    return max(candidates,key=rank)


def accepted_pair(candidate):
    return bool(candidate and candidate['compared_seconds']>=30. and
                candidate['pitch_agreement']>=.75 and candidate['score_voiced_coverage']>=.65
                and abs(candidate['transpose_semitones'])<=12)


def acoustic_design(frames):
    """Whole-song unsupervised centering; never uses held-out control labels."""
    from scipy.ndimage import uniform_filter1d
    frames=np.asarray(frames,float)
    if (frames.ndim!=2 or frames.shape[1]!=7 or len(frames)<3 or
            not np.isfinite(frames).all() or np.any(np.diff(frames[:,0])<=0)):
        raise ValueError('Invalid acoustic feature frames')
    valid=frames[:,6].astype(bool)&(frames[:,3]>.4)
    if valid.sum()<15:raise ValueError('Insufficient voiced feature frames')
    cues=frames[:,1:6].copy()
    center=np.median(cues[valid],axis=0)
    scale=np.maximum(np.percentile(cues[valid],90,axis=0)-np.percentile(cues[valid],10,axis=0),
                     [3.,3.,.05,6.,6.])
    cues=np.clip((cues-center)/scale,-3,3)
    derivative=np.zeros(len(cues))
    edges=np.diff(np.r_[False,valid,False].astype(int))
    for left,right in zip(np.where(edges==1)[0],np.where(edges==-1)[0]):
        block=cues[left:right]
        smooth=uniform_filter1d(block,size=5,axis=0,mode='nearest')
        cues[left:right]=smooth
        if right-left>1:
            derivative[left:right]=np.clip(np.gradient(smooth[:,0],frames[left:right,0])*.1,-3,3)
    return cues,derivative,valid,{'center':center.tolist(),'scale':scale.tolist(),
                                'centering':'whole_song_unlabeled_acoustics'}


def exact_acoustic_samples(record,selection,frames,pitch):
    """Only locally pitch-supported, exact source knots in one selected track.

    A local match does not override a failed global-pair test. The caller must
    preserve that distinction in provenance and keep this trial shadow-only.
    """
    from reference_corpus import project,used_groups,curve_info
    from vocal_expression import TempoMap
    data,_=project(record['file']);clock=TempoMap(data['time']['tempo'])
    cues,derivative,valid,norm=acoustic_design(frames)
    times=frames[:,0];offset=selection['offset_seconds'];shift=selection['transpose_semitones']
    xs=[];ys=[];ts=[];phases=[];groups=[]
    for ti,track,g,ref,key in used_groups(data):
        if ti!=selection['track_index']:continue
        c=g.get('parameters',{}).get('tension')
        try:info=curve_info('tension',c) if c else None
        except ValueError:continue
        if not info or not info['varying']:continue
        raw=np.asarray(c['points'],float);kx,ky=raw[::2],raw[1::2]
        blockx=[];blocky=[];blockt=[];blockp=[]
        for n in sorted(g['notes'],key=lambda n:n['onset']):
            if n.get('attributes',{}).get('muted'):continue
            ref_offset=ref.get('blickOffset',0)
            start=clock.to_seconds(n['onset']+ref_offset)+offset
            stop=clock.to_seconds(n['onset']+n['duration']+ref_offset)+offset
            duration=stop-start
            if not .1<=duration<=12:continue
            note_pitch=n['pitch']+ref.get('pitchOffset',0)+shift
            knots=np.flatnonzero((kx>=n['onset'])&(kx<n['onset']+n['duration']))
            if len(knots)>5:knots=knots[np.linspace(0,len(knots)-1,5).astype(int)]
            margin=min(.04,duration*.15)
            for k in knots:
                time=clock.to_seconds(float(kx[k])+ref_offset)+offset
                if not start+margin<=time<stop-margin:continue
                nearest=int(np.argmin(abs(times-time)))
                if abs(times[nearest]-time)>.011 or nearest<2 or nearest>=len(times)-2:continue
                local=slice(nearest-2,nearest+3)
                # Require >=60 ms of local measured support, not a single
                # coincidental pitch point from another voice.
                if np.sum(valid[local]&(abs(frames[local,5]-note_pitch)<=.65))<3:continue
                pi=int(round(time/pitch.time_step_seconds))
                if not 0<=pi<len(pitch.midi_pitch) or not pitch.voiced_mask[pi]:continue
                if not math.isfinite(float(pitch.midi_pitch[pi])) or abs(pitch.midi_pitch[pi]-note_pitch)>.65:continue
                phase=(time-start)/duration
                blockx.append([1.,*cues[nearest],derivative[nearest],2*phase-1,math.log(duration)])
                blocky.append(float(ky[k]));blockt.append(time);blockp.append(phase)
        if len(blocky)<15 or np.ptp(blocky)<.01:continue
        # This benchmark intentionally measures relative shape, not a bank's
        # absolute offset. Median subtraction is part of the target definition.
        center=float(np.median(blocky))
        xs.extend(blockx);ys.extend(np.asarray(blocky)-center);ts.extend(blockt);phases.extend(blockp)
        groups.append({'group':key,'voice':ref.get('database',{}).get('name',''),
                       'labels':len(blocky),'label_median':center,'interpolation':info['mode']})
    return {'x':np.asarray(xs).reshape((-1,len(FEATURES))),'y':np.asarray(ys),'times':np.asarray(ts),
            'phases':np.asarray(phases),'groups':groups,'normalization':norm,
            'project':record['sha256'],'song':record.get('song_cluster',record['bvid']),
            'bvid':record['bvid'],'id':record['id'],
            'globally_accepted':accepted_pair(selection),'label_sampling':'exact_source_knots',
            'target':'within_group_median_centered_tension'}


def leave_song_out(datasets,*,objective='ridge'):
    """Fixed ridge model; entire songs, including alternate projects, held out."""
    if objective not in ('ridge','pseudo_huber'):raise ValueError('Unknown research objective')
    usable=[d for d in datasets if len(d['y'])>=15]
    songs=sorted({d['song'] for d in usable});folds=[]
    if len(songs)<3:
        return {'status':'insufficient_independent_songs','songs':len(songs),'runtime_enabled':False,'objective':objective}
    for song in songs:
        train=[d for d in usable if d['song']!=song];test=[d for d in usable if d['song']==song]
        counts=collections.Counter(d['song'] for d in train)
        xx=np.zeros((len(FEATURES),len(FEATURES)));xy=np.zeros(len(FEATURES))
        for d in train:
            x,y=d['x'],d['y'];weight=1/(len(y)*counts[d['song']])
            xx+=x.T@x*weight;xy+=x.T@y*weight
        coef=np.linalg.solve(xx+np.diag([.00001]+[.1]*(len(FEATURES)-1)),xy)
        if objective=='pseudo_huber':
            from scipy.optimize import minimize
            # Fixed loss/regularization, not tuned against held-out songs. The
            # 0.02 control-unit smoothing approaches absolute error while
            # keeping the optimizer differentiable at an exact match.
            x=np.concatenate([d['x'] for d in train]);y=np.concatenate([d['y'] for d in train])
            w=np.concatenate([np.full(len(d['y']),1/(len(d['y'])*counts[d['song']])) for d in train])
            penalty=np.asarray([.00001]+[.1]*(len(FEATURES)-1))
            def loss(beta):
                residual=x@beta-y;distance=np.sqrt(residual**2+.02**2)
                return float(np.sum(w*(distance-.02))+.5*np.dot(penalty,beta**2)), x.T@(w*residual/distance)+penalty*beta
            fitted=minimize(loss,np.zeros(len(FEATURES)),jac=True,method='L-BFGS-B',options={'maxiter':200,'ftol':1e-11})
            if not fitted.success or not np.isfinite(fitted.x).all():raise ValueError('Research fit failed to converge')
            coef=fitted.x
        before=[];after=[]
        for d in test:
            before.append(float(np.mean(abs(d['y']))))
            after.append(float(np.mean(abs(np.clip(d['x']@coef,-2,2)-d['y']))))
        b,a=float(np.mean(before)),float(np.mean(after))
        folds.append({'heldout_song':song,'heldout_videos':sorted({d.get('bvid',d['song']) for d in test}),
                      'training_songs':sorted({d['song'] for d in train}),
                      'heldout_ids':[d['id'] for d in test],'labels':sum(len(d['y']) for d in test),
                      'baseline_mae':b,'candidate_mae':a,'relative_improvement':1-a/max(b,1e-12),
                      'coefficients':coef.tolist()})
    baseline=float(np.mean([r['baseline_mae'] for r in folds]))
    candidate=float(np.mean([r['candidate_mae'] for r in folds]))
    return {'status':'shadow_only','runtime_enabled':False,'objective':objective,'folds':folds,'songs':len(songs),
            'features':FEATURES,'baseline_mae':baseline,'candidate_mae':candidate,
            'relative_improvement':1-candidate/max(baseline,1e-12),
            'globally_accepted_songs':len({d['song'] for d in usable if d['globally_accepted']}),
            'acceptance':'not_deployable_without_more_verified_pairs_and_target_bank_validation'}


def run(corpus,manifest,output):
    """Analyze an explicitly prepared cohort of local vocal/pitch references.

    Manifest entries contain id, audio, pitch_cache and selection. Relative
    paths resolve against the manifest. No downloads or editor operations occur.
    """
    import json
    import time
    from common import atomic_json,file_hash
    from reference_corpus import project,song_clusters
    from vocal_timbre import cached_features,map_features
    from pitch_accuracy import load_pitch
    corpus=Path(corpus).resolve();manifest=Path(manifest).resolve();output=Path(output).resolve()
    records=json.loads(corpus.read_text(encoding='utf-8'))
    clusters=song_clusters([r for r in records if r['eligible']])
    prepared=json.loads(manifest.read_text(encoding='utf-8'))
    datasets=[];inventory=[];output.mkdir(parents=True,exist_ok=True)
    for entry in prepared:
        index=entry['id'];chosen=entry['selection'];record=next(r for r in records if r['id']==index)
        if not record['eligible']:
            inventory.append({'id':index,'status':'skipped_no_effective_expression'});continue
        record={**record,'song_cluster':clusters[record['sha256']]}
        if not chosen or chosen['pitch_agreement']<.5 or chosen['score_voiced_coverage']<.65:
            inventory.append({'id':index,'status':'skipped_pair_mismatch','pair':chosen});continue
        if file_hash(record['file'])!=record['sha256']:raise ValueError('Reference project changed after audit')
        data,_=project(record['file'])
        notes=next(n for ti,name,n in score_tracks(data) if ti==chosen['track_index'])
        notes=[(a+chosen['offset_seconds'],b+chosen['offset_seconds'],p+chosen['transpose_semitones']) for a,b,p in notes]
        audio=manifest.parent/entry['audio'];pitch_file=manifest.parent/entry['pitch_cache']
        audio_hash=file_hash(audio);pitch_hash=file_hash(pitch_file)
        started=time.monotonic()
        frames,metadata=cached_features(audio,notes,cache=output/('%03d.acoustics.npz'%index))
        dataset=exact_acoustic_samples(record,chosen,frames,load_pitch(pitch_file))
        result=map_features(frames,[(a,b) for a,b,p in notes],1.,('tension',),
                            pitches=[p for a,b,p in notes],preserve_samples=True)['tension']
        prediction=np.zeros(len(dataset['y']))
        if result.get('points') and len(prediction):
            points=np.asarray(result['points']);prediction=np.interp(dataset['times'],points[:,0],points[:,1])
            first=0
            for group in dataset['groups']:
                last=first+group['labels'];prediction[first:last]-=np.median(prediction[first:last]);first=last
        dataset['heuristic_mae']=float(np.mean(abs(prediction-dataset['y']))) if len(prediction) else None
        datasets.append(dataset)
        row={k:v for k,v in dataset.items() if k not in ('x','y','times','phases')}
        row.update(labels=len(dataset['y']),status='exploratory_local_support',pair=chosen,
                   baseline_mae=float(np.mean(abs(dataset['y']))) if len(prediction) else None,
                   analysis_seconds=round(time.monotonic()-started,3),analyzer=metadata,
                   audio_sha256=audio_hash,pitch_sha256=pitch_hash)
        if file_hash(audio)!=audio_hash or file_hash(pitch_file)!=pitch_hash:
            raise ValueError('Reference audio or pitch changed during analysis')
        inventory.append(row)
        np.savez_compressed(output/('%03d.tension-samples.npz'%index),x=dataset['x'],y=dataset['y'],times=dataset['times'])
        print(json.dumps(row,ensure_ascii=False),flush=True)
    fitted=leave_song_out(datasets)
    fitted['robust_exploratory']=leave_song_out(datasets,objective='pseudo_huber')
    fitted['verified_pair_evaluation']=leave_song_out([d for d in datasets if d['globally_accepted']])
    fitted['datasets']=inventory
    heuristics=[d['heuristic_mae'] for d in datasets if d['heuristic_mae'] is not None]
    if heuristics:fitted['current_heuristic_mae']=float(np.mean(heuristics))
    fitted['source_projects_unchanged']=all(file_hash(r['file'])==r['sha256'] for r in records if r.get('sha256'))
    if not fitted['source_projects_unchanged']:raise ValueError('Source projects changed during analysis')
    atomic_json(output/'acoustic-shadow-fit.json',fitted)
    return fitted


if __name__=='__main__':
    import argparse
    import json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('corpus');parser.add_argument('manifest');parser.add_argument('output')
    args=parser.parse_args()
    result=run(args.corpus,args.manifest,args.output)
    print(json.dumps({k:v for k,v in result.items() if k!='datasets'},ensure_ascii=False,indent=2))
