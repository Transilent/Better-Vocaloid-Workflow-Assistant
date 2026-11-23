"""Local singing transcription and forced-alignment adapters."""
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
import sys
import unicodedata


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


def same_text(a, b):
    def canonical(value):
        value = unicodedata.normalize('NFKC', value).casefold()
        # Hiragana and katakana are equivalent here. The aligner still
        # receives the user's original notation for pronunciation.
        value = ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in value)
        return ''.join(c for c in value if c.isalnum() or c == 'ー')

    return bool(a and b and a.confirmed and b.confirmed and a.language == b.language
                and canonical(a.text) and canonical(a.text) == canonical(b.text))


def speech_batch(clips, operation, cache, check, progress):
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
    label = '识别歌词' if operation == 'asr' else '提取音素'
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


def _align(request, runtime, core):
    bundle = runtime / 'models/tifa-1.0-onnx'
    fallback = core / '1218_hfa_model_new_dict'
    tifa = hfa = None
    pipeline = None
    tifa_note = ''
    if all((bundle / name).is_file() for name in ('config.json', 'vocabulary.json', 'model.onnx',
                                                 'spectrogram.onnx', 'select.onnx', 'score.onnx', 'prepare.onnx')):
        try:
            from inference.TiFA.aligner import run_tifa_fa
            from inference.TiFA.g2p_pipeline import build_g2p_pipeline
            from inference.TiFA.runtime import load_tifa_model

            tifa = load_tifa_model(bundle, device='cpu')
            kashi = runtime / 'models/kashi-g2p-onnx'
            pipeline = build_g2p_pipeline(bundle, 'kashi-g2p-onnx' if (kashi / 'model.onnx').is_file()
                                          else 'pyopenjtalk', kashi)
        except Exception as error:
            tifa = None
            tifa_note = f'TiFA 载入失败：{error}'
    if tifa is None and not (fallback / 'model.onnx').is_file():
        raise FileNotFoundError('未安装 TiFA 或 HubertFA 音素模型。')

    for index, row in enumerate(request['clips']):
        folder = Path(request['folder'])
        (folder / 'progress.txt').write_text(str(index), encoding='utf-8')
        one = folder / f'clip-{index}'
        one.mkdir()
        wav = one / 'chunk_0.wav'
        language = row['lang']
        phones = []
        if tifa is not None:
            try:
                if not _read_clip(row, wav, tifa.samplerate):
                    yield {'route': '', 'phones': [], 'detail': '唱段没有有效录音'}
                    continue
                wav.with_suffix('.txt').write_text(row['text'], encoding='utf-8')
                found, _ = run_tifa_fa(tifa, one, language=language, g2p_pipeline=pipeline)
                if 'chunk_0' in found:
                    phones = _phones(found['chunk_0'][2])
                if phones:
                    yield {'route': 'TiFA', 'phones': phones}
                    continue
            except Exception as error:
                tifa_note = f'TiFA 未完成此段：{error}'
        try:
            if language == 'yue' or not (fallback / 'model.onnx').is_file():
                yield {'route': '', 'phones': [], 'detail': tifa_note or '当前音素模型未给出边界'}
                continue
            from inference.API.hfa_api import load_hfa_model, run_hubert_fa

            if hfa is None:
                hfa = load_hfa_model(fallback, device='cpu')
            if not _read_clip(row, wav, 44100):
                yield {'route': '', 'phones': [], 'detail': '唱段没有有效录音'}
                continue
            lab = _lab(row['text'], language, runtime)
            wav.with_suffix('.lab').write_text(lab, encoding='utf-8')
            found = run_hubert_fa(hfa, one, language=language)
            if 'chunk_0' in found:
                phones = _phones(found['chunk_0'][2])
            yield {'route': 'HubertFA' if phones else '', 'phones': phones,
                   'detail': '' if phones else '歌词或读音未得到可用边界'}
        except Exception as error:
            yield {'route': '', 'phones': [], 'detail': str(error)[:300]}


def _recognize(request, runtime, core):
    from v2m_runtime import configure

    romaji = qwen = None
    folder = Path(request['folder'])
    wav = folder / 'asr.wav'
    try:
        for index, row in enumerate(request['clips']):
            (folder / 'progress.txt').write_text(str(index), encoding='utf-8')
            lang = row['lang']
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
    finally:
        if qwen is not None:
            qwen.shutdown()


def _worker(request):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from runtime_devices import prepare_environment
    from v2m_runtime import configure

    spec = json.loads(request.read_text(encoding='utf-8'))
    spec['folder'] = str(request.parent)
    runtime, core = Path(spec['runtime']), Path(spec['core'])
    configure(runtime, pitch_only=True)
    prepare_environment({'midi_device': 'cpu', 'cpu_threads': 4, 'dml_adapter': 'auto'})
    analyze = _recognize if spec['operation'] == 'asr' else _align
    result = list(analyze(spec, runtime, core))
    (request.parent / 'results.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    _worker(Path(sys.argv[1]))
