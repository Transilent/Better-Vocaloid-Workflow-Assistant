"""Versioned job artifacts and human handoff, layered over producer receipts.

The index is evidence about files, never permission to delete them. Request and
producer receipts remain authoritative for inference and export cache reuse.
"""
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import threading
import time

from common import app_path, atomic_json, file_hash, saved_path

SCHEMA = 1
MAX_BYTES = 8 * 1024**2
MAX_ENTRIES = 4096
STAGES = ('metadata', 'download', 'audio', 'separation', 'midi', 'subtitles', 'exports')
PARENTS = {'metadata': (), 'download': ('metadata',), 'audio': ('download',),
           'separation': ('audio',), 'midi': ('separation',),
           'subtitles': ('midi',), 'exports': ('midi', 'separation')}
NEXT = {'processing': '等待处理结束', 'retry': '继续 / 重试', 'continue': '继续处理',
        'audio_ready': '分轨音频已就绪', 'awaiting_tuning': '在编辑器中调音并导出 Mixdown',
        'mixdown_ready': '合成视频，或直接登记成品', 'video_ready': '准备封面与发布文案',
        'awaiting_upload': '上传并核对发布内容', 'awaiting_publish': '在创作中心核对并发布',
        'needs_review': '检查文件与依赖', 'ready': '开始处理'}
_LOCK = threading.RLock()


class ContractError(ValueError):
    pass


def _json(path, default=None):
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(default if default is not None else {})
    if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 1024:
        raise ContractError('任务记录不能是链接：' + path.name)
    if path.stat().st_size > MAX_BYTES:
        raise ContractError('任务记录超出大小限制：' + path.name)
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError) as exc:
        raise ContractError('任务记录无法读取：' + path.name + '。请先检查文件。') from exc


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) is not None


def _safe(path, boundary=None):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (part.exists() and getattr(part.lstat(), 'st_file_attributes', 0) & 1024):
            raise ContractError('文件路径包含链接或重解析点：' + str(path))
        if boundary and part == boundary:
            break
    path = path.resolve()
    if boundary and (path == boundary or not path.is_relative_to(boundary)):
        raise ContractError('任务产物路径超出任务目录。')
    return path


def location(job, path):
    job, path = Path(job).resolve(), _safe(path)
    if path.is_relative_to(job):
        return {'kind': 'task', 'path': path.relative_to(job).as_posix()}
    return {'kind': 'external', 'path': saved_path(path)}


def resolve(job, entry):
    loc = entry['location']
    if not isinstance(loc, dict) or not isinstance(loc.get('path'), str):
        raise ContractError('产物位置无效。')
    name = loc['path']
    if loc.get('kind') == 'task':
        relative = PurePosixPath(name)
        if (not name or relative.is_absolute() or '..' in relative.parts or '\\' in name
                or ':' in name or name in ('.', '')):
            raise ContractError('产物相对路径无效。')
        return _safe(Path(job).resolve() / name, Path(job).resolve())
    if loc.get('kind') != 'external' or not (name.startswith('@/') or Path(name).is_absolute()):
        raise ContractError('外部引用必须使用明确的完整路径。')
    return _safe(app_path(name))


def read(job):
    data = _json(Path(job) / 'artifacts.json', {'schema': SCHEMA, 'entries': {}, 'stages': {}})
    if data.get('schema') != SCHEMA:
        raise ContractError('产物清单版本不受支持，请升级助手后继续。')
    entries, stages = data.get('entries'), data.get('stages')
    if not isinstance(entries, dict) or len(entries) > MAX_ENTRIES or not isinstance(stages, dict):
        raise ContractError('产物清单结构无效。')
    for key, entry in entries.items(): _validate_entry(job, key, entry)
    if any(key not in STAGES or not isinstance(value, dict)
           or value.get('state') not in ('ready', 'running', 'failed', 'cancelled', 'invalidated')
           or (value.get('generation') is not None and not _sha(value['generation']))
           for key, value in stages.items()):
        raise ContractError('产物阶段记录无效。')
    return data


