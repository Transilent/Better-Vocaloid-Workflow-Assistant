"""Missing-language isolation, original-weight repair and explicit device settings."""
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

APP=Path(__file__).resolve().parents[1];sys.path.insert(0,str(APP))
import check_dependencies as checks
import feature_components as components
import optional_components
import dependency_profiles as profiles
from common import atomic_json, native_path
from runtime_devices import prepare_environment, providers, adapters
from v2m_runtime import configure
configure(APP/'dependencies/vocal2midi2')
from application.config import PipelineConfig
from application.pipeline import _validate_model_paths
from inference.qwen3asr_dml import llama

assert (Path(llama.__file__).parent/'bin').resolve()==(APP/'dependencies/vocal2midi/inference/qwen3asr_dml/bin').resolve()
assert profiles.installation_plan('japanese')['omitted_bytes']>4*1024**3
before=dict(os.environ)
try:
    chosen=next((a for a in adapters() if not a.is_software and a.dedicated_vram_bytes>=1024**3),None)
    if chosen:
        mode,values=providers({'dml_adapter':chosen.index,'cpu_threads':4},'dml')
        assert mode=='dml' and values[0][1]['device_id']==str(chosen.index)
    prepare_environment({'dml_adapter':'auto','cpu_threads':0})
    assert 'BVWA_CPU_THREADS' not in os.environ and 'BVWA_DML_ADAPTER' not in os.environ
finally:
    for key in ('BVWA_CPU_THREADS','BVWA_DML_ADAPTER'):
        if key in before:os.environ[key]=before[key]
        else:os.environ.pop(key,None)

payload=b'original model fixture'*1024
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
try:
    with tempfile.TemporaryDirectory(dir=native_path(APP/'work'),prefix='fp-') as tmp:
        root=Path(tmp)
        native=root/'native';native.mkdir()
        audio=root/'audio.wav';audio.write_bytes(b'not consumed by model validation')
        kwargs={'audio_path':str(audio),'output_filename':'audio.wav','output_dir':root,
                'game_model_dir':str(native),'hfa_model_dir':str(native),'asr_model_path':str(root/'missing-chinese'),
                'phoneme_asr_model_path':str(native),'language':'ja','japanese_asr_engine':'romaji'}
        kwargs.update(device='cpu',ts=[0.,.5],alignment_engine='hfa')
        _validate_model_paths(PipelineConfig(**kwargs))
        _validate_model_paths(PipelineConfig(**dict(kwargs,language='zh',output_lyrics=False)))
        try:
            _validate_model_paths(PipelineConfig(**dict(kwargs,language='zh')))
            raise AssertionError('Missing required Chinese weights accepted')
        except Exception as error:
            assert 'ASR model path' in str(error) or 'Model path validation' in str(error)
        (root/'dependencies').mkdir()
        name='dependencies/vocal2midi/experiments/Qwen3-ASR-1.7B-dml/payload.bin'
        entry={'file':name,'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
        atomic_json(root/'dependencies/manifest.json',{'entries':[entry]})
        tools={'python':str(audio),'ffmpeg':str(audio)}
        with patch.object(checks,'ROOT',root),patch.object(checks,'load_config',return_value=tools):
            assert checks.check(features={'core'})['state']=='passed'
            assert checks.check(features={'chinese'})['state']=='failed'
        revision='a'*40
        spec={'revision':revision,'bytes':len(payload),
              'files':[{**entry,'url':f'https://huggingface.co/example/fixture/resolve/{revision}/payload.bin'}]}
        actual_download=optional_components.download
        def local_download(item,folder,progress,check=None,source='auto'):
            # Keep manifest validation realistic while serving fixture bytes
            # locally through the real size/SHA/resume downloader.
            return actual_download({**item,'url':f'http://127.0.0.1:{server.server_port}/model'},
                                   folder,progress,check,source=source)
        with patch.object(components,'ROOT',root),patch.object(components,'specification',return_value=spec), \
             patch.object(components,'dependency_manifest',return_value={'entries':[entry]}), \
             patch.object(optional_components,'download',side_effect=local_download):
            components.install('chinese',lambda *args:None,source='direct')
            target=root/name;assert target.read_bytes()==payload
            modified=target.stat().st_mtime_ns
            components.install('chinese',lambda *args:None,source='direct')
            assert target.stat().st_mtime_ns==modified,'An intact original model must remain unchanged'
            target.write_bytes(b'x'*len(payload))
            try:
                components.install('chinese',lambda *args:None,check=lambda:True,source='direct')
                raise AssertionError('Cancel ignored')
            except InterruptedError:pass
            assert target.read_bytes()==b'x'*len(payload)
            components.install('chinese',lambda *args:None,source='direct')
            assert target.read_bytes()==payload
            with patch.object(components,'specification',return_value={**spec,'files':[{**spec['files'][0],'sha256':'0'*64}]}):
                try:
                    components.install('chinese',lambda *args:None,source='direct')
                    raise AssertionError('Changed weight identity accepted')
                except ValueError:pass
finally:
    server.shutdown();server.server_close()
print('PASS: Japanese/notes-only independence, direct shared DLLs, adapter selection and pinned original-model repair/cancel')
