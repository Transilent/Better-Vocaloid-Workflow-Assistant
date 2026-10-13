"""Feature-scoped dependency checks and an install plan using original weights."""
from pathlib import Path
from common import ROOT, dependency_manifest
PACKAGE_ROOT = Path(__file__).resolve().parent

LABELS = {'core': '基础运行库', 'midi': 'MIDI 提取', 'alignment': '歌词对齐',
          'chinese': '中文歌词识别', 'japanese': '日语歌词识别', 'pitch': '音高曲线',
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


def status(features=None):
    chosen = set(features or LABELS)
    rows = {key: {'title': LABELS[key], 'bytes': 0, 'missing': [], 'files': 0} for key in chosen}
    for item in dependency_manifest().get('entries', []):
        key = group(item['file'])
        if key not in chosen: continue
        row = rows[key]; row['bytes'] += item['bytes']; row['files'] += 1
        path = PACKAGE_ROOT / item['file']
        if not path.is_file() or path.stat().st_size != item['bytes']:
            row['missing'].append(item['file'])
    for row in rows.values(): row['ready'] = not row['missing']
    return rows


def task_features(request, stages):
    features = {'core'}
    if 'midi' in stages:
        features.add('midi')
        if request.get('recognize_lyrics', True):
            features.update(('alignment', 'chinese' if request['language'] == 'zh' else 'japanese'))
        if request.get('project_formats') and request.get('export_pitch'): features.add('pitch')
    if 'exports' in stages and request.get('project_formats'): features.add('projects')
    return features


def installation_plan(profile='complete'):
    selections = {'complete': set(LABELS), 'music': set(LABELS) - {'publishing'},
                  'japanese': set(LABELS) - {'chinese', 'publishing'}}
    if profile not in selections: raise ValueError('Unknown dependency profile')
    entries = dependency_manifest().get('entries', [])
    kept = [item for item in entries if group(item['file']) in selections[profile]]
    return {'profile': profile, 'features': sorted(selections[profile]), 'files': len(kept),
            'bytes': sum(item['bytes'] for item in kept),
            'omitted_bytes': sum(item['bytes'] for item in entries) - sum(item['bytes'] for item in kept)}