def _validate_entry(job, key, entry):
    if (not isinstance(entry, dict) or not isinstance(key, str) or not re.fullmatch('[a-f0-9]{32}', key)
            or not _sha(entry.get('sha256')) or not _sha(entry.get('revision')) or type(entry.get('bytes')) is not int
            or entry['bytes'] < 0 or entry.get('retention') not in ('source', 'result', 'reference', 'cache')
            or not isinstance(entry.get('producer'), str) or not isinstance(entry.get('role'), str)
            or not isinstance(entry.get('dependencies'), list) or len(entry['dependencies']) > MAX_ENTRIES):
        raise ContractError('产物条目无效：' + str(key))
    resolve(job, entry)
    for dep in entry['dependencies']:
        if (not isinstance(dep, dict) or not isinstance(dep.get('id'), str)
                or not re.fullmatch('[a-f0-9]{32}', dep['id']) or not _sha(dep.get('revision'))):
            raise ContractError('产物依赖无效。')


def _metadata(job):
    value = _json(Path(job) / 'task.json')
    if value.get('schema') not in (None, SCHEMA): raise ContractError('任务登记版本不受支持。')
    bindings = value.get('bindings', {})
    if not isinstance(bindings, dict) or set(bindings) - {'project', 'mixdown', 'video'}:
        raise ContractError('人工成果登记无效。')
    publication = value.get('publication')
    if publication is not None and not isinstance(publication, dict): raise ContractError('发布登记无效。')
    sessions = value.get('calibration_sessions', {})
    if not isinstance(sessions, dict) or len(sessions) > 128: raise ContractError('校准任务登记无效。')
    for row in [*bindings.values(), *sessions.values(), *([publication] if publication else [])]:
        if not isinstance(row, dict) or 'id' not in row or 'entry' not in row:
            raise ContractError('成果登记缺少文件记录。')
        _validate_entry(job, row['id'], row['entry'])
        if not isinstance(row.get('files', {}), dict): raise ContractError('关联文件登记无效。')
        for key, file in row.get('files', {}).items(): _validate_entry(job, key, file)
    if publication:
        if not isinstance(publication.get('platforms', {}), dict) or not isinstance(publication.get('files', {}), dict):
            raise ContractError('发布登记无效。')
        for key, row in publication.get('files', {}).items(): _validate_entry(job, key, row)
    value.setdefault('schema', SCHEMA)
    return value


@contextmanager
def _locked(job):
    """Independent of run.lock: inference children also commit their own outputs."""
    import msvcrt
    job = Path(job).resolve()
    with _LOCK:
        lock_path = _safe(job / 'contract.lock', job)
        with lock_path.open('a+b') as stream:
            deadline = time.monotonic() + 20
            while True:
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ContractError('任务文件记录正在使用，请稍后重试。')
                    time.sleep(.025)
            try:
                # Windows can lock a byte beyond EOF. Initialize only after
                # acquiring it: another process may already own byte zero.
                if stream.seek(0, 2) == 0:
                    stream.write(b'0'); stream.flush()
                yield
            finally:
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _write(job, data):
    data['updated_at'] = time.time()
    if len(data['entries']) > MAX_ENTRIES:
        raise ContractError('产物条目过多，请另建任务。')
    atomic_json(Path(job) / 'artifacts.json', data)


def _dependencies(data, producers):
    return [{'id': key, 'revision': entry['revision']} for key, entry in data['entries'].items()
            if entry['producer'] in producers and entry.get('active', True)
            and entry['role'] not in ('report', 'description_draft', 'publication_asset')]


def _entry(job, path, role, producer, *, expected=None, signature=None, retention='result'):
    path = _safe(path)
    if producer in STAGES and not path.is_relative_to(Path(job).resolve()):
        raise ContractError('自动产物必须位于任务目录内。')
    exists = path.is_file()
    if not exists and not expected:
        raise FileNotFoundError(str(path))
    before = path.stat() if exists else None
    digest = expected or file_hash(path)
    if expected is None:
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ContractError('登记期间文件发生变化，请保存完成后重试。')
    if not _sha(digest):
        raise ContractError('产物校验值无效。')
    loc = location(job, path)
    key = _digest([producer, role, loc])[:32]
    row = {'location': loc, 'role': role, 'producer': producer, 'retention': retention,
           'bytes': path.stat().st_size if exists else 0, 'sha256': digest,
           'revision': _digest([digest, signature]), 'input_signature': signature,
           'dependencies': [], 'active': True, 'recorded_at': time.time()}
    return key, row


