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
print('Inference device: ' + args.device)
