"""Conservative, bounded-memory recovery of coherent lead copies in backing."""
from pathlib import Path
import math
import time

from multi_file_commit import (
    OUTPUT_COMMIT_ROLLBACK_FAILED,
    STALE_OUTPUT_BACKUP,
    FileTransactionError,
    commit_file_pair,
)

ALGORITHM_VERSION = 3
MAX_REPORTED_SAMPLE_RANGES = 8
SUM_RECONSTRUCTION_TOLERANCE = 1e-4


_ERROR_META = {
    'invalid_audio_shape': (False, ['vwa_jobs_get', 'vwa_preparation_plan']),
    'invalid_input_paths': (False, ['vwa_jobs_get', 'vwa_preparation_plan']),
    'nonfinite_audio_input': (True, ['vwa_jobs_get', 'vwa_preparation_start']),
    'invalid_sample_rate': (False, ['vwa_jobs_get', 'vwa_preparation_plan']),
    'invalid_spectrum': (False, ['vwa_jobs_get']),
    'invalid_spectral_power': (False, ['vwa_jobs_get']),
    'negative_spectral_power': (False, ['vwa_jobs_get']),
    'invalid_cross_spectrum': (False, ['vwa_jobs_get']),
    'invalid_coherence_mask': (False, ['vwa_jobs_get']),
    'nonfinite_partition_output': (False, ['vwa_jobs_get']),
    'nonfinite_input_samples': (True, ['vwa_jobs_get', 'vwa_preparation_start']),
    'non_complementary_inputs': (True, ['vwa_jobs_get', 'vwa_preparation_start']),
    'stale_partition_backup': (False, ['vwa_jobs_get']),
    'partition_commit_rollback_failed': (False, ['vwa_jobs_get']),
}


class VocalPartitionError(ValueError):
    """Fail-closed error shaped for agent_errors.error_payload."""

    component = 'vocal_partition'

    def __init__(self, message, *, code, details, retryable=None, remediation=None):
        super().__init__(message)
        if not isinstance(details, dict):
            raise TypeError('VocalPartitionError details must be a dict')
        default_retryable, default_remediation = _ERROR_META.get(code, (False, ['vwa_jobs_get']))
        self.code = code
        self.details = details
        self.retryable = bool(default_retryable if retryable is None else retryable)
        self.remediation = list(default_remediation if remediation is None else remediation)
        if not all(isinstance(name, str) and name.startswith('vwa_') for name in self.remediation):
            raise ValueError('VocalPartitionError remediation must contain VWA tool names')

    def as_dict(self):
        return {'code': self.code, 'message': str(self), 'component': self.component,
                'retryable': self.retryable, 'remediation': self.remediation,
                'details': self.details}


def _sample_ranges(flags, offset=0, limit=MAX_REPORTED_SAMPLE_RANGES):
    import numpy as np
    flags = np.asarray(flags, dtype=bool)
    if not flags.any():
        return [], 0
    edges = np.diff(np.r_[False, flags, False].astype(np.int8))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    ranges = [[int(offset + start), int(offset + end)] for start, end in zip(starts, ends)]
    return ranges[:limit], len(ranges)


def _nonfinite_audio_detail(role, path, audio, offset=0):
    import numpy as np
    channel_details = []
    all_ranges = []
    count = 0
    for channel in range(audio.shape[1]):
        bad = ~np.isfinite(audio[:, channel])
        channel_count = int(bad.sum())
        if not channel_count:
            continue
        ranges, range_count = _sample_ranges(bad, offset)
        bad_indices = np.flatnonzero(bad)
        first_sample, last_sample = int(offset + bad_indices[0]), int(offset + bad_indices[-1] + 1)
        all_ranges.append([first_sample, last_sample])
        count += channel_count
        channel_details.append({
            'channel': channel,
            'dtype': str(audio.dtype),
            'nonfinite_count': channel_count,
            'first_sample': first_sample,
            'last_sample_exclusive': last_sample,
            'sample_ranges': ranges,
            'range_count': range_count,
        })
    if not channel_details:
        return None
    sample_start = min(row[0] for row in all_ranges)
    sample_end = max(row[1] for row in all_ranges)
    return {
        'role': role,
        'file': None if path is None else str(path),
        'channels': [row['channel'] for row in channel_details],
        'sample_range': [sample_start, sample_end],
        'nonfinite_count': count,
        'channel_details': channel_details,
    }


