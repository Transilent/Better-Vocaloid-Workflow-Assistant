"""Audit SVP reference projects and evaluate score-conditioned contour priors.

No source project is modified. Fitting is a score-only experiment: it does not
establish a reference-audio-to-voicebank mapping and never enables a runtime model.
"""
import argparse
import collections
import csv
import hashlib
import html
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))

import numpy as np

NEUTRAL={'pitchDelta':0.,'vibratoEnv':1.,'loudness':0.,'tension':0.,'breathiness':0.,
         'voicing':1.,'gender':0.,'toneShift':0.,'mouthOpening':0.}
EXPRESSION=('loudness','tension','breathiness','voicing','gender','toneShift')
ATTRIBUTES={'tF0Offset','tF0Left','tF0Right','dF0Left','dF0Right','tF0VbrStart',
            'tF0VbrLeft','tF0VbrRight','dF0Vbr','fF0Vbr','pF0Vbr','tNoteOffset',
            'dF0VbrMod','dF0Jitter','dur','strength','phonemes','paramLoudness',
            'paramTension','paramBreathiness','paramVoicing','paramGender','paramToneShift',
            'rTone','rIntonation','expValueX','expValueY'}
FEATURES=('bias','phase','phase2','phase3','log_duration','phrase_start','phrase_end',
          'phase_at_start','phase_at_end','long_note_phase','interval_in','interval_out')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
                                     ensure_ascii=False).encode()).hexdigest()


def project(path):
    raw=Path(path).read_bytes()
    if len(raw)>32*1024**2:raise ValueError('Reference project exceeds 32 MiB')
    data=json.loads(raw.removesuffix(b'\x00').decode('utf-8-sig'))
    if not isinstance(data,dict) or not isinstance(data.get('tracks'),list):raise ValueError('Invalid SVP')
    return data,hashlib.sha256(raw).hexdigest()


def used_groups(data):
    """Only groups reached by an audible track; do not train on spare library copies."""
    library={g['uuid']:g for g in data.get('library',[]) if isinstance(g,dict) and 'uuid' in g}
    seen=set()
    for ti,track in enumerate(data['tracks']):
        if track.get('mainRef',{}).get('isInstrumental') or track.get('mixer',{}).get('mute'):continue
        refs=[(track.get('mainGroup',{}),track.get('mainRef',{}))]
        for ref in track.get('groups',[]):
            if ref.get('groupID') not in library:raise ValueError('Missing referenced note group')
            refs.append((library[ref['groupID']],ref))
        for group,ref in refs:
            if ref.get('muted') or not group.get('notes'):continue
            # A repeated chorus or harmony clone must not count as new supervision.
            key=digest({'notes':[{k:v for k,v in n.items() if k!='uuid'} for n in group['notes']],
                        'parameters':group.get('parameters',{}),'pitchControls':group.get('pitchControls',[])})
            if key in seen:continue
            seen.add(key)
            yield ti,track,group,ref,key


def curve_info(name,curve):
    if not isinstance(curve,dict):raise ValueError('Invalid curve object')
    raw=curve.get('points',[])
    if not raw:return None
    values=np.asarray(raw,float)
    if (values.ndim!=1 or len(values)%2 or not np.isfinite(values).all()
            or np.any(np.diff(values[::2])<=0)):raise ValueError('Invalid curve values')
    y=values[1::2];neutral=NEUTRAL.get(name)
    return {'points':len(y),'varying':bool(np.ptp(y)>1e-6),'mode':curve.get('mode','cubic'),
            'nonneutral':bool(neutral is None or np.any(abs(y-neutral)>1e-6)),
            'minimum':float(y.min()),'maximum':float(y.max())}


def attribute_evidence(notes):
    observed=collections.defaultdict(list)
    for n in notes:
        for k,v in n.get('attributes',{}).items():
            if k in ATTRIBUTES:observed[k].append(v)
    dynamic={}
    for name,values in observed.items():
        # Distinct numeric/phoneme edits within one group are evidence. A
        # uniform converter preset (e.g. vibrato=0 on every note) is not a gesture.
        unique={json.dumps(v,sort_keys=True) for v in values}
        if len(unique)>1:dynamic[name]={'notes':len(values),'distinct_values':len(unique)}
        elif name in ('phonemes','dur','strength') and values:
            v=values[0]
            if isinstance(v,list) and any(
                    isinstance(x,(int,float)) and abs(x-1)>1e-6 or
                    isinstance(x,dict) and any(abs(float(x.get(k,default))-default)>1e-6
                        for k,default in (('leftOffset',0),('strength',0)))
                    for x in v):
                dynamic[name]={'notes':len(values),'distinct_values':1}
    return dynamic


def score_fingerprint(notes):
    """Group alternate tuning versions of the same score, ignoring expression."""
    notes=sorted(notes,key=lambda n:n['onset'])
    first=notes[0]
    return digest([(n['onset']-first['onset'],n['duration'],n['pitch']-first['pitch'],
        unicodedata.normalize('NFKC',str(n.get('lyrics',''))).replace(' ','')) for n in notes])


