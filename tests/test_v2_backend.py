"""V2 import isolation and the Japanese gap safeguard without model inference."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from v2m_runtime import configure
configure(ROOT/'dependencies/vocal2midi2')
from inference.API.hfa_api import _repair_short_word_boundaries
from inference.HubertFA.tools.align_word import Word
from inference.io.audio_io import _find_ffmpeg
from application.config import PipelineConfig
from application.pipeline import run_auto_lyric_job
import json
import tempfile
from unittest.mock import patch
import midi_bridge
import pipeline as assistant_pipeline
from common import atomic_json
assert not any(name.startswith(('PySide6','qfluentwidgets')) for name in sys.modules)
words=[Word(0,.8,'a',init_phoneme=True),Word(.8,.81,'i',init_phoneme=True),
       Word(.81,5,'SP',init_phoneme=True),Word(5,5.8,'u',init_phoneme=True)]
_repair_short_word_boundaries(words,max_gap_sec=.15)
assert words[1].end==.81 and len(words)==4
assert Path(_find_ffmpeg()).is_file()
assert PipelineConfig.__dataclass_fields__['output_pitch_curve'].default is False

# Exercise the memory-dependent scheduling branch without allocating another
# multi-GB model on low-memory test machines. Real inference has a separate QA.
with tempfile.TemporaryDirectory(dir=ROOT/'work', prefix='native-session-') as temporary:
    job = Path(temporary)
    request = {'language':'zh', 'midi_backend':'v2', 'recognize_lyrics':True,
               'tools':{'vocal2midi':str(ROOT/'dependencies/vocal2midi2'), 'midi_device':'cpu'}}
    atomic_json(job/'request.json', request)
    session = type('Session', (), {'close': lambda self: setattr(self, 'closed', True)})()
    session.closed = False
    received = []
    def voice_run(path, voice, asr_session=None):
        assert path == job
        received.append((voice, asr_session))
    with patch('runtime_devices.available_memory', return_value=20*1024**3), \
         patch('application.pipeline.open_asr_session', return_value=session) as opened, \
         patch.object(midi_bridge, 'run', side_effect=voice_run):
        midi_bridge.run_many(job, ['lead','backing'])
    assert received == [('lead',session),('backing',session)] and session.closed
    assert opened.call_count == 1
    worker = json.loads((job/'midi/worker-report.json').read_text(encoding='utf-8'))
    assert worker['asr_reused'] and set(worker['seconds']) == {'lead','backing'}

    received.clear()
    with patch('runtime_devices.available_memory', return_value=4*1024**3), \
         patch('application.pipeline.open_asr_session') as opened, \
         patch.object(midi_bridge, 'run', side_effect=voice_run):
        midi_bridge.run_many(job, ['lead','backing'])
    assert received == [('lead',None),('backing',None)] and not opened.called

    for failure in ('error', 'cancel'):
        session.closed = False
        def failing_run(path, voice, asr_session=None):
            if voice == 'lead' and failure == 'cancel':
                (job/'cancel.flag').touch()
            if voice == 'backing':
                raise RuntimeError('recognizer failure')
        with patch('runtime_devices.available_memory', return_value=20*1024**3), \
             patch('application.pipeline.open_asr_session', return_value=session), \
             patch.object(midi_bridge, 'run', side_effect=failing_run):
            try:
                midi_bridge.run_many(job, ['lead','backing'])
                raise AssertionError('Worker failure was ignored')
            except (RuntimeError, InterruptedError):
                pass
        assert session.closed
        worker = json.loads((job/'midi/worker-report.json').read_text(encoding='utf-8'))
        assert set(worker['seconds']) == {'lead'}, 'The completed voice must survive a later failure'
        (job/'cancel.flag').unlink(missing_ok=True)

    class CapturedCommand(Exception):
        pass
    # Chinese GPU keeps isolated voices; Japanese and CPU can reuse native
    # sessions. Capture dispatch before any model loads or output writes.
    for language, device, backend, lyrics, batch in [
        ('zh','dml','v2',True,False), ('zh','cpu','v2',True,True),
        ('ja','dml','v2',True,True), ('zh','dml','v2',False,True),
        ('ja','dml','v1',True,False),
    ]:
        case = dict(request, language=language, midi_backend=backend, recognize_lyrics=lyrics,
                    voice_mode='dual', tools={'python':sys.executable,'midi_device':device})
        commands = []
        def capture(command, *args, **kwargs):
            commands.append(command)
            raise CapturedCommand()
        with patch.object(assistant_pipeline, 'completed_voice', return_value=False), \
             patch.object(assistant_pipeline, 'run_process', side_effect=capture):
            try:
                assistant_pipeline.midi(job,case)
                raise AssertionError('The dispatcher did not start a voice')
            except CapturedCommand:
                pass
        assert ('--voices' in commands[0]) is batch, (language,device,backend,lyrics)

# Run the real bridge/commit path with a deterministic native-engine fixture;
# preserve a MIDI edited in place without loading large models in this test.
with tempfile.TemporaryDirectory(dir=ROOT/'work',prefix='native-edit-') as temporary:
    import numpy as np
    import soundfile as sf
    import mido
    from common import load_config
    job=Path(temporary)
    (job/'audio').mkdir()
    sf.write(job/'audio/vocals.wav',np.full(44100,.1,dtype='float32'),44100,subtype='FLOAT')
    tools=load_config(False)
    tools.update(vocal2midi='dependencies/vocal2midi2',midi_device='cpu',cpu_threads=4)
    request={'language':'zh','midi_backend':'v2','recognize_lyrics':True,'project_formats':[],
             'tools':tools,'midi_steps':8,'zh_lyric_mode':'hanzi'}
    atomic_json(job/'request.json',request)
    def native_fixture(cfg):
        name=Path(cfg.output_filename).stem
        midi=mido.MidiFile(charset='utf8')
        midi.tracks.append(mido.MidiTrack([mido.MetaMessage('set_tempo',tempo=500000),
            mido.MetaMessage('lyrics',text='测',time=240),mido.Message('note_on',note=60,velocity=80),
            mido.Message('note_off',note=60,time=480)]))
        midi.save(str(cfg.output_dir/(name+'.mid')))
        for suffix in ('txt','csv'):(cfg.output_dir/(name+'.'+suffix)).write_text('generated',encoding='utf-8')
    with patch('application.pipeline.run_auto_lyric_job',side_effect=native_fixture):
        midi_bridge.run(job)
        original=job/'midi/vocals.mid'
        edited=mido.MidiFile(str(original),charset='utf8')
        for message in edited.tracks[0]:
            if message.type in ('note_on','note_off'):message.note=64
        edited.save(str(original))
        edited_bytes=original.read_bytes()
        midi_bridge.run(job)
    committed=json.loads((job/'midi/report.json').read_text(encoding='utf-8'))
    assert (job/committed['preserved_copies']['mid']).read_bytes()==edited_bytes
    assert len(committed['output_sha256'])==3

print('PASS: V2 headless runtime, Japanese gap safeguard, memory-aware reuse, failure/cancel cleanup and device isolation')