def _checked_nonnegative_power(power, label):
    """Reject bad power values and clamp only demonstrable roundoff negatives."""
    import numpy as np
    power = np.asarray(power, dtype=np.float64)
    if not np.isfinite(power).all():
        bad = ~np.isfinite(power)
        index = tuple(int(value) for value in np.argwhere(bad)[0])
        raise VocalPartitionError(
            f'{label}功率包含非有限值，已停止声部修复。',
            code='invalid_spectral_power',
            details={'field': label, 'nonfinite_count': int(bad.sum()),
                     'first_index': list(index),
                     'repair_hint': '重新运行分离；若仍复现，请保留音频与本诊断并报告数值问题。'})
    scale = max(1.0, float(np.max(np.abs(power))))
    tolerance = 128.0 * np.finfo(np.float64).eps * scale
    minimum = float(np.min(power))
    materially_negative = power < -tolerance
    if materially_negative.any():
        index = tuple(int(value) for value in np.argwhere(materially_negative)[0])
        raise VocalPartitionError(
            f'{label}功率出现无法解释的负值，已停止声部修复。',
            code='negative_spectral_power',
            details={'field': label, 'minimum': minimum, 'tolerance': tolerance,
                     'negative_count': int(materially_negative.sum()),
                     'first_index': list(index),
                     'repair_hint': '重新运行分离并保留 stem；不要清零或掩盖该数值异常。'})
    clipped = int(np.count_nonzero(power < 0.0))
    return np.maximum(power, 0.0), {
        'negative_power_values_clamped': clipped,
        'power_roundoff_tolerance': tolerance,
        'minimum_power_before_guard': minimum,
    }


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


