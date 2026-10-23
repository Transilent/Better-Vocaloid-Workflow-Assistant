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


def context_requested(request):
    """Inspect optional settings before importing the independent context layer."""
    enabled = request.get('context_enabled', False)
    if type(enabled) is not bool:
        raise ValueError('context_enabled必须为布尔值。')
    plan = request.get('context_plan')
    if plan is not None and (not isinstance(plan, str) or not plan.strip()):
        raise ValueError('context_plan必须为非空计划文件路径。')
    return enabled or plan is not None


def voices_for(request):
    return ('vocals',) if request.get('voice_mode') == 'single' else ('lead', 'backing')


def read_receipts(job):
    try:
        data = json.loads((job / 'exports/receipts.json').read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def project_input(job, request):
    instrumental = job/'audio/instrumental.wav'
    return fingerprint({'schema': 3, 'language': request['language'], 'pitch': bool(request.get('export_pitch',False)),
        'native': {v: file_hash(job / 'midi' / ('' if v == 'vocals' else v) / (v + '.ustx')) for v in voices_for(request)},
        'absolute_pitch': {v: file_hash(job/'midi'/('' if v=='vocals' else v)/(v+'.pitch.npz'))
             for v in voices_for(request) if request.get('export_pitch') and (job/'midi'/('' if v=='vocals' else v)/(v+'.pitch.npz')).is_file()},
        'pitch_adapter': file_hash(Path(__file__).with_name('pitch_accuracy.py')) if request.get('export_pitch') else None,
        'instrumental': file_hash(instrumental) if request.get('include_instrumental',True) and instrumental.is_file() else None,
        'converter': file_hash(Path(__file__))})


def project_signatures(job, request):
    """Expression settings affect SVP export, never MIDI inference or other formats."""
    from vocal_expression import expression_settings
    enabled, strength, timbre = expression_settings(request)
    from calibration_refinement import density_settings
    density_mode=density_settings(request)
    base = project_input(job, request)
    signatures = {fmt: fingerprint([base, fmt]) for fmt in request.get('project_formats', [])}
    if 'svp' in signatures and (enabled or timbre) and strength > 0:
        signatures['svp'] = fingerprint([base, 'svp', strength,
            file_hash(Path(__file__).with_name('vocal_expression.py')),
            enabled, timbre,
            [file_hash(Path(__file__).with_name(name)) for name in ('vocal_timbre.py', 'expression_component.py')] if timbre else [],
            {voice: file_hash(job / 'audio' / (voice + '.wav')) for voice in voices_for(request)}])
    if request.get('reference_calibration') and 'svp' in signatures:
        from expression_calibration import signature
        from common import app_path
        signatures['svp']=fingerprint([signatures['svp'],signature(app_path(request['reference_calibration'])),strength,
            {voice:file_hash(job/'audio'/(voice+'.wav')) for voice in voices_for(request)},
            [file_hash(Path(__file__).with_name(name)) for name in ('vocal_timbre.py','expression_component.py')]])
    if request.get('retained_expression') and not request.get('reference_calibration') and 'svp' in signatures:
        from calibration_workflow import retained_signature
        signatures['svp']=fingerprint([signatures['svp'],retained_signature(job,request)])
    if 'svp' in signatures:
        signatures['svp']=fingerprint([signatures['svp'],density_mode,
            [file_hash(Path(__file__).with_name(name)) for name in
             ('tuning_engine.py','calibration_refinement.py','vocal_expression.py','expression_calibration.py','calibration_workflow.py')]])
    return signatures


def cached_output(job, path, signature, receipts):
    item = receipts.get(path.relative_to(job).as_posix(), {})
    if item.get('active') is False:
        return None
    try:
        return item if item.get('input_signature') == signature and path.stat().st_size == item['bytes'] and file_hash(path) == item['sha256'] else None
    except (OSError, KeyError):
        return None


def _style_target(job, request, baseline):
    from style_presets import settings
    from style_pitch import enabled
    preset,strength,scope=settings(request)
    signature=fingerprint(['style-candidate-v2',baseline['sha256'],preset,strength,scope,enabled(request),
        request.get('expression_density','dense'),
        [file_hash(Path(__file__).with_name(name)) for name in
         ('tuning_engine.py','style_presets.py','style_pitch.py','calibration_refinement.py','vocal_expression.py','expression_calibration.py')]])
    return job/'exports'/f'voices-style-{preset}-{signature[:10]}.svp',signature


def _style_active(request):
    from style_presets import settings
    preset,strength,_=settings(request)
    return preset!='none' and strength>0 and 'svp' in request.get('project_formats',[])


def _style_intact(job, request, receipts):
    active={name:item for name,item in receipts.items() if isinstance(item,dict)
        and item.get('kind')=='style_candidate' and item.get('active',True)}
    if not _style_active(request):
        return not active
    signature=project_signatures(job,request)['svp']
    baseline=cached_output(job,job/'exports/voices.svp',signature,receipts)
    if not baseline:return False
    path,signature=_style_target(job,request,baseline)
    return bool(cached_output(job,path,signature,receipts)) and set(active)=={path.relative_to(job).as_posix()}


def export_style(job, request):
    """Keep baseline SVP and style candidates separate, including on cache reuse."""
    from style_presets import settings,track_indices_for
    from tuning_engine import process_project
    from style_pitch import enabled
    preset,strength,scope=settings(request)
    job=Path(job).resolve();receipts=read_receipts(job)
    previous=[name for name,item in receipts.items() if isinstance(item,dict)
              and item.get('kind')=='style_candidate' and item.get('active',True)]
    def retire(keep=None):
        old=[name for name in previous if name!=keep]
        if old:
            for name in old:receipts[name]['active']=False
            atomic_json(job/'exports/receipts.json',receipts)
            from task_contract import retire_exports
            retire_exports(job,old)
    if not _style_active(request):
        retire()
        return {'status':'not_selected','preset':preset,'strength':strength,'scope':scope,'outputs':[]}
    baseline=cached_output(job,job/'exports/voices.svp',project_signatures(job,request)['svp'],receipts)
    if not baseline:raise ValueError('风格预设需要本次有效的原表现 SVP。')
    path,signature=_style_target(job,request,baseline)
    item=cached_output(job,path,signature,receipts)
    if item:
        from task_contract import index_export
        index_export(job,item,replace=True)
    else:
        source=job/'exports/voices.svp';original_hash=baseline['sha256']
        data=json.loads(source.read_text(encoding='utf-8-sig').rstrip('\x00'))
        result,transaction=process_project(data,{'performance_style':preset,'style_strength':strength,
            'track_indices':track_indices_for(data,scope),'expression_density':request.get('expression_density','dense'),
            'style_pitch':enabled(request)},cancel=lambda:cancelled(job))
        adaptation=dict(transaction['style'])
        adaptation['transaction']={key:value for key,value in transaction.items() if key!='style'}
        adaptation['expression_density']=transaction['expression_density']
        adaptation['stage_output_content_sha256']=adaptation.get('output_content_sha256')
        adaptation['output_content_sha256']=transaction['output_content_sha256']
        if adaptation['status']=='skipped':
            raise ValueError('本次风格没有可应用的参数通道，原表现工程已保留；请检查风格回执或目标曲线。')
        pending=path.with_name(path.stem+'.pending.svp')
        try:
            atomic_json(pending,result)
            decoded=json.loads(pending.read_text(encoding='utf-8-sig'))
            if decoded!=result:raise ValueError('风格工程序列化回读不一致。')
            if file_hash(source)!=original_hash:raise ValueError('生成风格期间原表现工程发生变化。')
            if _style_target(job,request,baseline)[1]!=signature:raise ValueError('风格预设代码发生变化，请重试。')
            cancelled(job)
            preserved=preserve_changed_file(job,path,receipts.get(path.relative_to(job).as_posix(),{}).get('sha256'),file_hash(pending))
            pending.replace(path)
            item=commit_receipt(job,path,signature,receipts,kind='style_candidate',active=True,
                preset=preset,strength=strength,scope=scope,style_pitch=enabled(request),baseline_sha256=original_hash,
                adaptation=adaptation,verified_notes=sum(len(t.get('mainGroup',{}).get('notes',[])) for t in result['tracks']))
            if preserved:
                item['preserved_copy']=preserved;atomic_json(job/'exports/receipts.json',receipts)
        finally:pending.unlink(missing_ok=True)
    retire(item['file'])
    status=item['adaptation']['status']
    print('已生成独立风格候选：'+item['adaptation']['label']+'，状态 '+status+'；原表现 SVP 已保留。',flush=True)
    if status=='partial':print('提示：部分参数通道保持原样，原因已记入风格回执。',flush=True)
    return {'status':status,'preset':preset,'strength':strength,'scope':scope,'outputs':[item]}


def commit_receipt(job, path, signature, receipts, **details):
    item = {'file': path.relative_to(job).as_posix(), 'bytes': path.stat().st_size,
            'sha256': file_hash(path), 'input_signature': signature, **details}
    receipts[item['file']] = item
    atomic_json(job / 'exports/receipts.json', receipts)
    from task_contract import index_export
    index_export(job, item, replace=True)
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
        if context_requested(request):
            from context_workflow import outputs_intact as context_intact
            if not context_intact(job, request):
                return False
        if request.get('project_formats'):
            if vsqx_audio_required(job, request) and not cached_output(job, vsqx_audio_path(job), vsqx_audio_signature(job), receipts):
                return False
            signatures = project_signatures(job, request)
            for fmt in request['project_formats']:
                if not cached_output(job, job / 'exports' / ('voices.' + fmt), signatures[fmt], receipts):
                    return False
        if not _style_intact(job,request,receipts):
            return False
        if not request.get('audio_formats', ['wav']):
            return True
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


def remove_pitch_data(path, fmt):
    """Remove explicit tuning data while retaining editor structure and notes."""
    if fmt == 'ustx':
        import yaml
        data = read_ustx(path)
        for part in data.get('voice_parts', []):
            part['curves'] = [curve for curve in part.get('curves', []) if curve.get('abbr') not in ('pitd','pbs')]
            for note in part.get('notes', []):
                note.pop('pitch', None)
                # Keep vibrato disabled without injecting depth/period presets.
                note['vibrato'] = {'length': 0}
        path.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False),encoding='utf-8')
    elif fmt == 'svp':
        raw = path.read_bytes()
        data = json.loads(raw.removesuffix(b'\x00').decode('utf-8-sig'))
        groups = list(data.get('library', [])) + [track.get('mainGroup', {}) for track in data.get('tracks', [])]
        for group in groups:
            group.pop('pitchControls', None)
            parameters = group.get('parameters', {})
            parameters.pop('pitchDelta', None)
            parameters.pop('vibratoEnv', None)
            for note in group.get('notes', []):
                attributes = note.get('attributes', {})
                for key in list(attributes):
                    if key.startswith(('tF0','dF0','pF0')):
                        attributes.pop(key)
        path.write_bytes(json.dumps(data,ensure_ascii=False,separators=(',', ':')).encode('utf-8')
                         + (b'\x00' if raw.endswith(b'\x00') else b''))
    elif fmt == 'vsqx':
        tree = ET.parse(path)
        root = tree.getroot()
        namespace = root.tag.partition('}')[0].removeprefix('{')
        ET.register_namespace('',namespace)
        for parent in root.iter():
            for child in list(parent):
                if child.tag.rsplit('}',1)[-1] in ('cc','mCtrl') and any(
                    element.get('id') in ('P','S','PIT','PBS','pit','pbs') for element in child.iter()
                ):
                    parent.remove(child)
        tree.write(path,encoding='utf-8',xml_declaration=True)


