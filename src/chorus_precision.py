"""Confirmed lyrics, matching evidence and sub-phrase source maps."""
from dataclasses import replace
from difflib import SequenceMatcher
from statistics import median
from uuid import uuid4

from chorus_project import Edit, LYRIC_LANGUAGES, Lyric, Phrase
from chorus_speech import SpeechClip, alignment_source, same_text, speech_batch


def phrase_for(project, selection):
    first, last = selection
    return next((phrase for phrase in project.phrases
                 if abs(phrase.start - first) < .006 and abs(phrase.end - last) < .006), None)


def update_phrase(project, selection, *, line=None, reference=None, automatic=False):
    """Edit one stable phrase. Model drafts leave a confirmed line intact."""
    first, last = selection
    if first >= last:
        raise ValueError('请先选中一段录音。')
    current = phrase_for(project, selection)
    if current is None:
        if any(phrase.start < last - .006 and first + .006 < phrase.end
               for phrase in project.phrases if phrase.lyrics or phrase.reference):
            raise ValueError('选区已包含核对唱段。可点上方唱段条定位后修改。')
        current = Phrase(first, last, id=uuid4().hex)
    if line is not None:
        if line.language not in LYRIC_LANGUAGES:
            raise ValueError('请选择受支持的歌词语言。')
        previous = current.lyric(line.track)
        if automatic and previous and previous.confirmed:
            return project
        lines = tuple(row for row in current.lyrics if row.track != line.track)
        current = replace(current, label=current.label or line.text[:32],
                          lyrics=(*lines, line) if line.text else lines)
    if reference is not None:
        current = replace(current, reference=reference)
    if not current.id:
        current = replace(current, id=uuid4().hex)
    other = [phrase for phrase in project.phrases
             if phrase.end <= current.start or phrase.start >= current.end]
    return replace(project, phrases=tuple(sorted((*other, current), key=lambda phrase: phrase.start)))


def analysis_phrases(stored, inferred, first, last):
    """Re-use checked boundaries and only infer positions in the gaps."""
    selected = [row for row in stored if row.start < last and first < row.end
                and (row.lyrics or row.reference)]
    result = list(selected)
    for item in inferred:
        start = item.start
        for kept in selected:
            if kept.end <= start or kept.start >= item.end:
                continue
            if kept.start - start >= .10:
                result.append(replace(item, start=start, end=kept.start))
            start = max(start, kept.end)
        if item.end - start >= .08:
            result.append(replace(item, start=start))
    return tuple(sorted(result, key=lambda item: item.start))


def remembered_phrases(project, found):
    retained = [row for row in project.phrases if row.lyrics or row.reference]
    retained.extend(row for row in found if not (row.lyrics or row.reference))
    return tuple(sorted(retained, key=lambda item: item.start))


def phone_requests(phrase, ref, take, movement, audio):
    top, bottom = phrase.resolved_lyric(ref.id), phrase.resolved_lyric(take.id)
    if not same_text(top, bottom):
        return ()
    shift = movement or 0.
    return (SpeechClip(audio[ref.id], phrase.start - .10, phrase.end + .12, top.language, top.text),
            SpeechClip(audio[take.id], phrase.start - shift - .14, phrase.end - shift + .16,
                       bottom.language, bottom.text))


def _measure_phones(pairs, known, cache, check, progress, selection):
    engine = selection
    try:
        source = alignment_source(selection)
        engine = source[0]
        clips = {clip.key: clip for pair in pairs for clip in pair}
        evidence = {key: known[source, key] for key in clips if (source, key) in known}
        missing = {key: clip for key, clip in clips.items() if key not in evidence}
        if missing:
            results = speech_batch(tuple(missing.values()), 'phones', cache, check,
                                   lambda index, text: progress(index, len(missing), text), source=source)
            evidence.update(zip(missing, results))
            known.update(((source, key), value) for key, value in zip(missing, results) if value.get('phones'))
        return evidence, engine, ''
    except InterruptedError:
        raise
    except (OSError, RuntimeError) as error:
        return {}, engine, str(error)


