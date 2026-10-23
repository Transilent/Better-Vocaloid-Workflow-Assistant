"""User-selected experimental singing styles over an independent SVP copy.

Recipes are hand-designed audition drafts, not learned coefficients, target-bank
calibration or lyric emotion predictions. Four expression channels may change;
optional structural pitch gestures are recorded separately. Score and bank stay.
"""
from __future__ import annotations


def canonical_kind(value):
    return 'vwa.' + value[5:] if isinstance(value, str) and value.startswith('bvwa.') else value



import argparse
import bisect
import copy
import hashlib
import json
import math
from pathlib import Path

VERSION = 2
PRESETS = {
    'none': {'label': '保留原表现', 'controls': {}},
    'soft_ballad': {'label': '柔和抒情〔实验〕', 'controls': {
        'loudness': (-.8, .5, -1.5), 'tension': (-.18, -.08, -.18),
        'breathiness': (.06, .02, .12), 'voicing': (0., 0., -.06)}},
    'intimate_narration': {'label': '轻声叙述〔实验〕', 'controls': {
        'loudness': (-2.5, -2., -4.), 'tension': (-.40, -.28, -.40),
        'breathiness': (.28, .14, .34), 'voicing': (-.10, -.05, -.20)}},
    'power_pop': {'label': '有力流行〔实验〕', 'controls': {
        'loudness': (2.5, 1.8, .5), 'tension': (.30, .20, .10),
        'breathiness': (-.10, -.06, -.02)}},
}
CONTROLS = {'loudness': (-48., 12., 0.), 'tension': (-1., 1., 0.),
            'breathiness': (-1., 1., 0.), 'voicing': (0., 1., 1.)}
MAX_POINTS = 2_000_000
PHRASE_GAP = .25
STYLE_GRID = .02


def settings(options):
    from style_pitch import enabled
    enabled(options)
    preset = options.get('performance_style', 'none')
    strength = options.get('style_strength', .6)
    scope = options.get('style_scope', 'lead')
    if not isinstance(preset, str) or preset not in PRESETS:
        raise ValueError('演唱风格预设无效。')
    if type(strength) not in (int, float) or not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError('演唱风格强度应为 0–100%。')
    if scope not in ('lead', 'all_vocals'):
        raise ValueError('风格作用声部无效。')
    return preset, float(strength), scope


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _protected(project, changed):
    data = copy.deepcopy(project)
    for index, name in changed:
        parameters = data['tracks'][index]['mainGroup'].get('parameters')
        if isinstance(parameters, dict):
            parameters.pop(name, None)
            if not parameters:
                # Missing and an empty parameter map have the same protected
                # content only here; all unknown channel entries are retained.
                data['tracks'][index]['mainGroup'].pop('parameters', None)
    return _canonical(data)


def track_indices_for(project, scope='lead'):
    settings({'style_scope': scope})
    tracks = project.get('tracks')
    if not isinstance(tracks, list):
        raise ValueError('SVP 轨道列表无效。')
    vocals = [i for i,t in enumerate(tracks) if isinstance(t,dict)
              and not t.get('mainRef',{}).get('isInstrumental')]
    if scope == 'all_vocals':
        return vocals
    named = [i for i in vocals if tracks[i].get('name') in ('Vocals','Lead Vocal')]
    return named or vocals[:1]


def _phrases(track, clock):
    ref = track.get('mainRef', {})
    if (not isinstance(ref, dict) or ref.get('isInstrumental') or track.get('groups')
            or ref.get('blickAbsoluteBegin',0) != 0 or ref.get('blickAbsoluteEnd',-1) != -1):
        raise ValueError('风格预设目前仅支持未裁剪、未链接库组的演唱 mainGroup。')
    offset = ref.get('blickOffset',0)
    if type(offset) is not int:
        raise ValueError('风格轨道的时间偏移必须为整数。')
    group = track.get('mainGroup')
    if not isinstance(group,dict) or not isinstance(group.get('notes'),list):
        raise ValueError('风格轨道缺少 mainGroup 音符。')
    spans=[]
    for note in group['notes']:
        if (not isinstance(note,dict) or type(note.get('onset')) is not int
                or type(note.get('duration')) is not int or note['duration']<=0):
            raise ValueError('风格轨道包含无效音符时间。')
        lyric=str(note.get('lyrics','')).strip().lower()
        if lyric in ('sil','sp','pau') or (lyric.startswith('br') and lyric[2:].isdigit()) or lyric=='br':
            continue
        a=clock.to_seconds(note['onset']+offset)
        b=clock.to_seconds(note['onset']+note['duration']+offset)
        if not all(math.isfinite(v) for v in (a,b)) or b<=a:
            raise ValueError('风格音符时间无法换算。')
        spans.append((a,b))
    phrases=[]
    for a,b in sorted(spans):
        if phrases and a-phrases[-1][1]<=PHRASE_GAP+1e-9:
            phrases[-1]=(phrases[-1][0],max(b,phrases[-1][1]))
        else:phrases.append((a,b))
    return phrases, offset