def vsqx_audio_path(job):
    return job/'exports/voices.wavparts/instrumental.wav'


def vsqx_audio_required(job, request):
    return ('vsqx' in request.get('project_formats',[]) and request.get('include_instrumental',True)
            and (job/'audio/instrumental.wav').is_file())


def vsqx_audio_signature(job):
    return fingerprint([1, file_hash(job/'audio/instrumental.wav'), 'pcm_s16le', 44100, 2])


def prepare_vsqx_audio(job, request, receipts):
    """VSQX accepts only 44.1-kHz 16-bit WAV. Preserve the original float stem."""
    import numpy as np
    import soundfile as sf
    from pipeline import run_process
    source, destination = job/'audio/instrumental.wav', vsqx_audio_path(job)
    signature = vsqx_audio_signature(job)
    if saved := cached_output(job, destination, signature, receipts):
        from task_contract import index_export
        index_export(job, saved, replace=True)
        return saved
    destination.parent.mkdir(parents=True,exist_ok=True)
    pending = destination.with_name('instrumental.pending.wav')
    peak = 0.
    for block in sf.blocks(str(source),blocksize=65536,dtype='float32',always_2d=True):
        cancelled(job)
        if not np.isfinite(block).all(): raise ValueError('伴奏包含无效采样，无法导出 VSQX。')
        peak = max(peak,float(np.max(np.abs(block),initial=0)))
    gain = .95/peak if peak>1 else 1.
    tools = resolve_tools(request.get('tools',{}))
    command = [tools['ffmpeg'],'-hide_banner','-loglevel','error','-y','-i',str(source),
               '-map','0:a:0','-vn','-af',f'volume={gain:.12g}','-ar','44100','-ac','2','-c:a','pcm_s16le',str(pending)]
    try:
        run_process(command,job,'export_vsqx_audio')
        info, original = sf.info(str(pending)), sf.info(str(source))
        if (info.samplerate!=44100 or info.channels!=2 or info.subtype!='PCM_16'
                or abs(info.duration-original.duration)>1/44100):
            raise ValueError('VSQX 伴奏转换后的格式或时长不一致。')
        if vsqx_audio_signature(job)!=signature: raise ValueError('导出期间伴奏发生变化，请重试。')
        cancelled(job)
        preserved = preserve_changed_file(job,destination,receipts.get(destination.relative_to(job).as_posix(),{}).get('sha256'),file_hash(pending))
        pending.replace(destination)
        result = commit_receipt(job,destination,signature,receipts,gain=gain)
        if preserved:
            result['preserved_copy']=preserved
            atomic_json(job/'exports/receipts.json',receipts)
        return result
    finally: pending.unlink(missing_ok=True)