def register(job, specs, producer, *, signature=None, replace=True, parents=None, state=None, links=()):
    """Commit producer evidence. Never learn a new hash merely by inspecting a file."""
    job = Path(job).resolve()
    known = read(job)['entries'] if not replace else {}
    prepared, keys = [], []
    for spec in specs:
        key = _digest([producer, spec['role'], location(job, spec['path'])])[:32]
        keys.append(key)
        if key not in known:
            prepared.append(_entry(job, **spec, producer=producer, signature=signature))
    with _locked(job):
        data = read(job)
        deps = _dependencies(data, PARENTS.get(producer, ()) if parents is None else parents)
        deps.extend({'id': key, 'revision': data['entries'][key]['revision']} for key in links)
        for key, row in prepared:
            if replace or key not in data['entries']:
                row['dependencies'] = [dep for dep in deps if dep['id'] != key]
                row['generation'] = data['stages'].get(producer, {}).get('generation')
                data['entries'][key] = row
        if state and producer in STAGES:
            previous = data['stages'].get(producer, {})
            if replace or previous.get('state') not in ('invalidated', 'failed', 'cancelled'):
                data['stages'][producer] = {'state': state, 'updated_at': time.time(),
                                           'generation': previous.get('generation')}
        _write(job, data)
    return keys


def begin_stage(job, stage):
    if stage not in STAGES:
        raise ContractError('未知处理阶段。')
    affected = {stage}
    for key in STAGES:
        if affected.intersection(PARENTS[key]):
            affected.add(key)
    with _locked(job):
        data = read(job)
        for key in affected:
            data['stages'][key] = {'state': 'running' if key == stage else 'invalidated',
                                   'reason': '上游阶段重新处理', 'updated_at': time.time(),
                                   'generation': _digest([time.time_ns(), os.getpid(), key])}
        _write(job, data)


def fail_stage(job, stage, state, error):
    if stage not in STAGES:
        return
    with _locked(job):
        data = read(job)
        data['stages'].setdefault(stage, {}).update(state=state, error=str(error)[:2000], updated_at=time.time())
        _write(job, data)


def _role(name):
    if name in ('source-info.json', 'upstream-info.json'): return 'source_metadata'
    if name == '发布简介草稿.txt': return 'description_draft'
    suffix = Path(name).suffix.lower()
    if suffix in ('.mid', '.midi'): return 'midi'
    if suffix in ('.svp', '.ustx', '.vsqx'): return 'editor_project'
    if name.endswith('.pitch.npz'): return 'absolute_pitch'
    if suffix in ('.wav', '.mp3', '.flac', '.ogg'): return 'audio'
    if suffix in ('.srt', '.ass', '.lrc'): return 'subtitles'
    if suffix in ('.mp4', '.mkv'): return 'source_video'
    if suffix in ('.jpg', '.png', '.webp'): return 'cover'
    return 'report'


def index_stage(job, stage, files=(), *, receipt=None, replace=False):
    expected = dict((receipt or {}).get('sha256', {}))
    if stage == 'midi':
        report = _json(Path(job) / 'midi/report.json')
        if report.get('merged_sha256'): expected['midi/voices.mid'] = report['merged_sha256']
        files = [name for name in files if not any(
            name == f'midi/{voice}/{voice}.mid' and (Path(job) / f'midi/{voice}/report.json').is_file()
            for voice in ('lead', 'backing')) and not (
                name == 'midi/vocals.mid' and report.get('midi_sha256'))]
    specs = [{'path': Path(job) / name, 'role': _role(name), 'expected': expected.get(name),
              'retention': 'source' if stage in ('download', 'audio') else 'result'} for name in files]
    # Optional original artwork is a real cover; a decoded video frame has its own role.
    if stage == 'metadata':
        from cover_art import cached_cover
        info = _json(Path(job) / 'source-info.json')
        cover = cached_cover(info, Path(job))
        if cover:
            image, _ = cover
            specs.extend({'path': p, 'role': 'original_cover' if p == image else 'report'}
                         for p in (image, Path(job) / 'original-cover.json'))
    return register(job, specs, stage, signature=(receipt or {}).get('signature'),
                    replace=replace, state='ready')


def index_voice(job, voice, *, replace=False):
    folder = Path(job) / 'midi' / ('' if voice == 'vocals' else voice)
    report = _json(folder / 'report.json')
    hashes = {**report.get('native_sha256', {}), **report.get('output_sha256', {})}
    if report.get('midi_sha256'): hashes.setdefault('mid', report['midi_sha256'])
    specs = [{'path': folder / (voice + '.' + suffix), 'role': _role(voice + '.' + suffix),
              'expected': digest} for suffix, digest in hashes.items()]
    if not specs:
        return []
    specs.append({'path': folder / 'report.json', 'role': 'report'})
    return register(job, specs, 'midi', signature=report.get('input_signature'), replace=replace)


