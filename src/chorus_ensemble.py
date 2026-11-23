"""Find same-lyric voices and align whole songs on their shared time axis."""
from collections import defaultdict
from dataclasses import replace
from difflib import SequenceMatcher
from itertools import combinations
import re
from uuid import uuid4

import numpy as np

from chorus_analysis import FRAME, Suggestion, _project_slice, _runs
from chorus_precision import analysis_phrases, phoneme_cache
from chorus_project import Lyric, Phrase
from chorus_speech import SpeechClip, normalized_lyrics, same_text, speech_batch


def ensemble_phrases(project, targets, features, selection):
    """Split on measured pauses across the voices that will be edited."""
    first, last = selection
    grid = np.arange(round(first / FRAME), round(last / FRAME))
    active = np.zeros(len(grid), dtype=bool)
    level = np.zeros(len(grid), dtype=float)
    for track in targets:
        take = features[track.id]
        active |= _project_slice(take.active, track.offset, grid)
        level += _project_slice(take.energy, track.offset, grid) / max(.001, np.percentile(take.energy, 90))
    level /= max(1, len(targets))
    origin = round(first / FRAME) * FRAME
    ranges = []
    for start, end in _runs(active):
        if end - start < 10:
            continue
        if ranges and start - ranges[-1][1] < 30:
            ranges[-1] = (ranges[-1][0], end)
        else:
            ranges.append((start, end))
    sections = []
    for start, end in ranges:
        while end - start > 820:
            choices = np.arange(start + 340, min(end - 190, start + 690))
            cost = np.maximum.reduce((level[choices - 4], level[choices], level[choices + 4]))
            cost += .10 * np.abs(choices - start - 550) / 210
            cut = int(choices[np.argmin(cost)])
            sections.append((start, cut))
            start = cut
        sections.append((start, end))
    inferred = []
    for i, (start, end) in enumerate(sections):
        left = origin + (sections[i - 1][1] + start) * FRAME / 2 if i else first
        right = origin + (end + sections[i + 1][0]) * FRAME / 2 if i + 1 < len(sections) else last
        a, b = max(first, left, origin + start * FRAME - .13), min(last, right, origin + end * FRAME + .15)
        if a < b:
            inferred.append(Phrase(a, b))
    return analysis_phrases(project.phrases, inferred, first, last)


def _voice_has_audio(track, span, features):
    first, last = span
    clock = np.arange(round(first / FRAME), round(last / FRAME))
    present = _project_slice(features[track.id].active, track.offset, clock)
    return np.count_nonzero(present) >= max(11, len(clock) * .075)


def _preferred_guide(project):
    selected = project.track(project.guide)
    return selected if selected and selected.role == 'guide' else next(
        (track for track in project.tracks if track.role == 'guide'), None)


def _words(text):
    return re.findall(r"[a-z0-9]+(?:['’][a-z]+)?|[ぁ-ゖー\u3400-\u9fff]", normalized_lyrics(text))


def _verified_overlap(left, right):
    if left.language != right.language:
        return False
    if left.confirmed and right.confirmed:
        return same_text(left, right)
    a, b = _words(left.text), _words(right.text)
    shared = [part.size for part in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()]
    return max(shared) >= 3 and sum(shared) >= max(4, .57 * min(len(a), len(b)))