def read_ustx(path):
    """Read USTX's YAML 1.2 booleans without converting Japanese 'no' to False."""
    import re
    import yaml
    class Loader(yaml.SafeLoader):
        pass
    # Copy the resolver table: changing SafeLoader would affect unrelated code.
    Loader.yaml_implicit_resolvers = {
        key:[rule for rule in rules if rule[0]!='tag:yaml.org,2002:bool']
        for key,rules in yaml.SafeLoader.yaml_implicit_resolvers.items()}
    Loader.add_implicit_resolver('tag:yaml.org,2002:bool',
        re.compile(r'^(?:true|True|TRUE|false|False|FALSE)$'), list('tTfF'))
    return yaml.load(path.read_text(encoding='utf-8-sig'),Loader=Loader)


def portable_audio_reference(path, fmt, source):
    """Resolve probes absolutely, then serialize an editor-relative audio link."""
    base = path.parent/ 'voices.wavparts' if fmt=='vsqx' else path.parent
    relative = Path(os.path.relpath(source,base)).as_posix()
    count=0
    if fmt=='svp':
        raw=path.read_bytes();data=json.loads(raw.removesuffix(b'\x00').decode('utf-8-sig'))
        for track in data.get('tracks',[]):
            ref=track.get('mainRef',{})
            if ref.get('isInstrumental') and ref.get('audio'):
                ref['audio']['filename']=relative
                count+=1
        path.write_bytes(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode('utf-8')+(b'\x00' if raw.endswith(b'\x00') else b''))
    elif fmt=='ustx':
        import yaml
        data=read_ustx(path)
        for part in data.get('wave_parts',[]): part['relative_path']=relative;count+=1
        path.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False),encoding='utf-8')
    elif fmt=='vsqx':
        tree=ET.parse(path);root=tree.getroot()
        ET.register_namespace('',root.tag.partition('}')[0].removeprefix('{'))
        for element in root.iter():
            if element.tag.rsplit('}',1)[-1]=='filePath': element.text=relative;count+=1
        tree.write(path,encoding='utf-8',xml_declaration=True)
    if count!=1: raise ValueError(fmt.upper()+' 导出未保留伴奏，请检查导出日志。')


