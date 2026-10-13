"""Merge vocal projects with LibreSVIP and export separated audio with FFmpeg."""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from common import atomic_json, cancelled, file_hash, resolve_tools, preserve_changed_file
from artifact_cache import fingerprint

PROJECT_FORMATS=('ustx','vsqx','svp')
AUDIO_FORMATS=('wav','flac','mp3','ogg')


def voices_for(request):
    return ('vocals',) if request.get('voice_mode') == 'single' else ('lead', 'backing')


def read_receipts(job):
    try:
        data = json.loads((job / 'exports/receipts.json').read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def project_input(job, request):
    return fingerprint({'schema': 1, 'language': request['language'],
        'native': {v: file_hash(job / 'midi' / ('' if v == 'vocals' else v) / (v + '.ustx')) for v in voices_for(request)},
        'instrumental': request.get('include_instrumental', True), 'converter': file_hash(Path(__file__))})


def cached_output(job, path, signature, receipts):
    item = receipts.get(path.relative_to(job).as_posix(), {})
    try:
        return item if item.get('input_signature') == signature and path.stat().st_size == item['bytes'] and file_hash(path) == item['sha256'] else None
    except (OSError, KeyError):
        return None


def commit_receipt(job, path, signature, receipts, **details):
    item = {'file': path.relative_to(job).as_posix(), 'bytes': path.stat().st_size,
            'sha256': file_hash(path), 'input_signature': signature, **details}
    receipts[item['file']] = item
    atomic_json(job / 'exports/receipts.json', receipts)
    return item


def audio_inputs(job, request, receipts):
    """One bounded peak scan per input revision; all compressed stems share gain."""
    sources = {voice: job/'audio'/(voice+'.wav') for voice in (*voices_for(request),'instrumental')}
    hashes = {voice: file_hash(path) for voice, path in sources.items()}
    if not any(fmt != 'wav' for fmt in request.get('audio_formats',['wav'])):
        return hashes, 1.0, None
    import numpy as np
    import soundfile as sf
    saved = receipts.get('_audio_peaks', {})
    if not isinstance(saved,dict): saved = {}
    peaks = {}
    for voice, source in sources.items():
        item = saved.get(voice, {})
        peak = item.get('peak') if isinstance(item,dict) else None
        valid = (isinstance(item,dict) and item.get('sha256') == hashes[voice]
                 and type(peak) in (int,float) and math.isfinite(peak) and peak >= 0)
        if not valid:
            peak, frames = 0.0, 0
            for block in sf.blocks(str(source),blocksize=65536,dtype='float32',always_2d=True):
                cancelled(job)
                if not np.isfinite(block).all():
                    raise ValueError('音频包含无效采样，无法导出：'+source.name)
                frames += len(block)
                peak = max(peak,float(np.max(np.abs(block),initial=0)))
            if not frames: raise ValueError('音频没有有效采样：'+source.name)
            if file_hash(source) != hashes[voice]:
                raise ValueError('导出期间音频发生变化，请重试：'+source.name)
        peaks[voice] = {'sha256':hashes[voice], 'peak':peak}
    maximum = max(item['peak'] for item in peaks.values())
    gain = .95 / maximum if maximum > 1 else 1.0
    return hashes, gain, peaks


def audio_signature(source_hash, fmt, gain):
    return fingerprint([1,source_hash,fmt] if fmt=='wav' else [2,source_hash,fmt,gain])


def outputs_intact(job, request):
    """Check selected outputs and their own inputs, rather than a stale format list."""
    job = Path(job)
    receipts = read_receipts(job)
    try:
        if request.get('project_formats'):
            signature = project_input(job, request)
            for fmt in request['project_formats']:
                if not cached_output(job, job / 'exports' / ('voices.' + fmt), fingerprint([signature, fmt]), receipts):
                    return False
        hashes, gain, _ = audio_inputs(job, request, receipts)
        for voice in (*voices_for(request), 'instrumental'):
            source = job / 'audio' / (voice + '.wav')
            source_hash = hashes[voice]
            for fmt in request.get('audio_formats', ['wav']):
                path = source if fmt == 'wav' else job / 'exports/audio' / (voice + '.' + fmt)
                if not cached_output(job, path, audio_signature(source_hash, fmt, gain), receipts):
                    return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def validate_options(projects, audio):
    if (not isinstance(projects,list) or not isinstance(audio,list)
            or any(fmt not in PROJECT_FORMATS for fmt in projects)
            or any(fmt not in AUDIO_FORMATS for fmt in audio)
            or len(set(projects))!=len(projects) or len(set(audio))!=len(audio)):
        raise ValueError('请选择支持的工程与音频格式。')


def library():
    runtime=ROOT/'dependencies/export'
    if not (runtime/'libresvip/__init__.py').is_file():
        raise FileNotFoundError('工程转换运行库缺失，请恢复 dependencies/export 目录。')
    settings=ROOT/'cache/export-app'
    settings.mkdir(parents=True,exist_ok=True)
    os.environ['APPDATA']=os.environ['LOCALAPPDATA']=str(settings)
    sys.path.insert(0,str(runtime))
    from libresvip.plugins.ustx.ustx_converter import OpenUtauConverter
    from libresvip.plugins.vsqx.vsqx_converter import VsqxConverter
    from libresvip.plugins.svp.synthv_studio_converter import SynthVStudioConverter
    from libresvip.model.base import Project, SingingTrack, InstrumentalTrack
    return {'ustx':OpenUtauConverter,'vsqx':VsqxConverter,'svp':SynthVStudioConverter},Project,SingingTrack,InstrumentalTrack


def restore_vsqx_lyrics(path, tracks):
    """Keep the user's lyric script while retaining generated pronunciation data."""
    tree=ET.parse(path)
    root=tree.getroot()
    namespace=root.tag.partition('}')[0].removeprefix('{')
    ET.register_namespace('',namespace)
    parts=[element for element in root.iter() if element.tag.rsplit('}',1)[-1]=='vsTrack']
    if len(parts)!=len(tracks):
        raise ValueError('VSQX 导出声部数量不一致。')
    for part,track in zip(parts,tracks):
        notes=[element for element in part.iter() if element.tag.rsplit('}',1)[-1]=='note']
        if len(notes)!=len(track.note_list):
            raise ValueError('VSQX 导出音符数量不一致。')
        for element,note in zip(notes,track.note_list):
            lyric=next((child for child in element if child.tag.rsplit('}',1)[-1] in ('y','lyric')),None)
            if lyric is not None:
                lyric.text=note.lyric
    tree.write(path,encoding='utf-8',xml_declaration=True)


def export_projects(job, request):
    formats=request.get('project_formats',[])
    if not formats:
        return {'outputs':[], 'tracks':[]}
    receipts = read_receipts(job)
    signature = project_input(job, request)
    existing = {fmt: cached_output(job, job / 'exports' / ('voices.' + fmt), fingerprint([signature, fmt]), receipts)
                for fmt in formats}
    if all(existing.values()):
        previous = json.loads((job / 'exports/report.json').read_text(encoding='utf-8')) if (job / 'exports/report.json').is_file() else {}
        return {**previous.get('projects', {}), 'outputs': [existing[fmt] for fmt in formats]}
    converters,Project,SingingTrack,InstrumentalTrack=library()
    voices=voices_for(request)
    projects=[]
    tracks=[]
    for voice in voices:
        cancelled(job)
        folder=job/'midi' if voice=='vocals' else job/'midi'/voice
        path=folder/(voice+'.ustx')
        if not path.is_file():
            raise FileNotFoundError('缺少声部工程，请启用工程导出后重新提取 MIDI：'+str(path.name))
        project=converters['ustx'].load(path, {'import_pitch':True,'import_instrumental_track':False,
            'plus_handling_mode':'monosyllabic' if request['language']=='zh' else 'polysyllabic'})
        vocal=[track for track in project.track_list if isinstance(track,SingingTrack)]
        if not vocal:
            vocal=[SingingTrack(title=voice)]
        for track in vocal:
            for note in track.note_list:
                note.pronunciation=None
            track.title={'lead':'Lead Vocal','backing':'Backing Vocal','vocals':'Vocals'}[voice]
            tracks.append({'voice':voice,'notes':len(track.note_list),
                           'pitch_points':sum(point.y!=-100 for point in track.edited_params.pitch.points.root)})
        project.track_list=vocal
        projects.append(project)
    merged=Project.merge_projects(projects) if len(projects)>1 else projects[0]
    destination=job/'exports'
    destination.mkdir(exist_ok=True)
    instrumental=job/'audio/instrumental.wav'
    if request.get('include_instrumental',True) and instrumental.is_file():
        merged.track_list.append(InstrumentalTrack(title='Instrumental',audio_file_path='../audio/instrumental.wav',offset=0))
    outputs=[]
    previous=Path.cwd()
    try:
        os.chdir(destination)
        for fmt in formats:
            cancelled(job)
            if existing[fmt]:
                outputs.append(existing[fmt])
                continue
            path=destination/('voices.'+fmt)
            pending=path.with_name('voices.pending.' + fmt)
            options={}
            if fmt=='svp':
                options={'down_sample':0,'language_override':'japanese' if request['language']=='ja' else 'mandarin'}
            converters[fmt].dump(pending,merged,options)
            if fmt=='vsqx':
                restore_vsqx_lyrics(pending,[track for track in merged.track_list if isinstance(track,SingingTrack)])
            # Read the actual file back to catch broken serializers and lost notes.
            loaded=converters[fmt].load(pending,{'import_pitch':True,'import_instrumental_track':False})
            expected=sum(item['notes'] for item in tracks)
            actual=sum(len(track.note_list) for track in loaded.track_list if isinstance(track,SingingTrack))
            if expected!=actual:
                raise ValueError(f'{fmt.upper()} 导出后的音符数量不一致，请查看导出日志。')
            cancelled(job)
            preserved = preserve_changed_file(job,path,receipts.get(path.relative_to(job).as_posix(),{}).get('sha256'),file_hash(pending))
            pending.replace(path)
            outputs.append(commit_receipt(job, path, fingerprint([signature, fmt]), receipts, verified_notes=actual))
            if preserved:
                receipts[path.relative_to(job).as_posix()]['preserved_copy'] = preserved
                atomic_json(job/'exports/receipts.json',receipts)
    finally:
        os.chdir(previous)
    return {'outputs':outputs,'tracks':tracks,'engine':'LibreSVIP','pitch_requested':request.get('export_pitch',False)}


def export_audio(job, request):
    from pipeline import run_process
    formats=request.get('audio_formats',['wav'])
    destination=job/'exports/audio'
    destination.mkdir(parents=True,exist_ok=True)
    voices=(*voices_for(request),'instrumental')
    codec={'flac':['-c:a','flac','-sample_fmt','s32','-compression_level','8'],
           'mp3':['-c:a','libmp3lame','-q:a','2'], 'ogg':['-c:a','libvorbis','-q:a','5']}
    outputs=[]
    receipts=read_receipts(job)
    hashes, gain, peaks = audio_inputs(job, request, receipts)
    if peaks is not None and receipts.get('_audio_peaks') != peaks:
        receipts['_audio_peaks'] = peaks
        atomic_json(job/'exports/receipts.json',receipts)
    gain_db = 20 * math.log10(gain)
    if gain < 1:
        print(f'压缩分轨统一应用 {gain_db:.2f} dB 峰值衰减，保持声部之间的音量关系。增益已记录在导出报告中。',flush=True)
    for voice in voices:
        source=job/'audio'/(voice+'.wav')
        source_hash=hashes[voice]
        missing=[]
        selected={}
        for fmt in formats:
            cancelled(job)
            path=source if fmt=='wav' else destination/(voice+'.'+fmt)
            signature=audio_signature(source_hash,fmt,gain)
            selected[fmt]=cached_output(job,path,signature,receipts)
            if selected[fmt]:
                continue
            if fmt=='wav':
                selected[fmt]=commit_receipt(job,path,signature,receipts)
            else:
                missing.append((fmt,path,path.with_name(voice+'.pending.'+fmt),signature))
        if missing:
            # One decode per stem; each encoder receives the same original PCM.
            command=[resolve_tools(request['tools'])['ffmpeg'],'-hide_banner','-loglevel','error','-y','-i',str(source)]
            for fmt,path,pending,signature in missing:
                command.extend(['-map','0:a:0','-vn'])
                if gain < 1: command.extend(['-af',f'volume={gain:.17g}:precision=double'])
                command.extend([*codec[fmt],str(pending)])
            try:
                run_process(command,job,'export_'+voice)
                import soundfile as sf
                source_info=sf.info(str(source))
                for fmt,path,pending,signature in missing:
                    info=sf.info(str(pending))
                    tolerance=0 if fmt=='flac' else 4096
                    if info.samplerate!=source_info.samplerate or info.channels!=source_info.channels or abs(info.frames-source_info.frames)>tolerance:
                        raise ValueError('音频导出的采样率、声道或时长不一致：'+path.name)
                    cancelled(job)
                    preserved = preserve_changed_file(job,path,receipts.get(path.relative_to(job).as_posix(),{}).get('sha256'),file_hash(pending))
                    pending.replace(path)
                    selected[fmt]=commit_receipt(job,path,signature,receipts,gain=gain,gain_db=gain_db)
                    if preserved:
                        selected[fmt]['preserved_copy'] = preserved
                        atomic_json(job/'exports/receipts.json',receipts)
            finally:
                for fmt,path,pending,signature in missing:
                    pending.unlink(missing_ok=True)
        for fmt in formats:
            path=source if fmt=='wav' else destination/(voice+'.'+fmt)
            if not path.is_file() or path.stat().st_size==0:
                raise ValueError('音频导出未生成有效文件：'+path.name)
            outputs.append(selected[fmt])
    return outputs


def export_job(job, request):
    job=Path(job).resolve()
    validate_options(request.get('project_formats',[]),request.get('audio_formats',['wav']))
    project=export_projects(job,request)
    audio=export_audio(job,request)
    (job/'exports').mkdir(exist_ok=True)
    compressed = [item for item in audio if item['file'].rsplit('.',1)[-1]!='wav']
    gain = compressed[0].get('gain',1.) if compressed else 1.
    report={'projects':project,'audio':audio,'timeline_offset_seconds':0,'cache_schema':2,
            'audio_encoding':{'gain':gain,'gain_db':20*math.log10(gain),
                              'policy':'shared_peak_headroom','original_wav_preserved':True}}
    atomic_json(job/'exports/report.json',report)
    print('工程与分轨音频导出完成。',flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--job',required=True,type=Path)
    args=parser.parse_args()
    export_job(args.job,json.loads((args.job/'request.json').read_text(encoding='utf-8')))