def phoneme_cache(pairs, known, cache, check, progress, selection):
    pairs = tuple(pair for pair in pairs if pair)
    if not pairs:
        return {}, ''
    primary, engine, failure = _measure_phones(pairs, known, cache, check, progress, selection)
    evidence = {tuple(clip.key for clip in pair): primary for pair in pairs}
    if selection == 'auto' and engine == 'tifa':
        remaining = [pair for pair in pairs if not phone_anchors(pair, primary)[1]]
        if remaining:
            fallback, _, error = _measure_phones(remaining, known, cache, check, progress, 'hubertfa')
            for pair in remaining:
                if phone_anchors(pair, fallback)[1]:
                    evidence[tuple(clip.key for clip in pair)] = fallback
            failure = failure or error
    return evidence, failure


def phone_anchors(clips, results):
    if len(clips) != 2:
        return '', ()
    ref, dur = (results.get(clip.key, {}) for clip in clips)
    left, right = ref.get('phones') or (), dur.get('phones') or ()
    if not left or not right or not ref.get('route') or ref.get('route') != dur.get('route'):
        return '', ()
    origins = tuple(clip.track.offset + clip.frames[0] / clip.track.rate for clip in clips)
    blocks = SequenceMatcher(None, [p[0] for p in left], [p[0] for p in right], autojunk=False)
    matches = [(left[i + j], right[k + j]) for i, k, n in blocks.get_matching_blocks() for j in range(n)]
    if len(matches) < max(2, .65 * max(len(left), len(right))):
        return '', ()
    points = [(a[0], origins[0] + a[1], origins[1] + b[1]) for a, b in matches]
    a, b = matches[-1]
    points.append(('尾', origins[0] + a[2], origins[1] + b[2]))
    return ref['route'], tuple(points)


def note_anchors(reference_notes, target_notes, phrase, shift):
    """Match onset geometry in the two takes while leaving harmony independent."""
    if shift is None:
        return ()
    left, right = [], []
    for notes, dest, offset in ((reference_notes, left, 0.), (target_notes, right, shift)):
        for start, end, _pitch in notes:
            if phrase.start - .05 <= start + offset <= phrase.end + .05 and end - start >= .10:
                dest.append((start, end))
    if len(left) < 2 or len(right) < 2:
        return ()
    out = []
    for a, stop in left:
        b, tail = min(right, key=lambda note: abs(note[0] + shift - a))
        if abs(b + shift - a) > .15 or not .4 < (tail - b) / (stop - a) < 2.5:
            continue
        if a != min(left, key=lambda note: abs(note[0] - b - shift))[0]:
            continue
        out.append(('♪', a, b))
    return tuple(out) if len(out) >= max(2, .55 * min(len(left), len(right))) else ()


def warp(anchors, first, last, strength, radius):
    knots, matched = [], []
    if strength <= 0.:
        return (), (), 0.
    for label, reference, source in anchors:
        output = source + (reference - source) * strength
        if not first + .012 < output < last - .012 or abs(output - source) > radius:
            continue
        if knots:
            a, b = knots[-1]
            if output - a < .045 or not .45 <= (source - b) / (output - a) <= 2.2:
                continue
        knots.append((output, source))
        matched.append((label, output, source))
    if len(knots) < 2:
        return (), (), 0.
    lag = median(source - output for output, source in knots)
    if all(abs(source - output) < .002 for output, source in knots):
        return (), tuple(matched), 0.
    a, b = knots[0]
    c, d = knots[-1]
    return ((first, first + b - a), *knots, (last, last + d - c)), tuple(matched), -lag


def move_anchor(track, interval, points, original, destination):
    """Move the output pose of one source position without inverting time."""
    left, right = interval
    index = min(range(1, len(points) - 1), key=lambda i: abs(points[i][0] - original))
    previous, source, following = points[index - 1], points[index][1], points[index + 1]
    low = max(previous[0] + .014, previous[0] + (source - previous[1]) / 2.8,
              following[0] - (following[1] - source) / .34)
    high = min(following[0] - .014, following[0] - (following[1] - source) / 2.8,
               previous[0] + (source - previous[1]) / .34)
    if high < low:
        raise ValueError('相邻时间锚点离得太近。')
    snapped = min(high, max(low, destination))
    updated = points[:index] + ((snapped, source),) + points[index + 1:]
    return Edit(track, 'timing', left, right, snapped - source, updated, manual=True)


