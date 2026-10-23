"""Optional structural pitch gestures, added to authored pitch rather than replacing it.

Numerical recipes are manual audition drafts. No semantic or acoustic model is
used. Nonlinear curves retain their original interpolation and are skipped.
"""
import copy
import math

VERSION=1
GRID_SECONDS=.005
MAX_POINTS=2_000_000
# Entry cents/seconds, interval lag cents per semitone / limit / seconds,
# release cents/seconds. Different phases; no constant detuning of whole notes.
RECIPES={
    'soft_ballad':{'entry':(-24.,.16),'connection':(3.,18.,.10),'release':(-28.,.22),'vibrato':(7.,5.,.45)},
    'intimate_narration':{'entry':(-12.,.08),'connection':(1.5,9.,.06),'release':(-20.,.14),'vibrato':None},
    'power_pop':{'entry':(18.,.07),'connection':(2.5,18.,.075),'release':(-14.,.12),'vibrato':(10.,5.5,.38)},
}


def enabled(options):
    value=options.get('style_pitch',False)
    if type(value) is not bool:raise ValueError('风格音高开关必须为布尔值。')
    return value


def _pulse(t,a,b,peak):
    import numpy as np
    u=np.clip((t-a)/(b-a),0.,1.)
    v=np.where(u<=peak,u/peak,(1.-u)/(1.-peak))
    return v*v*(3.-2.*v)