def retire_exports(job, files):
    """Deactivate superseded export records while preserving their files."""
    names = set(files)
    for name in names:
        resolve(job, {'location': {'kind': 'task', 'path': name}})
    with _locked(job):
        data = read(job)
        changed = False
        for row in data['entries'].values():
            loc = row.get('location', {})
            if row.get('producer') == 'exports' and loc.get('kind') == 'task' and loc.get('path') in names:
                if row.get('active', True):
                    row['active'] = False
                    changed = True
        if changed:
            _write(job, data)


def index_export(job, item, *, replace=False):
    if item.get('active') is False:
        retire_exports(job, [item['file']])
        return []
    path = resolve(job, {'location': {'kind': 'task', 'path': item['file']}})
    role = _role(item['file'])
    key = _digest(['exports', role, location(job, path)])[:32]
    # Missing/changed legacy exports still have useful receipt evidence. Do not
    # parse their current contents or adopt new attached audio while rebuilding.
    needs_links = role == 'editor_project' and (replace or key not in read(job)['entries'])
    can_read = needs_links and path.is_file() and (replace or file_hash(path) == item['sha256'])
    links = _index_project_audio(job, path, accept=replace) if can_read else []
    parents = (('separation',) if _role(item['file']) == 'audio' else
               ('midi', 'separation', 'calibration') if path.suffix.lower() == '.svp' else ('midi', 'separation'))
    return register(job, [{'path': path, 'role': _role(item['file']), 'expected': item['sha256']}],
                    'exports', signature=item.get('input_signature'), replace=replace, parents=parents, links=links)


def _project_audio_paths(path):
    if path.stat().st_size > 64 * 1024**2: raise ContractError('工程文件过大。')
    base = path.parent
    if path.suffix.lower() == '.svp':
        from calibration_session import project_data
        value = project_data(path)
        names = [t.get('mainRef', {}).get('audio', {}).get('filename') for t in value.get('tracks', [])
                 if t.get('mainRef', {}).get('isInstrumental')]
    elif path.suffix.lower() == '.ustx':
        from project_exports import read_ustx
        names = [part.get('relative_path') or part.get('file_path') for part in read_ustx(path).get('wave_parts', [])]
    elif path.suffix.lower() == '.vsqx':
        import xml.etree.ElementTree as ET
        names = [element.text for element in ET.parse(path).iter() if element.tag.rsplit('}', 1)[-1] == 'filePath']
        base = path.parent / (path.stem + '.wavparts')
    else:
        return []
    result = set()
    for name in names:
        if not isinstance(name, str) or not name.strip(): continue
        resource = Path(name)
        resolved = _safe(resource if resource.is_absolute() else base / resource)
        if resolved == path: raise ContractError('工程的音频引用不能指向工程自身。')
        result.add(resolved)
    return sorted(result)


def _index_project_audio(job, path, *, accept):
    known = read(job)['entries']
    specs = []
    for audio in _project_audio_paths(path):
        expected = None
        if not accept:
            expected = next((row['sha256'] for row in known.values()
                             if resolve(job, row) == audio and row['role'] in ('audio','project_audio')), None)
        specs.append({'path':audio, 'role':'project_audio', 'expected':expected, 'retention':'cache'})
    return register(job, specs, 'project_reference', replace=accept, parents=()) if specs else []