def _phone_edges(pair, evidence):
    """Match ordered phones within a bounded search around their actual positions."""
    rows = [evidence.get(clip.key, {}) for clip in pair]
    route = rows[0].get('route', '')
    if not route or rows[1].get('route') != route or not all(row.get('phones') for row in rows):
        return '', ()
    phones = []
    for clip, row in zip(pair, rows):
        origin = clip.track.offset + clip.frames[0] / clip.track.rate
        phones.append([(label, a + origin, b + origin) for label, a, b in row['phones']])
    left, right = phones
    width, length = len(left), len(right)
    scores = np.zeros((width + 1, length + 1))
    paths = np.zeros_like(scores, dtype=np.uint8)
    for i, (a, first, last) in enumerate(left, 1):
        for j, (b, start, end) in enumerate(right, 1):
            top, side = scores[i - 1, j], scores[i, j - 1]
            score, direction = (top, 1) if top >= side else (side, 2)
            distance = abs(first - start)
            if a == b and distance < .50:
                duration = min(last - first, end - start) / max(.01, last - first, end - start)
                matched = scores[i - 1, j - 1] + (1. - 1.28 * distance) * (.72 + .28 * duration)
                if matched > score:
                    score, direction = matched, 3
            scores[i, j], paths[i, j] = score, direction
    found = []
    i, j = width, length
    while i and j:
        direction = paths[i, j]
        if direction == 3:
            found.append((left[i - 1][0], left[i - 1][1], right[j - 1][1]))
        i -= direction in (1, 3)
        j -= direction in (2, 3)
    if found:
        lag = np.median([a - b for _, a, b in found])
        found = [(label, a, b) for label, a, b in reversed(found) if abs(a - b - lag) <= .22]
    return (route, tuple(found)) if len(found) >= max(5, .48 * min(width, length)) else ('', ())


def _timing_map(span, anchors, strength):
    """Use spaced time knots with bounded rates and pinned outer edges."""
    first, last = span
    inside = sorted((source, strength * (reference - source), label)
                    for label, reference, source in anchors if first + .055 <= source <= last - .055)
    if len(inside) < 4 or inside[-1][0] - inside[0][0] < .22:
        return (), (), 0.
    location, correction, labels = zip(*inside)
    location, correction = np.array(location), np.array(correction)
    center = float(np.median(correction))
    sample = np.linspace(location[0], location[-1], 1 + max(1, int((location[-1] - location[0]) / .24)))
    joints = [(first, 0., '')]
    for source in sample:
        near = np.abs(location - source)
        active = correction[near <= .25]
        value = np.median(active) if len(active) else center
        joints.append((float(source), float(np.clip(.8 * value + .2 * center, -.24, .24)),
                       labels[int(np.argmin(near))]))
    joints.append((last, 0., ''))
    positions, movements, knot_labels = (list(values) for values in zip(*joints))
    for indexes, direction in ((range(1, len(joints) - 1), -1), (range(len(joints) - 2, 0, -1), 1)):
        for i in indexes:
            peer = i + direction
            limit = .20 * abs(positions[i] - positions[peer])
            movements[i] = min(movements[peer] + limit, max(movements[peer] - limit, movements[i]))
    points = tuple((source + delta, source) for source, delta in zip(positions, movements))
    labels = tuple((label, output, source) for label, (output, source) in zip(knot_labels[1:-1], points[1:-1]))
    if max(abs(delta) for delta in movements) < .012:
        return (), labels, 0.
    return points, labels, float(np.median(movements[1:-1]))


def _relevant_clips(project, targets, phrase, span, audio, features, language):
    active = [track for track in targets if _voice_has_audio(track, span, features)]
    guide = _preferred_guide(project)
    refs, needed = {}, {track.id: track for track in active}
    for track in active:
        explicit = project.track(phrase.reference or track.timing_ref)
        chosen = explicit if explicit and explicit.id != track.id and explicit.role != 'backing' else None
        ref = chosen or guide
        if ref:
            needed[ref.id] = ref
        refs[track.id] = (ref.id if ref else '', bool(chosen))
    clips, lines = {}, {}
    for key, track in needed.items():
        line = phrase.resolved_lyric(key)
        if line and line.confirmed:
            lines[key] = line
        margin = .12 if track in active else .43
        clip = SpeechClip(audio[key], span[0] - margin, span[1] + margin, line.language if key in lines else language)
        if clip.frames[1] - clip.frames[0] >= .08 * clip.track.rate:
            clips[key] = clip
    return active, refs, clips, lines


