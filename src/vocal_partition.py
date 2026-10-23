"""Conservative, bounded-memory recovery of coherent lead copies in backing."""
from pathlib import Path
import math
import time

ALGORITHM_VERSION = 2


def suppress_shared_notes(notes, backing_pitch, context, backing_audio):
    """Exclude residual copies only with shared fine-f0 and separation evidence."""
    import numpy as np
    import soundfile as sf
    lead_pitch=context['pitch'];intervals=context['intervals']
    kept,audit=[],[]
    with sf.SoundFile(str(context['audio'])) as lead, sf.SoundFile(str(backing_audio)) as backing:
        if (lead.frames,lead.samplerate,lead.channels)!=(backing.frames,backing.samplerate,backing.channels):
            raise ValueError('跨声部检查的音频时间轴不一致。')
        for note in notes:
            start,end=note.onset,note.offset
            covered=sum(max(0.,min(end,r['end'])-max(start,r['start'])) for r in intervals)
            if end-start<.08 or covered<.5*(end-start):kept.append(note);continue
            first=max(0,math.ceil(start/backing_pitch.time_step_seconds))
            last=min(len(backing_pitch.midi_pitch),math.floor(end/backing_pitch.time_step_seconds))
            index=np.arange(first,last)
            peer=np.rint(index*backing_pitch.time_step_seconds/lead_pitch.time_step_seconds).astype(int)
            usable=(peer>=0)&(peer<len(lead_pitch.midi_pitch))
            index,peer=index[usable],peer[usable]
            p=backing_pitch.midi_pitch[index];q=lead_pitch.midi_pitch[peer]
            good=np.isfinite(p)&np.isfinite(q)&backing_pitch.voiced_mask[index]&lead_pitch.voiced_mask[peer]
            if len(index)<8 or good.sum()<8 or good.mean()<.75:kept.append(note);continue
            delta=abs(p[good]-q[good])
            fraction=float(np.mean(delta<=.2));median=float(np.median(delta))
            if fraction<.9 or median>.08:kept.append(note);continue
            # Whole-note agreement can hide an independent final harmony.
            # Keep the note when either edge has a sustained voiced contour
            # absent from the lead, even if its shared body passes the test.
            own=(np.isfinite(p)&backing_pitch.voiced_mask[index]
                 & (~np.isfinite(q)|~lead_pitch.voiced_mask[peer]|(abs(p-q)>.35)))
            edge=max(1,math.ceil(.12/backing_pitch.time_step_seconds))
            runs=np.diff(np.r_[False,own,False].astype(int))
            independent_edge=any((b-a)*backing_pitch.time_step_seconds>=.06-1e-9
                and (a<edge or b>len(own)-edge) for a,b in zip(np.where(runs==1)[0],np.where(runs==-1)[0]))
            if independent_edge:kept.append(note);continue
            audio=[]
            for source in (lead,backing):
                source.seek(round(start*source.samplerate))
                audio.append(source.read(round((end-start)*source.samplerate),dtype='float32',always_2d=True).ravel())
            a,b=audio
            power_a,power_b=float(np.dot(a,a)),float(np.dot(b,b))
            correlation=float(np.dot(a,b)/math.sqrt(max(power_a*power_b,1e-30)))
            if power_a<1e-10 or power_b>.49*power_a or correlation<.4:
                kept.append(note);continue
            audit.append({**vars(note),'reason':'residual_shared_lead',
                'matching_pitch_fraction':round(fraction,4),'median_pitch_difference_cents':round(median*100,3),
                'waveform_correlation':round(correlation,4),'backing_to_lead_db':round(10*math.log10(max(power_b,1e-30)/power_a),2),
                'partition_coverage':round(min(1.,covered/(end-start)),4)})
    return kept,audit


def coherent_transfer(lead, backing, rate):
    """Return only positively coherent spectral copies of an audible lead.

    This cannot distinguish intentionally phase-locked unison doubling from
    leakage. It never promotes a backing-only region or swaps entire stems.
    """
    import numpy as np
    from scipy.signal import stft, istft
    from scipy.ndimage import uniform_filter1d, gaussian_filter
    if lead.shape != backing.shape or lead.ndim != 2 or lead.shape[1] != 2 or not len(lead):
        raise ValueError('声部检查需要等长的双声道音频。')
    if rate != 44100 or not np.isfinite(lead).all() or not np.isfinite(backing).all():
        raise ValueError('声部检查需要有效的 44.1 kHz 音频。')
    length = len(lead)
    if length < 2048:
        lead = np.pad(lead, ((0,2048-length),(0,0)))
        backing = np.pad(backing, ((0,2048-length),(0,0)))
    _,_,a = stft(lead.T, fs=rate, nperseg=2048, noverlap=1536)
    _,_,b = stft(backing.T, fs=rate, nperseg=2048, noverlap=1536)
    def mean(x):return uniform_filter1d(x,size=15,axis=-1,mode='nearest')
    p = mean(np.sum(abs(a)**2,axis=0))
    q = mean(np.sum(abs(b)**2,axis=0))
    product = np.sum(a*np.conj(b),axis=0)
    cross = mean(product.real)+1j*mean(product.imag)
    coherence = abs(cross)**2/(p*q+1e-22)
    phase = cross.real/(np.sqrt(p*q)+1e-12)
    ratio = np.sqrt(q/(p+1e-22))
    mask = np.clip((coherence-.94)/.05,0,1)*np.clip((phase-.95)/.04,0,1)
    mask *= np.clip((ratio-.08)/.1,0,1)*np.clip((2.5-ratio)/.5,0,1)
    mask *= p > np.maximum(np.max(p,axis=0,keepdims=True)*1e-5,1e-12)
    mask = gaussian_filter(mask,sigma=(.6,1.))
    _,moved = istft(b*mask[None,:,:],fs=rate,nperseg=2048,noverlap=1536)
    return moved.T[:length].astype('float32',copy=False)


