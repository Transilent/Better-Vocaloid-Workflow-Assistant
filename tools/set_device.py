import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description='Set the portable inference device')
parser.add_argument('device', choices=['cpu', 'dml'])
args = parser.parse_args()
path = ROOT / 'config.json'
data = json.loads(path.read_text(encoding='utf-8-sig'))
data.update(separation_device=args.device, midi_device=args.device)
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
state_path = ROOT/'cache/workspace.json'
if state_path.exists():
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
        saved = state.get('workflow', {})
        options = saved.get('options', {})
        if isinstance(options, dict) and options:
            options.update(separation_device=args.device, midi_device=args.device)
            saved['preset'] = '自定义'
            temp = state_path.with_suffix('.json.tmp')
            temp.write_text(json.dumps(state, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            temp.replace(state_path)
    except (ValueError, AttributeError):
        pass
print('Inference device: ' + args.device)
