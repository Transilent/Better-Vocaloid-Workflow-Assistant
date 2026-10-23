"""Detailed expression curves and optional conservative performance transfer."""
import math

VERSION=2
FEATURE_WEIGHTS=(1.,.9,.35,.65,.55,.45,.35,.30,.25,.20,.15,.15)
CONTROL_PENALTIES={'loudness':1.,'tension':1.3,'breathiness':2.8,'voicing':3.,'gender':7.,'toneShift':8.}


def density_settings(options):
    mode = options.get('expression_density', 'dense')
    if mode not in ('original', 'dense'):
        raise ValueError('参数采样请选择保留原始采样或加密采样。')
    return mode


def _curve_points(curve, *, track_index, channel, source):
    """Validate one serialized curve and return exact Python coordinates."""
    def failure(detail):
        return ValueError(f'参数曲线{detail}（来源={source}，轨道={track_index}，通道={channel}）。')
    if not isinstance(curve,dict):
        raise failure('格式无效')
    points=curve.get('points',[])
    if not isinstance(points,list) or len(points)%2:
        raise failure('点对数量无效')
    result=[]
    for index in range(0,len(points),2):
        x,y=points[index:index+2]
        if type(x) not in (int,float) or not math.isfinite(x) or abs(x)>2**53:
            raise failure(f'坐标不是有限安全数值（样本={index//2}，坐标={x!r}）')
        if type(y) not in (int,float) or not math.isfinite(y):
            raise failure(f'数值不是有限数值（样本={index//2}，坐标={x!r}，数值={y!r}）')
        result.append((x,y))
    for index,(first,second) in enumerate(zip(result,result[1:])):
        if first[0]==second[0]:
            raise failure(f'坐标重复（相邻样本={index}/{index+1}，坐标={first[0]!r}）')
        if first[0]>second[0]:
            raise failure(f'坐标乱序（相邻样本={index}/{index+1}，坐标={first[0]!r}>{second[0]!r}）')
    return result


def _serialized_position(value):
    """Represent a computed blick compactly without truncating fractions."""
    value=float(value);nearest=round(value)
    return int(nearest) if math.isclose(value,nearest,rel_tol=1e-12,abs_tol=1e-9) else value


