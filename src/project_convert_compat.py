"""Compatibility, language-detection, and normalization helpers for Project Convert.

Kept separate from project_convert_worker.py and project_convert_service.py so both
stay below the 800-line architecture ratchet with zero circular imports.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

_CJK_RE = re.compile(r'[\u4e00-\u9fff]')
_KANA_RE = re.compile(r'[\u3040-\u30ff]')
_LATIN_RE = re.compile(r'[A-Za-z]')
_USTX_MASKED_CURVE_RE = re.compile(r'(?m)^([ \t]*type:[ \t]*)MaskedCurve([ \t]*(?:#.*)?)$')
_NON_WORD_EDGE_RE = re.compile(r'^[^\w\u3040-\u30ff\u4e00-\u9fff]+|[^\w\u3040-\u30ff\u4e00-\u9fff]+$')
_GEMINATE_RE = re.compile(r'^([bcdfghjklmnpqrstvwxyz])\1([a-z]+)$')

# Standard Japanese romaji syllables (Hepburn + Kunrei + loanword morae + common vowel clusters)
_JA_ROMAJI_SYLLABLES = frozenset({
    'a', 'i', 'u', 'e', 'o', 'n', 'nn',
    'ka', 'ki', 'ku', 'ke', 'ko', 'kya', 'kyu', 'kyo',
    'ga', 'gi', 'gu', 'ge', 'go', 'gya', 'gyu', 'gyo',
    'sa', 'si', 'shi', 'su', 'se', 'so', 'sha', 'shu', 'sho', 'sya', 'syu', 'syo', 'she',
    'za', 'zi', 'ji', 'zu', 'ze', 'zo', 'ja', 'ju', 'jo', 'jya', 'jyu', 'jyo', 'je',
    'ta', 'ti', 'chi', 'tu', 'tsu', 'te', 'to', 'cha', 'chu', 'cho', 'tya', 'tyu', 'tyo', 'che', 'tsa', 'tsi', 'tse', 'tso',
    'da', 'di', 'du', 'de', 'do', 'dzu', 'dya', 'dyu', 'dyo',
    'na', 'ni', 'nu', 'ne', 'no', 'nya', 'nyu', 'nyo',
    'ha', 'hi', 'hu', 'fu', 'he', 'ho', 'hya', 'hyu', 'hyo', 'fa', 'fi', 'fe', 'fo',
    'ba', 'bi', 'bu', 'be', 'bo', 'bya', 'byu', 'byo',
    'pa', 'pi', 'pu', 'pe', 'po', 'pya', 'pyu', 'pyo',
    'ma', 'mi', 'mu', 'me', 'mo', 'mya', 'myu', 'myo',
    'ya', 'yi', 'yu', 'ye', 'yo',
    'ra', 'ri', 'ru', 're', 'ro', 'rya', 'ryu', 'ryo',
    'wa', 'wi', 'wu', 'we', 'wo',
    'va', 'vi', 'vu', 've', 'vo',
    # Common diphthong / nasalized romaji single-note lyrics in SV/UTAU/VSQX
    'ai', 'ao', 'au', 'ei', 'oi', 'ou', 'ui',
    'kai', 'sai', 'tai', 'nai', 'hai', 'mai', 'rai', 'wai', 'gai', 'zai', 'dai', 'bai', 'pai',
    'kei', 'sei', 'tei', 'nei', 'hei', 'mei', 'rei', 'gei', 'zei', 'dei', 'bei', 'pei',
    'kou', 'sou', 'tou', 'nou', 'hou', 'mou', 'rou', 'gou', 'zou', 'dou', 'bou', 'pou',
    'kan', 'san', 'tan', 'nan', 'han', 'man', 'ran', 'wan', 'gan', 'zan', 'dan', 'ban', 'pan',
    'kin', 'sin', 'shin', 'tin', 'chin', 'nin', 'hin', 'min', 'rin', 'gin', 'jin', 'bin', 'pin',
    'kun', 'sun', 'tsun', 'nun', 'fun', 'mun', 'run', 'gun', 'zun', 'bun', 'pun',
    'ken', 'sen', 'ten', 'nen', 'hen', 'men', 'ren', 'gen', 'zen', 'den', 'ben', 'pen',
    'kon', 'son', 'ton', 'non', 'hon', 'mon', 'ron', 'gon', 'zon', 'don', 'bon', 'pon',
    'shun', 'chun', 'jun', 'ryo', 'kyo', 'sho', 'cho', 'nyo', 'hyo', 'myo',
})
_EN_STRONG_WORDS = frozenset({
    'the', 'and', 'you', 'that', 'with', 'this', 'from', 'your', 'have', 'they',
    'will', 'what', 'when', 'where', 'there', 'their', 'about', 'would', 'could',
    'should', 'love', 'heart', 'world', 'night', 'light', 'dream', 'time', 'life',
    'eyes', 'mind', 'soul', 'feel', 'know', 'want', 'need', 'just', 'like', 'never',
    'always', 'every', 'still', 'again', 'away', 'back', 'down', 'into', 'over',
    'under', 'through', 'because', 'before', 'after', 'until', 'while', 'hello',
    'good', 'more', 'some', 'only', 'come', 'make', 'take', 'give', 'find', 'hold',
    'keep', 'let', 'say', 'see', 'look', 'here', 'now', 'all', 'out', 'not', 'but',
    'for', 'are', 'was', 'were', 'been', 'being', 'has', 'had', 'can', 'may', 'our',
    'his', 'her', 'she', 'him', 'who', 'why', 'how', 'one', 'two', 'day', 'way',
})
_CONTROL_LYRICS = frozenset({'', '-', '+', '+~', '+*', '~', 'r', 'sil', 'sp', 'pau', 'br', 'ap', 'ー'})


def strip_extended_prefix(path: str | Path) -> str:
    """Remove Windows \\\\?\\ extended-length prefix for clean UI display & QUrl.fromLocalFile."""
    text = str(path or '')
    if text.startswith('\\\\?\\UNC\\'):
        return '\\\\' + text[8:]
    if text.startswith('\\\\?\\'):
        return text[4:]
    return text


def friendly_project_error(exc: Exception, *, file_name: str = '') -> str:
    """Translate raw low-level parser exceptions into clear Chinese user guidance."""
    msg = str(exc or '').strip()
    label = f'「{file_name}」' if file_name else '所选文件'
    if isinstance(exc, json.JSONDecodeError):
        return f'{label} JSON 结构损坏或不完整，无法解析为有效工程。'
    if isinstance(exc, ET.ParseError):
        return f'{label} XML 结构损坏或不完整，无法解析为有效工程。'
    if isinstance(exc, zipfile.BadZipFile):
        return f'{label} 压缩包结构损坏，不是有效的 VPR/工程文件。'
    if isinstance(exc, UnicodeDecodeError):
        return f'{label} 文本编码异常或文件已损坏，无法读取。'
    if isinstance(exc, PermissionError):
        return f'没有权限访问 {label}，请检查文件是否被其他程序占用。'
    if isinstance(exc, FileNotFoundError):
        if _CJK_RE.search(msg):
            return msg
        return f'找不到 {label} 或关联依赖文件，请确认路径有效。'
    if msg and _CJK_RE.search(msg):
        return msg
    return f'无法解析 {label}：{msg or type(exc).__name__}'


def _is_ja_romaji_token(token: str) -> bool:
    t = token.lower()
    if t in _JA_ROMAJI_SYLLABLES:
        return True
    # Geminate sokuon (e.g. tto, kka, ssa, cchi, ttsu)
    m = _GEMINATE_RE.match(t)
    if m and (m.group(1) + m.group(2)) in _JA_ROMAJI_SYLLABLES:
        return True
    if t.startswith('tch') and ('ch' + t[3:]) in _JA_ROMAJI_SYLLABLES:
        return True
    return False


def detect_tracks_language(tracks) -> str:
    """Detect zh / ja / en from SingingTrack note lyrics, distinguishing Japanese romaji from English."""
    cjk = kana = latin_total = ja_romaji = en_strong = non_romaji = 0
    for track in tracks:
        for note in getattr(track, 'note_list', ()):
            lyr = str(getattr(note, 'lyric', '') or '').strip()
            if not lyr or lyr.casefold() in _CONTROL_LYRICS:
                continue
            if _KANA_RE.search(lyr):
                kana += 1
            elif _CJK_RE.search(lyr):
                cjk += 1
            elif _LATIN_RE.search(lyr):
                parts = [p for p in re.split(r"[\s_\-]+", lyr) if _LATIN_RE.search(p)]
                for part in parts:
                    clean = re.sub(r"^[^A-Za-z']+|[^A-Za-z']+$", '', part).lower()
                    if not clean or clean in _CONTROL_LYRICS:
                        continue
                    latin_total += 1
                    if clean in _EN_STRONG_WORDS or "'" in clean:
                        en_strong += 1
                    if _is_ja_romaji_token(clean):
                        ja_romaji += 1
                    else:
                        non_romaji += 1

    if kana > 0 and kana >= cjk and kana >= latin_total:
        return 'ja'
    if cjk > 0 and cjk >= latin_total:
        return 'zh'
    if latin_total > 0:
        if en_strong >= max(2, int(latin_total * 0.08)):
            return 'en'
        if ja_romaji >= max(3, int(latin_total * 0.72)) and non_romaji <= int(latin_total * 0.20):
            return 'ja'
        return 'en'
    return 'zh'


def patch_ustx_masked_curve(raw_text: str) -> tuple[str, int]:
    """Downgrade modern OpenUtau `type: MaskedCurve` expressions to `type: Curve` for LibreSVIP 2.9.2."""
    return _USTX_MASKED_CURVE_RE.subn(r'\1Curve\2', raw_text)


def detect_ust_encoding(path: Path) -> str:
    """Detect whether a .ust file is UTF-8 (UST 2.0 / OpenUtau) or legacy Shift-JIS."""
    try:
        raw = path.read_bytes()[:4096]
    except OSError:
        return 'SHIFT_JIS'
    if raw.startswith(b'\xef\xbb\xbf'):
        return 'utf-8'
    head_ascii = raw.decode('latin-1', errors='ignore').lower()
    if 'charset=utf-8' in head_ascii or 'charset=utf8' in head_ascii:
        return 'utf-8'
    try:
        full = path.read_bytes()
        full.decode('utf-8')
        if any(b >= 0x80 for b in full):
            try:
                full.decode('shift_jis')
            except UnicodeDecodeError:
                return 'utf-8'
    except (OSError, UnicodeDecodeError):
        pass
    return 'SHIFT_JIS'


def normalize_ds_candidate(token: str, table: dict[str, str], *, is_ja: bool) -> str | None:
    """Try progressively normalized forms (case, edge punctuation, sokuon, multi-token split) against DS dict."""
    raw = str(token or '').strip()
    if not raw:
        return None
    if raw in table:
        return raw
    low = raw.lower()
    if low in table:
        return low
    stripped = _NON_WORD_EDGE_RE.sub('', low)
    if stripped and stripped in table:
        return stripped
    # Space/hyphen-separated compound syllable (e.g. "ro n" -> "ro")
    if ' ' in stripped or '_' in stripped:
        first = re.split(r'[\s_]+', stripped)[0]
        if first in table:
            return first
    if is_ja and stripped:
        # Japanese sokuon geminate consonant (e.g. "tto" -> "to", "kka" -> "ka", "cchi" -> "chi", "tchi" -> "chi")
        m = _GEMINATE_RE.match(stripped)
        if m:
            folded = m.group(1) + m.group(2)
            if folded in table:
                return folded
        if stripped.startswith('tch') and ('ch' + stripped[3:]) in table:
            return 'ch' + stripped[3:]
        if stripped.startswith('ltu') or stripped.startswith('xtu'):
            if 'tsu' in table:
                return 'tsu'
        # Trailing repeated vowel (e.g. "aa" -> "a", "oo" -> "o")
        dedup = re.sub(r'([aeiou])\1+$', r'\1', stripped)
        if dedup != stripped and dedup in table:
            return dedup
        # Two-mora romaji compounds written on one note (e.g. "kai"->"ka", "nai"->"na", "mon"->"mo", "shou"->"sho")
        if len(stripped) >= 3 and stripped[-1] in ('i', 'u', 'n'):
            stem = stripped[:-1]
            if stem in table:
                return stem
    return None


def prepare_diffsinger_project(draft, *, language: str) -> tuple[Any, str, int, list[str]]:
    """Auto-select DS dictionary and normalize rest/slur/geminate/compound syllables before dump."""
    from libresvip.core.lyric_phoneme.chinese import get_pinyin_series
    from libresvip.core.lyric_phoneme.japanese import to_romaji
    from libresvip.model.base import SingingTrack
    from libresvip.plugins.ds.utils.phoneme_dict import get_opencpop_dict

    zh_table = get_opencpop_dict('opencpop-extension')
    ja_table = get_opencpop_dict('japanese_dict_full')
    rest_markers = {'', 'r', 'sil', 'sp', 'pau', 'br', 'ap'}
    slur_markers = {'+', '~', '+~', '+*', 'ー'}

    def _resolve(lyr: str, pron: str, table: dict[str, str], is_ja: bool) -> str | None:
        for cand in (pron, lyr):
            hit = normalize_ds_candidate(cand, table, is_ja=is_ja)
            if hit is not None:
                return hit
        if is_ja:
            rom = to_romaji(lyr).strip().lower()
            hit = normalize_ds_candidate(rom, table, is_ja=True)
            if hit is not None:
                return hit
        else:
            py_list = get_pinyin_series([lyr])
            py = (py_list[0] if py_list else '').strip().lower()
            hit = normalize_ds_candidate(py, table, is_ja=False)
            if hit is not None:
                return hit
        return None

    singing_notes = [n for tr in draft.track_list if isinstance(tr, SingingTrack) for n in tr.note_list]
    zh_hits = ja_hits = 0
    for note in singing_notes:
        lyr = str(note.lyric or '').strip()
        pron = str(note.pronunciation or '').strip() if note.pronunciation is not None else ''
        if (lyr.casefold() in rest_markers and not pron) or lyr in slur_markers or '-' in lyr:
            continue
        if _resolve(lyr, pron, zh_table, False) is not None:
            zh_hits += 1
        if _resolve(lyr, pron, ja_table, True) is not None:
            ja_hits += 1

    use_ja = (ja_hits > zh_hits) or (ja_hits == zh_hits and language == 'ja')
    dict_name = 'japanese_dict_full' if use_ja else 'opencpop-extension'
    table = ja_table if use_ja else zh_table
    fallback_vowel = 'a' if 'a' in table else next(iter(table))

    skipped = 0
    first_track_skipped = 0
    singing_idx = 0
    substituted: list[str] = []
    cleaned_tracks = []
    for tr in draft.track_list:
        if not isinstance(tr, SingingTrack):
            cleaned_tracks.append(tr)
            continue
        is_first = (singing_idx == 0)
        singing_idx += 1
        kept = []
        last_pron = fallback_vowel
        prev_was_skipped = False
        for note in tr.note_list:
            lyr = str(note.lyric or '').strip()
            pron = str(note.pronunciation or '').strip() if note.pronunciation is not None else ''
            if lyr.casefold() in rest_markers and not pron:
                skipped += 1
                if is_first:
                    first_track_skipped += 1
                prev_was_skipped = True
                continue
            if lyr in slur_markers or '-' in lyr:
                if not kept:
                    skipped += 1
                    if is_first:
                        first_track_skipped += 1
                    prev_was_skipped = True
                    continue
                prev_end = int(kept[-1].start_pos) + int(kept[-1].length)
                gap = int(note.start_pos) - prev_end
                # Never emit a DS slur after a rest or gap (>30 ticks) — DiffSinger inserts 'rest'
                # before the slur, desynchronizing note_seq vs text in LibreSVIP's DS parser.
                if prev_was_skipped or gap > 30:
                    kept.append(note.model_copy(update={'lyric': last_pron, 'pronunciation': last_pron}))
                else:
                    kept.append(note.model_copy(update={'lyric': '-', 'pronunciation': None}))
                prev_was_skipped = False
                continue
            resolved = _resolve(lyr, pron, table, use_ja)
            if resolved is None:
                # Pure digits or punctuation carry no vocal syllable in DS
                if not _CJK_RE.search(lyr) and not _KANA_RE.search(lyr) and not _LATIN_RE.search(lyr):
                    skipped += 1
                    if is_first:
                        first_track_skipped += 1
                    prev_was_skipped = True
                    continue
                resolved = fallback_vowel
                if len(substituted) < 8 and lyr not in substituted:
                    substituted.append(lyr)
            last_pron = resolved
            prev_was_skipped = False
            updates: dict[str, Any] = {'pronunciation': resolved}
            # DiffSinger serializes `text` as a space-delimited string; whitespace inside a single
            # note's lyric splits into multiple tokens and desynchronizes `text` vs `note_seq`.
            if not lyr or any(ch.isspace() for ch in lyr):
                updates['lyric'] = resolved
            kept.append(note.model_copy(update=updates))
        cleaned_tracks.append(tr.model_copy(update={'note_list': kept}))

    warnings: list[str] = []
    if substituted:
        sample = '、'.join(substituted[:5])
        warnings.append(
            f'DiffSinger 词典（{dict_name}）中未收录部分特殊音节（如 {sample}），已自动回退为「{fallback_vowel}」以保证可导出，请在目标软件中复核。'
        )
    return draft.model_copy(update={'track_list': cleaned_tracks}), dict_name, first_track_skipped, warnings


def vsqx_english_dict_available(root: Path) -> bool:
    """Return True when the optional vocal2midi CMU English dictionary is installed."""
    from vocaloid_english import DICTIONARY_PATH

    candidates = [root / DICTIONARY_PATH]
    explicit = os.environ.get('VWA_EXPORT_RUNTIME', '').strip()
    if explicit:
        exp_path = Path(explicit)
        candidates.append(exp_path.parent.parent / DICTIONARY_PATH)
    return any(p.is_file() for p in candidates)
