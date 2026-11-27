import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description='Set the portable inference device')
parser.add_argument('device', choices=['cpu', 'webgpu', 'dml'])
args = parser.parse_args()
target_device = 'webgpu' if args.device == 'dml' else args.device
path = ROOT / 'config.json'
data = json.loads(path.read_text(encoding='utf-8-sig'))
data.update(separation_device=target_device, midi_device=target_device)
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
state_path = ROOT/'cache/workspace.json'
if state_path.exists():
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
        saved = state.get('workflow', {})
        options = saved.get('options', {})
        if isinstance(options, dict) and options:
            options.update(separation_device=target_device, midi_device=target_device)
            saved['preset'] = '自定义'
        quick = state.get('quick_preparation', {})
        quick_options = quick.get('options', {}) if isinstance(quick, dict) else {}
        if isinstance(quick, dict) and quick.get('version') == 1 and isinstance(quick_options, dict):
            quick_options.update(separation_device=target_device, midi_device=target_device)
            quick['options'] = quick_options
        if isinstance(state, dict):
            temp = state_path.with_suffix('.json.tmp')
            temp.write_text(json.dumps(state, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            temp.replace(state_path)
    except (ValueError, AttributeError):
        pass
print('Inference device: ' + target_device)