def finalize_expression(data, *, step=.005, mode='dense', cancel=lambda:None):
    """Densify serialized linear SVP expression without touching score or pitch.

    Keep every input knot, including neutral and manually edited knots. Sample
    changing spans and existing controls during sustained notes on an audio-time
    grid. Constant rests remain compact; flat controls acquire no movement.
    Interpolate in blicks so tempo changes
    and group offsets remain correct. Repeated finalization is idempotent.
    Nonlinear editor curves retain their original interpolation unchanged.
    """
    import numpy as np
    from expression_calibration import CONTROLS
    from vocal_expression import TempoMap,SV_QUARTER
    if type(step) not in (int,float) or not math.isfinite(step) or not .001<=step<=.02:
        raise ValueError('参数曲线的采样间隔无效。')
    density_settings({'expression_density':mode})
    clock=TempoMap(data['time']['tempo']);rows=[];updates=[]
    tempo_seconds=np.asarray(clock.seconds);tempo_positions=np.asarray(clock.positions)
    tempo_bpms=np.asarray(clock.bpms)
    for index,track in enumerate(data.get('tracks',[])):
        cancel()
        if track.get('mainRef',{}).get('isInstrumental'):continue
        offset=track.get('mainRef',{}).get('blickOffset',0)
        if type(offset) is not int:raise ValueError('演唱轨的时间偏移无效。')
        notes=track.get('mainGroup',{}).get('notes',[])
        note_spans=[]
        for note in notes if mode=='dense' else ():
            onset,duration=note.get('onset'),note.get('duration')
            if type(onset) is not int or type(duration) is not int or duration<=0:
                raise ValueError('演唱轨的音符时间无效。')
            note_spans.append((clock.to_seconds(onset+offset),clock.to_seconds(onset+duration+offset)))
        controls={}
        for name in CONTROLS:
            curve=track.get('mainGroup',{}).get('parameters',{}).get(name)
            if curve is None:continue
            if not isinstance(curve,dict):raise ValueError('参数曲线格式无效：'+name)
            parsed=_curve_points(curve,track_index=index,channel=name,source='expression_density')
            points=curve.get('points',[]);lo,hi,_,_=CONTROLS[name]
            count=len(parsed)
            fractional_positions=any(type(x) is float for x,_ in parsed)
            state={'points_before':count,'points_after':count,'added_points':0,
                   'original_knots_preserved':True,'original_coordinate_types_preserved':True,
                   'fractional_input_positions':fractional_positions,
                   'range_unchanged':True,
                   'outside_nominal_solver_range':any(v<lo or v>hi for _,v in parsed)}
            controls[name]=state
            if mode=='original':
                state['status']='preserved_original';continue
            if not count:
                state['status']='empty';continue
            # Do not replace a user's cubic interpolation with another spline.
            if curve.get('mode','cubic')!='linear':
                state['status']='preserved_nonlinear';continue
            x=np.asarray([value for value,_ in parsed],dtype=np.float64)
            y=np.asarray([value for _,value in parsed],dtype=np.float64)
            seconds=np.asarray([clock.to_seconds(float(v)+offset) for v in x])
            changing=np.diff(y)!=0
            state.update(minimum=float(y.min()),maximum=float(y.max()),
                         max_changing_gap_ms_before=float(np.diff(seconds)[changing].max(initial=0)*1000))
            # Densify flat sustained controls too, without padding long rests.
            edges=np.diff(np.r_[False,changing,False].astype(int))
            runs=list(zip(np.where(edges==1)[0],np.where(edges==-1)[0]))
            spans=[(seconds[a],seconds[b]) for a,b in runs]
            if count==1:
                # A single linear knot represents a constant value. Sampling
                # it on known vocal spans changes no part of that function.
                spans.extend(note_spans)
            else:
                spans.extend((max(a,seconds[0]),min(b,seconds[-1])) for a,b in note_spans
                             if min(b,seconds[-1])>max(a,seconds[0]))
            merged=[]
            for a,b in sorted(spans):
                if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
                else:merged.append((a,b))
            estimated=count+sum(math.ceil((b-a)/step)+4 for a,b in merged)
            if estimated>2_000_000:raise ValueError('参数曲线过长，请分段处理。')
            candidates=[x]
            for a,b in merged:
                cancel()
                t=np.unique(np.r_[a,np.arange(math.ceil(a/step),math.floor(b/step)+1)*step,b])
                ti=np.maximum(0,np.searchsorted(tempo_seconds,t,side='right')-1)
                absolute=tempo_positions[ti]+(t-tempo_seconds[ti])*tempo_bpms[ti]/60*SV_QUARTER
                if fractional_positions:
                    positions=absolute.astype(np.float64)-offset
                    nearest=np.rint(positions)
                    positions=np.where(np.isclose(positions,nearest,rtol=1e-12,atol=1e-9),nearest,positions)
                else:
                    positions=np.rint(absolute).astype(np.int64)-offset
                candidates.append(positions if count==1 else positions[(positions>x[0])&(positions<x[-1])])
            # Include tempo boundaries in changing spans even off the 5-ms grid.
            anchors=tempo_positions-offset
            adjacent=np.searchsorted(x,anchors,side='right')-1
            usable=(adjacent>=0)&(adjacent<len(changing))
            if len(changing):
                candidates.append(anchors[usable&changing[np.clip(adjacent,0,len(changing)-1)]])
            dense_x=np.unique(np.concatenate(candidates))
            if len(dense_x)==len(x):
                dense_y=y
            else:
                dense_y=np.interp(dense_x,x,y)
                knots=np.searchsorted(dense_x,x);dense_y[knots]=y
                if not np.array_equal(dense_y[knots],y):raise ValueError('原参数控制点校验失败。')
                if dense_y.min()<y.min()-1e-12 or dense_y.max()>y.max()+1e-12:
                    raise ValueError('参数插值超出原曲线范围。')
                original_at={int(slot):source_index for source_index,slot in enumerate(knots)}
                replacement=[]
                for output_index,(position,value) in enumerate(zip(dense_x.tolist(),dense_y.tolist())):
                    source_index=original_at.get(output_index)
                    if source_index is None:
                        replacement.extend((_serialized_position(position),float(value)))
                    else:
                        replacement.extend((parsed[source_index][0],parsed[source_index][1]))
                updates.append((curve,replacement))
            written_seconds=np.asarray([clock.to_seconds(float(v)+offset) for v in dense_x])
            gaps=np.diff(written_seconds)[np.diff(dense_y)!=0]*1000
            max_gap=float(gaps.max(initial=0))
            if max_gap>step*1000+1e-5:raise ValueError('参数曲线加密后的间隔校验失败。')
            sampled_gap=0.
            for a,b in merged:
                inside=(written_seconds[:-1]<b-1e-8)&(written_seconds[1:]>a+1e-8)
                sampled_gap=max(sampled_gap,float(np.diff(written_seconds)[inside].max(initial=0)*1000))
            if sampled_gap>step*1000+1e-5:raise ValueError('持音参数采样间隔校验失败。')
            state.update(status='densified' if len(dense_x)>count else ('no_sampling_span' if count==1 else 'constant' if not changing.any() else 'already_dense'),
                         points_after=len(dense_x),added_points=len(dense_x)-count,
                         continuous_linear_function_preserved=True,
                         sampling_spans_seconds=[[float(a),float(b)] for a,b in merged],
                         max_sampled_gap_ms=sampled_gap,
                         max_changing_gap_ms=max_gap,p95_changing_gap_ms=float(np.percentile(gaps,95)) if len(gaps) else 0.)
        if controls:rows.append({'track_index':index,'track_name':track.get('name',''),'controls':controls})
    # Validation/cancellation cannot leave a partially finalized in-memory file.
    cancel()
    for curve,points in updates:curve['points']=points
    controls=[c for row in rows for c in row['controls'].values()]
    unsupported_nonlinear=sum(c.get('status')=='preserved_nonlinear' for c in controls)
    unsupported_fractional=sum(c.get('status')=='preserved_unsupported_fractional_time' for c in controls)
    unsupported=unsupported_nonlinear+unsupported_fractional
    added=sum(c['added_points'] for c in controls)
    present=sum(c['points_before']>0 for c in controls)
    no_span=any(c.get('status')=='no_sampling_span' for c in controls)
    status=('preserved_original' if mode=='original' else 'partial_unsupported' if unsupported else
            'densified' if added else 'no_sampling_span' if no_span else 'already_dense' if present else 'no_curves')
    return {'schema':2,'mode':mode,'status':status,'added_points':added,
            'unsupported_nonlinear_controls':unsupported_nonlinear,
            'unsupported_fractional_time_controls':unsupported_fractional,
            'step_seconds':step,'interpolation':'original_piecewise_linear',
            'scope':'main_group_expression','pitch_and_notes_untouched':True,
            'constant_rests_expanded':False,'sustained_note_spans_sampled':mode=='dense','tracks':rows}


