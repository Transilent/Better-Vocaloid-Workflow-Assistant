"""Timestamped lyric conversion. Plain text timing is always an explicit estimate."""
from dataclasses import dataclass
import csv
import io
import math
import re
from pathlib import Path


@dataclass
class Cue:
    start: float
    end: float
    text: str


def validate(cues):
    if not cues:
        raise ValueError('没有找到可导出的歌词。')
    previous = -1
    for cue in cues:
        if not math.isfinite(cue.start) or not math.isfinite(cue.end) or cue.start < 0 or cue.end <= cue.start:
            raise ValueError('每行字幕的开始时间应大于等于 0，结束时间应晚于开始时间。')
        if cue.start < previous:
            raise ValueError('字幕开始时间应按顺序排列。')
        if not cue.text.strip():
            raise ValueError('字幕文字不能为空。')
        previous = cue.start
    return cues


def lrc(text, duration=None):
    offset = re.search(r'\[offset:\s*([+-]?\d+)\]', text, re.I)
    offset = int(offset[1]) / 1000 if offset else 0
    items = []
    for line in text.splitlines():
        tags = list(re.finditer(r'\[(\d+):(\d{1,2}(?:\.\d+)?)\]', line))
        if not tags:
            continue
        words = line[tags[-1].end():].strip()
        words = re.sub(r'<\d+:\d+(?:\.\d+)?>', '', words).strip()
        for tag in tags:
            if float(tag[2]) >= 60:
                raise ValueError('LRC 时间戳的秒数应小于 60。')
            items.append((max(0, int(tag[1]) * 60 + float(tag[2]) + offset), words))
    merged = {}
    for start, words in sorted(items):
        merged.setdefault(start, [])
        if words and words not in merged[start]:
            merged[start].append(words)
    items = [(start, '\n'.join(words)) for start, words in merged.items()]
    cues = [Cue(start, items[i+1][0] if i+1 < len(items) else duration if duration and duration > start else start+3, words)
            for i, (start, words) in enumerate(items) if words]
    return validate(cues)


def plain(text, duration):
    words = [line.strip() for line in text.splitlines() if line.strip()]
    if not words or not math.isfinite(duration) or duration <= 0:
        raise ValueError('纯文本歌词需要填写大于 0 的总时长，或逐行编辑时间。')
    step = duration / len(words)
    return validate([Cue(i*step, (i+1)*step, line) for i, line in enumerate(words)])


def timed_csv(text):
    rows = csv.DictReader(io.StringIO(text))
    if not {'start', 'end', 'text'} <= set(rows.fieldnames or []):
        raise ValueError('CSV 需要 start、end、text 三列，时间单位为秒。')
    return validate([Cue(float(r['start']), float(r['end']), r['text']) for r in rows])


def midi_cues(path, track=None):
    import mido
    midi = mido.MidiFile(str(path), charset='utf8')
    if midi.type == 2:
        raise ValueError('字幕导出暂不支持 MIDI Type 2。')
    # Preserve the global tempo map while selecting one voice's lyric events.
    selected = next((i for i, t in enumerate(midi.tracks) if any(m.type == 'lyrics' for m in t)), None) if track is None else track
    if selected is None or not 0 <= selected < len(midi.tracks):
        raise ValueError('MIDI 没有歌词事件；请选择带歌词的声部文件。')
    tracks = []
    for index, source in enumerate(midi.tracks):
        out, pending = mido.MidiTrack(), 0
        for msg in source:
            pending += msg.time
            if msg.type == 'set_tempo' or index == selected:
                out.append(msg.copy(time=pending))
                pending = 0
        tracks.append(out)
    seconds, tempo, syllables, duration = 0.0, 500000, [], 0.0
    for msg in mido.merge_tracks(tracks):
        seconds += mido.tick2second(msg.time, midi.ticks_per_beat, tempo)
        duration = seconds
        if msg.type == 'set_tempo':
            tempo = msg.tempo
        if msg.type == 'lyrics' and msg.text.strip():
            syllables.append((seconds, msg.text.strip()))
    groups = []
    for start, text in syllables:
        if groups and start - groups[-1][-1][0] <= .8 and start - groups[-1][0][0] < 6:
            groups[-1].append((start, text))
        else:
            groups.append([(start, text)])
    cues = []
    for i, group in enumerate(groups):
        roman = all(re.fullmatch(r'[\x20-\x7e]+', s) for _, s in group)
        end = min(group[-1][0]+1.5, groups[i+1][0][0]) if i+1 < len(groups) else max(duration, group[-1][0]+.5)
        cues.append(Cue(group[0][0], end, (' ' if roman else '').join(s for _, s in group)))
    return validate(cues)


def load(path, duration=180):
    path = Path(path)
    if path.suffix.lower() in ('.mid', '.midi'):
        return midi_cues(path), '来自 MIDI 歌词时间；分句由停顿推断，请预览核对。'
    text = path.read_text(encoding='utf-8-sig')
    if path.suffix.lower() == '.lrc':
        return lrc(text), '保留 LRC 时间戳。末行无结束标记时默认显示 3 秒，可修改。'
    if path.suffix.lower() == '.csv':
        return timed_csv(text), '保留 CSV 时间戳。'
    return plain(text, duration), '纯文本没有时间戳：已按总时长均分，请逐行校准后导出。'


def render(cues, format='srt', offset=0):
    validate(cues)
    if format not in ('srt', 'vtt') or not math.isfinite(offset):
        raise ValueError('字幕格式或偏移量无效。')
    def stamp(seconds):
        milliseconds = max(0, round(seconds * 1000))
        hours, remainder = divmod(milliseconds, 3600000)
        minutes, remainder = divmod(remainder, 60000)
        seconds, ms = divmod(remainder, 1000)
        return f'{hours:02}:{minutes:02}:{seconds:02}' + (',' if format == 'srt' else '.') + f'{ms:03}'
    blocks = []
    for cue in cues:
        start, end = max(0, cue.start+offset), cue.end+offset
        if end <= start:
            continue
        blocks.append(f'{len(blocks)+1}\n{stamp(start)} --> {stamp(end)}\n{cue.text.strip()}')
    if not blocks:
        raise ValueError('整体偏移后所有字幕都早于 0 秒，请修改偏移量。')
    return ('WEBVTT\n\n' if format == 'vtt' else '') + '\n\n'.join(blocks) + '\n'


def export_job(job, request):
    job = Path(job)
    voices = ['vocals'] if request.get('voice_mode', 'single') == 'single' else ['lead', 'backing']
    from common import atomic_json
    outputs, warnings = [], []
    for voice in voices:
        source = job / 'midi' / (voice+'.mid') if voice == 'vocals' else job / 'midi' / voice / (voice+'.mid')
        try:
            cues = midi_cues(source)
        except ValueError as exc:
            warnings.append(f'{voice}：{exc}')
            continue
        folder = job / 'subtitles'
        folder.mkdir(exist_ok=True)
        for format in ('srt', 'vtt'):
            path = folder / (voice+'.'+format)
            path.write_text(render(cues, format), encoding='utf-8')
            outputs.append(path.relative_to(job).as_posix())
    atomic_json(job / 'subtitles/report.json', {'outputs': outputs, 'warnings': warnings,
                'note': '分句由 MIDI 歌词停顿推断，使用前请检查时间与文字。'})
    for warning in warnings:
        print('字幕：'+warning, flush=True)
    if not outputs:
        raise ValueError('没有可用的 MIDI 歌词事件，无法自动生成字幕。MIDI 音符已保留，可在字幕工具中导入歌词并编辑时间。')