def export_projects(job, request):
    job=Path(job).resolve()
    formats=request.get('project_formats',[])
    if not formats:
        return {'outputs':[], 'tracks':[]}
    receipts = read_receipts(job)
    signatures = project_signatures(job, request)
    companion = prepare_vsqx_audio(job,request,receipts) if vsqx_audio_required(job,request) else None
    existing = {fmt: cached_output(job, job / 'exports' / ('voices.' + fmt), signatures[fmt], receipts)
                for fmt in formats}
    from task_contract import index_export
    for item in existing.values():
        if item: index_export(job, item, replace=True)
    if all(existing.values()):
        previous = json.loads((job / 'exports/report.json').read_text(encoding='utf-8')) if (job / 'exports/report.json').is_file() else {}
        return {**previous.get('projects', {}), 'outputs': [existing[fmt] for fmt in formats], 'vsqx_audio':companion}
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
        pitch_requested = bool(request.get('export_pitch',False))
        absolute = folder/(voice+'.pitch.npz')
        metadata_path = folder/'report.json'
        metadata = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.is_file() else {}
        if pitch_requested and metadata.get('pitch_source')=='absolute_rmvpe' and (
                not absolute.is_file() or metadata.get('output_sha256',{}).get('pitch.npz') != file_hash(absolute)):
            raise ValueError('连续音高缓存缺失或已改变，请重新提取 MIDI。')
        # A manually edited native score retains its own tuning. New unedited
        # results use the measured f0 instead of a simulated USTX base curve.
        raw_pitch = (pitch_requested and absolute.is_file()
            and metadata.get('native_sha256',{}).get('ustx') == file_hash(path)
            and metadata.get('output_sha256',{}).get('pitch.npz') == file_hash(absolute))
        project=converters['ustx'].load(path, {'import_pitch':pitch_requested and not raw_pitch,'import_instrumental_track':False,
            'plus_handling_mode':'monosyllabic' if request['language']=='zh' else 'polysyllabic'})
        vocal=[track for track in project.track_list if isinstance(track,SingingTrack)]
        if not vocal:
            vocal=[SingingTrack(title=voice)]
        for track in vocal:
            pitch_source = None
            if raw_pitch:
                from pitch_accuracy import load_pitch, absolute_curve
                pitch_source = absolute_curve(track,project,load_pitch(absolute))
            if not pitch_requested:
                track.edited_params.pitch.points.root.clear()
            for note in track.note_list:
                note.pronunciation=None
            track.title={'lead':'Lead Vocal','backing':'Backing Vocal','vocals':'Vocals'}[voice]
            tracks.append({'voice':voice,'notes':len(track.note_list),
                           'pitch_source':pitch_source,
                           'pitch_points':sum(point.y!=-100 for point in track.edited_params.pitch.points.root)})
        project.track_list=vocal
        projects.append(project)
    merged=Project.merge_projects(projects) if len(projects)>1 else projects[0]
    destination=job/'exports'
    destination.mkdir(exist_ok=True)
    instrumental=job/'audio/instrumental.wav'
    if request.get('include_instrumental',True) and instrumental.is_file():
        merged.track_list.append(InstrumentalTrack(title='Instrumental',audio_file_path=str(instrumental),offset=0))
    outputs=[]
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
        audio_source = vsqx_audio_path(job) if fmt=='vsqx' and companion else instrumental
        for track in merged.track_list:
            if isinstance(track,InstrumentalTrack): track.audio_file_path=str(audio_source)
        try:
            converters[fmt].dump(pending,merged,options)
            if request.get('include_instrumental',True) and instrumental.is_file():
                portable_audio_reference(pending,fmt,audio_source)
            if fmt=='vsqx':
                restore_vsqx_lyrics(pending,[track for track in merged.track_list if isinstance(track,SingingTrack)])
            if not request.get('export_pitch',False):
                remove_pitch_data(pending,fmt)
            details = {}
            if fmt=='svp':
                from tuning_engine import legacy_export
                original=pending.read_bytes()
                data=json.loads(original.decode('utf-8-sig').rstrip('\x00'))
                data,details=legacy_export(data,job,request,tracks,cancel=lambda:cancelled(job))
                if project_signatures(job,request)['svp']!=signatures['svp']:
                    raise ValueError('调音处理期间来源或配置发生变化，请重试。')
                pending.write_bytes(json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')+
                                    (b'\x00' if original.endswith(b'\x00') else b''))
                density=details['expression_density']
                print('SVP 参数采样：'+density['status']+'，新增 '+str(density['added_points'])+' 个点。',flush=True)
                if density['unsupported_nonlinear_controls']:
                    print('提示：已有非线性编辑器曲线保持原样，回执已记录未加密通道。',flush=True)
            loaded=converters[fmt].load(pending,{'import_pitch':True,'import_instrumental_track':False})
            expected=sum(item['notes'] for item in tracks)
            actual=sum(len(track.note_list) for track in loaded.track_list if isinstance(track,SingingTrack))
            if expected!=actual:
                raise ValueError(f'{fmt.upper()} 导出后的音符数量不一致，请查看导出日志。')
            cancelled(job)
            preserved = preserve_changed_file(job,path,receipts.get(path.relative_to(job).as_posix(),{}).get('sha256'),file_hash(pending))
            pending.replace(path)
            outputs.append(commit_receipt(job, path, signatures[fmt], receipts, verified_notes=actual, **details))
            if preserved:
                receipts[path.relative_to(job).as_posix()]['preserved_copy'] = preserved
                atomic_json(job/'exports/receipts.json',receipts)
        finally:
            pending.unlink(missing_ok=True)
    return {'outputs':outputs,'tracks':tracks,'engine':'LibreSVIP','pitch_requested':request.get('export_pitch',False),
            'vsqx_audio':companion}


