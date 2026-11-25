"""Local singing transcription and forced-alignment adapters."""
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
import sys
import unicodedata

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from chorus_project import ALIGNMENT_ENGINES


TIFA_FILES = (
    'config.json', 'vocabulary.json', 'spectrogram.onnx', 'prepare.onnx',
    'score.onnx', 'select.onnx', 'model.onnx', 'dictionaries/japanese_dict_full.txt',
    'dictionaries/ds-zh-pinyin-lite.txt', 'dictionaries/ds_cmudict-07b.txt',
)
ALIGNER_LABELS = {'tifa': 'TiFA', 'hubertfa': 'HubertFA'}


def _alignment_files(engine):
    from common import ROOT

    if engine == 'tifa':
        return ROOT / 'dependencies/vocal2midi2/models/tifa-1.0-onnx', TIFA_FILES
    return ROOT / 'dependencies/vocal2midi/experiments/1218_hfa_model_new_dict', ('model.onnx',)


def alignment_ready(engine):
    folder, names = _alignment_files(engine)
    return all((folder / name).is_file() for name in names)


def preferred_alignment(selection):
    if selection not in ALIGNMENT_ENGINES:
        raise ValueError('唱段对齐引擎无效。')
    return ('tifa' if alignment_ready('tifa') else 'hubertfa') if selection == 'auto' else selection


def alignment_source(selection):
    """Resolve automatic mode and stamp the files backing cached boundaries."""
    engine = preferred_alignment(selection)
    folder, names = _alignment_files(engine)
    stamps = []
    for name in names:
        try:
            info = (folder / name).stat()
        except FileNotFoundError:
            raise FileNotFoundError(f'{ALIGNER_LABELS[engine]} 模型不完整，请检查 {folder.name}。') from None
        stamps.append((info.st_size, info.st_mtime_ns))
    return engine, folder, tuple(stamps)


@dataclass(frozen=True)
class SpeechClip:
    track: object
    start: float
    end: float
    language: str
    text: str = ''

    @property
    def frames(self):
        return (max(0, round((self.start - self.track.offset) * self.track.rate)),
                min(self.track.frames, round((self.end - self.track.offset) * self.track.rate)))

    @property
    def key(self):
        return self.track.sha, self.frames, self.language, self.text


def normalized_lyrics(value):
    value = unicodedata.normalize('NFKC', value).casefold()
    # Compare the two kana scripts equivalently while preserving the input
    # notation for pronunciation in the model.
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in value)


def same_text(a, b):
    def canonical(value):
        return ''.join(c for c in normalized_lyrics(value) if c.isalnum() or c == 'ー')
    return bool(a and b and a.confirmed and b.confirmed and a.language == b.language
                and canonical(a.text) and canonical(a.text) == canonical(b.text))


def speech_batch(clips, operation, cache, check, progress, *, aligner='auto', source=None):
    from chorus_model_worker import model_batch
    from common import ROOT

    runtime = ROOT / 'dependencies/vocal2midi2'
    if not (runtime / 'inference').is_dir():
        raise FileNotFoundError('未找到 Vocal2Midi 本地运行库。')
    core = ROOT / 'dependencies/vocal2midi/experiments'
    rows = []
    for clip in clips:
        check()
        clip.track.check_source()
        left, right = clip.frames
        rows.append({'path': clip.track.path, 'left': left, 'right': right, 'lang': clip.language, 'text': clip.text})
    if not rows:
        return []
    spec = {'runtime': str(runtime), 'core': str(core), 'operation': operation, 'clips': rows}
    if operation == 'phones':
        engine, model, _ = source or alignment_source(aligner)
        spec.update(engine=engine, model=str(model))
        label = f'{ALIGNER_LABELS[engine]} · 提取音素'
    else:
        label = '识别歌词'
    with model_batch(__file__, spec, cache, check, progress, [f'{label} · {clip.track.name}' for clip in clips],
                     f'{label}运行失败') as folder:
        results = json.loads((folder / 'results.json').read_text(encoding='utf-8'))
    if len(results) != len(clips):
        raise RuntimeError('歌词分析返回的唱段数不一致。')
    for clip in clips:
        clip.track.check_source()
    return results


def _read_clip(row, path, rate, *, subtype='FLOAT'):
    import numpy as np
    import soundfile as sf
    import soxr

    with sf.SoundFile(row['path']) as stream:
        if row['right'] <= row['left']:
            return False
        stream.seek(row['left'])
        samples = stream.read(row['right'] - row['left'], dtype='float32', always_2d=True)
        source_rate = stream.samplerate
    if not len(samples) or not np.isfinite(samples).all():
        return False
    mono = samples.mean(axis=1)
    powers = np.mean(samples * samples, axis=0)
    if np.mean(mono * mono) < powers.max() * .2:
        mono = samples[:, int(powers.argmax())]
    if float(np.max(np.abs(mono), initial=0.)) < .0003:
        return False
    if source_rate != rate:
        mono = soxr.resample(mono, source_rate, rate)
    sf.write(path, mono, rate, subtype=subtype)
    return True


@lru_cache(maxsize=4)
def _g2p(language, runtime):
    if language == 'ja':
        from inference.LyricFA.tools.JaG2p import JaG2p

        kashi = runtime / 'models/kashi-g2p-onnx'
        engine = 'kashi-g2p-onnx' if (kashi / 'model.onnx').is_file() else 'pyopenjtalk'
        return JaG2p(engine=engine, model_dir=kashi if engine == 'kashi-g2p-onnx' else None)
    if language in ('zh', 'yue'):
        from inference.LyricFA.tools.ZhG2p import ZhG2p

        return ZhG2p('mandarin' if language == 'zh' else 'cantonese')
    return None