def coherent_transfer(lead, backing, rate, *, input_paths=None, diagnostics=None):
    """Return only positively coherent spectral copies of an audible lead.

    This cannot distinguish intentionally phase-locked unison doubling from
    leakage. It never promotes a backing-only region or swaps entire stems.
    Powers and coherence are evaluated in float64. A temporary negative moving
    power is accepted only at floating-point roundoff level because power is
    mathematically nonnegative; all other non-finite or negative values block.
    """
    import numpy as np
    from scipy.signal import stft, istft
    from scipy.ndimage import uniform_filter1d, gaussian_filter
    if (not hasattr(lead, 'shape') or not hasattr(backing, 'shape')
            or lead.shape != backing.shape or lead.ndim != 2 or lead.shape[1] != 2
            or not len(lead)):
        raise VocalPartitionError(
            '声部检查需要等长的双声道音频。',
            code='invalid_audio_shape',
            details={'lead_shape': list(getattr(lead, 'shape', ())),
                     'backing_shape': list(getattr(backing, 'shape', ())),
                     'repair_hint': '确认主唱与和声均为未截断的等长双声道 audio。'})
    if input_paths is None:
        paths = (None, None)
    else:
        paths = tuple(None if value is None else str(value) for value in input_paths)
        if len(paths) != 2:
            raise VocalPartitionError(
                '声部检查的输入路径数量无效。',
                code='invalid_input_paths',
                details={'input_paths': list(paths),
                         'repair_hint': '传入主唱、和声两个文件路径，或省略该可选参数。'})
    invalid = []
    for role, path, audio in zip(('lead', 'backing'), paths, (lead, backing)):
        detail = _nonfinite_audio_detail(role, path, audio)
        if detail is not None:
            invalid.append(detail)
    if invalid:
        raise VocalPartitionError(
            '声部检查输入包含无效采样，已保留原文件。',
            code='nonfinite_audio_input',
            details={'files': invalid,
                     'repair_hint': '重新运行分离；若必须保留现有文件，请检查所列通道与样本区间并重新分离，不能直接清零。'})
    if rate != 44100:
        raise VocalPartitionError(
            '声部检查需要有效的 44.1 kHz 音频。',
            code='invalid_sample_rate',
            details={'rate': rate, 'expected_rate': 44100,
                     'repair_hint': '将总人声、主唱和和声统一重采样到 44.1 kHz 后重试。'})
    length = len(lead)
    if length < 2048:
        lead = np.pad(lead, ((0,2048-length),(0,0)))
        backing = np.pad(backing, ((0,2048-length),(0,0)))
    # scipy preserves float32 through the STFT. Promoting before the transform
    # avoids overflow and subtractive moving-average artifacts in power terms.
    lead = np.asarray(lead, dtype=np.float64)
    backing = np.asarray(backing, dtype=np.float64)
    _,_,a = stft(lead.T, fs=rate, nperseg=2048, noverlap=1536)
    _,_,b = stft(backing.T, fs=rate, nperseg=2048, noverlap=1536)
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        raise VocalPartitionError(
            '声部检查频谱包含非有限值，已停止自动修复。',
            code='invalid_spectrum',
            details={'lead_nonfinite': int((~np.isfinite(a)).sum()),
                     'backing_nonfinite': int((~np.isfinite(b)).sum()),
                     'repair_hint': '检查音频是否处于浮点可表示范围，并重新运行分离。'})
    def mean(x):return uniform_filter1d(x,size=15,axis=-1,mode='nearest')
    p_raw = np.sum(a.real*a.real + a.imag*a.imag, axis=0, dtype=np.float64)
    q_raw = np.sum(b.real*b.real + b.imag*b.imag, axis=0, dtype=np.float64)
    p, p_guard = _checked_nonnegative_power(mean(p_raw), 'lead')
    q, q_guard = _checked_nonnegative_power(mean(q_raw), 'backing')
    product = np.sum(a*np.conj(b),axis=0,dtype=np.complex128)
    cross = mean(product.real)+1j*mean(product.imag)
    if not np.isfinite(cross).all():
        raise VocalPartitionError(
            '声部交叉谱包含非有限值，已停止自动修复。',
            code='invalid_cross_spectrum',
            details={'nonfinite_count': int((~np.isfinite(cross)).sum()),
                     'repair_hint': '重新运行分离；保留输入与诊断供数值问题定位。'})
    root_p = np.sqrt(p)
    root_q = np.sqrt(q)
    denominator = root_p*root_q
    zero = np.zeros_like(p)
    with np.errstate(over='ignore', under='ignore', invalid='ignore', divide='ignore'):
        coherence = np.divide(cross.real*cross.real + cross.imag*cross.imag,
                              denominator*denominator, out=zero.copy(),
                              where=denominator > 0.0)
        phase = np.divide(cross.real, denominator, out=zero.copy(),
                          where=denominator > 0.0)
        ratio = np.divide(root_q, root_p, out=zero.copy(),
                          where=root_p > 0.0)
    mask = np.clip((coherence-.94)/.05,0,1)*np.clip((phase-.95)/.04,0,1)
    mask *= np.clip((ratio-.08)/.1,0,1)*np.clip((2.5-ratio)/.5,0,1)
    # Keep the established activity gate; the failed jobs passed this gate and
    # were not rejected by it, so there is no evidence for loosening it.
    mask *= p > np.maximum(np.max(p,axis=0,keepdims=True)*1e-5,1e-12)
    mask = gaussian_filter(mask,sigma=(.6,1.))
    if not np.isfinite(mask).all():
        raise VocalPartitionError(
            '声部相干掩码包含非有限值，已停止自动修复。',
            code='invalid_coherence_mask',
            details={'nonfinite_count': int((~np.isfinite(mask)).sum()),
                     'repair_hint': '重新运行分离；保留输入与诊断供数值问题定位。'})
    _,moved = istft(b*mask[None,:,:],fs=rate,nperseg=2048,noverlap=1536)
    moved = moved.T[:length]
    if not np.isfinite(moved).all():
        bad = ~np.isfinite(moved)
        channels = [channel for channel in range(moved.shape[1])
                    if bad[:, channel].any()]
        bad_samples = bad.any(axis=1)
        ranges, range_count = _sample_ranges(bad_samples)
        bad_indices = np.flatnonzero(bad_samples)
        raise VocalPartitionError(
            '声部检查输出包含无效采样，已停止自动修复。',
            code='nonfinite_partition_output',
            details={'file': paths[1], 'channels': channels,
                     'sample_range': [int(bad_indices[0]), int(bad_indices[-1] + 1)],
                     'sample_ranges': ranges, 'range_count': range_count,
                     'nonfinite_count': int(bad.sum()),
                     'repair_hint': '不要写出或清零这些样本；保留输入和诊断并修复数值路径后重试。'})
    if diagnostics is not None:
        diagnostics.update({
            'algorithm_version': ALGORITHM_VERSION,
            'spectral_dtype': str(a.dtype),
            'power_dtype': 'float64',
            'negative_power_values_clamped': p_guard['negative_power_values_clamped']+q_guard['negative_power_values_clamped'],
            'power_roundoff_tolerance': max(p_guard['power_roundoff_tolerance'],q_guard['power_roundoff_tolerance']),
            'minimum_power_before_guard': min(p_guard['minimum_power_before_guard'],q_guard['minimum_power_before_guard']),
        })
    return moved.astype('float32',copy=False)

