"""Language choices and recognition requirements shared by preparation entry points."""

from dataclasses import dataclass
import re
from typing import Literal, get_args
import unicodedata


PreparationLanguage = Literal['zh', 'ja', 'en']
LANGUAGES = get_args(PreparationLanguage)
QWEN_FEATURE = 'chinese'  # Persisted component ID used by existing downloads.
QWEN_TITLE = '中文 / 英文歌词识别'
QWEN_MODEL = 'experiments/Qwen3-ASR-1.7B-dml'
ROMAJI_MODEL = 'experiments/romajiASR'
HUBERTFA_MODEL = 'experiments/1218_hfa_model_new_dict'
ENGLISH_DICTIONARY = 'ds_cmudict-07b.txt'
ENGLISH_ENGINE_HINT = '英文使用 Vocal2Midi 2.0 和 HubertFA。'
ENGLISH_EXPORT_HINT = '英文支持 MIDI 和 USTX。'


@dataclass(frozen=True)
class LanguageProfile:
    label: str
    recognition_label: str
    asr_feature: str
    asr_settings_key: str
    asr_model: str


PROFILES = {
    'zh': LanguageProfile('中文', '中文识别', QWEN_FEATURE, 'asr_model', QWEN_MODEL),
    'ja': LanguageProfile('日语', '日语识别', 'japanese', 'phoneme_asr_model', ROMAJI_MODEL),
    'en': LanguageProfile('英文', '英文识别', QWEN_FEATURE, 'asr_model', QWEN_MODEL),
}


def validate_english_midi_options(language, backend, alignment_engine):
    if language == 'en' and (backend not in (None, 'v2') or alignment_engine != 'hfa'):
        raise ValueError(ENGLISH_ENGINE_HINT)


def validate_project_formats(language, formats):
    if language == 'en' and any(fmt != 'ustx' for fmt in formats):
        raise ValueError(ENGLISH_EXPORT_HINT)


def effective_preparation_options(options):
    """Apply English engine and export constraints without overwriting saved choices."""
    if options['language'] != 'en':
        return options
    formats = [fmt for fmt in options['project_formats'] if fmt == 'ustx']
    return {**options, 'midi_backend': 'v2', 'alignment_engine': 'hfa', 'project_formats': formats,
            'project_export_enabled': bool(formats) and options['project_export_enabled']}


def english_words(text):
    """Match the bundled English ASR/HFA word alphabet and joiner rules."""
    text = unicodedata.normalize('NFKC', text).translate(str.maketrans('‘’‐‑', "''--"))
    return re.findall(r"[A-Za-z]+(?:['-][A-Za-z]+)*", text)


def lyric_output_mode(request, get_setting):
    language = request['language']
    if language == 'en':
        return 'word'
    if language == 'zh' and 'zh_lyric_mode' in request:
        mode = request['zh_lyric_mode']
        if mode not in ('hanzi', 'pinyin'):
            raise ValueError('任务中的中文歌词格式无效，请重新建立任务。')
        return mode
    default = '汉字' if language == 'zh' else '罗马音'
    return {'汉字': 'hanzi', '拼音': 'pinyin', '罗马音': 'romaji', '假名': 'kana'}.get(
        get_setting('lyric_output_mode_' + language, default), 'auto')