def _lab(text, language, runtime):
    # The dictionary-ready romaji/pinyin spelling can be edited directly.
    text = unicodedata.normalize('NFKC', text)
    if re.fullmatch(r"[a-zA-Z0-9'\s-]+", text):
        return ' '.join(text.lower().split())
    engine = _g2p(language, runtime)
    if engine:
        return engine.convert(text, include_tone=False, convert_number=True)
    return ' '.join(re.findall(r"[a-z]+(?:['-][a-z]+)*", text.casefold().replace('’', "'")))


def _phones(words):
    excluded = {'sp', 'sil', 'pau', 'ap', 'ep', 'gs', 'br'}
    return [[phone.text, round(phone.start, 6), round(phone.end, 6)] for word in words
            for phone in word.phonemes if phone.text.casefold() not in excluded and phone.end > phone.start]


def _align(request, runtime):
    engine = request['engine']
    route = ALIGNER_LABELS[engine]
    model_dir = Path(request['model'])
    folder = Path(request['folder'])
    if engine == 'tifa':
        from inference.TiFA.aligner import run_tifa_fa
        from inference.TiFA.g2p_pipeline import build_g2p_pipeline
        from inference.TiFA.runtime import load_tifa_model

        model = load_tifa_model(model_dir, device='cpu')
        kashi = runtime / 'models/kashi-g2p-onnx'
        pipeline = build_g2p_pipeline(model_dir, 'kashi-g2p-onnx' if (kashi / 'model.onnx').is_file()
                                      else 'pyopenjtalk', kashi)
        rate = model.samplerate

        def infer(wav, language, text):
            wav.with_suffix('.txt').write_text(text, encoding='utf-8')
            found, _ = run_tifa_fa(model, wav.parent, language=language, g2p_pipeline=pipeline)
            return found
    else:
        from inference.API.hfa_api import load_hfa_model, run_hubert_fa

        model = load_hfa_model(model_dir, device='cpu')
        rate = 44100

        def infer(wav, language, text):
            if language == 'yue':
                return {}
            wav.with_suffix('.lab').write_text(_lab(text, language, runtime), encoding='utf-8')
            return run_hubert_fa(model, wav.parent, language=language)

    for index, row in enumerate(request['clips']):
        (folder / 'progress.txt').write_text(str(index), encoding='utf-8')
        one = folder / f'clip-{index}'
        one.mkdir()
        wav = one / 'chunk_0.wav'
        try:
            if not _read_clip(row, wav, rate):
                yield {'route': route, 'phones': [], 'detail': '唱段没有有效录音'}
                continue
            found = infer(wav, row['lang'], row['text'])
            phones = _phones(found[wav.stem][2]) if wav.stem in found else []
            yield {'route': route, 'phones': phones,
                   'detail': '' if phones else f'{route} 未从歌词取得可用音素边界'}
        except Exception as error:
            yield {'route': route, 'phones': [], 'detail': f'{route}：{error}'[:300]}


def _recognize(request, runtime, core):
    from v2m_runtime import configure

    romaji = qwen = None
    folder = Path(request['folder'])
    wav = folder / 'asr.wav'
    try:
        for index, row in enumerate(request['clips']):
            (folder / 'progress.txt').write_text(str(index), encoding='utf-8')
            lang = row['lang']
            try:
                if not _read_clip(row, wav, 16000, subtype='PCM_16'):
                    yield {'text': '', 'route': '', 'detail': '唱段没有有效录音'}
                    continue
                if lang == 'ja' and (core / 'romajiASR/model.onnx').is_file():
                    from inference.romaji_asr.runtime import RomajiASROnnxModel
                    from inference.API.lfa_api import _phoneme_tokens_to_romaji_moras

                    if romaji is None:
                        romaji = RomajiASROnnxModel.from_model_path(core / 'romajiASR', device='cpu')
                    rows = romaji.transcribe(wav)
                    tokens = _phoneme_tokens_to_romaji_moras(rows[0]['phonemes']) if rows else []
                    yield {'text': ' '.join(tokens), 'route': 'RomajiASR'}
                    continue
                directory = core / 'Qwen3-ASR-1.7B-dml'
                if not directory.is_dir():
                    yield {'text': '', 'route': '', 'detail': '请先通过模型与组件安装歌词识别模型'}
                    continue
                if qwen is None:
                    configure(runtime)
                    from inference.qwen3asr_dml.runtime import Qwen3ASRDmlModel

                    qwen = Qwen3ASRDmlModel.from_model_path(directory, device='auto', llama_backend='auto',
                                                            chunk_size=28, max_decode_tokens=384, verbose=False)
                language = {'ja': 'Japanese', 'zh': 'Chinese', 'yue': 'Cantonese', 'en': 'English'}[lang]
                result = qwen.transcribe(wav, language=language)
                yield {'text': result.text.strip(), 'route': 'Qwen3-ASR'}
            except Exception as error:
                yield {'text': '', 'route': '', 'detail': f'歌词识别失败：{error}'[:300]}
    finally:
        if qwen is not None:
            qwen.shutdown()


def _worker(request):
    from runtime_devices import prepare_environment
    from v2m_runtime import configure

    spec = json.loads(request.read_text(encoding='utf-8'))
    spec['folder'] = str(request.parent)
    runtime, core = Path(spec['runtime']), Path(spec['core'])
    configure(runtime, pitch_only=True)
    prepare_environment({'midi_device': 'cpu', 'cpu_threads': 4, 'dml_adapter': 'auto'})
    result = list(_recognize(spec, runtime, core) if spec['operation'] == 'asr' else _align(spec, runtime))
    (request.parent / 'results.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    _worker(Path(sys.argv[1]))