def song_title(text):
    text=unicodedata.normalize('NFKC',html.unescape(text)).casefold()
    text=re.sub(r'\[[^]]*(?:svp|ust|midi)[^]]*\]|【[^】]*(?:svp|ust|midi)[^】]*】','',text)
    text=re.split(r'\b(?:svp|ustx?|midi)\b',text,maxsplit=1)[0]
    return ''.join(c for c in text if c.isalnum())


def inspect(path):
    from vocal_expression import TempoMap
    data,sha=project(path);clock=TempoMap(data['time']['tempo']);groups=[]
    curve_count=note_edits=pitch_controls=static=0;warnings=[]
    for ti,track,g,ref,key in used_groups(data):
        curves={}
        for name,c in g.get('parameters',{}).items():
            try:
                if (info:=curve_info(name,c)) is not None:curves[name]=info
            except ValueError as error:
                warnings.append({'track':ti,'group':key,'control':name,'reason':str(error)})
        attrs=attribute_evidence(g['notes'])
        curve_count+=sum(c['varying'] for c in curves.values());note_edits+=bool(attrs)
        meaningful_pitch=any(p.get('points') for p in g.get('pitchControls',[]) if isinstance(p,dict))
        pitch_controls+=bool(meaningful_pitch);static+=sum(c['nonneutral'] and not c['varying'] for c in curves.values())
        offset=ref.get('blickOffset',0)
        notes=sorted(g['notes'],key=lambda n:n['onset'])
        start=clock.to_seconds(notes[0]['onset']+offset)
        end=max(clock.to_seconds(n['onset']+n['duration']+offset) for n in notes)
        groups.append({'track':ti,'name':track.get('name',''),'fingerprint':key,
            'score_fingerprint':score_fingerprint(notes),
            'voice':ref.get('database',{}).get('name',''),'notes':len(notes),'start':start,'end':end,
            'curves':curves,'note_attributes':attrs,'pitch_controls':bool(meaningful_pitch)})
    eligible=bool(curve_count or note_edits or pitch_controls)
    return {'file':str(Path(path).resolve()),'sha256':sha,'eligible':eligible,
            'reason':'expressive' if eligible else ('static_only' if static else 'no_effective_expression'),
            'varying_curves':curve_count,'edited_attribute_groups':note_edits,
            'groups':groups,'notes':sum(g['notes'] for g in groups),'warnings':warnings}


def song_clusters(records):
    """Union same-video, title and duplicate-score projects before validation."""
    parent=list(range(len(records)))
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    seen={}
    for i,row in enumerate(records):
        keys=['video:'+row.get('bvid','')] if row.get('bvid') else []
        keys +=['score:'+g['fingerprint'] for g in row.get('groups',[])]
        keys +=['musical_score:'+g['score_fingerprint'] for g in row.get('groups',[]) if g.get('score_fingerprint')]
        if title:=song_title(row.get('title','')):keys.append('song_title:'+title)
        for key in keys:
            if key in seen:parent[find(i)]=find(seen[key])
            else:seen[key]=i
    clusters=collections.defaultdict(list)
    for i,row in enumerate(records):clusters[find(i)].append(row)
    return {r['sha256']:digest(sorted(item['sha256'] for item in rows))
            for rows in clusters.values() for r in rows}


def song_splits(records):
    clusters=song_clusters(records)
    held={cluster for i,cluster in enumerate(sorted(set(clusters.values()))) if i%5==0}
    return {sha:('holdout' if cluster in held else 'train') for sha,cluster in clusters.items()}


def sample_groups(record,name):
    """Use exact source knots inside notes, never approximated cubic labels."""
    from vocal_expression import TempoMap
    data,_=project(record['file']);clock=TempoMap(data['time']['tempo'])
    for ti,track,g,ref,key in used_groups(data):
        curve=g.get('parameters',{}).get(name)
        try:info=curve_info(name,curve) if curve is not None else None
        except ValueError:continue
        if not info or not info['varying']:continue
        notes=sorted(g['notes'],key=lambda n:n['onset'])
        offset=ref.get('blickOffset',0);raw=np.asarray(curve['points'],float)
        x,y=raw[::2],raw[1::2]
        rows=[];labels=[]
        for i,n in enumerate(notes):
            a=clock.to_seconds(n['onset']+offset);b=clock.to_seconds(n['onset']+n['duration']+offset)
            duration=b-a
            if not .06<=duration<=12 or n.get('attributes',{}).get('muted'):continue
            previous=notes[i-1] if i else None;following=notes[i+1] if i+1<len(notes) else None
            incoming=clock.to_seconds(n['onset']+offset)-clock.to_seconds(previous['onset']+previous['duration']+offset) if previous else 99
            outgoing=clock.to_seconds(following['onset']+offset)-b if following else 99
            start,end=float(incoming>.12),float(outgoing>.12)
            pin=np.clip((n['pitch']-previous['pitch'])/12,-1,1) if previous else 0.
            pout=np.clip((following['pitch']-n['pitch'])/12,-1,1) if following else 0.
            candidates=np.flatnonzero((x>=n['onset'])&(x<n['onset']+n['duration']))
            # Limit handwritten/dense-curve imbalance within one note.
            if len(candidates)>5:candidates=candidates[np.linspace(0,len(candidates)-1,5).astype(int)]
            for j in candidates:
                phase=(clock.to_seconds(float(x[j])+offset)-a)/duration
                value=float(y[j])
                labels.append(value)
                rows.append([1.,phase,phase**2,phase**3,math.log(duration),start,end,
                    phase*start,phase*end,phase*float(duration>=.5),pin,pout])
        if len(labels)<12:continue
        labels=np.asarray(labels);center=float(np.median(labels));scale=float(np.percentile(abs(labels-center),90))
        if scale<(.05 if name=='loudness' else 1. if name=='toneShift' else .005):continue
        yield np.asarray(rows,float),np.clip((labels-center)/scale,-3,3),{
            'project':record['sha256'],'group':key,'voice':ref.get('database',{}).get('name',''),
            'interpolation':info['mode'],'label_sampling':'exact_source_knots_inside_notes'}