def _validate_committed_partition(paths, check):
    """Re-read published stems and apply the same hard quality gates."""
    import numpy as np
    import soundfile as sf
    from contextlib import ExitStack

    paths = [Path(path) for path in paths]
    check()
    metadata = [sf.info(str(path)) for path in paths]
    shape = lambda info: (info.frames, info.samplerate, info.channels)
    if (not metadata[0].frames or metadata[0].samplerate != 44100 or metadata[0].channels != 2
            or any(shape(info) != shape(metadata[0]) for info in metadata)):
        raise ValueError('声部提交验证失败：总人声、主唱和和声的格式或长度不一致。')
    rate, frames = 44100, metadata[0].frames
    block = 10 * rate
    with ExitStack() as stack:
        sources = [stack.enter_context(sf.SoundFile(str(path))) for path in paths]
        for start in range(0, frames, block):
            check()
            arrays = []
            for source in sources:
                source.seek(start)
                arrays.append(source.read(min(block, frames - start), dtype='float32', always_2d=True))
            vocals, lead, backing = arrays
            input_detail = _nonfinite_audio_detail('vocals', paths[0], vocals, start)
            if input_detail is not None:
                raise VocalPartitionError(
                    '提交后的分离结果包含无效采样，已恢复原文件。',
                    code='nonfinite_input_samples',
                    details={'block_range': [start, start + len(vocals)], 'files': [input_detail],
                             'repair_hint': '重新运行分离；若必须保留现有文件，请检查所列通道与样本区间并重新分离，不能直接清零。'})
            output_bad = []
            for role, path, audio in (('primary', paths[1], lead), ('secondary', paths[2], backing)):
                detail = _nonfinite_audio_detail(role, path, audio, start)
                if detail is not None:
                    output_bad.append(detail)
            if output_bad:
                raise VocalPartitionError(
                    '提交后的声部输出包含无效采样，已恢复原文件。',
                    code='nonfinite_partition_output',
                    details={'block_range': [start, start + len(vocals)], 'files': output_bad,
                             'repair_hint': '不要写出或清零这些样本；保留输入和诊断并修复数值路径后重试。'})
            delta = np.abs(lead.astype(np.float64) + backing.astype(np.float64) - vocals.astype(np.float64))
            error = float(np.max(delta))
            if error > SUM_RECONSTRUCTION_TOLERANCE:
                channel, sample = (int(value) for value in np.unravel_index(np.argmax(delta), delta.shape))
                raise VocalPartitionError(
                    '提交后的主唱与和声无法还原总人声，已恢复原文件。',
                    code='non_complementary_inputs',
                    details={'files': [str(path) for path in paths],
                             'block_range': [start, start + len(vocals)],
                             'sample_range': [start + sample, start + sample + 1],
                             'channel': channel, 'max_abs_error': error,
                             'samples_over_threshold': int((delta > SUM_RECONSTRUCTION_TOLERANCE).sum()),
                             'threshold': SUM_RECONSTRUCTION_TOLERANCE,
                             'repair_hint': '重新运行分离生成同一时间轴上的互补主唱/和声；不要通过放宽阈值继续。'})
    check()


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
    preserve_pending = False
    metrics, moved_energy, backing_energy, input_error, sum_error = [],0.,0.,0.,0.
    numeric_guard = {'negative_power_values_clamped':0,'minimum_power_before_guard':None,'maximum_power_roundoff_tolerance':0.0}
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
                bad_inputs = [detail for role,path,audio in zip(names,paths,arrays)
                              if (detail := _nonfinite_audio_detail(role,path,audio,left)) is not None]
                if bad_inputs:
                    raise VocalPartitionError(
                        '分离结果包含无效采样，已保留原文件。',
                        code='nonfinite_input_samples',
                        details={'block_range':[left,right], 'files':bad_inputs,
                                 'repair_hint':'重新运行分离；若必须保留现有文件，请检查所列通道与样本区间并重新分离，不能直接清零。'})
                delta = np.abs(lead.astype(np.float64)+backing.astype(np.float64)-vocals.astype(np.float64))
                error = float(np.max(delta))
                input_error = max(input_error,error)
                if error > SUM_RECONSTRUCTION_TOLERANCE:
                    channel,sample = (int(value) for value in np.unravel_index(np.argmax(delta),delta.shape))
                    raise VocalPartitionError(
                        '主唱与和声无法还原总人声，已停止自动串音修复。',
                        code='non_complementary_inputs',
                        details={'files':[str(path) for path in paths],
                                 'block_range':[left,right],
                                 'sample_range':[left+sample,left+sample+1],
                                 'channel':channel, 'max_abs_error':error,
                                 'samples_over_threshold':int((delta>SUM_RECONSTRUCTION_TOLERANCE).sum()),
                                 'threshold':SUM_RECONSTRUCTION_TOLERANCE,
                                 'repair_hint':'重新运行分离生成同一时间轴上的互补主唱/和声；不要通过放宽阈值继续。'})
                block_diagnostics = {}
                moved = coherent_transfer(lead,backing,rate,input_paths=paths[1:],diagnostics=block_diagnostics)
                numeric_guard['negative_power_values_clamped'] += block_diagnostics.get('negative_power_values_clamped',0)
                minimum = block_diagnostics.get('minimum_power_before_guard')
                if minimum is not None:
                    previous = numeric_guard['minimum_power_before_guard']
                    numeric_guard['minimum_power_before_guard'] = minimum if previous is None else min(previous,minimum)
                numeric_guard['maximum_power_roundoff_tolerance'] = max(
                    numeric_guard['maximum_power_roundoff_tolerance'],
                    block_diagnostics.get('power_roundoff_tolerance',0.0))
                a,b = start-left,min(frames,start+block)-left
                lead,backing,moved = lead[a:b],backing[a:b],moved[a:b]
                primary,secondary = lead+moved,backing-moved
                output_bad = []
                for role,path,audio in (('primary',paths[1],primary),('secondary',paths[2],secondary)):
                    detail = _nonfinite_audio_detail(role,path,audio,start)
                    if detail is not None:
                        output_bad.append(detail)
                if output_bad:
                    raise VocalPartitionError(
                        '声部检查输出包含无效采样，已停止自动修复。',
                        code='nonfinite_partition_output',
                        details={'block_range':[start,start+len(vocals)], 'files':output_bad,
                                 'repair_hint':'不要写出或清零这些样本；保留输入和诊断并修复数值路径后重试。'})
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
        backups = [directory / (name + '.partition.previous.wav') for name in ('lead', 'backing')]
        try:
            commit_file_pair(
                pending,
                paths[1:],
                backup_paths=backups,
                validate=lambda _targets: _validate_committed_partition(paths, check),
            )
        except FileTransactionError as error:
            preserve_pending = error.preserve_staging
            if error.code == STALE_OUTPUT_BACKUP:
                stale = error.details.get('files', [str(path) for path in backups if path.exists()])
                raise VocalPartitionError(
                    '发现未清理的声部提交备份，已停止覆盖。',
                    code='stale_partition_backup',
                    details={'files': stale,
                             'repair_hint': '先核对备份与当前主唱/和声，再移走备份后重试；不要直接删除唯一副本。'},
                ) from error
            if error.code == OUTPUT_COMMIT_ROLLBACK_FAILED:
                details = dict(error.details)
                details['repair_hint'] = ('保留目录内 .partition.previous.wav 与 pending 文件，核对后手动恢复；'
                                          '不要覆盖现有文件。')
                raise VocalPartitionError(
                    '声部提交失败且自动回滚未完整完成。',
                    code='partition_commit_rollback_failed',
                    details=details,
                ) from error
            raise
    finally:
        if not preserve_pending:
            for path in pending:
                path.unlink(missing_ok=True)
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
        'input_sum_tolerance':SUM_RECONSTRUCTION_TOLERANCE,
        'transferred_energy_fraction':round(fraction,6),'intervals':intervals,
        'coherence_floor':.94,'phase_floor':.95,'normalization':False,'timeline_offset_seconds':0,
        'numeric_guard':numeric_guard}
