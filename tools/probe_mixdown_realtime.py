"""Reproducible four-track comparison. Unattended WASAPI output is silent.

This is a development probe, not the application's default preview player.
It measures request-to-native-submission, never claims acoustic latency, and
leaves final-limiter compatibility as an explicit production gate.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'src'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def wait_for(session, predicate, timeout=5):
    until = time.perf_counter() + timeout
    while time.perf_counter() < until:
        value = session.snapshot()
        if value['state'] == 'failed':
            raise RuntimeError(value['error'])
        if predicate(value):
            return value
        time.sleep(.001)
    raise TimeoutError(json.dumps(session.snapshot(), ensure_ascii=False)[-2000:])


def summary(values, np):
    return {'count': len(values), 'p50': float(np.percentile(values, 50)),
            'p95': float(np.percentile(values, 95)), 'max': max(values)} if values else None


def fixture(folder, np, sf, seconds=16, rate=48000):
    folder.mkdir()
    t = np.arange(rate * seconds, dtype=np.float32) / rate
    vocals = []
    for index, (frequency, amplitude) in enumerate(((220, .11), (330, .09), (440, .08))):
        gate = .65 + .35 * np.sin(2 * np.pi * (.37 + index * .07) * t)
        value = amplitude * gate * np.sin(2 * np.pi * frequency * t + .08 * np.sin(2 * np.pi * 4.5 * t))
        path = folder / ('vocal-%d.wav' % index)
        sf.write(path, np.column_stack((value, value * .91)), rate, subtype='FLOAT')
        vocals.append({'path': str(path), 'gain_db': -6.0 - index * 2,
                       'offset_seconds': (0, -.01, .035)[index], 'mute': False,
                       'solo': False, 'pan': (0, -.2, .25)[index], 'reverb_send': .3 + .1 * index})
    backing = folder / 'backing.flac'
    sf.write(backing, np.column_stack((.025 * np.sin(2 * np.pi * 110 * t),
                                      .025 * np.sin(2 * np.pi * 137 * t))), rate)
    return {'vocals': vocals, 'instrumental_path': str(backing),
            'output_path': str(folder / 'unused-render-output.wav'),
            'instrumental_db': -4.0, 'instrumental_mute': False,
            'instrumental_solo': False, 'instrumental_pan': 0.0,
            'eq_low_cut_hz': 80.0, 'eq_presence_db': 2.0,
            'compression_strength': .65, 'reverb_mix': .15,
            'reverb_profile': 'natural', 'reverb_predelay_ms': 12.0,
            'width': 1.15, 'preview_start_seconds': 0.0,
            'preview_duration_seconds': 12.0}


def variations(request):
    changes = []
    for index in range(9):
        value = copy.deepcopy(request)
        if index % 3 == 0:
            value['vocals'][0]['gain_db'] = -7.0 + index * .1
            label = 'track_gain'
        elif index % 3 == 1:
            value['width'] = 1.05 + index * .01
            label = 'width'
        else:
            value['eq_presence_db'] = 1.0 + index * .2
            label = 'eq_presence'
        changes.append((label, value))
    return changes


def profile(mode, block, request, root, args, np, sf, pb):
    from mixdown_native_output import NativeOutput
    from mixdown_realtime_renderer import PreLimiterMixSession
    from mixdown_realtime_session import RealtimeAuditionSession
    renderer = PreLimiterMixSession(request, cache_dir=root / 'source-cache',
                                   numpy=np, soundfile=sf, pedalboard=pb,
                                   ffmpeg=args.ffmpeg)
    output = None
    session = None
    consumer = None
    consumer_done = threading.Event()
    consumer_errors = []
    try:
        outputs = []
        def output_factory():
            value = NativeOutput(args.dll, sample_rate=renderer.sample_rate,
                                 channels=renderer.channels, capacity_frames=max(4096, block * 8),
                                 period_frames=256, mode=mode)
            outputs.append(value)
            return value
        session = RealtimeAuditionSession(renderer, output_factory, block_frames=block,
                                          silent=True, numpy=np)
        session.start()
        wait_for(session, lambda s: s['native'] is not None)
        output = outputs[0]
        if mode == 'null':
            def consume():
                try:
                    sizes = (127, 256, 511, 193)
                    index = 0
                    while not consumer_done.is_set():
                        count = sizes[index % len(sizes)]
                        output.pull(count)
                        index += 1
                        consumer_done.wait(count / renderer.sample_rate)
                except Exception as error:
                    consumer_errors.append(str(error))
            consumer = threading.Thread(target=consume, name='VWA null device probe', daemon=True)
            consumer.start()
        first = session.seek(int(renderer.sample_rate * .25), autoplay=True)
        warm = wait_for(session, lambda s: (s.get('last_seek') or {}).get('seek_id') == first
                        and s['last_seek']['phase'] == 'submitted')
        initial_underrun = int(warm['native'].get('underrun_frames', 0))
        updates = []
        for label, value in variations(request):
            started = time.perf_counter()
            revision = session.set_parameters(value)
            observed = wait_for(session, lambda s: s['parameter_revision'] == revision
                                and s['native']['media_frame'] > s['parameter_first_frame'])
            updates.append({'label': label, 'revision': revision,
                            'request_to_submit_ms': (time.perf_counter() - started) * 1000,
                            'parameter_processing_ms': observed['parameter_processing_ms']})
        steady = session.snapshot()
        steady_underrun = int(steady['native'].get('underrun_frames', 0)) - initial_underrun
        seeks = []
        for seconds in (1.3, 6.2, 2.1):
            serial = session.seek(int(seconds * renderer.sample_rate), autoplay=True)
            value = wait_for(session, lambda s: (s.get('last_seek') or {}).get('seek_id') == serial
                             and s['last_seek']['phase'] == 'submitted')
            seeks.append(value['last_seek'])
        session.seek(int(renderer.sample_rate * 3.1), autoplay=True)
        session.seek(int(renderer.sample_rate * 4.0), autoplay=True)
        latest = session.seek(int(renderer.sample_rate * 5.1), autoplay=True)
        latest_check = wait_for(session, lambda s: (s.get('last_seek') or {}).get('seek_id') == latest
                                and s['last_seek']['phase'] == 'submitted')
        session.pause()
        wait_for(session, lambda s: s['state'] == 'paused')
        time.sleep(.03)
        held = session.snapshot()['native']['media_frame']
        time.sleep(.04)
        pause_holds = session.snapshot()['native']['media_frame'] == held
        session.play()
        wait_for(session, lambda s: s['native']['media_frame'] > held)
        soak = None
        if args.soak_seconds > 0:
            began = time.perf_counter()
            until = began + args.soak_seconds
            before_underrun = int(session.snapshot()['native'].get('underrun_frames', 0))
            update_times = []
            rewind_count = 0
            next_update = began
            last_request = variations(request)[-1][1]
            while time.perf_counter() < until:
                current = session.snapshot()
                if current['state'] == 'failed':
                    raise RuntimeError(current['error'])
                if current['native']['media_frame'] >= renderer.duration_frames - renderer.sample_rate:
                    serial = session.seek(renderer.sample_rate, autoplay=True)
                    wait_for(session, lambda s: (s.get('last_seek') or {}).get('seek_id') == serial
                             and s['last_seek']['phase'] == 'submitted')
                    rewind_count += 1
                if time.perf_counter() >= next_update:
                    changed = copy.deepcopy(last_request)
                    changed['vocals'][0]['gain_db'] += .25 if len(update_times) % 2 else -.25
                    requested = time.perf_counter()
                    revision = session.set_parameters(changed)
                    wait_for(session, lambda s: s['parameter_revision'] == revision
                             and s['native']['media_frame'] > s['parameter_first_frame'])
                    update_times.append((time.perf_counter() - requested) * 1000)
                    next_update = time.perf_counter() + .2
                time.sleep(.01)
            soak = {'wall_seconds': time.perf_counter() - began,
                    'parameter_submit_ms': summary(update_times, np), 'rewinds': rewind_count,
                    'underrun_frames': int(session.snapshot()['native'].get('underrun_frames', 0)) - before_underrun}
        before_close = session.snapshot()
        stats = summary(before_close['render_ms'], np)
        control = summary([x['request_to_submit_ms'] for x in updates], np)
        seek_stats = summary([x['request_to_submit_ms'] for x in seeks], np)
        gates = {'queue_bounded': before_close['max_queue_frames'] <= session.target_queue_frames,
                 'paused_media_holds': pause_holds,
                 'latest_seek_wins': (latest_check['last_seek']['seek_id'] == latest
                                      and latest_check['native']['epoch'] == latest_check['last_seek']['epoch']),
                 'no_steady_underrun': steady_underrun == 0,
                 'parameter_submit_p95_under_50ms': control['p95'] < 50,
                 'seek_submit_p95_under_150ms': seek_stats['p95'] < 150,
                 'consumer_healthy': not consumer_errors}
        if soak:
            gates['no_soak_underrun'] = soak['underrun_frames'] == 0
        return {'mode': mode, 'block_frames': block, 'gates': gates,
                'all_probe_gates_pass': all(gates.values()), 'silent_output': True,
                'real_four_track_dsp': True, 'pre_limiter': True,
                'render_ms': stats, 'parameter_submit_ms': control,
                'seek_submit_ms': seek_stats, 'steady_underrun_frames': steady_underrun,
                'updates': updates, 'seeks': seeks, 'snapshot': before_close,
                'latest_seek_check': latest_check['last_seek'],
                'soak': soak, 'null_timing_is_python_paced': mode == 'null',
                'measurement': 'request to callback-submitted media; excludes acoustic/device latency'}
    except Exception as error:
        failure = {'mode': mode, 'block_frames': block, 'error': str(error),
                   'consumer_errors': consumer_errors,
                   'snapshot': session.snapshot() if session is not None else None}
        (root / ('failed-%s-%d.json' % (mode, block))).write_text(
            json.dumps(failure, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        raise
    finally:
        consumer_done.set()
        if consumer is not None:
            consumer.join(2)
            if consumer.is_alive():
                raise RuntimeError('null consumer did not close')
        if session is not None:
            session.close()
        else:
            if output is not None:
                output.close()
            renderer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dll', type=Path, required=True)
    parser.add_argument('--packages', type=Path, required=True)
    parser.add_argument('--ffmpeg', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--mode', choices=('null', 'wasapi', 'all'), default='null')
    parser.add_argument('--blocks', default='256,512,1024,2048')
    parser.add_argument('--legacy-baseline', action='store_true')
    parser.add_argument('--soak-seconds', type=float, default=0,
                        help='Optional silent stability run, with live parameters and bounded rewinds')
    args = parser.parse_args()
    if not 0 <= args.soak_seconds <= 120:
        parser.error('soak-seconds must be between 0 and 120')
    args.out = args.out.resolve()
    for path in (args.dll, args.ffmpeg):
        if not path.is_file():
            parser.error('missing required file: ' + str(path))
    if (args.out / 'REPORT.json').exists():
        parser.error('report already exists; choose a new output directory')
    args.out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.packages.resolve()))
    os.environ['PATH'] = str(args.ffmpeg.resolve().parent) + os.pathsep + os.environ.get('PATH', '')
    import numpy as np
    import soundfile as sf
    import pedalboard as pb
    if pb.__version__ != '0.9.26':
        raise RuntimeError('probe requires the pinned Pedalboard 0.9.26')
    request = fixture(args.out / 'fixtures', np, sf)
    report = {'schema': 'vwa.mixdown.native-four-track-probe/v1',
              'source_sha256': {str(p.relative_to(APP)): digest(p) for p in (
                  Path(__file__).resolve(), APP / 'src/mixdown_realtime_session.py',
                  APP / 'src/mixdown_realtime_renderer.py', APP / 'src/mixdown_native_output.py')},
              'source_fingerprints': {str(p): digest(p) for p in (args.out / 'fixtures').iterdir()},
              'dll': str(args.dll.resolve()), 'dll_sha256': digest(args.dll),
              'ffmpeg_sha256': digest(args.ffmpeg), 'pedalboard_version': pb.__version__,
              'default_backend_eligible': False,
              'pending_production_gates': ['shared final limiter', 'complete seek/FX history equivalence',
                                           'audible/whole-song acceptance', 'GUI adapter and device recovery'],
              'profiles': [], 'errors': [], 'request': request}
    modes = ('null', 'wasapi') if args.mode == 'all' else (args.mode,)
    for mode in modes:
        for block in [int(x) for x in args.blocks.split(',')]:
            started = time.perf_counter()
            try:
                value = profile(mode, block, request, args.out, args, np, sf, pb)
                value['wall_seconds'] = time.perf_counter() - started
                report['profiles'].append(value)
                print('%s %d: %s' % (mode, block, 'PASS' if value['all_probe_gates_pass'] else 'GATES NOT MET'), flush=True)
            except Exception as error:
                report['errors'].append({'mode': mode, 'block_frames': block,
                                         'error': '%s: %s' % (type(error).__name__, error)})
                print('%s %d: %s' % (mode, block, error), flush=True)
                if mode == 'wasapi':
                    break
    if args.legacy_baseline:
        import audio_mixdown_audition as old
        timings = []
        for index, (label, value) in enumerate(variations(request)):
            started = time.perf_counter()
            try:
                result = old.render_audition_pair(
                    value, None, args.out / ('legacy-%d.wav' % index),
                    args.out / 'source-cache', variant='after')
            except Exception as error:
                report['errors'].append({'mode': 'legacy', 'label': label,
                                         'error': '%s: %s' % (type(error).__name__, error)})
                break
            timings.append({'label': label, 'wall_ms': (time.perf_counter() - started) * 1000,
                            'timings_ms': result.get('timings_ms'), 'cache_hit': result.get('cache_hit')})
        report['legacy_direct_render'] = {'measurements': timings,
            'wall_ms': summary([v['wall_ms'] for v in timings], np),
            'measurement': 'cached 12-second after-WAV publication including analysis, direct Python call; not the same workload as block submission'}
    report['real_device_verified'] = any(v['mode'] == 'wasapi' for v in report['profiles'])
    report['all_requested_profiles_pass'] = not report['errors'] and all(
        v['all_probe_gates_pass'] for v in report['profiles'])
    (args.out / 'REPORT.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(str(args.out / 'REPORT.json'), flush=True)
    return 0 if report['all_requested_profiles_pass'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