def reconcile(job):
    """Migrate known producer receipts, including successfully committed partial outputs."""
    job = Path(job).resolve()
    read(job)  # Refuse an unsupported/corrupt index before writing anything.
    status = _json(job / 'status.json')
    request = _json(job / 'request.json')
    from pipeline import requirements
    for stage in STAGES:
        if stage == 'midi':
            for voice in ('vocals', 'lead', 'backing'):
                if (job / 'midi' / ('' if voice == 'vocals' else voice) / 'report.json').is_file():
                    index_voice(job, voice)
        row = status.get('steps', {}).get(stage, {})
        if row.get('state') == 'done':
            files = requirements(stage, request)
            expected = row.get('receipt', {}).get('sha256', {})
            available = [name for name in files if name in expected or (job / name).is_file()]
            index_stage(job, stage, available, receipt=row.get('receipt'))
    for item in _json(job / 'exports/receipts.json').values():
        if isinstance(item, dict) and isinstance(item.get('file'), str) and _sha(item.get('sha256')):
            index_export(job, item)
    for row in _json(job / 'expression-result.json').get('tracks', {}).values():
        if _sha(row.get('sha256')):
            register(job, [{'path':app_path(row['project']), 'role':'calibrated_project', 'expected':row['sha256']}],
                     'calibration_result', replace=False, parents=('audio','midi'))
    report = _json(job / 'subtitles/report.json')
    if report.get('sha256'):
        register(job, [{'path': resolve(job, {'location': {'kind': 'task', 'path': name}}),
                        'role': 'subtitles', 'expected': digest} for name, digest in report['sha256'].items()],
                 'subtitles', replace=False)
    # User selections live outside the rebuildable artifact index.
    meta = _metadata(job)
    bindings = meta.get('bindings', {})
    publication = meta.get('publication')
    sessions = meta.get('calibration_sessions', {})
    if bindings or publication or sessions:
        with _locked(job):
            data = read(job)
            def restore(key, row):
                previous = data['entries'].get(key)
                if not previous or previous.get('recorded_at', 0) < row.get('recorded_at', 0):
                    data['entries'][key] = row
            for binding in [*bindings.values(), *sessions.values(), *([publication] if publication else [])]:
                key, row = binding['id'], binding['entry']
                resolve(job, row)
                restore(key, row)
                for key, row in binding.get('files', {}).items():
                    restore(key, row)
            if publication and publication.get('files'):
                for key, row in publication['files'].items():
                    resolve(job, row)
                    data['entries'].setdefault(key, row)
            elif publication:
                bundle = resolve(job, publication['entry']).parent
                for item in _json(bundle / 'cleanup-inventory.json').get('entries', []):
                    name = item['file']
                    if Path(name).name != name or name in ('.', '..'): raise ContractError('发布产物路径无效。')
                    if name == 'manifest.json': continue
                    k, row = _entry(job, bundle / name, 'publication_asset', 'publication', expected=item['sha256'])
                    data['entries'].setdefault(k, row)
            _write(job, data)
    return read(job)


def rebuild(job):
    """Explicit recovery: preserve the old index and rebuild from producer evidence."""
    job = Path(job).resolve()
    from task_store import locked
    if locked(job): raise ContractError('任务正在运行，暂不能重建清单。')
    original = job / 'artifacts.json'
    backup = job / ('artifacts.backup-' + str(time.time_ns()) + '.json')
    with _locked(job):
        if original.exists(): original.replace(backup)
    try:
        reconcile(job)
    except BaseException:
        with _locked(job):
            if backup.exists(): backup.replace(original)
        raise
    return backup if backup.exists() else None


def inspect(job, *, verify=True):
    data = read(job)
    results, own = {}, {}
    for key, entry in data['entries'].items():
        if not entry.get('active', True): continue
        try:
            path = resolve(job, entry)
            stat = path.stat()
            own[key] = ('modified' if not path.is_file() or (verify and file_hash(path) != entry['sha256'])
                        else 'verified' if verify else 'unchecked')
            if not verify and stat.st_size != entry['bytes']: own[key] = 'modified'
        except FileNotFoundError:
            own[key] = 'missing'
        except (OSError, ValueError):
            own[key] = 'unreadable'
    def state(key, visiting):
        if key in results: return results[key]
        if key in visiting: return 'invalid_dependency'
        entry = data['entries'].get(key)
        if not entry or key not in own: return 'missing_dependency'
        value = own[key]
        if value not in ('verified', 'unchecked'):
            results[key] = value; return value
        stage = data['stages'].get(entry['producer'], {})
        if stage.get('state') == 'invalidated' or (stage.get('generation')
                and stage.get('generation') != entry.get('generation')):
            value = 'stale'
        for dep in entry['dependencies']:
            other = data['entries'].get(dep['id'])
            if (not other or other['revision'] != dep['revision']
                    or state(dep['id'], visiting | {key}) not in ('verified', 'unchecked')):
                value = 'stale'; break
        results[key] = value
        return value
    for key in own: state(key, set())
    return [{'id': key, **data['entries'][key], 'state': value,
             'path': str(resolve(job, data['entries'][key]))} for key, value in results.items()]