def finalize_svp(path, *, step=.005, mode='dense', cancel=lambda:None):
    """Validate actual serialized output and commit expression changes atomically."""
    import json
    import os
    import tempfile
    from pathlib import Path
    from common import native_path
    path=Path(path);raw=path.read_bytes()
    data=json.loads(raw.removesuffix(b'\x00').decode('utf-8-sig'))
    report=finalize_expression(data,step=step,mode=mode,cancel=cancel)
    changed=any(c['added_points'] for row in report['tracks'] for c in row['controls'].values())
    report['changed']=changed
    if not changed:return report
    payload=json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    if json.loads(payload)!=data:raise ValueError('参数曲线保存后校验失败。')
    if raw.endswith(b'\x00'):payload+=b'\x00'
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=native_path(path.parent),prefix=path.name+'.',suffix='.pending',delete=False) as stream:
            temporary=stream.name;stream.write(payload);stream.flush();os.fsync(stream.fileno())
        cancel()
        if path.read_bytes()!=raw:raise ValueError('加密参数曲线时工程已改变，请重试。')
        os.replace(temporary,native_path(path));temporary=None
    finally:
        if temporary:Path(temporary).unlink(missing_ok=True)
    return report


def dense_points(points,*,step=.005,neutral=0.,cancel=lambda:None):
    """Densify a measured contour without reducing its knot amplitudes.

    Shape-preserving cubic interpolation adds continuity, not new measurements.
    Every existing knot is retained; only interiors of neutral plateaus are omitted.
    """
    import numpy as np
    from scipy.interpolate import PchipInterpolator
    data=np.asarray(points,float)
    if (data.ndim!=2 or data.shape[1]!=2 or len(data)<2 or not np.isfinite(data).all()
            or not np.all(np.diff(data[:,0])>0) or type(step) not in (int,float)
            or not math.isfinite(step) or not .001<=step<=.02 or not math.isfinite(neutral)):
        raise ValueError('加密曲线的时间、数值或采样间隔无效。')
    cancel()
    start,end=data[0,0],data[-1,0]
    if (end-start)/step>2_000_000:raise ValueError('曲线过长，请分段处理。')
    regular=np.arange(math.ceil(start/step),math.floor(end/step)+1)*step
    # Avoid nearly duplicate float timestamps while retaining original knots.
    regular=regular[(regular>start+1e-9)&(regular<end-1e-9)]
    indices=np.searchsorted(data[:,0],regular)
    distance=np.minimum(abs(regular-data[np.maximum(indices-1,0),0]),abs(regular-data[np.minimum(indices,len(data)-1),0]))
    x=np.sort(np.r_[data[:,0],regular[distance>1e-9]])
    y=PchipInterpolator(data[:,0],data[:,1])(x)
    # Set original measurements exactly, including their floating point values.
    y[np.searchsorted(x,data[:,0])]=data[:,1]
    keep=np.ones(len(x),bool)
    zero=y==neutral
    if len(x)>2:keep[1:-1]=~(zero[:-2]&zero[1:-1]&zero[2:])
    cancel()
    return list(zip(x[keep].tolist(),y[keep].tolist()))