def _curve(curve, *, float_positions=True, track_index=None, channel=None):
    if curve is None:
        return [], []
    from calibration_refinement import _curve_points
    parsed=_curve_points(curve,track_index=track_index,channel=channel,source='style')
    if parsed and curve.get('mode','cubic')!='linear':
        raise ValueError('preserved_nonlinear')
    return [x for x,_ in parsed],[y for _,y in parsed]


def _weights(times, phrases, definition):
    import numpy as np
    weights=np.zeros(len(times),dtype=float)
    for a,b in phrases:
        first,last=np.searchsorted(times,[a,b],side='left')
        if last<=first:continue
        duration=b-a;t=times[first:last]-a
        onset=min(.16,duration*.25);release=min(.24,duration*.30)
        # Local accent -> voiced core -> release, with smooth edge guards.
        values=np.interp(t,[0.,onset,max(onset,duration-release),duration],definition)
        edge=min(.04,duration*.15)
        gate=np.minimum(np.clip(t/edge,0,1),np.clip((duration-t)/edge,0,1))
        gate=gate*gate*(3-2*gate)
        weights[first:last]=values*gate
    return weights


def apply_style(project, preset, *, strength=.6, track_indices=None, density_mode='dense', pitch_enabled=False, cancel=lambda:None, finalize=True):
    """Return an independent candidate and a receipt; no synthesis or I/O.

    Baseline linear knots are never removed. Changes are additive smooth
    phrase envelopes. A whole-channel headroom scale avoids per-point clipping
    and preserves the source variation instead of flattening its peaks.
    """
    import numpy as np
    from vocal_expression import TempoMap
    from calibration_refinement import density_settings,finalize_expression,_serialized_position
    preset,strength,_=settings({'performance_style':preset,'style_strength':strength,'style_pitch':pitch_enabled})
    density_settings({'expression_density':density_mode})
    if not isinstance(project,dict) or not isinstance(project.get('tracks'),list):
        raise ValueError('风格输入必须为 SVP 工程对象。')
    clock=TempoMap(project.get('time',{}).get('tempo',[]))
    selected=track_indices_for(project) if track_indices is None else list(track_indices)
    if (any(type(i) is not int or not 0<=i<len(project['tracks']) for i in selected)
            or len(selected)!=len(set(selected))):
        raise ValueError('风格目标轨道索引无效或重复。')
    out=copy.deepcopy(project);rows=[];changed=set();remaining=MAX_POINTS
    receipt={'schema':1,'kind':'vwa.style-adaptation','version':VERSION,'preset':preset,
        'label':PRESETS[preset]['label'],'strength':strength,'density_mode':density_mode,
        'track_indices':selected,'status':'not_selected','experimental':True,
        'recipe_origin':'manual_audition_draft','semantic_model_used':False,
        'learned_coefficients':False,'voicebank_calibrated':False,'rendered':False,'listened':False,
        'phrase_gap_seconds':PHRASE_GAP,'style_grid_seconds':STYLE_GRID,'tracks':rows,
        'pitch':{'enabled':pitch_enabled,'status':'not_selected'}}
    if preset=='none' or strength==0:
        return out,receipt
    for index in selected:
        cancel();track=project['tracks'][index];phrases,offset=_phrases(track,clock)
        controls={};row={'track_index':index,'name':track.get('name',''),
            'phrases':[{'start':a,'end':b} for a,b in phrases],'controls':controls}
        rows.append(row)
        if not phrases:row['status']='no_singing_notes';continue
        parameters=track['mainGroup'].get('parameters',{})
        if not isinstance(parameters,dict):raise ValueError('风格目标参数列表无效。')
        sample_seconds=[]
        for a,b in phrases:
            count=math.ceil((b-a)/STYLE_GRID)
            if count>remaining:raise ValueError('风格选区超过参数采样容量。')
            sample_seconds.extend(a+np.arange(count+1)*STYLE_GRID)
            duration=b-a
            sample_seconds.extend([a,b,a+min(.04,duration*.15),b-min(.04,duration*.15),
                a+min(.16,duration*.25),b-min(.24,duration*.30)])
        # Guards at one blick outside the edited interval preserve extrapolation
        # and all original piecewise-linear spans outside selected phrases.
        positions=[clock.to_position(float(t))-offset for t in sample_seconds]
        positions.extend(p for a,b in phrases for p in (clock.to_position(a)-offset-1,clock.to_position(b)-offset+1))
        for name,values in PRESETS[preset]['controls'].items():
            cancel();original=parameters.get(name)
            state={'status':'skipped','recipe_start_core_release':list(values)};controls[name]=state
            try:xs,ys=_curve(original,track_index=index,channel=name)
            except ValueError as error:
                if str(error)!='preserved_nonlinear':raise
                state['reason']='preserved_nonlinear';continue
            state.update(original_coordinate_types_preserved=True,
                         fractional_input_positions=any(type(x) is float for x in xs))
            grid=np.unique(np.asarray(xs+positions,dtype=np.float64))
            remaining-=len(grid)
            if remaining<0:raise ValueError('风格工程超过参数采样容量。')
            base=np.interp(grid,np.asarray(xs,dtype=np.float64),np.asarray(ys,dtype=np.float64)) if xs else np.full(len(grid),CONTROLS[name][2])
            times=np.asarray([clock.to_seconds(float(x)+offset) for x in grid])
            # Three recipe values are interpolation anchors at onset/core/release.
            # The duplicated core anchor makes long sustained notes retain shape.
            definition=[values[0],values[1],values[1],values[2]]
            delta=_weights(times,phrases,definition)*strength
            active=np.abs(delta)>1e-12
            if not active.any():state['reason']='no_effective_delta';continue
            low,high,_=CONTROLS[name]
            if np.any((base[active]<low)|(base[active]>high)):
                state['reason']='source_outside_nominal_range_preserved';continue
            positive=delta>1e-12;negative=delta<-1e-12
            scale=1.
            if positive.any():scale=min(scale,float(np.min((high-base[positive])/delta[positive])))
            if negative.any():scale=min(scale,float(np.min((low-base[negative])/delta[negative])))
            scale=max(0.,scale)
            if scale<=1e-8:state.update(reason='no_headroom_preserved',headroom_scale=scale);continue
            result=base+delta*scale
            # Restore exact source objects wherever no deliberate delta applies.
            lookup={float(x):(x,y) for x,y in zip(xs,ys)}
            points=[]
            for x,before,after,d in zip(grid,base,result,delta):
                source=lookup.get(float(x))
                if source is None:
                    output_x=_serialized_position(x)
                    output_y=float(after) if abs(d)>1e-12 else float(before)
                else:
                    output_x,original_y=source
                    output_y=float(after) if abs(d)>1e-12 else original_y
                points.extend([output_x,output_y])
            curve=copy.deepcopy(original) if isinstance(original,dict) else {}
            curve.update(mode='linear',points=points)
            out['tracks'][index]['mainGroup'].setdefault('parameters',{})[name]=curve
            changed.add((index,name))
            state.update(status='applied',headroom_scale=scale,original_points=len(xs),
                points_before_density=len(grid),original_positions_retained=True,
                original_values_intentionally_changed=True,
                source_range=[min(ys),max(ys)] if ys else [CONTROLS[name][2]]*2,
                output_range=[float(min(points[1::2])),float(max(points[1::2]))],
                maximum_applied_delta=float(np.max(np.abs(delta*scale))),
                changed_samples=int(np.count_nonzero(np.abs(delta*scale)>1e-12)))
    if pitch_enabled:
        from style_pitch import apply_pitch
        receipt['pitch'],pitch_changed=apply_pitch(out,preset,strength=strength,track_indices=selected,cancel=cancel)
        changed.update(pitch_changed)
    view={'time':out['time'],'tracks':[]}
    for index in selected:
        names={name for i,name in changed if i==index}
        if names:view['tracks'].append({'mainRef':out['tracks'][index].get('mainRef',{}),
            'mainGroup':{'notes':out['tracks'][index]['mainGroup']['notes'],
                         'parameters':{n:out['tracks'][index]['mainGroup']['parameters'][n] for n in names}}})
    density=(finalize_expression(view,mode=density_mode,cancel=cancel) if changed else None) if finalize else {'status':'deferred','mode':density_mode,'added_points':0}
    assert _protected(project,changed)==_protected(out,changed),'Style changed protected project content'
    applied=sum(s['status']=='applied' for r in rows for s in r['controls'].values())
    skipped=sum(s['status']=='skipped' for r in rows for s in r['controls'].values())
    if pitch_enabled:
        applied+=receipt['pitch']['applied_tracks'];skipped+=receipt['pitch']['skipped_tracks']+receipt['pitch']['partial_tracks']
    receipt.update(status='partial' if applied and skipped else 'applied' if applied else 'skipped',
        applied_controls=applied,skipped_controls=skipped,expression_density=density,
        source_content_sha256=_sha(_canonical(project).encode('utf-8')),
        output_content_sha256=_sha(_canonical(out).encode('utf-8')),
        protected_content_unchanged=True,notes_unchanged=True,
        notes_and_pitch_unchanged=not any(name=='pitchDelta' for _,name in changed),
        pitch_changed=any(name=='pitchDelta' for _,name in changed),
        source_knot_positions_preserved=True,outside_selected_phrases_unchanged=True)
    return out,receipt