def repair(directory, job=None, *, cancel_checker=None, progress=None):
    """Check model outputs and commit a complementary pair after all checks.

    Imported stems bypass this operation at the pipeline boundary. Original
    tasks are not migrated in place merely by opening/re-exporting them.
    """
    import numpy as np
    import soundfile as sf
    from contextlib import ExitStack
    directory = Path(directory)
    def check():
        if cancel_checker is not None and cancel_checker():
            raise InterruptedError('声部检查已取消。')
        if job is not None:
            from common import cancelled
            cancelled(job)
    check()
    started = time.monotonic()
    names = ('vocals','lead','backing')
    paths = [directory/(name+'.wav') for name in names]
    metadata = [sf.info(str(p)) for p in paths]
    shape = lambda info:(info.frames,info.samplerate,info.channels)
    if (not metadata[0].frames or metadata[0].samplerate!=44100 or metadata[0].channels!=2
            or any(shape(info)!=shape(metadata[0]) for info in metadata)):
        raise ValueError('声部检查失败：总人声、主唱和和声的格式或长度不一致。')
    rate, frames = 44100, metadata[0].frames
    block, context = 10*rate, rate//2
    pending = [directory/(name+'.partition.pending.wav') for name in ('lead','backing')]
    metrics, moved_energy, backing_energy, input_error, sum_error = [],0.,0.,0.,0.
    try:
        with ExitStack() as stack:
            sources = [stack.enter_context(sf.SoundFile(str(p))) for p in paths]
            outputs = [stack.enter_context(sf.SoundFile(str(p),'w',samplerate=rate,channels=2,subtype='FLOAT')) for p in pending]
            for start in range(0,frames,block):
                check()
                # Keep the same STFT grid in every block; changing frame phase
                # at a chunk boundary can otherwise modulate a sustained note.
                left=max(0,(start-context)//512*512)
                right=min(frames,math.ceil((start+block+context)/512)*512)
                arrays=[]
                for source in sources:
                    source.seek(left)
                    arrays.append(source.read(right-left,dtype='float32',always_2d=True))
                vocals,lead,backing = arrays
                if any(not np.isfinite(x).all() for x in arrays):
                    raise ValueError('分离结果包含无效采样，请重新分离音频。')
                error = float(np.max(abs(lead+backing-vocals)))
                input_error = max(input_error,error)
                if error > 1e-4:
                    raise ValueError('主唱与和声无法还原总人声，已停止自动串音修复。')
                moved = coherent_transfer(lead,backing,rate)
                a,b = start-left,min(frames,start+block)-left
                lead,backing,moved = lead[a:b],backing[a:b],moved[a:b]
                primary,secondary = lead+moved,backing-moved
                if not np.isfinite(primary).all() or not np.isfinite(secondary).all():
                    raise ValueError('声部检查输出包含无效采样。')
                outputs[0].write(primary); outputs[1].write(secondary)
                moved_energy += float(np.sum(moved.astype('float64')**2))
                backing_energy += float(np.sum(backing.astype('float64')**2))
                sum_error = max(sum_error,float(np.max(abs(primary+secondary-vocals[a:b]))))
                # Quarter-second summaries are local observations, not a
                # percentage claiming perceptual separation accuracy.
                for pos in range(0,b-a,rate//4):
                    old=backing[pos:pos+rate//4]; new=secondary[pos:pos+rate//4]
                    before=float(np.mean(old**2)); after=float(np.mean(new**2))
                    reduction=10*math.log10((before+1e-20)/(after+1e-20))
                    if before>1e-10 and reduction>=3.:
                        metrics.append({'start':(start+pos)/rate,
                            'end':min(frames,start+pos+len(old))/rate,
                            'backing_reduction_db':round(reduction,2)})
                if progress:progress(min(frames,start+block),frames)
        check()
        for source,target in zip(pending,paths[1:]):source.replace(target)
    finally:
        for path in pending:path.unlink(missing_ok=True)
    intervals=[]
    for row in metrics:
        if intervals and row['start']-intervals[-1]['end']<.001:
            intervals[-1]['end']=row['end']
            intervals[-1]['max_backing_reduction_db']=max(intervals[-1]['max_backing_reduction_db'],row['backing_reduction_db'])
        else:
            intervals.append({'start':row['start'],'end':row['end'],
                'max_backing_reduction_db':row['backing_reduction_db']})
    fraction=moved_energy/max(backing_energy,1e-30)
    return {'algorithm_version':ALGORITHM_VERSION,'method':'coherent_shared_components',
        'status':'applied' if fraction>1e-6 else 'unchanged',
        'sample_rate':rate,'samples':frames,'processing_seconds':round(time.monotonic()-started,3),
        'input_sum_max_error':input_error,'sum_max_error':sum_error,
        'transferred_energy_fraction':round(fraction,6),'intervals':intervals,
        'coherence_floor':.94,'phase_floor':.95,'normalization':False,'timeline_offset_seconds':0}