def detail_curves(curves,times,active,notes,reference,*,cancel=lambda:None):
    """Keep the original fit and bridge only short audible analysis dropouts."""
    import numpy as np
    times=np.asarray(times,float);active=np.asarray(active,bool);reference=np.asarray(reference,float)
    if (times.ndim!=1 or len(times)<2 or active.shape!=times.shape or not np.isfinite(times).all()
            or not np.all(np.diff(times)>0) or reference.ndim!=2 or reference.shape[0]!=len(times)
            or reference.shape[1]<4 or not np.isfinite(reference).all()):
        raise ValueError('细节曲线的时间或特征无效。')
    inside=np.zeros(len(times),bool)
    for start,end,pitch in notes:inside|=(times>=start)&(times<end)
    observed=active&inside;support=observed.copy();hop=float(np.median(np.diff(times)));bridges=0
    edges=np.diff(np.r_[False,~support,False].astype(int))
    for left,right in zip(np.where(edges==1)[0],np.where(edges==-1)[0]):
        if (left==0 or right==len(times) or (right-left)*hop>.080001 or not inside[left:right].all()
                or not observed[left-1] or not observed[right]):continue
        floor=min(reference[left-1,0],reference[right,0])-9.
        if (reference[left:right,0]>=floor).all() and (reference[left:right,3]>=.5).all():
            support[left:right]=True;bridges+=1
    output={}
    for name,values in curves.items():
        cancel();neutral=1. if name=='voicing' else 0.
        values=np.asarray(values,float)
        if values.shape!=times.shape or not np.isfinite(values).all():raise ValueError('细节曲线包含无效数值。')
        result=np.where(observed,values,neutral)
        missing=support&~observed
        if missing.any():result[missing]=np.interp(times[missing],times[observed],values[observed])
        output[name]=result
    return output,support,{'version':2,'strategy':'detailed','input_hop_seconds':hop,'output_step_seconds':.005,
        'interpolation':'shape_preserving_cubic','original_supported_values_preserved':True,
        'bridged_gaps':bridges,'bridged_seconds':round(float(np.count_nonzero(support&~observed))*hop,4),
        'perceptual_improvement_verified':False}