def export_audio(job, request):
    from pipeline import run_process
    formats=request.get('audio_formats',['wav'])
    if not formats:
        return []
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
                from task_contract import index_export
                index_export(job, selected[fmt], replace=True)
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
    from task_contract import register_calibration
    register_calibration(job, request)
    project=export_projects(job,request)
    styles=export_style(job,request)
    audio=export_audio(job,request)
    (job/'exports').mkdir(exist_ok=True)
    compressed = [item for item in audio if item['file'].rsplit('.',1)[-1]!='wav']
    gain = compressed[0].get('gain',1.) if compressed else 1.
    report={'projects':project,'styles':styles,'audio':audio,'timeline_offset_seconds':0,'cache_schema':2,
            'audio_encoding':{'gain':gain,'gain_db':20*math.log10(gain),
                              'policy':'shared_peak_headroom','original_wav_preserved':True}}
    if context_requested(request):
        from context_workflow import prepare_context
        report['context'] = prepare_context(job, request)
    atomic_json(job/'exports/report.json',report)
    print('工程与分轨音频导出完成。',flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--job',required=True,type=Path)
    parser.add_argument('--context',action='store_true',help='Prepare context evidence and advisory plan')
    parser.add_argument('--context-plan',help='Validate an existing context plan without changing project parameters')
    parser.add_argument('--performance-style',choices=('none','soft_ballad','intimate_narration','power_pop'))
    parser.add_argument('--style-strength',type=float)
    parser.add_argument('--style-scope',choices=('lead','all_vocals'))
    parser.add_argument('--style-pitch',action=argparse.BooleanOptionalAction,default=None)
    args=parser.parse_args()
    options=json.loads((args.job/'request.json').read_text(encoding='utf-8'))
    if args.context: options['context_enabled']=True
    if args.context_plan: options['context_plan']=args.context_plan
    for key in ('performance_style','style_strength','style_scope','style_pitch'):
        if getattr(args,key) is not None:options[key]=getattr(args,key)
    export_job(args.job,options)
