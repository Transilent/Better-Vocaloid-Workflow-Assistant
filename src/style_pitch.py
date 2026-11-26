"""Optional structural pitch gestures, added to authored pitch rather than replacing it.

Numerical recipes are manual audition drafts. No semantic or acoustic model is
used. Nonlinear curves retain their original interpolation and are skipped.
"""
import copy
import math

VERSION=6
GRID_SECONDS=.005
MAX_POINTS=2_000_000
EXTERNAL_MIN_NOTE_SECONDS = .16
EXTERNAL_EDGE_SECONDS = .24
EXTERNAL_PEAKS = {'onset': .33, 'release': .72}
CONNECTION_GAP = .09
CONNECTION_PEAK = .22
CONNECTION_FRACTION = .32


def connection_position(interval):
    if abs(interval) < 1.:
        return 'repeated'
    return 'rising' if interval > 0 else 'falling'


# Entry cents/seconds, interval lag cents per semitone / limit / seconds,
# release cents/seconds. Different phases; no constant detuning of whole notes.
RECIPES={
    'gentle':{'entry':(-20.,.11),'connection':(1.8,16.,.07),'release':(-12.,.09),'vibrato':None},
    'sad':{'entry':(-12.,.18),'connection':(4.,32.,.13),'release':(-58.,.30),'vibrato':None},
    'bold':{'entry':(14.,.065),'connection':(2.,16.,.06),'release':(-10.,.10),'vibrato':None},
    'chinese_opera':{'entry':(-220.,.24),'connection':(20.,180.,.20),'release':(160.,.30),'vibrato':(48.,5.3,.30)},
    'soft_ballad':{'entry':(-24.,.16),'connection':(3.,18.,.10),'release':(-28.,.22),'vibrato':(7.,5.,.45)},
    'intimate_narration':{'entry':(-12.,.08),'connection':(1.5,9.,.06),'release':(-20.,.14),'vibrato':None},
    'power_pop':{'entry':(18.,.07),'connection':(2.5,18.,.075),'release':(-14.,.12),'vibrato':(10.,5.5,.38)},
    'melancholy':{'entry':(-16.,.13),'connection':(1.5,10.,.09),'release':(-24.,.20),'vibrato':None},
    'bright_joy':{'entry':(12.,.06),'connection':(1.7,11.,.055),'release':(-7.,.08),'vibrato':None},
    'fragile_tender':{'entry':(6.,.08),'connection':(1.,8.,.06),'release':(24.,.16),'vibrato':None},
}


def pitch_structure_seed(preset):
    """Project an existing built-in gesture recipe into editable parameters."""
    recipe = RECIPES.get(preset)
    if recipe is None:
        return {}
    entry, entry_seconds = recipe['entry']
    cents, limit, seconds = recipe['connection']
    release, release_seconds = recipe['release']
    opera = preset == 'chinese_opera'
    pattern = {
        'onset': {'cents': entry, 'seconds': entry_seconds, 'peak': .10 if opera else .22},
        'connection': {'cents_per_semitone': cents, 'limit': limit, 'seconds': seconds,
                       'direction': 'follow' if opera else 'settle',
                       'repeat_cents': -32. if opera else 6. if preset == 'bold' else 0.,
                       'anticipate_descent': opera},
        'release': {'cents': release, 'seconds': release_seconds, 'peak': .72},
    }
    if recipe['vibrato']:
        depth, rate, delay = recipe['vibrato']
        pattern['vibrato'] = {'depth': depth, 'rate': rate, 'delay': delay, 'minimum_note': .85}
    return pattern


def _connection_amount(action, interval):
    if abs(interval) < 1.:
        return action['repeat_cents']
    amount = math.copysign(min(action['limit'], abs(interval) * action['cents_per_semitone']), interval)
    return amount if action['direction'] == 'follow' else -amount


def enabled(options):
    value=options.get('style_pitch',False)
    if type(value) is not bool:raise ValueError('风格音高开关必须为布尔值。')
    return value


def _pulse(t,a,b,peak):
    import numpy as np
    u=np.clip((t-a)/(b-a),0.,1.)
    v=np.where(u<=peak,u/peak,(1.-u)/(1.-peak))
    return v*v*(3.-2.*v)