def mark_note_edges(track, curve, position, selection, edit=None):
    """Insert a detected note's end points into the current time map."""
    from chorus_analysis import _pitch_runs

    first, last = (edit.start, edit.end) if edit else selection
    if not first < position < last:
        raise ValueError('把播放头放在所选唱段的音符上。')
    midi, step = curve
    source = (edit.source_time(position) if edit else position) - track.offset
    note = next(((a + track.offset, b + track.offset) for a, b, _ in _pitch_runs(midi, step)
                 if a <= source < b), None)
    if note is None:
        raise ValueError('播放头附近没有可标记的稳定音符。')

    if edit and not (edit.source_time(first) <= note[0] < note[1] <= edit.source_time(last)):
        raise ValueError('请选中包含音符首尾的唱段。')
    start, end = tuple(edit.output_time(value) for value in note) if edit else note
    if start <= first + .014 or end >= last - .014:
        raise ValueError('请选中包含音符首尾的唱段。')
    points = ((first, first), (last, last))
    if edit:
        points = edit.points or ((first, edit.source_time(first)), (last, edit.source_time(last)))
    marks = []
    for output, original in zip((start, end), note):
        if not any(abs(output - at) < .014 for at, _ in points):
            marks.append((output, original))
    knots = tuple(sorted((*points, *marks)))
    return Edit(track.id, 'timing', first, last, edit.value if edit else 0., knots,
                manual=edit is None or edit.manual, basis='音符首尾')


def merge_speech_drafts(project, track, language, phrases, results):
    count = 0
    for phrase, result in zip(phrases, results):
        text = result.get('text', '').strip()
        if text:
            edited = update_phrase(project, (phrase.start, phrase.end), automatic=True,
                                   line=Lyric(track, text, language, False))
            count += edited != project
            project = edited
    return project, count


def mapped_time(project, track, output):
    edit = next((row for row in project.edits_for(track.id, 'timing')
                 if row.start <= output < row.end), None)
    return edit.source_time(output) if edit else output


def pitch_from_stroke(project, track, curve, points, timing):
    import numpy as np

    if len(points) < 2:
        return ()
    midi, step = curve
    x, y = zip(*points)
    samples = np.linspace(x[0], x[-1], max(2, int((x[-1] - x[0]) / .010) + 1))
    groups = []
    for output, note in zip(samples, np.interp(samples, x, y)):
        time = (mapped_time(project, track, output) if timing else output) - track.offset
        index = round(time / step)
        if not 0 <= index < len(midi) or not np.isfinite(midi[index]):
            continue
        if groups and time - groups[-1][-1][0] < .004:
            continue
        if not groups or time - groups[-1][-1][0] > .075:
            groups.append([])
        groups[-1].append((time, max(-1200., min(1200., (note - midi[index]) * 100))))
    return pitch_edits(track.id, groups)


def reference_pitch(project, track, ref, selection, curves, mode, strength):
    import numpy as np

    own, own_step = curves[track.id]
    guide, ref_step = curves[ref.id]
    groups, present = [], []
    last = None
    for time in np.arange(*selection, max(.012, own_step, ref_step)):
        source = mapped_time(project, track, time) - track.offset
        a = round(source / own_step)
        b = round((time - ref.offset) / ref_step)
        if not (0 <= a < len(own) and 0 <= b < len(guide)
                and np.isfinite(own[a]) and np.isfinite(guide[b])):
            if present:
                groups.append(present)
                present = []
            continue
        band = int(round(own[a]))
        if present and (source <= present[-1][0] or source - present[-1][0] > .075
                        or mode == 'contour' and abs(band - last) >= 2):
            groups.append(present)
            present = []
        present.append((source, (guide[b] - own[a]) * 100))
        last = band
    if present:
        groups.append(present)
    lines = []
    for group in groups:
        offset = median(cents for _, cents in group) if mode == 'contour' else 0.
        limit = 190. if mode == 'contour' else 1200.
        lines.append([(time, max(-limit, min(limit, (value - offset) * strength))) for time, value in group])
    return pitch_edits(track.id, lines)


def pitch_edits(track, groups):
    return tuple(Edit(track, 'pitch', line[0][0], line[-1][0], 0.,
                      tuple(line), manual=True)
                 for line in groups if len(line) >= 2 and line[-1][0] - line[0][0] >= .025)
