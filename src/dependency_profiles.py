"""Feature-scoped dependency checks and an install plan using original weights."""
from pathlib import Path

from common import ROOT, dependency_manifest

PACKAGE_ROOT = Path(__file__).resolve().parent

LABELS = {'core': '基础运行库', 'midi': 'MIDI 提取', 'alignment': '歌词对齐',
          'chinese': '中文歌词识别', 'japanese': '日语歌词识别', 'pitch': '音高分析（RMVPE）',
          'projects': '工程转换', 'publishing': '网页上传'}


def group(name):
    if name.startswith(('dependencies/browser/', 'vendor/playwright/', 'vendor/playwright-')): return 'publishing'
    if name.startswith('dependencies/export/'): return 'projects'
    if '/experiments/Qwen3-ASR-1.7B-dml/' in name: return 'chinese'
    if '/experiments/romajiASR/' in name: return 'japanese'
    if '/experiments/1218_hfa_model_new_dict/' in name: return 'alignment'
    if '/experiments/RMVPE/' in name: return 'pitch'
    if '/experiments/GAME-' in name: return 'midi'
    return 'core'


def _inside_root(path):
    try:
        return path.resolve().is_relative_to(PACKAGE_ROOT.resolve())
    except OSError:
        return False


def status(features=None):
    """Report target-file presence without hashing multi-gigabyte weights.

    The Chinese row additionally reports resumable cache bytes. The explicit
    ComponentsPage action performs the full pinned SHA-256 verification.
    """
    chosen = set(LABELS) if features is None else set(features)
    unknown = chosen - set(LABELS)
    if unknown:
        raise ValueError('未知的依赖功能：' + ', '.join(sorted(unknown)))
    rows = {key: {'title': LABELS[key], 'bytes': 0, 'installed_bytes': 0,
                  'partial_bytes': 0, 'resumable_bytes': 0, 'downloaded_bytes': 0,
                  'missing': [], 'files': 0, 'complete_files': 0,
                  'state': 'missing', 'feature_error': False} for key in chosen}
    for item in dependency_manifest().get('entries', []):
        key = group(item['file'])
        if key not in chosen:
            continue
        row = rows[key]
        row['bytes'] += item['bytes']
        row['files'] += 1
        path = PACKAGE_ROOT / item['file']
        if path.is_file() and not path.is_symlink() and _inside_root(path) and path.stat().st_size == item['bytes']:
            row['installed_bytes'] += item['bytes']
            row['complete_files'] += 1
        else:
            row['missing'].append(item['file'])
    if 'chinese' in chosen:
        try:
            from feature_components import status as feature_status
            detail = feature_status('chinese', root=PACKAGE_ROOT)
        except InterruptedError:
            raise
        except (OSError, ValueError, KeyError, TypeError):
            detail = {}
        if detail and detail.get('error'):
            rows['chinese']['state'] = 'error'
            rows['chinese']['feature_error'] = True
            rows['chinese']['error'] = detail.get('error')
        elif detail:
            rows['chinese'].update({
                'bytes': detail.get('bytes', rows['chinese']['bytes']),
                'installed_bytes': detail.get('complete_bytes', rows['chinese']['installed_bytes']),
                'partial_bytes': detail.get('partial_bytes', 0),
                'resumable_bytes': detail.get('resumable_bytes', 0),
                'downloaded_bytes': detail.get('downloaded_bytes', 0),
                'missing': detail.get('missing', rows['chinese']['missing']),
                'files': detail.get('files', rows['chinese']['files']),
                'complete_files': detail.get('complete_files', rows['chinese']['complete_files']),
                'present_files': detail.get('present_files', 0),
                'installed': detail.get('installed', detail.get('ready', False)),
                'invalid_files': detail.get('invalid_files', []),
                'verified': detail.get('verified', False),
                'revision': detail.get('revision'),
                'state': detail.get('state', rows['chinese']['state']),
            })
    for key, row in rows.items():
        row['ready'] = not row['missing'] and not row.get('feature_error')
        if row['ready']:
            row['state'] = 'ready'
        elif row.get('feature_error'):
            row['state'] = 'error'
        elif row.get('state') != 'damaged' and (row['downloaded_bytes'] or row.get('complete_files')):
            row['state'] = 'partial'
    return rows


def task_features(request, stages):
    """Return only the runtime groups used by this task.

    Japanese recognition never requests the Chinese Qwen group, and disabling
    lyrics recognition requests no language model at all.
    """
    features = {'core'}
    if 'midi' in stages:
        features.add('midi')
        if request.get('recognize_lyrics', True):
            language = request.get('language', 'zh')
            features.update(('alignment', 'chinese' if language == 'zh' else 'japanese'))
        from common import resolve_tools
        tools = dict(request.get('tools', {}))
        tools.setdefault('vocal2midi', 'dependencies/vocal2midi2' if request.get('midi_backend') == 'v2' else 'dependencies/vocal2midi')
        backend = Path(resolve_tools(tools)['vocal2midi'])
        if ((backend / 'backend-version.json').is_file()
                or request.get('midi_backend') == 'v2'
                or request.get('project_formats') and request.get('export_pitch')):
            features.add('pitch')
    if 'exports' in stages and request.get('project_formats'):
        features.add('projects')
    return features


def installation_plan(profile='complete'):
    selections = {'complete': set(LABELS), 'music': set(LABELS) - {'publishing'},
                  'japanese': set(LABELS) - {'chinese', 'publishing'}}
    if profile not in selections:
        raise ValueError('Unknown dependency profile')
    entries = dependency_manifest().get('entries', [])
    kept = [item for item in entries if group(item['file']) in selections[profile]]
    return {'profile': profile, 'features': sorted(selections[profile]), 'files': len(kept),
            'bytes': sum(item['bytes'] for item in kept),
            'omitted_bytes': sum(item['bytes'] for item in entries) - sum(item['bytes'] for item in kept)}