def apply_pitch(data,preset,*,strength,track_indices,cancel=lambda:None):
    """Mutate only the independent output's selected pitchDelta, return audit data.

    Explicitly authored/generated pitch modes are not flattened or converted.
    All old knot coordinates remain. Additive gestures have compact support;
    outside those windows the original piecewise-linear function is retained.
    """
    import numpy as np
    from vocal_expression import TempoMap
    from style_presets import _curve,_phrases,PHRASE_GAP
    recipe=RECIPES[preset];clock=TempoMap(data['time']['tempo'])
    report={'enabled':True,'version':VERSION,'status':'skipped','units':'cents',
            'recipe_origin':'manual_audition_draft','recipe':{k:list(v) if v is not None else None for k,v in recipe.items()},
            'sample_seconds':GRID_SECONDS,'tracks':[],'note_attributes_unchanged':True,
            'vibratoEnv_unchanged':True,'original_knot_positions_preserved':True}
    changed=set()
    for index in track_indices:
        cancel();track=data['tracks'][index];_,offset=_phrases(track,clock)
        group=track['mainGroup'];params=group.get('parameters',{});original=params.get('pitchDelta')
        row={'track_index':index,'status':'skipped','gestures':[],'skipped_notes':[]}
        report['tracks'].append(row)
        try:xs,ys=_curve(original,float_positions=True)
        except ValueError as error:row['reason']=str(error);continue
        notes=[]
        for ni,note in enumerate(group['notes']):
            text=str(note.get('lyrics','')).strip().lower();attr=note.get('attributes',{})
            if (text in ('sil','sp','pau','br') or (text.startswith('br') and text[2:].isdigit())
                    or note.get('musicalType')=='rap' or attr.get('muted') is True):
                row['skipped_notes'].append({'note_index':ni,'reason':'non_singing_or_rap'});continue
            pitch=note.get('pitch')
            if type(pitch) not in (int,float) or not math.isfinite(pitch):
                row['skipped_notes'].append({'note_index':ni,'reason':'invalid_note_pitch'});continue
            a=clock.to_seconds(note['onset']+offset)
            b=clock.to_seconds(note['onset']+note['duration']+offset)
            notes.append((a,b,float(pitch),ni,note))
        notes.sort(key=lambda n:(n[0],n[1],n[3]))
        gestures=row['gestures'];skipped=row['skipped_notes']
        def add(kind,a,b,amount,ni,peak=.22,frequency=None):
            if b-a<.025 or abs(amount)<1e-9:return
            gestures.append({'kind':kind,'start':a,'end':b,'cents_at_full_strength':amount,
                             'note_index':ni,'peak_fraction':peak,'frequency_hz':frequency})
        for j,(a,b,pitch,ni,note) in enumerate(notes):
            cancel();duration=b-a;previous=notes[j-1] if j else None
            following=notes[j+1] if j+1<len(notes) else None
            if (previous and previous[1]>a+1e-8) or (following and following[0]<b-1e-8):
                skipped.append({'note_index':ni,'reason':'overlapping_notes'});continue
            if duration<.16:
                skipped.append({'note_index':ni,'reason':'short_note_preserved'});continue
            entry=previous is None or a-previous[1]>PHRASE_GAP
            ending=following is None or following[0]-b>PHRASE_GAP
            if entry:
                amount,seconds=recipe['entry']
                add('phrase_entry',a,a+min(seconds,duration*.36),amount,ni)
            elif a-previous[1]<=.09 and abs(pitch-previous[2])>=1.:
                per_semitone,limit,seconds=recipe['connection']
                amount=-math.copysign(min(limit,abs(pitch-previous[2])*per_semitone),pitch-previous[2])
                add('interval_connection',a,a+min(seconds,duration*.32),amount,ni)
            if ending:
                amount,seconds=recipe['release']
                add('phrase_release',b-min(seconds,duration*.35),b,amount,ni,peak=.72)
            # Add delayed vibrato only to long, locally flat pitch with an
            # explicit zero depth. Unknown/default or existing vibrato stays.
            if recipe['vibrato'] and duration>=.85:
                depth=note.get('attributes',{}).get('dF0Vbr')
                if depth!=0 or type(depth) not in (int,float):
                    skipped.append({'note_index':ni,'reason':'existing_or_unknown_vibrato_preserved'});continue
                amplitude,frequency,delay=recipe['vibrato'];left=a+delay;right=b-min(.18,duration*.2)
                if right-left<.25:continue
                probe=np.arange(left,right+.00001,.01)
                positions=np.array([clock.to_position(float(t))-offset for t in probe])
                values=np.interp(positions,xs,ys) if xs else np.zeros(len(probe))
                if float(np.ptp(values))>10.:
                    skipped.append({'note_index':ni,'reason':'authored_pitch_variation_preserved'});continue
                add('delayed_vibrato',left,right,amplitude,ni,peak=.5,frequency=frequency)
        if not gestures:row['reason']='no_eligible_gestures';continue
        extra=[]
        for gesture in gestures:
            a,b=gesture['start'],gesture['end'];count=math.ceil((b-a)/GRID_SECONDS)
            if count+len(extra)+len(xs)>MAX_POINTS:raise ValueError('风格音高超过采样容量。')
            extra.extend(clock.to_position(float(t))-offset for t in np.linspace(a,b,count+1))
            extra.extend((clock.to_position(a)-offset-1,clock.to_position(b)-offset+1,
                          clock.to_position(a+(b-a)*gesture['peak_fraction'])-offset))
        # SVP permits numeric automation times. Preserve fractional authored
        # blicks exactly rather than truncating them into integer coordinates.
        grid=np.unique(np.asarray(xs+extra,dtype=np.float64))
        if len(grid)>MAX_POINTS:raise ValueError('风格音高超过采样容量。')
        times=np.array([clock.to_seconds(float(p)+offset) for p in grid])
        base=np.interp(grid,xs,ys) if xs else np.zeros(len(grid))
        delta=np.zeros(len(grid));accepted=[];omitted=[]
        for gesture in gestures:
            cancel();a,b=gesture['start'],gesture['end'];left,right=np.searchsorted(times,[a,b])
            t=times[left:right]
            shape=_pulse(t,a,b,gesture['peak_fraction'])
            if gesture['frequency_hz'] is not None:
                shape*=np.sin(2.*np.pi*gesture['frequency_hz']*(t-a))
            local=shape*gesture['cents_at_full_strength']*strength
            active_local=abs(local)>1e-10;source=base[left:right]
            if np.any(abs(source[active_local])>1200.):
                omitted.append({**gesture,'reason':'source_outside_nominal_range_preserved'});continue
            local_scale=1.;pos=local>1e-10;neg=local<-1e-10
            if pos.any():local_scale=min(local_scale,float(np.min((1200.-source[pos])/local[pos])))
            if neg.any():local_scale=min(local_scale,float(np.min((-1200.-source[neg])/local[neg])))
            local_scale=max(0.,local_scale)
            if local_scale<=1e-8:
                omitted.append({**gesture,'reason':'no_headroom_preserved'});continue
            accepted.append({**gesture,'headroom_scale':local_scale})
            delta[left:right]+=local*local_scale
        row['gestures']=accepted;row['omitted_gestures']=omitted
        active=abs(delta)>1e-10
        if not active.any():row['reason']='no_effective_delta';continue
        scale=1.;positive=delta>1e-10;negative=delta<-1e-10
        if positive.any():scale=min(scale,float(np.min((1200.-base[positive])/delta[positive])))
        if negative.any():scale=min(scale,float(np.min((-1200.-base[negative])/delta[negative])))
        scale=max(0.,scale)
        if scale<=1e-8:row.update(reason='no_headroom_preserved',headroom_scale=scale);continue
        result=base+delta*scale;lookup=dict(zip(xs,ys));positions={float(x):x for x in xs};points=[]
        for x,y,old,d in zip(grid,result,base,delta):
            x=positions.get(float(x),int(x) if float(x).is_integer() else float(x))
            points.extend((x,lookup.get(x,float(old)) if abs(d)<=1e-10 else float(y)))
        curve=copy.deepcopy(original) if isinstance(original,dict) else {}
        curve.update(mode='linear',points=points)
        group.setdefault('parameters',{})['pitchDelta']=curve;changed.add((index,'pitchDelta'))
        row.update(status='partial' if omitted else 'applied',headroom_scale=scale,original_points=len(xs),output_points=len(grid),
                   maximum_applied_delta_cents=float(np.max(abs(delta*scale))),
                   changed_samples=int(np.count_nonzero(active)),original_curve_metadata_preserved=True)
    applied=sum(row['status'] in ('applied','partial') for row in report['tracks'])
    skipped=sum(row['status']=='skipped' for row in report['tracks'])
    partial=sum(row['status']=='partial' for row in report['tracks'])
    report.update(status='partial' if applied and (skipped or partial) else 'applied' if applied else 'skipped',
                  applied_tracks=applied,skipped_tracks=skipped,partial_tracks=partial)
    return report,changed