def bind(job, kind, path, *, validate=True, dependencies=None):
    """Explicitly accept a user's completed file without changing automatic outputs."""
    if kind not in ('project', 'mixdown', 'video'):
        raise ContractError('未知人工产物类型。')
    job, path = Path(job).resolve(), _safe(path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise ContractError('请选择存在且非空的文件。')
    if validate:
        if kind == 'video':
            from publication import inspect_video
            inspect_video(path)
        elif kind == 'mixdown':
            import numpy as np
            import soundfile as sf
            info = sf.info(str(path))
            if info.frames <= 0 or info.samplerate <= 0: raise ContractError('Mixdown 音频为空。')
            for block in sf.blocks(str(path), blocksize=65536, dtype='float32', always_2d=True):
                if not np.isfinite(block).all(): raise ContractError('Mixdown 含有无效采样。')
        elif path.suffix.lower() == '.svp':
            if path.stat().st_size > 64 * 1024**2: raise ContractError('工程文件过大。')
            from calibration_session import project_data
            value = project_data(path)
            if not any(track.get('mainGroup', {}).get('notes') for track in value.get('tracks', [])
                       if not track.get('mainRef', {}).get('isInstrumental')):
                raise ContractError('工程中没有演唱音符。')
        elif path.suffix.lower() in ('.ustx', '.vsqx'):
            if path.stat().st_size > 64 * 1024**2: raise ContractError('工程文件过大。')
            if path.suffix.lower() == '.ustx':
                from project_exports import read_ustx
                count = sum(len(part.get('notes', [])) for part in read_ustx(path).get('voice_parts', []))
            else:
                import xml.etree.ElementTree as ET
                count = sum(element.tag.rsplit('}', 1)[-1] == 'note' for element in ET.parse(path).iter())
            if count <= 0: raise ContractError('工程中没有音符。')
        else:
            raise ContractError('请选择 SVP、USTX 或 VSQX 工程。')
    key, entry = _entry(job, path, 'tuned_project' if kind == 'project' else kind, 'human')
    links = _index_project_audio(job, path, accept=True) if kind == 'project' else []
    with _locked(job):
        data, meta = read(job), _metadata(job)
        bindings = meta.setdefault('bindings', {})
        if dependencies is None:
            # A deliberately edited score may replace a generated project. Bind
            # to source and MIDI, not the mutable generated editor project.
            producers = ('audio', 'midi') if kind == 'project' else ('audio', 'separation', 'midi') if kind == 'mixdown' else ('audio',)
            deps = [{'id': k, 'revision': row['revision']} for k, row in data['entries'].items()
                    if row['producer'] in producers and row['role'] in ('audio', 'midi') and row.get('active', True)]
            prior = bindings.get({'mixdown': 'project', 'video': 'mixdown'}.get(kind))
            if prior: deps.append({'id': prior['id'], 'revision': prior['entry']['revision']})
        else:
            deps = dependencies
        deps.extend({'id': k, 'revision': data['entries'][k]['revision']} for k in links)
        entry['dependencies'] = deps
        old = bindings.get(kind)
        if old and old['id'] != key and old['id'] in data['entries']:
            data['entries'][old['id']]['active'] = False
        data['entries'][key] = entry
        bindings[kind] = {'id': key, 'entry': entry, 'files': {k:data['entries'][k] for k in links}}
        # Authoritative user selection first; reconcile repairs an interrupted index write.
        atomic_json(job / 'task.json', meta)
        _write(job, data)
    return key


def update_metadata(job, **values):
    with _locked(job):
        meta = _metadata(job)
        meta.update(values)
        atomic_json(Path(job) / 'task.json', meta)


def unbind(job, kind):
    if kind not in ('project', 'mixdown', 'video'): raise ContractError('未知人工产物类型。')
    with _locked(job):
        data, meta = read(job), _metadata(job)
        binding = meta.get('bindings', {}).pop(kind, None)
        if binding and binding['id'] in data['entries']: data['entries'][binding['id']]['active'] = False
        if kind == 'video':
            meta.pop('publication', None)
            for row in data['entries'].values():
                if row['producer'] == 'publication': row['active'] = False
        atomic_json(Path(job) / 'task.json', meta)
        _write(job, data)


def register_calibration(job, request):
    specs = []
    profile = request.get('reference_calibration')
    if profile:
        path = app_path(profile)
        from expression_calibration import read_profile
        value = read_profile(path)
        specs.append({'path': path, 'role': 'calibration_profile', 'retention': 'reference'})
        for row in value['tracks'].values():
            for path in (row['baseline'], *(p['audio'] for p in row['probes'])):
                specs.append({'path': Path(path), 'role': 'calibration_audio', 'retention': 'reference'})
    for row in request.get('retained_expression', {}).get('tracks', {}).values():
        path = resolve(job, {'location': {'kind': 'task', 'path': row['file']}})
        specs.append({'path': path, 'role': 'retained_expression', 'expected': row['sha256'], 'retention': 'reference'})
    if specs:
        register(job, specs, 'calibration', replace=False)


def register_session(session):
    """Link an SV render handoff to its actual source task, when one exists."""
    session = Path(session).resolve()
    spec = _json(session)
    reference = Path(spec['reference']).resolve()
    job = reference.parent.parent
    if reference != job/'audio'/(spec['voice']+'.wav') or not (job/'request.json').is_file(): return
    keys = register(job, [{'path':session, 'role':'calibration_session', 'retention':'reference'},
                   {'path':Path(spec['project']), 'role':'calibration_input', 'expected':spec['project_sha256'], 'retention':'reference'},
                   {'path':reference, 'role':'calibration_input', 'expected':spec['reference_sha256'], 'retention':'reference'}],
             'calibration_session', parents=('audio','midi'))
    with _locked(job):
        data, meta = read(job), _metadata(job)
        entry = data['entries'][keys[0]]
        entry['dependencies'].extend({'id': k, 'revision': data['entries'][k]['revision']} for k in keys[1:])
        meta.setdefault('calibration_sessions', {})[keys[0]] = {'id':keys[0], 'entry':entry,
            'files':{k:data['entries'][k] for k in keys[1:]}}
        if len(meta['calibration_sessions']) > 128: raise ContractError('校准记录过多，请另建任务。')
        atomic_json(job/'task.json', meta)
        _write(job, data)


def selected(job, kind, *, verify=True):
    binding = _metadata(job).get('bindings', {}).get(kind)
    if not binding: return None
    rows = {item['id']: item for item in inspect(job, verify=verify)}
    row = rows.get(binding['id'])
    if not row or row['state'] not in ('verified', 'unchecked'):
        raise ContractError('已登记的文件或其依赖已改变，请在“文件与下一步”中检查并重新登记。')
    return Path(row['path'])


def handoff(job, *, verify=False, rows=None):
    status = _json(Path(job) / 'status.json')
    state = status.get('state', 'ready')
    if state == 'running':
        from task_store import locked
        return 'processing' if locked(job) else 'retry'
    if state in ('failed', 'cancelled', 'interrupted'): return 'retry'
    rows = inspect(job, verify=verify) if rows is None else rows
    meta = _metadata(job)
    active = {row['id']: row for row in rows}
    for binding in meta.get('bindings', {}).values():
        if active.get(binding['id'], {}).get('state') not in ('verified', 'unchecked'):
            return 'needs_review'
    publication = meta.get('publication', {})
    if publication:
        entry = active.get(publication.get('id'), {})
        if entry.get('state') not in ('verified', 'unchecked'): return 'needs_review'
        return 'awaiting_publish' if 'waiting_review' in publication.get('platforms', {}).values() else 'awaiting_upload'
    for kind, action in (('video', 'video_ready'), ('mixdown', 'mixdown_ready')):
        if kind in meta.get('bindings', {}): return action
    if state == 'partial': return 'continue'
    if state == 'done':
        if any(row['role'] == 'midi' and row['state'] in ('verified', 'unchecked') for row in rows):
            return 'awaiting_tuning'
        if 'midi' in status.get('planned_steps', ()) or status.get('steps', {}).get('midi', {}).get('state') == 'done':
            return 'needs_review'
        return 'audio_ready'
    return 'ready'


def register_publication(job, bundle):
    bundle = _safe(bundle)
    manifest = _json(bundle / 'manifest.json')
    if Path(manifest['source_job']).resolve() != Path(job).resolve():
        raise ContractError('发布资料与来源任务不一致。')
    video = Path(manifest['video']['path'])
    if file_hash(video) != manifest['video']['sha256']:
        raise ContractError('成品视频在准备发布期间发生变化。')
    bind(job, 'video', video, validate=False)
    inventory = _json(bundle / 'cleanup-inventory.json')
    assets = []
    for item in inventory.get('entries', []):
        name = item['file']
        if Path(name).name != name or name in ('.', '..'): raise ContractError('发布产物路径无效。')
        if name == 'manifest.json': continue
        assets.append(_entry(job, bundle / name, 'publication_asset', 'publication', expected=item['sha256']))
    sources = []
    for name in ('source-info.json', 'upstream-info.json', '发布简介草稿.txt'):
        path = Path(job) / name
        if path.is_file(): sources.append(_entry(job, path, 'publication_source', 'publication'))
    key, entry = _entry(job, bundle / 'manifest.json', 'publication', 'publication')
    with _locked(job):
        data, meta = read(job), _metadata(job)
        binding = meta['bindings']['video']
        entry['dependencies'] = [{'id': binding['id'], 'revision': binding['entry']['revision']}]
        entry['dependencies'].extend({'id': k, 'revision': row['revision']} for k, row in assets)
        entry['dependencies'].extend({'id': k, 'revision': row['revision']} for k, row in sources)
        for row in data['entries'].values():
            if row['producer'] == 'publication': row['active'] = False
        data['entries'].update(assets)
        data['entries'].update(sources)
        data['entries'][key] = entry
        meta['publication'] = {'id': key, 'entry': entry, 'platforms': {}, 'final_publish': 'human_only',
                               'files': dict(assets + sources)}
        atomic_json(Path(job) / 'task.json', meta)
        _write(job, data)


def publication_state(bundle, platform, state):
    """Mirror upload assistance only. A website's final publish remains human controlled."""
    manifest = _json(Path(bundle) / 'manifest.json')
    if not manifest.get('source_job'): return
    job = Path(manifest['source_job'])
    if not (job / 'task.json').is_file(): return
    with _locked(job):
        meta = _metadata(job)
        item = meta.get('publication', {})
        if not item or resolve(job, item['entry']) != (Path(bundle) / 'manifest.json').resolve(): return
        states = item.setdefault('platforms', {})
        if not (states.get(platform) == 'waiting_review' and state in ('cancelled', 'login_window_closed')):
            states[platform] = state
        atomic_json(job / 'task.json', meta)


def protected_references(jobs):
    """Known live references for cleanup, including old calibration/retained inputs.

    On a broken index we refuse cleanup instead of guessing that a path is unused.
    External references are read-only; they never extend the deletion allowlist.
    """
    result = []
    for job in jobs:
        job = Path(job).resolve()
        data, meta = read(job), _metadata(job)
        entries = dict(data['entries'])
        for binding in meta.get('bindings', {}).values():
            entries[binding['id']] = binding['entry']
            entries.update(binding.get('files', {}))
        publication = meta.get('publication')
        if publication:
            entries[publication['id']] = publication['entry']
            entries.update(publication.get('files', {}))
        for session in meta.get('calibration_sessions', {}).values():
            entries[session['id']] = session['entry']
            entries.update(session.get('files', {}))
        pending = [k for k, e in entries.items() if e.get('active', True) and e.get('retention') != 'cache']
        visited = set()
        while pending:
            key = pending.pop()
            if key in visited or key not in entries: continue
            visited.add(key); entry = entries[key]
            result.append((resolve(job, entry), job, entry.get('role', 'reference')))
            pending.extend(dep['id'] for dep in entry.get('dependencies', []))
        request = _json(job / 'request.json')
        profile = request.get('reference_calibration')
        if profile:
            path = _safe(app_path(profile)); result.append((path, job, 'calibration_profile'))
            if path.is_file():
                from expression_calibration import read_profile
                value = read_profile(path, require_audio=False)
                for track in value['tracks'].values():
                    for value in [track['baseline'], *(p['audio'] for p in track['probes'])]:
                        p = Path(value); p = p if p.is_absolute() else path.parent / p
                        result.append((_safe(p), job, 'calibration_audio'))
        retained = request.get('retained_expression', {}).get('tracks', {})
        for item in retained.values():
            result.append((resolve(job, {'location': {'kind': 'task', 'path': item['file']}}), job, 'retained_expression'))
        expression = _json(job / 'expression-result.json')
        for item in expression.get('tracks', {}).values():
            result.append((_safe(app_path(item['project'])), job, 'calibrated_project'))
        sessions = set(list(job.glob('calibration*/session.json'))[:128])
        sessions.update(resolve(job, row) for row in entries.values()
                        if row.get('role') == 'calibration_session' and row.get('active', True))
        for session in sessions:
            value = _json(session)
            for key in ('project', 'reference'):
                if value.get(key): result.append((_safe(app_path(value[key])), job, 'calibration_input'))
            folder = value.get('render_folder')
            if folder: result.append((_safe(app_path(folder)), job, 'calibration_renders'))
    return result