def fit_priors(records,splits):
    reports={};models={}
    for name in EXPRESSION:
        train=[];held=[]
        for row in records:
            if not row['eligible']:continue
            for x,y,meta in sample_groups(row,name):
                (held if splits[row['sha256']]=='holdout' else train).append((x,y,meta))
        train_projects={m['project'] for x,y,m in train};held_projects={m['project'] for x,y,m in held}
        report={'training_projects':len(train_projects),'holdout_projects':len(held_projects),
            'training_groups':len(train),'holdout_groups':len(held),'runtime_enabled':False,
            'scope':'score_conditioned_relative_shape','audio_supervision':False}
        reports[name]=report
        if len(train_projects)<3 or not held:
            report['status']='insufficient_independent_projects';continue
        counts=collections.Counter(m['project'] for x,y,m in train)
        xx=np.zeros((len(FEATURES),len(FEATURES)));xy=np.zeros(len(FEATURES))
        for x,y,m in train:
            w=1/(len(y)*counts[m['project']]);xx+=x.T@x*w;xy+=x.T@y*w
        coef=np.linalg.solve(xx+np.diag([.00001]+[.05]*(len(FEATURES)-1)),xy)
        metrics=collections.defaultdict(list)
        for x,y,m in held:
            baseline=float(np.mean(abs(y)));error=float(np.mean(abs(np.clip(x@coef,-3,3)-y)))
            metrics[m['project']].append((baseline,error))
        project_metrics=[np.mean(v,axis=0) for v in metrics.values()]
        before,after=np.mean(project_metrics,axis=0)
        gain=1-after/max(before,1e-12)
        # Even a promising score-only predictor is not an acoustic inverse.
        report.update(status='shadow_only',baseline_mae=float(before),candidate_mae=float(after),
            relative_improvement=float(gain),minimum_holdout_met=len(held_projects)>=5,
            improved_holdout_projects=int(sum(a<b for b,a in project_metrics)),
            acceptance='not_applicable_without_paired_audio_and_bank_validation')
        models[name]={'features':FEATURES,'coefficients':coef.tolist(),'runtime_enabled':False}
    return {'schema':1,'reports':reports,'models':models,'source_projects_modified':False}


def run(directory,output):
    from common import atomic_json
    directory=Path(directory).resolve();output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    mapping=list(csv.DictReader((directory/'svp_bilibili_map.csv').open(encoding='utf-8-sig')))
    by_id={int(row['#']):row for row in mapping};records=[]
    for path in sorted((directory/'svp_100').glob('*.svp')):
        index=int(path.name.split('_')[0]);row=by_id[index]
        try:
            item=inspect(path);bv=re.search(r'BV[0-9A-Za-z]+',row['B站链接'])
            item.update(id=index,title=row['曲名/项目'],author=row['投稿者'],
                        bowlroll=row['BowlRoll链接'],bilibili=row['B站链接'],bvid=bv.group() if bv else '')
        except Exception as error:item={'id':index,'file':str(path),'eligible':False,'error':repr(error),'sha256':''}
        records.append(item)
    valid=[r for r in records if not r.get('error')]
    splits=song_splits([r for r in valid if r['eligible']])
    for row in records:row['split']=splits.get(row.get('sha256'),'excluded')
    atomic_json(output/'corpus.json',records)
    fitted=fit_priors(valid,splits);atomic_json(output/'shadow-model.json',fitted)
    # Re-read hashes after analysis; the user's source collection stays untouched.
    for row in valid:
        if hashlib.sha256(Path(row['file']).read_bytes()).hexdigest()!=row['sha256']:
            raise ValueError('Reference source changed during analysis')
    summary={'projects':len(records),'eligible':sum(r['eligible'] for r in records),
        'excluded':[(r['id'],r.get('reason',r.get('error'))) for r in records if not r['eligible']],
        'splits':dict(collections.Counter(splits.values())),'models':fitted['reports'],
        'source_projects_unchanged':True,'runtime_profile_enabled':False}
    atomic_json(output/'summary.json',summary)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory');parser.add_argument('output')
    args=parser.parse_args()
    print(json.dumps(run(args.directory,args.output),ensure_ascii=False,indent=2))