def apply_pitch(data,preset,*,strength,track_indices,detail=None,time_range=None,cancel=lambda:None,
                external=None,structure=None):
    """Mutate only the independent output's selected pitchDelta, return audit data.

    Explicitly authored/generated pitch modes are not flattened or converted.
    All old knot coordinates remain. Additive gestures have compact support;
    outside those windows the original piecewise-linear function is retained.
    """
    import numpy as np
    from tuning_clock import TempoMap
    from style_helpers import (_curve, _phrases, _note_spans, _range_gate, time_range_settings,
                               detail_settings, PHRASE_GAP, NOTE_ENVELOPE_PRESETS)
    detail=detail_settings(detail)
    time_range=time_range_settings(time_range) if time_range is not None else None
    authored = external is not None or structure is not None
    recipe = RECIPES[preset] if not authored else None
    pattern = structure if structure is not None else pitch_structure_seed(preset)
    clock=TempoMap(data['time']['tempo'])
    report={'enabled':True,'version':VERSION,'status':'skipped','units':'cents',
            'recipe_origin':('external_phrase_structure' if structure is not None else
                             'external_pitch_edges' if external is not None else 'manual_audition_draft'),
            'recipe':({k:list(v) if v is not None else None for k,v in recipe.items()} if recipe is not None
                      else copy.deepcopy(structure if structure is not None else external)),
            'sample_seconds':GRID_SECONDS,'detail':detail,'time_range_seconds':time_range,
            'tracks':[],'note_attributes_unchanged':True,
            'vibratoEnv_unchanged':True,'original_knot_positions_preserved':True}
    if structure is not None and 'vibrato' in structure:
        report['vibrato_policy'] = 'known_disabled_native_vibrato_and_flat_pitch_only'
    elif preset=='chinese_opera':
        report['vibrato_policy']='add_only_on_long_flat_notes_without_explicit_vibrato; native_default_is_not_verified'
    changed=set()
    for index in track_indices:
        cancel();track=data['tracks'][index];_,offset=_phrases(track,clock)
        bounded = authored or preset in NOTE_ENVELOPE_PRESETS
        allowed = set(_note_spans(track,clock,offset)[0]) if bounded else None
        group=track['mainGroup'];params=group.get('parameters',{});original=params.get('pitchDelta')
        # Preserve the true score adjacency even when an intervening short,
        # muted, overlapping or non-singing note is omitted from editing.
        physical = sorted(range(len(group['notes'])), key=lambda i: (
            group['notes'][i]['onset'], group['notes'][i]['duration'], i))
        physical_index = {ni: position for position, ni in enumerate(physical)}
        row={'track_index':index,'status':'skipped','gestures':[],'skipped_notes':[]}
        report['tracks'].append(row)
        system_pitch=track.get('mainRef',{}).get('systemPitchDelta')
        if isinstance(system_pitch,dict):system_pitch=system_pitch.get('points',system_pitch)
        if group.get('pitchControls') or system_pitch:
            row['reason']='native_pitch_controls_preserved';continue
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
            if allowed is not None and (a,b) not in allowed:
                row['skipped_notes'].append({'note_index':ni,'reason':'protected_note_preserved'});continue
            if structure is not None and (note.get('retakes') or note.get('pitchControls')):
                row['skipped_notes'].append({'note_index':ni,'reason':'native_pitch_controls_preserved'});continue
            notes.append((a,b,float(pitch),ni,note))
        notes.sort(key=lambda n:(n[0],n[1],n[3]))
        gestures=row['gestures'];skipped=row['skipped_notes']
        def add(kind,a,b,amount,ni,peak=CONNECTION_PEAK,frequency=None):
            phase={'phrase_entry':'onset','interval_connection':'connection',
                   'phrase_release':'release','delayed_vibrato':'sustain'}[kind]
            amount*=detail[phase]
            if b-a<.025 or abs(amount)<1e-9:return
            if time_range and min(b,time_range[1])-max(a,time_range[0]) < .025:return
            gestures.append({'kind':kind,'start':a,'end':b,'cents_at_full_strength':amount,
                             'phase_multiplier':detail[phase],
                             'note_index':ni,'peak_fraction':peak,'frequency_hz':frequency})
        for j,(a,b,pitch,ni,note) in enumerate(notes):
            cancel();duration=b-a;previous=notes[j-1] if j else None
            following=notes[j+1] if j+1<len(notes) else None
            if (previous and previous[1]>a+1e-8) or (following and following[0]<b-1e-8):
                skipped.append({'note_index':ni,'reason':'overlapping_notes'});continue
            if duration < EXTERNAL_MIN_NOTE_SECONDS:
                skipped.append({'note_index':ni,'reason':'short_note_preserved'});continue
            if authored and time_range and (a < time_range[0] - 1e-7 or b > time_range[1] + 1e-7):
                continue
            if external is not None:
                if note.get('retakes') or note.get('pitchControls'):
                    skipped.append({'note_index':ni,'reason':'native_pitch_controls_preserved'});continue
                for action in external:
                    kind = action['kind']
                    width = min(EXTERNAL_EDGE_SECONDS, duration * action['fraction'])
                    if kind == 'onset':
                        add('phrase_entry', a, a+width, action['cents'], ni, peak=EXTERNAL_PEAKS[kind])
                    else:
                        add('phrase_release', b-width, b, action['cents'], ni, peak=EXTERNAL_PEAKS[kind])
                continue
            if structure is not None or preset in ('chinese_opera','bold'):
                position=physical_index[ni]
                if previous and (position==0 or physical[position-1]!=previous[3]):previous=None
                if following and (position+1>=len(physical) or physical[position+1]!=following[3]):following=None
            entry=previous is None or a-previous[1]>PHRASE_GAP
            ending=following is None or following[0]-b>PHRASE_GAP
            if entry and 'onset' in pattern:
                action = pattern['onset']
                add('phrase_entry', a, a + min(action['seconds'], duration*.36), action['cents'], ni,
                    peak=action['peak'])
            if not entry and a-previous[1] <= CONNECTION_GAP + 1e-9:
                interval = pitch - previous[2]
                position = pattern.get(connection_position(interval))
                shared = pattern.get('connection')
                predecessor_inside = not time_range or structure is None or previous[0] >= time_range[0] - 1e-7
                if position:
                    if predecessor_inside and previous[1] - previous[0] >= EXTERNAL_MIN_NOTE_SECONDS:
                        end = a + min(position['seconds'], duration * CONNECTION_FRACTION)
                        add('interval_connection', a, end, position['cents'], ni)
                elif shared:
                    amount = _connection_amount(shared, interval)
                    end = a + min(shared['seconds'], duration * CONNECTION_FRACTION)
                    if (shared['anticipate_descent'] and interval <= -4. and predecessor_inside
                            and abs(a-previous[1]) <= 1e-8):
                        start=previous[1]-min(.11,(previous[1]-previous[0])*.25)
                        # One continuous residual across a real adjoining boundary.
                        # Never bridge a rest or a protected intervening note.
                        add('interval_connection',start,end,amount,ni,peak=(a-start)/(end-start))
                    else:
                        add('interval_connection',a,end,amount,ni)
            if ending and 'release' in pattern:
                action = pattern['release']
                add('phrase_release', b - min(action['seconds'], duration*.35), b, action['cents'], ni,
                    peak=action['peak'])
            # Opera may add a delayed pitch ornament on a long, locally flat
            # note when no explicit native vibrato is present. Existing curves,
            # explicit depth and already-modulated source pitch remain intact;
            # an omitted native depth is still not proof of acoustic absence.
            vibrato = pattern.get('vibrato')
            if vibrato and duration >= vibrato['minimum_note']:
                depth=note.get('attributes',{}).get('dF0Vbr')
                opera=preset=='chinese_opera'
                if (depth is not None and (type(depth) not in (int,float) or depth!=0)) or (not opera and depth is None):
                    skipped.append({'note_index':ni,'reason':'existing_or_unknown_vibrato_preserved'});continue
                if opera or structure is not None:
                    env=params.get('vibratoEnv')
                    if isinstance(env,dict) and env.get('points'):
                        skipped.append({'note_index':ni,'reason':'authored_vibrato_envelope_preserved'});continue
                left = a + vibrato['delay']
                right = b - min(.18, duration*.2)
                minimum = max(.25, 2 / vibrato['rate']) if structure is not None else .25
                if right - left < minimum:
                    if structure is not None:
                        skipped.append({'note_index':ni,'reason':'short_vibrato_window'})
                    continue
                probe=np.arange(left,right+.00001,.01)
                positions=np.array([clock.to_position(float(t))-offset for t in probe])
                values=np.interp(positions,xs,ys) if xs else np.zeros(len(probe))
                if float(np.ptp(values))>10.:
                    skipped.append({'note_index':ni,'reason':'authored_pitch_variation_preserved'});continue
                add('delayed_vibrato', left, right, vibrato['depth'], ni, peak=.5, frequency=vibrato['rate'])
        if not gestures:row['reason']='no_eligible_gestures';continue
        extra=[]
        for gesture in gestures:
            a,b=gesture['start'],gesture['end'];count=math.ceil((b-a)/GRID_SECONDS)
            if time_range:a,b=max(a,time_range[0]),min(b,time_range[1])
            count=math.ceil((b-a)/GRID_SECONDS)
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
            local*=_range_gate(t,time_range)
            active_local=abs(local)>1e-10;source=base[left:right]
            protect_transition = authored or (preset in ('chinese_opera','bold')
                                                           and gesture['kind']=='interval_connection')
            if (protect_transition and active_local.any() and
                    (np.ptp(source[active_local])>50. or np.max(np.abs(source[active_local]))>200.)):
                omitted.append({**gesture,'reason':'authored_transition_preserved'});continue
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