def refine_curves(curves,times,active,notes,reference,baseline,strength,*,cancel=lambda:None):
    """Note-aware continuity, noise/voicing guards and slow small identity moves.

    These are perceptual safeguards, not evidence that a rerender sounds better.
    No pitch or note positions are modified.
    """
    import numpy as np
    from scipy.ndimage import median_filter,uniform_filter1d
    times=np.asarray(times,float);active=np.asarray(active,bool)
    reference,baseline=np.asarray(reference,float),np.asarray(baseline,float)
    if (times.ndim!=1 or len(times)<2 or active.shape!=times.shape or not np.isfinite(times).all() or not np.all(np.diff(times)>0)
            or reference.ndim!=2 or reference.shape!=baseline.shape or reference.shape[0]!=len(times) or reference.shape[1]<4
            or not np.isfinite(reference).all() or not np.isfinite(baseline).all()
            or type(strength) not in (int,float) or not math.isfinite(strength) or not 0<=strength<=1):
        raise ValueError('参数稳定化的时间、特征或强度无效。')
    hop=float(np.median(np.diff(times)))
    ids=np.full(len(times),-1);long=np.zeros(len(times),bool);body=np.zeros(len(times),bool)
    for index,(start,end,pitch) in enumerate(notes):
        if not all(math.isfinite(float(v)) for v in (start,end,pitch)) or end<=start:raise ValueError('稳定化音符无效。')
        selected=(times>=start)&(times<end);ids[selected]=index
        margin=min(.06,(end-start)*.2);body|=(times>=start+margin)&(times<end-margin)
        if end-start>=.5:long|=selected
    inside=ids>=0;observed=active&inside;support=observed.copy();bridges=0
    edges=np.diff(np.r_[False,~support,False].astype(int))
    for left,right in zip(np.where(edges==1)[0],np.where(edges==-1)[0]):
        if (left==0 or right==len(times) or (right-left)*hop>.081 or not inside[left:right].all()
                or not observed[left-1] or not observed[right]):continue
        # Bridge a short analysis dropout within continuous written singing,
        # provided the reference stays audible and substantially periodic.
        floor=min(reference[left-1,0],reference[right,0])-9.
        if (reference[left:right,0]>=floor).all() and (reference[left:right,3]>=.5).all():
            support[left:right]=True;bridges+=1
    def window(seconds):return max(1,round(seconds/hop)//2*2+1)
    transitions=np.diff(np.r_[False,support,False].astype(int))
    runs=list(zip(np.where(transitions==1)[0],np.where(transitions==-1)[0]))
    output={};statistics={}
    # Limits are actual SV units at strength=1. Identity controls are kept
    # substantially smaller than a probe step, even at the highest strength.
    limits={'loudness':(-3.,3.),'tension':(-.105,.255),'breathiness':(-.15,.075),
            'voicing':(-.06,0.),'gender':(-.05,.05),'toneShift':(-40.,40.)}
    rates={'loudness':18.,'tension':1.4,'breathiness':.8,'voicing':.45,'gender':.10,'toneShift':70.}
    identity=('gender','toneShift')
    def bound_slew(delta,name,*,fade=False):
        max_step=rates[name]*strength*hop
        for left,right in runs:
            block=delta[left:right].copy()
            if fade:
                count=min(window(.08),len(block)//2)
                if count>1:
                    block[:count]*=np.linspace(0,1,count);block[-count:]*=np.linspace(1,0,count)
            # Anchor to neutral outside a run, including both endpoints.
            envelope=np.minimum(np.arange(1,len(block)+1),np.arange(len(block),0,-1))*max_step
            block=np.clip(block,-envelope,envelope)
            for i in range(1,len(block)):block[i]=np.clip(block[i],block[i-1]-max_step,block[i-1]+max_step)
            for i in range(len(block)-2,-1,-1):block[i]=np.clip(block[i],block[i+1]-max_step,block[i+1]+max_step)
            delta[left:right]=block
        delta[~support]=0
        return delta
    for name,values in curves.items():
        cancel()
        if name not in limits:raise ValueError('不支持的稳定化参数。')
        neutral=1. if name=='voicing' else 0.
        original=np.asarray(values,float)-neutral
        if original.shape!=times.shape or not np.isfinite(original).all():raise ValueError('参数曲线含无效数值。')
        delta=original.copy();delta[~observed]=0
        for left,right in runs:
            seen=np.flatnonzero(observed[left:right])+left
            if not len(seen):continue
            block=np.interp(times[left:right],times[seen],original[seen])
            if name in identity:
                # Constant within a note body, with slow transitions between
                # notes. This cannot chase consonants or a vibrato cycle.
                for note_id in np.unique(ids[left:right]):
                    local=ids[left:right]==note_id;reliable=local&observed[left:right]&body[left:right]
                    if reliable.any():block[local]=np.median(block[reliable])
                filtered=uniform_filter1d(median_filter(block,size=window(.18),mode='nearest'),size=window(.45),mode='nearest')*.3
            else:
                fast=.10 if name=='loudness' else .14
                slow=.22 if name=='loudness' else .34
                quick=uniform_filter1d(median_filter(block,size=window(.04),mode='nearest'),size=window(fast),mode='nearest')
                held=uniform_filter1d(median_filter(block,size=window(.10),mode='nearest'),size=window(slow),mode='nearest')
                weight=uniform_filter1d(long[left:right].astype(float),size=window(.08),mode='nearest')
                filtered=quick*(1-weight)+held*weight
            delta[left:right]=filtered
        lower,upper=limits[name];delta=np.clip(delta,lower*strength,upper*strength)
        if name=='breathiness':
            # A hissier reference alone does not justify synthetic aspiration.
            # Require a voiced note body and a sustained joint noise/periodicity
            # cue; unvoiced consonants retain their engine articulation.
            evidence=(reference[:,2]-baseline[:,2]>1.5)&(baseline[:,3]-reference[:,3]>.015)
            evidence&=observed&body&(reference[:,3]>=.70)
            accepted=np.zeros(len(times),bool)
            changes=np.diff(np.r_[False,evidence,False].astype(int))
            for left,right in zip(np.where(changes==1)[0],np.where(changes==-1)[0]):
                if (right-left)*hop>=.10:accepted[left:right]=True
            gate=np.zeros(len(times))
            for left,right in runs:
                gate[left:right]=uniform_filter1d(accepted[left:right].astype(float),size=window(.08),mode='nearest')
            delta=np.where(delta>0,delta*gate,delta)
        if name=='voicing':delta=np.maximum(delta,np.where(long,-.035,-.06)*strength)
        if name=='tension':delta=np.maximum(delta,np.where(long,-.065,-.105)*strength)
        delta=bound_slew(delta,name,fade=True)
        output[name]=neutral+delta
    def meaningful(name,delta):
        threshold=.05 if name=='loudness' else 1. if name=='toneShift' else .004
        edges=np.diff(np.r_[False,abs(delta)>threshold,False].astype(int))
        lengths=np.where(edges==-1)[0]-np.where(edges==1)[0]
        return lengths.max(initial=0)>=3
    if 'voicing' in output and 'breathiness' in output and meaningful('breathiness',output['breathiness']):
        breath=np.maximum(output['breathiness'],0)/max(.075*strength,1e-9)
        output['voicing']=1+bound_slew(-(1-output['voicing'])*(1-.75*np.clip(breath,0,1)),'voicing')
    omitted=[]
    for name,values in list(output.items()):
        neutral=1. if name=='voicing' else 0.;delta=values-neutral
        original=np.asarray(curves[name],float)-neutral
        if not meaningful(name,delta):
            omitted.append(name);del output[name];delta=np.zeros_like(delta)
        statistics[name]={'total_variation_before':round(float(np.abs(np.diff(original)).sum()),6),
                          'total_variation_after':round(float(np.abs(np.diff(delta)).sum()),6),
                          'max_abs_before':float(np.max(abs(original),initial=0)),
                          'max_abs_after':float(np.max(abs(delta),initial=0))}
    return output,support,{'version':VERSION,'strategy':'preserve','controls':statistics,
        'omitted_neutral_controls':omitted,
        'bridged_gaps':bridges,'bridged_seconds':round(float(np.count_nonzero(support&~observed))*hop,4),
        'long_note_frames':int(np.count_nonzero(long&support)),
        'scope':'conservative_performance_transfer','perceptual_improvement_verified':False}