def _recognized(clips, known, cache, check, progress):
    clips = {clip.key: clip for clip in clips}
    pending = [clip for key, clip in clips.items() if ('asr', key) not in known]
    failure = ''
    try:
        rows = speech_batch(pending, 'asr', cache, check,
                            lambda i, text: progress(i, len(pending), text)) if pending else []
        known.update((('asr', clip.key), row) for clip, row in zip(pending, rows) if row.get('text'))
        if rows and not any(row.get('text') for row in rows):
            failure = next((row['detail'] for row in rows if row.get('detail')), '')
    except InterruptedError:
        raise
    except (OSError, RuntimeError) as error:
        failure = str(error)
    return {key: known.get(('asr', key), {}).get('text', '') for key in clips}, failure


def ensemble_timing(project, targets, selection, features, audio, language, strength, speech, check, progress):
    phrases = ensemble_phrases(project, targets, features, selection)
    contexts = []
    for phrase in phrases:
        check()
        span = max(selection[0], phrase.start), min(selection[1], phrase.end)
        contexts.append((phrase, span, *_relevant_clips(project, targets, phrase, span, audio, features, language)))
    known, cache = speech
    drafts, asr_error = _recognized((clip for _, _, _, _, clips, lines in contexts
                                    for key, clip in clips.items() if key not in lines), known, cache, check, progress)
    measured, all_pairs, saved = [], [], []
    for phrase, span, vocals, refs, clips, lines in contexts:
        check()
        new_lines = []
        for key, clip in clips.items():
            if key in lines or not (text := drafts.get(clip.key, '').strip()):
                continue
            lines[key] = Lyric(key, text, language)
            if phrase.lyric(key) is None:
                new_lines.append(lines[key])
        saved.append(replace(phrase, id=phrase.id or uuid4().hex, lyrics=(*phrase.lyrics, *new_lines)))
        peers = {track.id for track in vocals}
        allowed = {key: {ref} if explicit else (peers - {key}) | {ref}
                   for key, (ref, explicit) in refs.items()}
        pairings = []
        for a, b in combinations(clips, 2):
            if b not in allowed.get(a, ()) and a not in allowed.get(b, ()):
                continue
            if a not in lines or b not in lines or not _verified_overlap(lines[a], lines[b]):
                continue
            # Confirmed text always supplies pronunciation; inferred text stays a draft.
            pair = (replace(clips[a], text=lines[a].text), replace(clips[b], text=lines[b].text))
            pairings.append((a, b, pair))
            all_pairs.append(pair)
        measured.append((span, vocals, refs, pairings))
    evidence, align_error = phoneme_cache(all_pairs, known, cache, check, progress, project.alignment_engine,
                                          matches=lambda pair, results: _phone_edges(pair, results)[1])
    generated = []
    for span, vocals, refs, pairings in measured:
        check()
        peers = {track.id for track in vocals}
        votes = defaultdict(lambda: defaultdict(list))
        for a, b, pair in pairings:
            route, points = _phone_edges(pair, evidence.get(tuple(clip.key for clip in pair), {}))
            for label, source, other in points:
                votes[a, route][label, source].append((b, other))
                votes[b, route][label, other].append((a, source))
        for track in vocals:
            ref, explicit = refs[track.id]
            row = None
            for route in ('TiFA', 'HubertFA'):
                anchors, same_group = [], 0
                for (label, source), matches in votes[track.id, route].items():
                    ensemble = [at for other, at in matches if other in peers] if not explicit else []
                    reference = next((at for other, at in matches if other == ref), None)
                    if ensemble:
                        target = source + .86 * (float(np.median([source, *ensemble])) - source)
                        same_group += 1
                    elif reference is not None:
                        target = source + .72 * (reference - source)
                    else:
                        continue
                    anchors.append((label, target, source))
                points, shown, correction = _timing_map(span, anchors, strength)
                if shown:
                    basis = '同词合唱' if same_group else '导唱参照'
                    row = Suggestion(track.id, 'timing', *span, correction, f'{route} · {basis} · {len(shown)} 个锚点',
                                     points=points, anchors=shown, route=route)
                    break
            if row is None:
                reason = align_error or asr_error or '没有足够的同词参照，保留当前节奏。'
                row = Suggestion(track.id, 'timing', *span, None, reason, review=True, route='需核对')
            generated.append(row)
    return tuple(saved), generated
