"""Syllable boundaries and evidence-based repairs for generated transcriptions."""
import copy
import math

ALGORITHM_VERSION = 11
PAUSE_LIMIT = .15
CONSONANT_LIMIT = .35
_state = {}
_installed = False


def expand_reference_match(text, phonetic, reference_text, reference_phonetic, cursor=0):
    """Restore only internal omitted syllables between strong reference anchors."""
    import numpy as np
    query = text.split() if isinstance(text,str) else list(text)
    target = reference_text.split() if isinstance(reference_text,str) else list(reference_text)
    phones = reference_phonetic.split() if isinstance(reference_phonetic,str) else list(reference_phonetic)
    if len(query)<5 or len(target)!=len(phones) or any(len(x)!=1 for x in query+target):
        return text,phonetic,cursor,0
    n,m=len(query),len(target)
    scores=np.zeros((n+1,m+1),dtype=float);scores[:,0]=np.arange(n+1)*4
    trace=np.zeros((n+1,m+1),dtype='int8')
    for i in range(1,n+1):
        for j in range(1,m+1):
            choices=(scores[i-1,j-1]+(0 if query[i-1]==target[j-1] else 4),
                     scores[i,j-1]+1,scores[i-1,j]+4)
            kind=min(range(3),key=choices.__getitem__)
            scores[i,j]=choices[kind];trace[i,j]=kind
    best=float(scores[n,1:].min())
    candidates=[j for j in range(1,m+1) if scores[n,j]==best]
    endings=sorted(candidates,key=lambda end:(end<cursor,abs(end-cursor)))
    for end in endings:
        i,j=n,end;matches=[]
        while i>0 and j>0:
            kind=int(trace[i,j])
            if kind==0:
                if query[i-1]==target[j-1]:matches.append((i-1,j-1))
                i-=1;j-=1
            elif kind==1:j-=1
            else:i-=1
        matches.reverse()
        if len(matches)<max(5,math.ceil(n*.9)) or matches[0][0]!=0 or matches[-1][0]!=n-1:
            continue
        start,stop=matches[0][1],matches[-1][1]+1
        extra=stop-start-n
        # Longer phrases may lose several short words, including the second
        # word after an intact first anchor. Only internal holes are eligible;
        # prefixes/suffixes outside the recognized span remain unassigned.
        limit = min(8,n//3) if n>=8 else min(4,max(1,n//4))
        if not 0<extra<=limit:
            continue
        # Short queries require two anchors per side. A long, >=90% matching
        # query can anchor one missing pair near its beginning/end. Reject long
        # runs of absent text rather than forcing a whole unsung lyric line.
        holes=[index for index in range(start,stop) if index not in {r for q,r in matches}]
        minimum = 1 if n>=8 else 2
        if any(sum(r<h for q,r in matches)<minimum or sum(r>h for q,r in matches)<minimum for h in holes):
            continue
        if n>=8 and any(holes[i+2]==h+2 for i,h in enumerate(holes[:-2])):
            continue
        return ' '.join(target[start:stop]),' '.join(phones[start:stop]),stop,extra
    return text,phonetic,cursor,0


def _syllable_boundaries(words, tokens, game, language):
    """Consume every lexical token, including failed/zero-duration alignments."""
    ignored = game._NON_SINGABLE_WORD_TOKENS
    durations, voiced, lyrics = [], [], []
    last, token_index = 0., 0
    for index, word in enumerate(words):
        if word.text in ignored:
            if word.end > last:
                durations.append(float(word.end - last)); voiced.append(0); lyrics.append('')
                last = float(word.end)
            continue
        lyric = tokens[token_index] if token_index < len(tokens) else word.text
        token_index += 1
        start = game._find_word_nucleus_start(word, language)
        if start is None:
            if word.end > last:
                durations.append(float(word.end - last)); voiced.append(0); lyrics.append('')
                last = float(word.end)
            continue
        start = max(last, float(start))
        end = float(word.end)
        if index + 1 < len(words) and words[index + 1].text not in ignored:
            following = game._find_word_nucleus_start(words[index + 1], language)
            # A consonant belongs to the following word, not to a phrase
            # pause. Its nucleus can start >150 ms after the previous vowel.
            if (following is not None and 0 <= following - end <= CONSONANT_LIMIT
                    and words[index + 1].start - word.end <= .04):
                end = float(following)
        if end <= start:
            continue
        if start > last:
            durations.append(start - last); voiced.append(0); lyrics.append('')
        durations.append(end - start); voiced.append(1); lyrics.append(str(lyric))
        last = end
    return durations, voiced, lyrics


def chinese_boundaries(words, tokens, game):
    return _syllable_boundaries(words,tokens,game,'zh')


def japanese_boundaries(words, tokens, game):
    return _syllable_boundaries(words,tokens,game,'ja')


def collapsed_windows(words, game):
    """Locate clusters of collapsed nuclei near an unexplained alignment gap."""
    lexical = [i for i, word in enumerate(words) if word.text not in game._NON_SINGABLE_WORD_TOKENS]
    windows = []
    consumed = -1
    for position, index in enumerate(lexical):
        if position <= consumed:
            continue
        word = words[index]
        nucleus = game._find_word_nucleus_start(word, 'zh')
        if nucleus is None or word.end - nucleus >= .045:
            continue
        nearby = lexical[position:position + 5]
        collapsed = sum((start := game._find_word_nucleus_start(words[i], 'zh')) is not None
                        and words[i].end - start < .045 for i in nearby)
        region = words[index:nearby[-1] + 1]
        unexplained_gap = any(w.text in game._NON_SINGABLE_WORD_TOKENS and w.end - w.start >= .20 for w in region)
        if collapsed < 2 or not unexplained_gap:
            continue
        # Include enough following words to anchor the new alignment. A
        # narrow crop can simply push the same error onto its final word.
        last = min(len(lexical) - 1, position + 7)
        while last > position + 4 and words[lexical[last]].end - word.start > 6:
            last -= 1
        if last - position < 4:
            continue
        windows.append((index, lexical[last]))
        consumed = last
    return windows


def alignment_coverage(words, game):
    tokens = [w.text for w in words if w.text not in game._NON_SINGABLE_WORD_TOKENS]
    durations, flags, _ = chinese_boundaries(words, tokens, game)
    return sum(flag and duration >= .06 for duration, flag in zip(durations, flags))


def retry_collapsed_alignment(model, directory, predictions, language, native_run,
                              cancel_checker=None, use_phoneme_g2p=False):
    """Retry only suspicious local Chinese windows, using the loaded HFA model."""
    import logging
    import tempfile
    from pathlib import Path
    import soundfile as sf
    from inference.API import game_api as game
    if language != 'zh' or use_phoneme_g2p:
        return []
    audit = []
    # Retry inference replaces these lists. Restore the original run before
    # optional TextGrid export or the next voice reuses the same model.
    saved = {key: getattr(model, key, None) for key in ('dataset', 'predictions', 'alignment_errors', 'dataset_errors')}
    try:
        for stem, prediction in predictions.items():
            if len(audit) >= 8:
                break
            words = prediction[2]
            for first, last in reversed(collapsed_windows(words, game)):
                if len(audit) >= 8:
                    break
                if cancel_checker and cancel_checker():
                    raise InterruptedError('音节重对齐已取消。')
                original = list(words[first:last + 1])
                lexical = [w for w in original if w.text not in game._NON_SINGABLE_WORD_TOKENS]
                start, end = max(0., original[0].start - .08), min(float(prediction[1]), original[-1].end + .02)
                entry = {'chunk': stem, 'start': start, 'end': end,
                         'before_usable': alignment_coverage(original, game), 'accepted': False}
                try:
                    with tempfile.TemporaryDirectory(prefix='vwa-align-retry-') as tmp:
                        folder = Path(tmp)
                        with sf.SoundFile(str(Path(directory)/(stem+'.wav'))) as audio:
                            rate = audio.samplerate
                            audio.seek(round(start * rate))
                            samples = audio.read(round((end-start)*rate), dtype='float32', always_2d=True).mean(axis=1)
                        sf.write(folder/'retry.wav', samples, rate)
                        (folder/'retry.lab').write_text(' '.join(w.text for w in lexical), encoding='utf-8')
                        retried = native_run(model, folder, language=language, cancel_checker=cancel_checker)
                    candidate = retried.get('retry')
                    replacement = copy.deepcopy(candidate[2]) if candidate else []
                    for word in replacement:
                        word.start += start; word.end += start
                        for phone in word.phonemes:
                            phone.start += start; phone.end += start
                    bounded = []
                    lower, upper = original[0].start, original[-1].end
                    for word in replacement:
                        phones = [phone for phone in word.phonemes if phone.end > lower and phone.start < upper]
                        for phone in phones:
                            phone.start = max(lower, phone.start)
                            phone.end = min(upper, phone.end)
                        if phones:
                            word.phonemes = phones
                            word.start, word.end = phones[0].start, phones[-1].end
                            bounded.append(word)
                    replacement = bounded
                    names = [w.text for w in replacement if w.text not in game._NON_SINGABLE_WORD_TOKENS]
                    score = alignment_coverage(replacement, game)
                    entry['after_usable'] = score
                    if (names == [w.text for w in lexical] and score >= entry['before_usable'] + 2
                            and all(w.end > w.start for w in replacement)):
                        words[first:last + 1] = replacement
                        entry['accepted'] = True
                        logging.getLogger(__name__).info('音节重对齐：%s %.2f–%.2f s，可用音节 %d → %d',
                            stem, start, end, entry['before_usable'], score)
                except (ValueError, KeyError, IndexError, OSError) as error:
                    entry['error'] = type(error).__name__ + ': ' + str(error)
                audit.append(entry)
    finally:
        for key, value in saved.items():
            if value is not None:
                setattr(model, key, value)
            elif hasattr(model, key):
                delattr(model, key)
    return audit


def connect_phrase_notes(notes, syllables):
    """Close short within-phrase articulation gaps without bridging rests."""
    changes = []
    for left, right in zip(notes, notes[1:]):
        gap = right.onset - left.offset
        if not .001 < gap <= CONSONANT_LIMIT:
            continue
        previous = [i for i, u in enumerate(syllables) if u['start'] < left.offset and u['end'] > left.onset]
        following = [i for i, u in enumerate(syllables) if u['start'] < right.offset and u['end'] > right.onset]
        if not previous or not following:
            continue
        a, b = previous[-1], following[0]
        if a == b or (b == a + 1 and syllables[b]['start'] - syllables[a]['end'] <= .08):
            changes.append({'start': left.onset, 'old_end': left.offset, 'new_end': right.onset})
            left.offset = right.onset
    return changes


def retry_voiced_pauses(model, outcome, chunks, sr, pitch, native_run, cancel_checker=None, audio_path=None):
    """Re-align short phrases when HFA labels a measured sung vowel as SP.

    Uses existing recognized phonemes and the already loaded HFA model. It
    neither guesses lyric timing nor bridges an acoustically silent rest.
    """
    import numpy as np
    import tempfile
    from pathlib import Path
    import soundfile as sf
    from inference.API import game_api as game
    if pitch is None or not outcome:
        return []
    step = pitch.time_step_seconds
    def voiced(chunk,a,b):
        left=max(0,math.ceil((chunk['offset']+a)/step))
        right=min(len(pitch.midi_pitch),math.ceil((chunk['offset']+b)/step))
        if right<=left:return 0.
        valid=np.isfinite(pitch.midi_pitch[left:right])
        if pitch.voiced_mask is not None:valid &= pitch.voiced_mask[left:right]
        return float(valid.mean())
    def missing(words,chunk):
        return sum((w.end-w.start)*voiced(chunk,w.start,w.end) for w in words
            if w.text in game._NON_SINGABLE_WORD_TOKENS and w.end-w.start>=.2)
    saved = {key:getattr(model,key,None) for key in ('dataset','predictions','alignment_errors','dataset_errors')}
    audit,budget=[],0.
    try:
        for index,chunk in enumerate(chunks):
            stem='chunk_'+str(index)
            prediction=outcome.pred_dict.get(stem)
            if not prediction:continue
            words=prediction[2]
            # Work right-to-left so earlier index ranges stay valid.
            windows=[]
            for pos,w in enumerate(words):
                if (w.text not in game._NON_SINGABLE_WORD_TOKENS or w.end-w.start<.25
                        or voiced(chunk,w.start+.02,w.end-.02)<.8):continue
                if pos==0 or pos+1==len(words):continue
                if (words[pos-1].text in game._NON_SINGABLE_WORD_TOKENS
                        or words[pos+1].text in game._NON_SINGABLE_WORD_TOKENS):continue
                sample=chunk['waveform'][round(w.start*sr):round(w.end*sr)]
                if not len(sample) or float(np.mean(sample**2))<1e-7:continue
                quiet=[k for k,q in enumerate(words) if q.text in game._NON_SINGABLE_WORD_TOKENS
                    and q.end-q.start>=.1 and voiced(chunk,q.start,q.end)<.35]
                first=max([k+1 for k in quiet if k<pos],default=0)
                last=min([k-1 for k in quiet if k>pos],default=len(words)-1)
                while first<pos and words[first].text in game._NON_SINGABLE_WORD_TOKENS:first+=1
                while last>pos and words[last].text in game._NON_SINGABLE_WORD_TOKENS:last-=1
                lexical=[q for q in words[first:last+1] if q.text not in game._NON_SINGABLE_WORD_TOKENS]
                if not 3<=len(lexical)<=16 or words[last].end-words[first].start>6.:continue
                if not any(a<=pos<=b for a,b in windows):windows.append((first,last))
            for first,last in reversed(windows):
                if len(audit)>=8:break
                if cancel_checker and cancel_checker():raise InterruptedError('局部歌词对齐已取消。')
                original=list(words[first:last+1]);lexical=[w for w in original if w.text not in game._NON_SINGABLE_WORD_TOKENS]
                for margin in (.15,.17,.13,.25):
                    if len(audit)>=8:break
                    duration = sf.info(str(audio_path)).duration if audio_path else len(chunk['waveform'])/sr
                    start=max(-chunk['offset'] if audio_path else 0.,original[0].start-margin)
                    end=min(duration-chunk['offset'] if audio_path else duration,original[-1].end+margin)
                    if budget+end-start>32.:continue
                    budget+=end-start
                    entry={'chunk':stem,'start':start+chunk['offset'],'end':end+chunk['offset'],
                        'context_seconds':margin, 'before_voiced_pause':round(missing(original,chunk),4),'accepted':False}
                    try:
                        with tempfile.TemporaryDirectory(prefix='vwa-phrase-retry-') as tmp:
                            folder=Path(tmp)
                            if audio_path:
                                with sf.SoundFile(str(audio_path)) as source:
                                    source.seek(round((start+chunk['offset'])*source.samplerate))
                                    samples=source.read(round((end-start)*source.samplerate),dtype='float32',always_2d=True).mean(axis=1)
                                    sf.write(folder/'retry.wav',samples,source.samplerate)
                            else:
                                sf.write(folder/'retry.wav',chunk['waveform'][round(start*sr):round(end*sr)],sr)
                            (folder/'retry.lab').write_text(' '.join(w.text for w in lexical),encoding='utf-8')
                            candidate=native_run(model,folder,language='zh',cancel_checker=cancel_checker).get('retry')
                        replacement=copy.deepcopy(candidate[2]) if candidate else []
                        for w in replacement:
                            w.start+=start;w.end+=start
                            for p in w.phonemes:p.start+=start;p.end+=start
                        # Keep exactly the original phrase bounds and phoneme order.
                        bounded=[]
                        lower,upper=original[0].start,original[-1].end
                        for w in replacement:
                            phones=[p for p in w.phonemes if p.end>lower and p.start<upper]
                            for p in phones:p.start=max(lower,p.start);p.end=min(upper,p.end)
                            if phones:
                                w.phonemes=phones;w.start=phones[0].start;w.end=phones[-1].end;bounded.append(w)
                        replacement=bounded
                        names=[w.text for w in replacement if w.text not in game._NON_SINGABLE_WORD_TOKENS]
                        new_lexical=[w for w in replacement if w.text not in game._NON_SINGABLE_WORD_TOKENS]
                        after=missing(replacement,chunk)
                        entry['after_voiced_pause']=round(after,4)
                        if (names==[w.text for w in lexical] and new_lexical
                                and all(w.end>w.start for w in replacement)
                                and all(a.end<=b.start+1e-6 for a,b in zip(replacement,replacement[1:]))
                                and alignment_coverage(replacement,game)>=alignment_coverage(original,game)
                                and after<=entry['before_voiced_pause']-.15
                                and abs(game._find_word_nucleus_start(new_lexical[0],'zh')-
                                    game._find_word_nucleus_start(lexical[0],'zh'))<=.12
                                and abs(new_lexical[-1].end-lexical[-1].end)<=.12):
                            words[first:last+1]=replacement;entry['accepted']=True
                    except (ValueError,TypeError,KeyError,IndexError,OSError) as error:
                        entry['error']=type(error).__name__+': '+str(error)
                    audit.append(entry)
                    if entry['accepted']:break
    finally:
        for key,value in saved.items():
            if value is not None:setattr(model,key,value)
            elif hasattr(model,key):delattr(model,key)
    return audit


def syllables_from_alignment(outcome, chunks, language):
    from inference.API import game_api as game
    records = []
    for i, chunk in enumerate(chunks):
        stem = 'chunk_' + str(i)
        prediction = outcome.pred_dict.get(stem)
        if not prediction:
            continue
        durations, voiced, lyrics = game.extract_vowel_boundaries(
            prediction[2], outcome.chars_dict.get(stem, []), language=language)
        position = float(chunk['offset'])
        for duration, flag, lyric in zip(durations, voiced, lyrics):
            if flag and duration >= .025:
                records.append({'start': position, 'end': position + duration, 'lyric': lyric})
            position += duration
    return sorted(records, key=lambda row: row['start'])


def unvoiced_from_alignment(outcome, chunks, language):
    """Conservative shared inventory; voiced/unknown consonants stay untouched."""
    unvoiced = {'p','t','k','s','sh','ch','ts','f','h','cl'} if language == 'ja' else {
        'p','t','k','s','sh','ch','c','q','f','h','x'} if language == 'zh' else set()
    records = []
    for index, chunk in enumerate(chunks):
        prediction = outcome.pred_dict.get('chunk_' + str(index))
        if not prediction:
            continue
        offset = float(chunk['offset'])
        for word in prediction[2]:
            for phone in word.phonemes:
                text = str(phone.text).split('/')[-1].strip().lower()
                if text in unvoiced and phone.end-phone.start > .02:
                    records.append({'start':offset+float(phone.start), 'end':offset+float(phone.end), 'phone':text})
    return sorted(records, key=lambda row:row['start'])


def japanese_tail_windows(outcome, chunks):
    """Retry recognized phrase-final moras, including contiguous weak endings."""
    from inference.API import game_api as game
    windows = []
    for chunk_index, chunk in enumerate(chunks):
        stem = 'chunk_' + str(chunk_index)
        prediction = outcome.pred_dict.get(stem)
        if not prediction:
            continue
        words, tokens = prediction[2], outcome.chars_dict.get(stem, [])
        lexical = [(index, word) for index, word in enumerate(words) if word.text not in game._NON_SINGABLE_WORD_TOKENS]
        for position, (index, word) in enumerate(lexical):
            nucleus = game._find_word_nucleus_start(word, 'ja')
            if (position == 0 or nucleus is None or word.end-nucleus >= .045
                    or (index+1 < len(words) and words[index+1].text not in game._NON_SINGABLE_WORD_TOKENS)):
                continue
            prior_index, previous = lexical[position-1]
            prior_nucleus = game._find_word_nucleus_start(previous, 'ja')
            gap = word.start-previous.end
            if (prior_nucleus is None or previous.end-prior_nucleus < .06 or not -.001 <= gap <= 2.):
                continue
            # A final vowel can be truncated into trailing SP. Give the same
            # recognized token a little acoustic context beyond that boundary.
            upper=float(word.end)
            if index+1<len(words) and words[index+1].text in game._NON_SINGABLE_WORD_TOKENS:
                upper=min(float(words[index+1].end),upper+.25,float(prediction[1]))
            lyric = str(tokens[position] if position < len(tokens) else word.text)
            windows.append({'chunk':chunk_index,'left':max(0.,float(previous.end)-.10),
                'right':min(float(prediction[1]),upper+.05),'anchor_end':float(previous.end),
                'tail_end':upper,'lyric':lyric,'asr_mora':word.text,
                'placeholder_start':float(nucleus),'placeholder_end':float(word.end),
                'collapsed_seconds':float(word.end-nucleus)})
    return windows


def accept_japanese_tails(proposals, result, syllables, energy):
    """Retain the recognized mora only where local GAME and voiced f0 agree."""
    import numpy as np
    if not proposals or result is None:
        return [], [{'status':'skipped','reason':'no_independent_pitch'}] if proposals else []
    times, db = energy
    anchor = np.zeros(len(times),dtype=bool)
    for unit in syllables:
        anchor |= (times >= unit['start']) & (times < unit['end'])
    baseline = float(np.percentile(db[anchor],80)) if anchor.any() else float(np.percentile(db,90))
    floor = max(-55.,baseline-22.)
    added, audit = [], []
    for proposal in proposals:
        accepted = []
        for candidate in proposal['notes']:
            start,end = candidate['onset'],candidate['offset']
            # GAME may extend a release into silence. Trim unsupported margins
            # instead of rejecting an otherwise well-supported sung tail.
            step = result.time_step_seconds
            first,last = max(0,math.ceil(start/step)),min(len(result.midi_pitch),math.ceil(end/step))
            values = result.midi_pitch[first:last]
            valid = np.isfinite(values) & (abs(values-candidate['pitch']) <= 1.5)
            if result.voiced_mask is not None:
                valid &= result.voiced_mask[first:last]
            supported = np.flatnonzero(valid)
            if len(supported):
                start = max(start,(first+int(supported[0]))*step)
                end = min(end,(first+int(supported[-1])+1)*step)
            if end-start < .06 or any(min(end,u['end'])-max(start,u['start']) > .025 for u in syllables+added):
                continue
            measured = _pitch_summary(result,start,end)
            brief = end-start <= .18
            if (not measured or measured['count'] < (4 if brief else 8) or measured['fraction'] < .85
                    or measured.get('spread',100) > (1. if brief else 1.2)
                    or abs(measured['median']-candidate['pitch']) > .9
                    or _interval_level(times,db,start,end) < floor):
                continue
            accepted.append({'start':start,'end':end,'lyric':proposal['lyric'] if not accepted else '-',
                             'source':proposal.get('source','local_GAME_tail')})
        # A skipped middle candidate leaves an ambiguous melisma. Do not assign
        # one recognized mora across a disconnected collection of vocal sounds.
        if accepted and any(b['start']-a['end']>.08 for a,b in zip(accepted,accepted[1:])):
            accepted=[]
        added.extend(accepted)
        audit.append({k:v for k,v in proposal.items() if k!='notes'} | {'candidate_notes':proposal['notes'],
            'status':'applied' if accepted else 'skipped','accepted':accepted,'energy_floor_db':round(floor,2)})
    return added,audit


def chinese_gap_windows(outcome,chunks,pitch,sr=44100):
    """Locate one known collapsed token between voiced, intact word anchors."""
    from inference.API import game_api as game
    if pitch is None:return []
    windows=[]
    for i,chunk in enumerate(chunks):
        stem='chunk_'+str(i);prediction=outcome.pred_dict.get(stem)
        if not prediction:continue
        words=prediction[2];tokens=outcome.chars_dict.get(stem,[])
        lexical=[(k,w) for k,w in enumerate(words) if w.text not in game._NON_SINGABLE_WORD_TOKENS]
        for pos,(index,w) in enumerate(lexical):
            nucleus=game._find_word_nucleus_start(w,'zh')
            if pos==0 or pos+1>=len(lexical) or nucleus is None or w.end-nucleus>=.025:continue
            a,previous=lexical[pos-1];b,following=lexical[pos+1]
            p=game._find_word_nucleus_start(previous,'zh');q=game._find_word_nucleus_start(following,'zh')
            lower,upper=float(previous.end),float(following.start)
            if (p is None or q is None or previous.end-p<.06 or following.end-q<.06
                    or not .08<=upper-lower<=2. or pos>=len(tokens)
                    or not any(x.text in game._NON_SINGABLE_WORD_TOKENS for x in words[a+1:b])):continue
            measured=_pitch_summary(pitch,chunk['offset']+lower,chunk['offset']+upper)
            if not measured or measured['count']<6 or measured['fraction']<.85:continue
            windows.append({'chunk':i,'left':max(0.,lower-.08),
                'right':min(len(chunk['waveform'])/sr,upper+.08),'anchor_end':lower,'tail_end':upper,
                'placeholder_start':nucleus,'placeholder_end':float(w.end),'lyric':str(tokens[pos]),
                'collapsed_seconds':float(w.end-nucleus),'source':'local_GAME_internal'})
    return windows


def bounded_candidates(notes,lower,upper,*,internal=False):
    """Clip internal-gap proposals; tail proposals must begin after the anchor."""
    return [{**vars(n),'onset':max(lower,n.onset),'offset':min(upper,n.offset)} for n in notes
        if (internal or n.onset>=lower-.02) and min(upper,n.offset)-max(lower,n.onset)>=.06]


def continue_releases(notes, result, syllables, energy):
    """Follow supported phrase-final vowels past prematurely aligned offsets.

    A strong voiced anchor starts the continuation; brief evidence dropouts can
    be crossed but the last supported frame determines the end. Existing lyric
    and neighboring note boundaries remain authoritative. No new text/pitch is
    invented, and pitch-only/unaligned passages do not qualify.
    """
    import numpy as np
    if result is None or energy is None or not syllables:return notes,[]
    times,db=energy;step=result.time_step_seconds
    output=[copy.copy(n) for n in notes];audit=[]
    for i,note in enumerate(output):
        if note.offset-note.onset<.08:continue
        anchors=[u for u in syllables if u['start']<=note.offset-.03
            and u['end']>=note.offset-.06 and min(u['end'],note.offset)-max(u['start'],note.onset)>=.04]
        if not anchors:continue
        unit=max(anchors,key=lambda u:u['end'])
        next_unit=min((u['start'] for u in syllables if u['start']>=note.offset-.005
            and u is not unit),default=float('inf'))
        next_note=output[i+1].onset if i+1<len(output) else float('inf')
        limit=min(note.offset+.6,next_unit,next_note,len(result.midi_pitch)*step)
        if limit-note.offset<.12:continue
        seed_start=max(note.onset,note.offset-.12)
        seed=_pitch_summary(result,seed_start,note.offset)
        if (not seed or seed.get('count',0)<6 or seed['fraction']<.8 or seed['mad']>.25
                or abs(seed['median']-round(note.pitch))>.8):continue
        body_level=_interval_level(times,db,seed_start,note.offset)
        if body_level<-55:continue
        floor=max(-65.,body_level-18.)
        first=max(0,math.ceil((note.offset-1e-9)/step));last=min(len(result.midi_pitch),math.floor(limit/step))
        indices=np.arange(first,last)
        values=result.midi_pitch[first:last]
        levels=np.interp(indices*step,times,db)
        valid=np.isfinite(values)&(abs(values-seed['median'])<=.85)&(levels>=floor)
        if result.voiced_mask is not None:valid&=result.voiced_mask[first:last]
        # A new strong attack after an energy dip is a separate event, not a
        # vowel release to absorb into the preceding note.
        bad=0;last_good=None;good=0;previous_level=body_level
        for j,ok in enumerate(valid):
            if levels[j]>previous_level+8.:break
            previous_level=float(levels[j])
            if ok:bad=0;last_good=j;good+=1
            else:bad+=1
            if bad*step>.020001:break
        if last_good is None:continue
        end=min(limit,(first+last_good+1)*step)
        if end-note.offset<.04 or good/(last_good+1)<.85:continue
        audit.append({'start':note.onset,'old_end':note.offset,'new_end':end,'lyric':note.lyric,
            'source':'voiced_release_continuity','supported_frames':good,
            'energy_floor_db':round(floor,2),'maximum_extension_seconds':.6})
        note.offset=end
    return output,audit


def _pitch_summary(result, start, end):
    import numpy as np
    if result is None:
        return None
    step = result.time_step_seconds
    margin = min(.04, max(0, end - start) / 6)
    a, b = max(0, math.ceil((start + margin) / step)), min(len(result.midi_pitch), math.ceil((end - margin) / step))
    values = result.midi_pitch[a:b]
    mask = np.isfinite(values)
    if result.voiced_mask is not None:
        mask &= result.voiced_mask[a:b]
    if not mask.any():
        return {'fraction': 0., 'count': 0}
    values = values[mask]
    median = float(np.median(values))
    return {'fraction': float(mask.mean()), 'count': len(values), 'median': median,
            'mad': float(np.median(abs(values - median))),
            'spread': float(np.percentile(values, 90) - np.percentile(values, 10))}


def _interval_level(times, db, start, end):
    import numpy as np
    left,right=np.searchsorted(times,[start,end])
    return float(np.median(db[left:right])) if right>left else float(np.interp((start+end)/2,times,db))


def _legacy_plateau_splits(note,result):
    """Existing long-note rule, retained for established multi-step melismas."""
    import numpy as np
    if result is None or note.offset-note.onset<.4:return []
    step=result.time_step_seconds;width=max(8,math.ceil(.14/step))
    first=max(0,math.ceil((note.onset+.04)/step));last=min(len(result.midi_pitch),math.floor((note.offset-.04)/step))
    previous=None;boundaries=[]
    for index in range(first,last-width+1,max(1,width//3)):
        values=result.midi_pitch[index:index+width];valid=np.isfinite(values)
        if result.voiced_mask is not None:valid&=result.voiced_mask[index:index+width]
        if valid.mean()<.85:continue
        values=values[valid];median=float(np.median(values))
        if np.percentile(values,90)-np.percentile(values,10)>.45:continue
        if previous is None:previous=median
        elif abs(median-previous)>=1.7:
            target=(previous+median)/2;lo=max(first,index-width)
            segment=result.midi_pitch[lo:index+width//2];candidates=np.flatnonzero(np.isfinite(segment))
            if not len(candidates):continue
            direction=1 if median>previous else -1
            crossing=candidates[(segment[candidates]-target)*direction>=0]
            at=lo+int(crossing[0] if len(crossing) else candidates[np.argmin(abs(segment[candidates]-target))])
            seconds=at*step
            if seconds-(boundaries[-1] if boundaries else note.onset)>=.10 and note.offset-seconds>=.12:
                boundaries.append(seconds);previous=median
        else:previous=median
    return boundaries


def _plateau_splits(note, result, *, confirmed=(), propose=False):
    """New brief/adjacent-step splits require a local GAME boundary as well.

    The existing long-melisma rule remains available. Contour-only short-note
    proposals are research evidence until the normal worker corroborates them
    with local audio inference; no human score is consulted at runtime.
    """
    import numpy as np
    established=_legacy_plateau_splits(note,result)
    if result is None or note.offset - note.onset < .24:
        return established
    step = result.time_step_seconds
    width = max(6, math.ceil(.08 / step))
    first = max(0, math.ceil((note.onset - 1e-9) / step))
    last = min(len(result.midi_pitch), math.floor((note.offset - .015) / step))
    plateaus=[]
    for index in range(first,last-width+1):
        values = result.midi_pitch[index:index+width]
        valid = np.isfinite(values)
        if result.voiced_mask is not None:
            valid &= result.voiced_mask[index:index+width]
        if valid.mean()<.875:
            continue
        positions=np.flatnonzero(valid)*step;values=values[valid]
        median=float(np.median(values));key=round(median)
        # A continuous slope can cross several semitones without representing
        # several notes. Require a compact, nearly stationary pitch core.
        slope=float(np.dot(positions-positions.mean(),values-values.mean())/
                    max(np.sum((positions-positions.mean())**2),1e-12))
        if (abs(median-key)>.23 or np.percentile(values,90)-np.percentile(values,10)>.45
                or abs(slope)>1.8):
            continue
        if plateaus and plateaus[-1]['key']==key and (index-plateaus[-1]['end'])*step<=.04:
            plateaus[-1]['end']=index+width
        else:
            plateaus.append({'start':index,'end':index+width,'key':key,'median':median})
    # Repeated rapid returns around a center are pitch modulation. Retain the
    # measured curve rather than turning each vibrato peak into a MIDI note.
    oscillating=set();modulation_spans=[]
    for i in range(len(plateaus)-2):
        a,b,c=plateaus[i:i+3]
        if (a['key']==c['key'] and abs(b['key']-a['key'])<=2
                and (c['end']-a['start'])*step<=.65):
            trace=result.midi_pitch[a['start']:c['end']]
            adjacent=np.isfinite(trace[:-1])&np.isfinite(trace[1:])
            # Flat alternating steps can be an intentional trill. Suppress
            # smooth periodic modulation, retaining clear step transitions.
            if np.any(abs(np.diff(trace)[adjacent])>=.5):continue
            oscillating.update((i,i+1));modulation_spans.append((a['start']*step,c['end']*step))
    established=[boundary for boundary in established if not any(a<=boundary<=b for a,b in modulation_spans)]
    boundaries=[]
    for i,(previous,current) in enumerate(zip(plateaus,plateaus[1:])):
        if i in oscillating or current['key']==previous['key']:continue
        if (current['start']-previous['end'])*step>.18:continue
        lo=(previous['start']+previous['end'])//2
        hi=(current['start']+current['end'])//2
        segment=result.midi_pitch[lo:hi+1]
        valid=np.isfinite(segment)
        if result.voiced_mask is not None:valid&=result.voiced_mask[lo:hi+1]
        candidates=np.flatnonzero(valid)
        target=(previous['median']+current['median'])/2
        direction=1 if current['key']>previous['key'] else -1
        crossing=candidates[(segment[candidates]-target)*direction>=0]
        if not len(crossing):continue
        seconds=(lo+int(crossing[0]))*step
        if seconds-(boundaries[-1] if boundaries else note.onset)>=.07 and note.offset-seconds>=.07:
            boundaries.append(seconds)
    return sorted(established+[b for b in boundaries if all(abs(b-a)>=.08 for a in established)
                  and (propose or any(abs(b-a)<=.015 for a in confirmed))])


def confirm_note_boundaries(boundaries,candidates,pitch):
    """Require a local GAME split and matching pitch centers on both sides."""
    approved=[]
    for boundary in boundaries:
        left=_pitch_summary(pitch,boundary-.09,boundary-.01)
        right=_pitch_summary(pitch,boundary+.01,boundary+.09)
        if not left or not right or left.get('count',0)<5 or right.get('count',0)<5:continue
        for a,b in zip(candidates,candidates[1:]):
            if (abs(a.offset-boundary)<=.06 and abs(b.onset-boundary)<=.06
                    and a.offset-a.onset>=.06 and b.offset-b.onset>=.06
                    and abs(round(a.pitch)-round(left['median']))<=.5
                    and abs(round(b.pitch)-round(right['median']))<=.5
                    and abs(a.pitch-round(a.pitch))<=.3
                    and abs(b.pitch-round(b.pitch))<=.3
                    # Rounding alone can hide almost a semitone of disagreement.
                    # Compare the measured centers too, before creating a note.
                    and abs(a.pitch-left['median'])<=.45
                    and abs(b.pitch-right['median'])<=.45):
                approved.append(boundary);break
    return approved


def verify_acoustic_pitch(path, start, end, expected):
    """Check large disagreements with an independent, weight-free YIN estimate."""
    import numpy as np
    import soundfile as sf
    import librosa
    from pitch_accuracy import prepare_audio
    duration = min(1., end-start)
    if duration < .16:
        return False
    center = (start+end)/2
    with sf.SoundFile(str(path)) as audio:
        audio.seek(max(0,round((center-duration/2)*audio.samplerate)))
        samples=audio.read(round(duration*audio.samplerate),dtype='float32',always_2d=True)
        rate=audio.samplerate
    x=prepare_audio(samples,rate)
    if len(x)<2560:
        return False
    f0=librosa.yin(x,sr=16000,fmin=65,fmax=1400,frame_length=2048,hop_length=160)
    values=69+12*np.log2(np.maximum(f0[3:-3],1e-12)/440)
    return bool(len(values)>=5 and abs(float(np.median(values))-expected)<.45
                and np.percentile(values,90)-np.percentile(values,10)<1.)


def aligned_coverage_gaps(notes, syllables, result, exclusions=()):
    """Expose unresolved timed syllables without inventing notes or lyrics.

    This is an inspection aid, not an error gold label. In particular, a
    varying voiced contour may be an ornament or an alignment mistake; broad
    pitch support is not permission to turn its median into a new note.
    """
    intentional=[n for n in exclusions if n.get('reason') in ('quiet_unmatched_filler','residual_shared_lead')]
    ordered=sorted([(n.onset,n.offset) for n in notes]+
                   [(n['onset'],n['offset']) for n in intentional])
    gaps=[]
    for index,unit in enumerate(syllables):
        start,end=unit['start'],unit['end']
        minimum=.06 if end-start<=.2 else .1
        cursor=start;missing=[]
        for onset,offset in ordered:
            if offset<=cursor or onset>=end:continue
            if onset-cursor>=minimum-1e-9:missing.append((cursor,min(end,onset)))
            cursor=min(end,max(cursor,offset))
        if end-cursor>=minimum-1e-9:missing.append((cursor,end))
        for left,right in missing:
            measured=_pitch_summary(result,left,right)
            supported=bool(measured and measured.get('count',0)>=(4 if right-left<=.18 else 8)
                           and measured['fraction']>=.8)
            gaps.append({'syllable_index':index,'lyric':unit['lyric'],
                'start':left,'end':right,'unit_start':start,'unit_end':end,
                'status':'voiced_uncovered' if supported else
                         'pitch_unavailable' if result is None else 'insufficient_pitch_evidence',
                'pitch_evidence':measured,'confirmed_missed_note':False})
    return {'status':'inspection_only','voiced_gap_count':sum(g['status']=='voiced_uncovered' for g in gaps),
            'gaps':gaps,'intentional_exclusions_ignored':len(intentional),
            'limits':'Only already timed syllables are checked; missing ASR text and score/listening truth remain unknown.'}


def refine_notes(notes, result, syllables=(), energy=None, verify_pitch=None, reference_text='', *, confirmed_boundaries=(), merge_aligned_continuations=False):
    """Preserve raw notes in the report; never guess missing lyric characters."""
    import numpy as np
    report = {'algorithm_version': ALGORITHM_VERSION, 'input_notes': len(notes),
              'removed': [], 'pitch_changes': [], 'splits': [], 'recovered': [], 'phrase_rests': [],
              'aligned_continuations': [],
              'aligned_continuation_policy': 'experimental_extend' if merge_aligned_continuations else 'separate'}
    output = []
    if energy is not None:
        times, db = energy
        anchors = np.zeros(len(times), dtype=bool)
        for unit in syllables:
            anchors |= (times >= unit['start']) & (times < unit['end'])
        baseline = float(np.percentile(db[anchors], 80)) if anchors.any() else float(np.percentile(db, 90))
        floor = max(-55., baseline - 26)
        report['fallback_energy_floor_db'] = round(floor, 2)
    else:
        floor = -math.inf
    for original in sorted(notes, key=lambda n: n.onset):
        note = copy.copy(original)
        units = [row for row in syllables if min(row['end'], note.offset) - max(row['start'], note.onset) >= .025]
        summary = _pitch_summary(result, note.onset, note.offset)
        level = _interval_level(times,db,note.onset,note.offset) if energy is not None else 0.
        # With a substantial reference selected, a quiet unmatched filler in
        # an interlude is not enough evidence for a synthesized sung vowel.
        # Strong ad-libs, explicitly referenced fillers and no-reference runs stay.
        fillers = {'啊','呀','啦','哦','喔','嗯','呜'}
        unsupported_filler = (len(reference_text)>=10 and energy is not None and bool(units)
            and all(str(unit['lyric']).strip() in fillers and str(unit['lyric']).strip() not in reference_text for unit in units)
            and level < baseline-10)
        if unsupported_filler:
            report['removed'].append({**vars(original),'reason':'quiet_unmatched_filler','level_db':round(level,2)})
            continue
        if not units and summary is not None:
            unsupported = summary['fraction'] < .2
            unstable = summary.get('spread', 0) > 4 and summary['fraction'] < .85
            if level < floor or unsupported or unstable:
                report['removed'].append({**vars(original), 'reason': 'unsupported_pitch_only', 'level_db': round(level, 2)})
                continue
        # Timed lyric anchors can describe fast syllables shorter than 70 ms.
        # Keep their boundaries; pitch-only subdivisions remain more conservative.
        lyric_boundaries = [row['start'] for row in units
                            if row['start'] - note.onset >= .035-1e-9 and note.offset - row['start'] >= .035-1e-9]
        pitch_boundaries = _plateau_splits(note, result,confirmed=confirmed_boundaries)
        boundaries = [(boundary, .035) for boundary in lyric_boundaries]
        boundaries += [(boundary, .07) for boundary in pitch_boundaries
                       if all(abs(boundary-anchor) >= .08 for anchor in lyric_boundaries)]
        selected = []
        for boundary, minimum in sorted(boundaries):
            if (boundary - (selected[-1] if selected else note.onset) >= minimum-1e-9
                    and note.offset - boundary >= minimum-1e-9):
                selected.append(boundary)
        segments = list(zip([note.onset, *selected], [*selected, note.offset]))
        for previous, following in zip(units, units[1:]):
            left, right = previous['end'], following['start']
            if right-left <= PAUSE_LIMIT:
                continue
            pause = _pitch_summary(result, left, right)
            quiet = energy is not None and _interval_level(times,db,left,right) < floor
            if not quiet and (pause is None or pause['fraction'] >= .2):
                continue
            # A raw note may span two aligned phrases. Preserve both syllables
            # while removing only the unsupported, explicitly bounded pause.
            segments = [(a,b) for start,end in segments
                        for a,b in ((start,min(end,left)),(max(start,right),end)) if b>a]
            report['phrase_rests'].append({'start':left,'end':right})
        for index, (start, end) in enumerate(segments):
            item = copy.copy(note); item.onset, item.offset = start, end
            matching = [row for row in units if row['start'] <= start + .035 and row['end'] > start
                        and min(end,row['end'])-max(start,row['start']) >= min(.025,(end-start)*.5)-1e-9]
            if matching:
                chosen = min(matching,key=lambda row:abs(row['start']-start))
                # A continuation does not repeat the word on each melodic note.
                item.lyric = chosen['lyric'] if abs(start - chosen['start']) <= .05 else (note.lyric if index == 0 else '-')
            elif index:
                item.lyric = '-'
            measured = _pitch_summary(result, start, end)
            brief = end-start <= .18 and measured and measured['count'] < 8
            if (measured and measured['count'] >= (5 if brief else 8)
                    and measured['fraction'] >= (.85 if brief else .7)
                    and measured.get('mad', 100) <= (.18 if brief else .28)
                    and measured.get('spread', 100) <= (.55 if brief else .9)):
                key = round(measured['median'])
                distance = abs(measured['median'] - round(item.pitch))
                # Small note-center errors can be corrected from a stable plateau.
                # Large disagreements need review rather than an octave guess.
                small = .75 <= distance <= 2.3 and abs(measured['median']-key)<=.3
                independent = (2.3 < distance <= 24 and bool(units) and verify_pitch is not None
                    and verify_pitch(start,end,measured['median']))
                new_plateau = index > 0 and any(abs(start-boundary) <= .08 for boundary in pitch_boundaries)
                if key != round(item.pitch) and (small or independent or new_plateau):
                    report['pitch_changes'].append({'start': start, 'old': item.pitch, 'new': key,
                        'evidence_frames': measured['count'], 'brief_evidence': bool(brief)})
                    item.pitch = key
            output.append(item)
        if selected:
            report['splits'].append({'start': note.onset, 'end': note.offset, 'boundaries': selected})
    # Recover only a genuinely aligned syllable with voiced evidence, not a
    # guessed token in an instrumental region or an arbitrary equal subdivision.
    if result is not None:
        for unit in syllables:
            start, end = unit['start'], unit['end']
            if (len(reference_text)>=10 and energy is not None and str(unit['lyric']).strip() in {'啊','呀','啦','哦','喔','嗯','呜'}
                    and str(unit['lyric']).strip() not in reference_text
                    and _interval_level(times,db,start,end)<baseline-10):
                continue
            minimum = .06 if end-start <= .2 else .10
            cursor, gaps = start, []
            for note in sorted(output, key=lambda n: n.onset):
                if note.offset <= cursor or note.onset >= end:
                    continue
                if note.onset - cursor >= minimum-1e-9:
                    gaps.append((cursor, note.onset))
                cursor = max(cursor, note.offset)
            if end - cursor >= minimum-1e-9:
                gaps.append((cursor, end))
            for left, right in gaps:
                measured = _pitch_summary(result, left, right)
                brief = right-left <= .18
                model_tail = unit.get('source') in ('local_GAME_tail','local_GAME_internal')
                # The acoustic consonant veto can remove a few frames from a
                # real aligned vowel. Retain a compact stable core instead of
                # discarding the entire syllable for narrowly missing 80%.
                compact_core = (not brief and measured and measured['count']>=10
                    and measured['fraction']>=.75 and measured.get('mad',100)<=.18
                    and measured.get('spread',100)<=.5)
                if (not measured or measured['count'] < (4 if brief else 8)
                        or (measured['fraction'] < (.85 if brief else .8) and not compact_core)
                        or measured.get('spread', 100) > (.55 if brief and not model_tail else 1.2)):
                    continue
                from inference.io.note_io import NoteInfo
                lyric = unit['lyric'] if abs(left - start) <= .025 else '-'
                # A stable, touching tail within this same aligned vowel does
                # not provide evidence of a new attack at the raw note offset.
                # Only this newly recovered gap can extend a note: existing
                # notes, distinct syllables and melodic steps stay separate.
                previous = next((n for n in reversed(sorted(output,key=lambda n:n.onset))
                    if abs(n.offset-left)<=1e-8 and start<=n.onset<left
                    and round(n.pitch)==round(measured['median'])),None)
                if merge_aligned_continuations and lyric=='-' and previous is not None:
                    report['aligned_continuations'].append({'start':previous.onset,
                        'old_end':previous.offset,'new_end':right,'lyric':previous.lyric,
                        'source':'stable_same_syllable_gap','evidence_frames':measured['count']})
                    previous.offset=right
                    continue
                item = NoteInfo(left, right, round(measured['median']), lyric)
                output.append(item); report['recovered'].append(vars(item).copy())
    output.sort(key=lambda n: n.onset)
    output,report['release_extensions']=continue_releases(output,result,syllables,energy)
    # Resolve only touching-boundary overlap; keep both neighboring notes.
    for left, right in zip(output, output[1:]):
        if left.onset < right.onset < left.offset:
            left.offset = right.onset
    output = [n for n in output if n.offset > n.onset]
    report['connections'] = connect_phrase_notes(output, syllables)
    report['aligned_coverage'] = aligned_coverage_gaps(output,syllables,result,report['removed'])
    report['output_notes'] = len(output)
    report['raw_notes'] = [vars(n).copy() for n in notes]
    return output, report


def install(language, audio_path, report_path, reference_text='', *, pitch_available=True, peer=None):
    global _state, _installed
    _state = {'language': language, 'audio': audio_path, 'report': report_path, 'syllables': [], 'unvoiced': [], 'tail_proposals': [],
              'reference':reference_text, 'alignment_retries': [], 'voiced_pause_retries': [],
              'hfa_model':None, 'pitch_available':pitch_available, 'peer':peer,'write_pitch':False,
              'note_boundary_checks':[],'confirmed_boundaries':[]}
    if _installed:
        return
    from inference.API import game_api, hfa_api, lfa_api
    import inference.pipeline.auto_lyric_hybrid as hybrid
    import inference.pipeline.lyric_alignment as alignment
    native_boundaries = game_api.extract_vowel_boundaries
    native_stage, native_export = hybrid._run_game_stage, hybrid._export_outputs
    native_match = lfa_api.LyricMatcher.align_lyric_with_asr
    native_hfa = alignment.run_hubert_fa
    native_pitch = hybrid._extract_pitch_curve

    def extract_pitch(waveform,sr,**kwargs):
        _state['write_pitch']=bool(kwargs['output_pitch_curve'])
        if _state['pitch_available']:
            kwargs={**kwargs,'output_pitch_curve':True,
                    'output_format_set':frozenset(kwargs['output_format_set'])|{'ustx'}}
        return native_pitch(waveform,sr,**kwargs)

    def match(self, *args, **kwargs):
        import inspect
        bound=inspect.signature(native_match).bind(self,*args,**kwargs)
        text,phonetic,reason=native_match(self,*args,**kwargs)
        if _state['language']=='zh' and text and phonetic:
            expanded,phones,cursor,count=expand_reference_match(text,phonetic,
                bound.arguments['lyric_text'],bound.arguments['lyric_phonetic'],getattr(self,'_vwa_cursor',0))
            if count:
                self._vwa_cursor=cursor
                return expanded,phones,str(reason or '')+f'；依据原文补入 {count} 个内部漏识别音节'
        return text,phonetic,reason

    def boundaries(words, tokens, language=None):
        if language == 'zh':
            return chinese_boundaries(words, tokens, game_api)
        if language == 'ja':
            return japanese_boundaries(words, tokens, game_api)
        return native_boundaries(words, tokens, language)

    def repairs(pred_dict, language='zh'):
        for prediction in pred_dict.values():
            if len(prediction) >= 3:
                hfa_api._repair_short_word_boundaries(prediction[2], max_gap_sec=PAUSE_LIMIT)

    def stage(chunks, sr, ctx, outcome, *args, **kwargs):
        if ctx.fa_language=='zh' and outcome and _state['hfa_model'] is not None:
            from pitch_accuracy import current_pitch
            _state['voiced_pause_retries'] = retry_voiced_pauses(_state['hfa_model'],outcome,chunks,sr,
                current_pitch(),native_hfa,kwargs.get('cancel_checker'),_state['audio'])
        _state['syllables'] = syllables_from_alignment(outcome, chunks, ctx.fa_language) if outcome else []
        _state['unvoiced'] = unvoiced_from_alignment(outcome, chunks, ctx.fa_language) if outcome else []
        notes = native_stage(chunks, sr, ctx, outcome, *args, **kwargs)
        _state['tail_proposals'] = []
        if ctx.fa_language in ('ja','zh') and outcome and _state['pitch_available']:
            from pitch_accuracy import current_pitch
            windows = (japanese_tail_windows(outcome,chunks) if ctx.fa_language=='ja'
                else chinese_gap_windows(outcome,chunks,current_pitch(),sr))
            model, budget = None, 0.
            for window in windows[:16]:
                checker = kwargs.get('cancel_checker')
                if checker and checker():
                    raise InterruptedError('句尾补提已取消。')
                if budget+window['right']-window['left'] > 24.:
                    break
                budget += window['right']-window['left']
                if model is None:
                    model = game_api.load_game_model(kwargs['game_model_dir'],device=ctx.device)
                chunk = chunks[window['chunk']]
                crop = {'offset':float(chunk['offset'])+window['left'],
                        'waveform':chunk['waveform'][round(window['left']*sr):round(window['right']*sr)]}
                candidates = game_api.extract_pitches_only([crop],sr,model,kwargs['ts'],
                    kwargs['seg_threshold'],kwargs['seg_radius'],kwargs['est_threshold'],1,
                    cancel_checker=checker,language=ctx.fa_language)
                lower,upper = chunk['offset']+window['anchor_end'],chunk['offset']+window['tail_end']
                bounded = bounded_candidates(candidates,lower,upper,internal=window.get('source')=='local_GAME_internal')
                _state['tail_proposals'].append({**window,'offset':float(chunk['offset']),'notes':bounded,
                    'raw_candidates':[vars(n).copy() for n in candidates]})
            # ASR-conditioned inference can merge an internal melodic step.
            # Recheck only ambiguous notes with both a measured contour change
            # and timed lyric support. Share the tail retry's 16-window/24 s
            # budget and loaded model rather than adding another model layer.
            from pathlib import Path
            import soundfile as sf
            pitch=current_pitch()
            for note in notes:
                if len(_state['tail_proposals'])+len(_state['note_boundary_checks'])>=16:break
                if not .24<=note.offset-note.onset<=3.:continue
                if not any(min(note.offset,u['end'])-max(note.onset,u['start'])>=.06 for u in _state['syllables']):continue
                established=_plateau_splits(note,pitch)
                proposed=[b for b in _plateau_splits(note,pitch,propose=True) if all(abs(b-a)>.02 for a in established)]
                if not proposed:continue
                lower=max(0.,note.onset-.2);upper=note.offset+.2
                if budget+upper-lower>24.:continue
                budget+=upper-lower
                entry={'start':note.onset,'end':note.offset,'proposed':proposed,'confirmed':[]}
                checker=kwargs.get('cancel_checker')
                if checker and checker():raise InterruptedError('音符边界复查已取消。')
                try:
                    if model is None:model=game_api.load_game_model(kwargs['game_model_dir'],device=ctx.device)
                    with sf.SoundFile(str(_state['audio'])) as audio:
                        audio.seek(round(lower*audio.samplerate))
                        wave=audio.read(round((upper-lower)*audio.samplerate),dtype='float32',always_2d=True).mean(axis=1)
                        rate=audio.samplerate
                    candidates=game_api.extract_pitches_only([{'waveform':wave,'offset':lower}],rate,model,
                        kwargs['ts'],kwargs['seg_threshold'],kwargs['seg_radius'],kwargs['est_threshold'],1,
                        cancel_checker=checker,language=ctx.fa_language)
                    approved=confirm_note_boundaries(proposed,candidates,pitch)
                    entry.update(confirmed=approved,candidates=[vars(n).copy() for n in candidates])
                    _state['confirmed_boundaries'].extend(approved)
                except InterruptedError:raise
                except (OSError,ValueError,RuntimeError) as error:
                    entry['error']=type(error).__name__+': '+str(error)
                _state['note_boundary_checks'].append(entry)
        return notes

    def hfa(model, directory, language='zh', cancel_checker=None, use_phoneme_g2p=False):
        _state['hfa_model']=model
        predictions = native_hfa(model, directory, language=language,
            cancel_checker=cancel_checker, use_phoneme_g2p=use_phoneme_g2p)
        _state['alignment_retries'] = retry_collapsed_alignment(model, directory, predictions, language,
            native_hfa, cancel_checker, use_phoneme_g2p)
        return predictions

    def export(notes, ctx, **kwargs):
        from common import atomic_json
        from vocal_expression import energy_frames
        times, db, _ = energy_frames(_state['audio'])
        pitch = kwargs.get('rmvpe_result')
        pitch_audit = {'status':'not_requested', 'excluded_frames':0}
        if pitch is not None:
            from pitch_accuracy import exclude_unvoiced_pitch, cache_export_pitch
            pitch, pitch_audit = exclude_unvoiced_pitch(pitch, notes, _state['unvoiced'])
            kwargs['rmvpe_result'] = pitch
            cache_export_pitch(pitch)
        recovered,tail_audit = accept_japanese_tails(_state['tail_proposals'],pitch,_state['syllables'],(times,db))
        _state['syllables'] = sorted(_state['syllables']+recovered,key=lambda unit:unit['start'])
        original_notes, placeholders = list(notes), []
        for entry in tail_audit:
            if entry.get('status') != 'applied':
                continue
            collapsed_start = entry['offset']+entry.get('placeholder_start',entry['tail_end']-entry['collapsed_seconds'])
            collapsed_end = entry['offset']+entry.get('placeholder_end',entry['tail_end'])
            keep=[]
            for note in notes:
                if (note.lyric == entry['lyric'] and note.offset-note.onset < .045
                        and collapsed_start-.025 <= note.onset and note.offset <= collapsed_end+.025):
                    placeholders.append(vars(note).copy())
                else:
                    keep.append(note)
            notes=keep
        result, report = refine_notes(notes, pitch, _state['syllables'], (times, db),
            lambda start,end,pitch: verify_acoustic_pitch(_state['audio'],start,end,pitch),_state['reference'],
            confirmed_boundaries=_state['confirmed_boundaries'])
        report['cross_voice_exclusions']=[]
        if pitch is not None and _state['peer'] is not None:
            from vocal_partition import suppress_shared_notes
            result,report['cross_voice_exclusions']=suppress_shared_notes(result,pitch,_state['peer'],_state['audio'])
            report['output_notes']=len(result)
        report['aligned_coverage']=aligned_coverage_gaps(result,_state['syllables'],pitch,
            report['removed']+report['cross_voice_exclusions'])
        report['syllables'] = _state['syllables']
        report['unvoiced_phonemes'] = _state['unvoiced']
        report['pitch_phoneme_guard'] = pitch_audit
        report['japanese_tail_repairs'] = [row for row in tail_audit if row.get('source')!='local_GAME_internal']
        report['internal_syllable_repairs'] = [row for row in tail_audit if row.get('source')=='local_GAME_internal']
        report['replaced_tail_placeholders'] = placeholders
        report['raw_notes'] = [vars(note).copy() for note in original_notes]
        report['input_notes'] = len(original_notes)
        report['alignment_retries'] = _state['alignment_retries']
        report['voiced_pause_retries'] = _state['voiced_pause_retries']
        report['note_boundary_checks'] = _state['note_boundary_checks']
        report['note_pitch_analysis']={'available':pitch is not None,
            'independent_of_curve_export':True,'curve_export_requested':_state['write_pitch']}
        atomic_json(_state['report'], report)
        print(f"音符检查：保留 {len(result)} 个音符；移除 {len(report['removed'])} 个缺少依据的片段；补回 {len(report['recovered'])} 个已对齐音节；延续 {len(report['aligned_continuations'])} 个同音节尾部；排除 {len(report['cross_voice_exclusions'])} 个重复主唱片段。", flush=True)
        if report['aligned_coverage']['voiced_gap_count']:
            print(f"音符覆盖提示：{report['aligned_coverage']['voiced_gap_count']} 处已有定时歌词和有声音高，但音符尚未完整覆盖；请按质量回执检查，这不是已确认漏音数。",flush=True)
        if not _state['write_pitch']:kwargs['rmvpe_result']=None
        return native_export(result, ctx, **kwargs)

    game_api.extract_vowel_boundaries = boundaries
    lfa_api.LyricMatcher.align_lyric_with_asr = match
    hfa_api._repair_pred_dict_short_words = repairs
    hybrid._run_game_stage, hybrid._export_outputs = stage, export
    hybrid._extract_pitch_curve = extract_pitch
    alignment.run_hubert_fa = hfa
    _installed = True