def apply_style_svp(source, destination, *, preset, strength=.6, track_indices=None, density_mode='dense', pitch_enabled=False, cancel=lambda:None):
    """Write a recoverable candidate and bound receipt, refusing input overwrite."""
    from common import atomic_json
    import os
    import tempfile
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if source==destination:raise ValueError('风格候选不能覆盖原工程。')
    sidecar=destination.with_suffix(destination.suffix+'.style.json')
    prior=source.with_suffix(source.suffix+'.style.json')
    if prior.is_file():
        record=json.loads(prior.read_text(encoding='utf-8'))
        if canonical_kind(record.get('kind'))=='vwa.style-candidate':raise ValueError('输入已是风格候选；请从原表现工程生成，避免重复叠加。')
    raw=source.read_bytes();source_sha=_sha(raw)
    project=json.loads(raw.decode('utf-8-sig').rstrip('\x00'))
    out,receipt=apply_style(project,preset,strength=strength,track_indices=track_indices,density_mode=density_mode,pitch_enabled=pitch_enabled,cancel=cancel)
    if preset!='none' and strength>0 and receipt['status']=='skipped':
        raise ValueError('目标曲线未形成可用风格变化；请检查非线性曲线、可用余量或空轨道。')
    payload=(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8')
    if source.read_bytes()!=raw:raise ValueError('生成风格期间原工程发生变化，请重试。')
    receipt.update(kind='vwa.style-candidate',source_sha256=source_sha,output_sha256=_sha(payload))
    receipt['producer_sha256']=_sha(Path(__file__).read_bytes())
    receipt['pitch_producer_sha256']=_sha(Path(__file__).with_name('style_pitch.py').read_bytes()) if pitch_enabled else None
    if destination.exists() and destination.read_bytes()!=payload:
        raise FileExistsError('已有不同的风格候选，未覆盖：'+str(destination))
    if sidecar.exists():
        previous=json.loads(sidecar.read_text(encoding='utf-8'))
        if previous!=receipt:raise FileExistsError('已有不同的风格回执，未覆盖：'+str(sidecar))
    cancel();destination.parent.mkdir(parents=True,exist_ok=True)
    if not destination.exists():
        with tempfile.NamedTemporaryFile(dir=destination.parent,prefix=destination.name+'.',suffix='.pending',delete=False) as stream:
            pending=Path(stream.name);stream.write(payload)
        try:
            cancel()
            # Windows rename refuses an intervening existing destination. A
            # distinct temporary name also avoids sharing a pending file.
            if os.name=='nt':pending.rename(destination)
            else:
                os.link(pending,destination)
        finally:pending.unlink(missing_ok=True)
    atomic_json(sidecar,receipt)
    return receipt


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('destination',type=Path)
    parser.add_argument('--preset',choices=tuple(PRESETS),required=True)
    parser.add_argument('--strength',type=float,default=.6)
    parser.add_argument('--tracks',help='Comma-separated zero-based track indices; default first vocal')
    parser.add_argument('--density',choices=('original','dense'),default='dense')
    parser.add_argument('--style-pitch',action='store_true',help='Add structural pitch gestures over the original curve')
    args=parser.parse_args()
    result=apply_style_svp(args.source,args.destination,preset=args.preset,strength=args.strength,
        track_indices=[int(x) for x in args.tracks.split(',')] if args.tracks else None,density_mode=args.density,pitch_enabled=args.style_pitch)
    print(json.dumps(result,ensure_ascii=False,indent=2))
