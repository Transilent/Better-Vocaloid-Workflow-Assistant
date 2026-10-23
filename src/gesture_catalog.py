"""Deterministic retrieval of reviewed, conditional vocal-method cards.

The catalog is deliberately small.  Enabled cards contain short paraphrases of
methods whose original bodies were reviewed in the saved round-two research.
Official pages available only through search-index text remain disabled source
references, labelled index_only; they are not verified mechanism definitions.

This module is pure and standard-library-only.  It does not inspect a voicebank,
infer emotion, choose parameters, write an SVP file, or claim acoustic validity.
"""
from __future__ import annotations

import copy
import re
import unicodedata


CATALOG_VERSION = 1

FEATURE_VOCABULARY = frozenset((
    'phrase_start',
    'phrase_end',
    'word_start',
    'word_end',
    'long_note',
    'rest_before',
    'rest_after',
    'repeat',
    'contrast',
    'negation',
    'addressee',
    'conditional',
    'question',
))

DIRECTION_KEYS = frozenset((
    'connected_onset',
    'clear_onset',
    'supported_sustain',
    'restrained_release',
    'preserve_contour',
    'phoneme_timing_advice',
    'pitch_texture_advice',
    'voice_mode_advice',
    'mix_space_advice',
    'synthetic_character_advice',
))

PHASE_VOCABULARY = frozenset(('onset', 'sustain', 'release'))
EVIDENCE_LEVELS = frozenset(('readable_method', 'official_mechanism', 'index_only'))

_CARD_FIELDS = frozenset((
    'id',
    'source_id',
    'source_url',
    'source_language',
    'scope',
    'observed_method',
    'alternative',
    'phases',
    'required_features',
    'optional_features',
    'directions',
    'limitations',
    'evidence_level',
    'enabled',
))

_COMMON_LIMITATIONS = (
    'Advisory research card only; no acoustic test or listening validation was performed for a current voicebank.',
    'No numerical Synthesizer V values are supplied; confirm local voicebank and feature availability before any trial.',
)


def _card(*, card_id, source_id, source_url, source_language, scope,
          observed_method, alternative, phases, required_features,
          optional_features, directions, limitations, evidence_level,
          enabled=True):
    return {
        'id': card_id,
        'source_id': source_id,
        'source_url': source_url,
        'source_language': source_language,
        'scope': scope,
        'observed_method': observed_method,
        'alternative': alternative,
        'phases': list(phases),
        'required_features': sorted(required_features),
        'optional_features': sorted(optional_features),
        'directions': list(directions),
        'limitations': list(limitations) + list(_COMMON_LIMITATIONS),
        'evidence_level': evidence_level,
        'enabled': enabled,
    }


_CARDS = (
    # Plain-language guidance from a general SV2 voice-shaping article.  The
    # card stays tied to an explicit word boundary because the article first
    # asks the reader to divide phrases and identify words.
    _card(
        card_id='phrased-word-articulation',
        source_id='R2-JA-01',
        source_url='https://note.com/sakutoku/n/n17730344333f',
        source_language='ja',
        scope='SV2 Pro general guidance; unknown voicebank; phrase and word roles were chosen before local controls.',
        observed_method='The author recommends dividing lyrics into phrases, identifying a word focus, then considering consonant onset, vowel continuation and word-tail timing locally. Broader tension, breath and voicing choices follow that phrase-level decision.',
        alternative='A deliberately flat, mechanical or non-narrative rendering may avoid naturalistic word emphasis; this is a researcher alternative and still requires a clear artistic goal.',
        phases=('onset', 'sustain', 'release'),
        required_features=('word_start',),
        optional_features=('word_end', 'phrase_start', 'phrase_end'),
        directions=('phoneme_timing_advice', 'voice_mode_advice', 'pitch_texture_advice'),
        limitations=(
            'Readable general guidance with sales links, not a controlled experiment; the voicebank is unspecified.',
            'The article does not establish an emotion from a word or require naturalistic delivery.',
        ),
        evidence_level='readable_method',
    ),
    _card(
        card_id='quiet-connected-phrase-entry',
        source_id='R2-JA-05',
        source_url='https://note.com/hiryukaoku/n/nedd8dbd1acc8',
        source_language='ja',
        scope='Japanese quiet, slow single-line creator procedure; Hanakuma Chifuyu case; editor version unspecified.',
        observed_method='For a quiet slow line, the author shaped the opening three syllables into a connected, breath-supported entry, let an accent arrive slightly later, and reduced excessive fricative loudness locally. The final syllable was treated as a separate rise-then-fall closing gesture.',
        alternative='For a direct and forceful opening, reduce breathiness and make the consonant attack clearer; do not transfer the original Tone Shift symbols or their loudness side effects.',
        phases=('onset', 'sustain', 'release'),
        required_features=('phrase_start',),
        optional_features=('rest_before', 'word_start', 'long_note', 'phrase_end'),
        directions=('connected_onset', 'supported_sustain', 'restrained_release', 'phoneme_timing_advice', 'pitch_texture_advice', 'voice_mode_advice'),
        limitations=(
            'One Japanese phrase in a specific quiet-song and voicebank context; no public project or complete curves.',
            'Some phoneme spellings depend on editor and voicebank behavior; the author marked a separate mouth-shape idea as future work.',
            'Tone Shift side effects are not promoted to a cross-voicebank rule.',
        ),
        evidence_level='readable_method',
    ),
    _card(
        card_id='forceful-selected-word-entry',
        source_id='R2-JA-02',
        source_url='https://note.com/ka_youtyu/n/n103748027233',
        source_language='ja',
        scope='SV2 AI strong-voice experiment with Miyamai Moca; note heads at selected phrase or high-tension word positions.',
        observed_method='At selected word or phrase openings, the author raised the note head sparingly and explored local vowel and mouth shaping plus a rough pitch texture. The gesture was a local accent, with warnings against applying it to every entry.',
        alternative='An unemphasized or softer entry can preserve a quieter reading; a lower, heavier voice may also suit a falling rather than lifted treatment, as the author suggested questioningly.',
        phases=('onset', 'sustain'),
        required_features=('word_start',),
        optional_features=('phrase_start', 'long_note', 'contrast'),
        directions=('clear_onset', 'preserve_contour', 'pitch_texture_advice', 'voice_mode_advice'),
        limitations=(
            'Not auditioned by this BVWA research round; the author reported a personal experiment without a public project or complete score, and the baseline retained a remembered vibrato and tension setting.',
            'Vowel shaping changes neighboring consonant brightness, and repeated use was judged tiring; the result is not a universal strong-voice setting.',
        ),
        evidence_level='readable_method',
    ),
    _card(
        card_id='phrase-end-direction-choice',
        source_id='R2-JA-02',
        source_url='https://note.com/ka_youtyu/n/n103748027233',
        source_language='ja',
        scope='SV2 AI strong-voice experiment with Miyamai Moca; selected phrase endings.',
        observed_method='The author tested a raised phrase ending as one of several strong-delivery gestures, while noting that continuous use can feel excessive.',
        alternative='Keep the ending level or let it fall, especially if a lower or heavier voice already supplies weight; this was the author question and researcher alternative.',
        phases=('release',),
        required_features=('phrase_end',),
        optional_features=('long_note', 'contrast', 'question'),
        directions=('preserve_contour', 'pitch_texture_advice', 'restrained_release'),
        limitations=(
            'One voicebank and one un-auditioned experiment; no claim that a rise signifies a particular emotion.',
            'The ending choice must be compared with the surrounding phrase and the local voice character.',
        ),
        evidence_level='readable_method',
    ),
    _card(
        card_id='rock-chorus-voice-role',
        source_id='R2-JA-03',
        source_url='https://note.com/in_densetsu/n/n9e434e7d41af',
        source_language='ja',
        scope='SV2 single-song rock-chorus case with TetO V2; vocal shaping and DAW treatment were intertwined.',
        observed_method='For a dense rock chorus, the author combined a strong or rock-oriented vocal mode with increased tension and reduced breathiness, retained consonant attack, and used mixer processing to place the voice against the arrangement.',
        alternative='When the voice already sounds hard or crowded, leave room in the arrangement or mix instead of increasing strong-voice controls; a softer or more breathy reading can also be appropriate for a different target.',
        phases=('onset', 'sustain', 'release'),
        required_features=('repeat',),
        optional_features=('contrast', 'phrase_start', 'long_note'),
        directions=('voice_mode_advice', 'clear_onset', 'mix_space_advice', 'supported_sustain'),
        limitations=(
            'One song, one voicebank and a dense rock-chorus context; tuning and DAW processing cannot be separated from the reported result.',
            'The case is un-auditioned here and provides no numerical values or universal genre mapping.',
            'A repeated section alone does not prove a rock style, chorus role, or intended intensity.',
            'Disabled: repetition is not eligible evidence of rock genre, chorus role or dense arrangement; enable only after reviewed contextual eligibility is available.',
        ),
        evidence_level='readable_method',
        enabled=False,
    ),
    _card(
        card_id='phoneme-boundary-timing',
        source_id='R2-ZH-04',
        source_url='https://www.midifan.com/modulearticle-detailview-7781.htm',
        source_language='zh',
        scope='SV Studio 2 Pro review; English worked example inside a Chinese article; voicebank-dependent timing detail.',
        observed_method='In the English example, the reviewer moves an onset consonant earlier, shortens a vowel, and leaves time for the next consonant. The case demonstrates word-boundary timing rather than a language-independent pronunciation rule.',
        alternative='Preserve the supplied word timing when a boundary is already audible; another valid choice is a deliberately clipped or separated articulation if that serves the phrase.',
        phases=('onset', 'release'),
        required_features=('word_start',),
        optional_features=('word_end', 'phrase_start', 'contrast'),
        directions=('phoneme_timing_advice', 'clear_onset'),
        limitations=(
            'The source language is Chinese, but the worked lyric and phonemes are English; Chinese and Japanese transfer remains conditional.',
            'Professional feature review without a supplied project or audition; phoneme support can differ by editor and voicebank.',
        ),
        evidence_level='readable_method',
    ),
    _card(
        card_id='ensemble-voice-role-balance',
        source_id='R2-JA-04',
        source_url='https://note.com/shuesueichiyu/n/ncfdf42d373eb',
        source_language='ja',
        scope='Japanese classical-ensemble retrospective; voicebank and part assignment before line-level shaping.',
        observed_method='The author assigned voicebanks by vocal range and ensemble role, then used doubling or width-oriented effects to create group density. A soft tenor remained an acknowledged gap rather than something the case solved.',
        alternative='A solo may benefit from a more defined voice than an ensemble member that needs to blend; reduce added density when exposed or unaccompanied passages reveal an artificial effect.',
        phases=('onset', 'sustain', 'release'),
        required_features=('repeat',),
        optional_features=('contrast', 'phrase_start', 'phrase_end'),
        directions=('voice_mode_advice', 'mix_space_advice', 'synthetic_character_advice', 'clear_onset'),
        limitations=(
            'Long-term multi-song retrospective, not a line-level curve tutorial; editor and voicebank versions require local rechecking.',
            'Voicebank availability, range, language support and ensemble suitability are unknown until checked locally.',
            'The author reported an unresolved soft-tenor gap and context-dependent artificiality in some mixes.',
            'Disabled: repetition is not evidence of ensemble intent or available voicebanks; enable only with an explicit reviewed ensemble/arrangement condition.',
        ),
        evidence_level='readable_method',
        enabled=False,
    ),
    _card(
        card_id='intentional-synthetic-voice-role',
        source_id='R2-JA-06',
        source_url='https://note.com/nonike/n/nd5eb2f8e9566',
        source_language='ja',
        scope='Japanese creator statement; intentionally low-semantic lyrics and a mixed SV/VOCALOID aesthetic.',
        observed_method='The composer removed recognizable wording and chose a voicebank that preserved both organic and synthetic qualities, treating the voice as part of the instrumental structure rather than as a narrative narrator.',
        alternative='Keep recognizable narrative words and articulate semantic peaks. Deliberate synthetic character can still retain clear articulation, rhythmic emphasis and section contrast.',
        phases=('onset', 'sustain', 'release'),
        required_features=('contrast',),
        optional_features=('repeat', 'phrase_start', 'phrase_end'),
        directions=('synthetic_character_advice', 'voice_mode_advice', 'preserve_contour', 'mix_space_advice'),
        limitations=(
            'A subjective retrospective creative statement, not a step-by-step tuning tutorial; it mixes engines and may rationalize decisions after the fact.',
            'Mechanical character is not evidence for a flat curve, absent vibrato, or any emotion; the original project was not inspected.',
            'Disabled: contrast is not evidence of a low-semantic or synthetic-performance goal; enable only with explicit reviewed contextual eligibility.',
        ),
        evidence_level='readable_method',
        enabled=False,
    ),
    # main-sources.json records OFF-01 through OFF-06 as indexed_text.
    # main-access-audit.json and the round-two access rechecks do not promote
    # them to readable bodies.  Keep these descriptions as disabled indexes;
    # verified text from another official page cannot validate these pages.
    _card(
        card_id='official-consonant-space-mechanism',
        source_id='OFF-04',
        source_url='https://sv2.docs.dreamtonics.com/en/phonemes',
        source_language='en',
        scope='Index-only reference to official SV2 pronunciation documentation; full method body not verified.',
        observed_method='The saved search index describes consonant and word-tail material crossing note boundaries and separate phoneme timing and strength controls. This description is a source lead, not a body-verified method.',
        alternative='Retain the current articulation when it is already clear; an intentional break may also be a valid phrase gesture and should not be converted into automatic legato.',
        phases=('onset', 'release'),
        required_features=('word_start',),
        optional_features=('word_end', 'phrase_start', 'phrase_end'),
        directions=('phoneme_timing_advice', 'clear_onset', 'connected_onset'),
        limitations=(
            'Disabled: OFF-04 was available as indexed_text only; mechanism-audit R05 records official_search_index_text_not_full_text for this URL, and the round-two recheck obtained no method body.',
            'The indexed description does not prescribe connected or uniform articulation.',
            'Some phoneme edits depend on the voicebank and editor generation.',
        ),
        evidence_level='index_only',
        enabled=False,
    ),
    _card(
        card_id='official-pitch-and-note-texture-mechanism',
        source_id='OFF-05',
        source_url='https://sv1.docs.dreamtonics.com/en/synthv/advanced-usage/note-properties',
        source_language='en',
        scope='Index-only reference to official SV1 note-properties documentation; full method body not verified.',
        observed_method='The saved search index describes note-level vibrato and transition settings and relative phoneme duration and strength controls. The full definitions and conditions have not been body-verified for this card.',
        alternative='Preserve the existing contour and texture when they already serve the phrase; changing a setting is optional and must be auditioned.',
        phases=('sustain', 'release'),
        required_features=('long_note',),
        optional_features=('phrase_end', 'word_end'),
        directions=('preserve_contour', 'pitch_texture_advice', 'supported_sustain'),
        limitations=(
            'Disabled: main-sources.json records OFF-05 as indexed_text; no readable full note-properties body was verified.',
            'SV1 mechanism documentation; its full audible behavior must not be assumed unchanged in SV2 or another voicebank.',
            'No curve values or interpolation behavior are generated by this catalog.',
        ),
        evidence_level='index_only',
        enabled=False,
    ),
    _card(
        card_id='official-vocal-mode-dimensions',
        source_id='OFF-03',
        source_url='https://sv2.docs.dreamtonics.com/en/voice-panel',
        source_language='en',
        scope='Index-only reference to official SV2 voice-panel documentation; full method body not verified.',
        observed_method='The saved search index describes vocal modes in relation to pitch, timbre and pronunciation and notes voicebank-specific mode sets. This remains an unverified source description, not an emotion taxonomy.',
        alternative='Keep the current mode when it already supports the phrase; a mode change is a local research choice and should be compared by audition.',
        phases=('onset', 'sustain', 'release'),
        required_features=('phrase_start',),
        optional_features=('repeat', 'contrast', 'long_note'),
        directions=('voice_mode_advice', 'pitch_texture_advice'),
        limitations=(
            'Disabled: OFF-03 is indexed_text in main-sources.json; the round-two access recheck explicitly obtained no method body or access-depth promotion.',
            'A mode available in one voicebank may be unavailable or respond differently in another; this module cannot verify that availability.',
            'Mode names do not certify a mood, quality level, or intended delivery.',
        ),
        evidence_level='index_only',
        enabled=False,
    ),
    _card(
        card_id='official-base-parameter-separation',
        source_id='OFF-01',
        source_url='https://sv2.docs.dreamtonics.com/en/parameters',
        source_language='en',
        scope='Index-only reference to official SV2 base-parameter documentation; full method body not verified.',
        observed_method='The saved search index describes separate tension, breathiness, voicing, vocal-range character and vibrato-envelope controls. The indexed description is not a body-verified definition or an emotion-to-value table.',
        alternative='Leave the original value when the local voice already communicates the intended gesture; parameter changes are optional and require voicebank-specific comparison.',
        phases=('onset', 'sustain', 'release'),
        required_features=('long_note',),
        optional_features=('phrase_start', 'phrase_end', 'word_start'),
        directions=('supported_sustain', 'voice_mode_advice', 'pitch_texture_advice'),
        limitations=(
            'Disabled: OFF-01 was available as indexed_text only; mechanism-audit R01 records official_search_index_text_not_full_text for this URL.',
            'No numerical values, direction magnitudes, or universal response curve are inferred from the source index.',
        ),
        evidence_level='index_only',
        enabled=False,
    ),
    _card(
        card_id='official-generated-pitch-context',
        source_id='OFF-02',
        source_url='https://sv2.docs.dreamtonics.com/en/pitch',
        source_language='en',
        scope='Index-only reference to official SV2 pitch documentation; full method body not verified.',
        observed_method='The saved search index describes generated pitch in relation to lyrics, score, vocal modes and retakes and lists expression choices. This source description has not been verified against the full Pitch page body.',
        alternative='Keep a stable or mechanical contour if that is already the chosen direction; expression presets are options rather than mandatory naturalness targets.',
        phases=('sustain', 'release'),
        required_features=('long_note',),
        optional_features=('phrase_end', 'word_end', 'contrast'),
        directions=('preserve_contour', 'pitch_texture_advice'),
        limitations=(
            'Disabled: main-sources.json records OFF-02 as indexed_text; readable Automation documentation does not verify this separate Pitch page.',
            'The indexed description does not establish that any contour shape suits a current phrase.',
            'No numeric pitch curve, intensity or probability is produced.',
        ),
        evidence_level='index_only',
        enabled=False,
    ),
    # The migration page remains an index-only reference as well as being
    # outside automatic line-editing scope.  The retrieval path never returns it.
    _card(
        card_id='legacy-phonetic-note-migration',
        source_id='OFF-06',
        source_url='https://sv1.docs.dreamtonics.com/en/synthv/upgrade/migration-guide',
        source_language='en',
        scope='Index-only reference to official SV1-to-SV2 migration documentation; outside normal line-editing scope.',
        observed_method='The saved search index describes migration concerns around independent consonant short notes, modes, retakes and pitch data. The full body at this specific URL was not verified for this card.',
        alternative='Keep and re-audition the imported project before replacing timing gestures; use saved curves as evidence instead of assuming they are obsolete.',
        phases=('onset', 'sustain', 'release'),
        required_features=('word_start',),
        optional_features=('long_note', 'phrase_end'),
        directions=('clear_onset', 'phoneme_timing_advice'),
        limitations=(
            'Disabled: main-sources.json records OFF-06 as indexed_text; a readable migration article at a different URL does not promote this source reference.',
            'Disabled: migration-only information is not evidence for automatically rewriting an existing SVP or phonetic-note layout.',
            'No project migration or source-file mutation is authorized by this card.',
        ),
        evidence_level='index_only',
        enabled=False,
    ),
)


def _validate_catalog(cards):
    ids = set()
    for position, card in enumerate(cards):
        if set(card) != _CARD_FIELDS:
            raise RuntimeError('gesture catalog card has an invalid field set at position %d' % position)
        card_id = card['id']
        if (not isinstance(card_id, str) or not card_id
                or card_id != card_id.strip() or card_id in ids):
            raise RuntimeError('gesture catalog IDs must be nonempty and unique')
        ids.add(card_id)
        if not isinstance(card['source_id'], str) or not card['source_id']:
            raise RuntimeError('gesture catalog source_id must be nonempty')
        if (not isinstance(card['source_url'], str)
                or not card['source_url'].startswith('https://')):
            raise RuntimeError('gesture catalog source_url must be HTTPS')
        if card['source_language'] not in ('zh', 'ja', 'en'):
            raise RuntimeError('gesture catalog source_language is unsupported')
        if not all(isinstance(card[key], str) and card[key].strip() for key in
                   ('scope', 'observed_method', 'alternative')):
            raise RuntimeError('gesture catalog text fields must be nonempty')
        phases = card['phases']
        if (not isinstance(phases, list) or not phases or len(phases) != len(set(phases))
                or any(phase not in PHASE_VOCABULARY for phase in phases)):
            raise RuntimeError('gesture catalog phases are invalid')
        required = card['required_features']
        optional = card['optional_features']
        if (not isinstance(required, list) or not required
                or not isinstance(optional, list)
                or len(required) != len(set(required))
                or len(optional) != len(set(optional))
                or set(required) & set(optional)
                or not set(required) <= FEATURE_VOCABULARY
                or not set(optional) <= FEATURE_VOCABULARY):
            raise RuntimeError('gesture catalog feature conditions are invalid')
        directions = card['directions']
        if (not isinstance(directions, list) or not directions
                or len(directions) != len(set(directions))
                or not set(directions) <= DIRECTION_KEYS):
            raise RuntimeError('gesture catalog directions are invalid')
        limitations = card['limitations']
        if (not isinstance(limitations, list) or not limitations
                or any(not isinstance(item, str) or not item.strip()
                       for item in limitations)):
            raise RuntimeError('gesture catalog limitations are invalid')
        if card['evidence_level'] not in EVIDENCE_LEVELS:
            raise RuntimeError('gesture catalog evidence level is invalid')
        if type(card['enabled']) is not bool:
            raise RuntimeError('gesture catalog enabled flag must be boolean')
        if card['evidence_level'] == 'index_only' and card['enabled']:
            raise RuntimeError('index-only source references must remain disabled')
    return True


_validate_catalog(_CARDS)


_LANGUAGE_ALIASES = {
    'zh': 'zh', 'zho': 'zh', 'chi': 'zh', 'chinese': 'zh',
    'cmn': 'zh', 'mandarin': 'zh',
    'ja': 'ja', 'jpn': 'ja', 'japanese': 'ja',
    'en': 'en', 'eng': 'en', 'english': 'en',
}

def catalog():
    """Return a fresh deep copy of all reviewed cards, enabled or disabled."""
    return copy.deepcopy(list(_CARDS))


def _normalise_language(value):
    if not isinstance(value, str):
        raise ValueError('language must be a string')
    value = unicodedata.normalize('NFC', value).strip().casefold()
    if not value:
        raise ValueError('language must not be empty')
    base = re.split(r'[-_/]', value, maxsplit=1)[0]
    return _LANGUAGE_ALIASES.get(value, _LANGUAGE_ALIASES.get(base, value))


def _normalise_features(features):
    if isinstance(features, (str, bytes)) or features is None:
        raise ValueError('features must be an iterable of feature names')
    try:
        raw = list(features)
    except TypeError as error:
        raise ValueError('features must be an iterable of feature names') from error
    result = set()
    for item in raw:
        if not isinstance(item, str):
            raise ValueError('features must contain only strings')
        name = unicodedata.normalize('NFC', item).strip()
        if name not in FEATURE_VOCABULARY:
            raise ValueError('unknown structure feature: %s' % name)
        result.add(name)
    return frozenset(result)


def _validate_goal(goal):
    if not isinstance(goal, str):
        raise ValueError('goal must be a string')


def _normalise_limit(limit):
    if type(limit) is not int or limit < 0:
        raise ValueError('limit must be a nonnegative integer')
    return limit


_PENDING_CONTEXT_CHECKS = {
    'phrased-word-articulation': (
        'Confirm whether naturalistic or deliberately mechanical delivery is the explicit target and whether the selected word is the intended phrase focus.',
        'Verify current voicebank pronunciation and local control behavior for the requested language and register.',
    ),
    'quiet-connected-phrase-entry': (
        'Confirm a quiet, slow solo-phrase context and the local Hanakuma Chifuyu voicebank response before borrowing the gesture.',
        'Verify phoneme support and re-audition any local articulation or Tone Shift analogue; the original side effects do not transfer.',
    ),
    'forceful-selected-word-entry': (
        'Confirm a strong-voice target, the local Miyamai Moca-like voice character, and that this particular word position merits a local accent.',
        'Verify current voicebank behavior and compare the entry with an unaccented alternative.',
    ),
    'phrase-end-direction-choice': (
        'Confirm that the selected phrase ending needs an explicit direction and compare rise, level and fall alternatives in context.',
        'Verify the current voicebank and surrounding phrase; a phrase-end structural tag alone does not establish emotion or intensity.',
    ),
    'rock-chorus-voice-role': (
        'Confirm rock genre, chorus role, dense arrangement and the current voicebank before considering the case.',
        'A repeated section alone is insufficient; mixing and arrangement context must be reviewed separately.',
    ),
    'phoneme-boundary-timing': (
        'Verify the target language phonetics locally because the worked example uses English words inside a Chinese article.',
        'Confirm current voicebank support and compare the boundary with the supplied articulation before changing timing.',
    ),
    'ensemble-voice-role-balance': (
        'Confirm an ensemble role, available voicebanks, vocal ranges and the intended amount of blend before considering this case.',
        'A repeated structure is insufficient; inspect arrangement, exposed passages and mix context separately.',
    ),
    'intentional-synthetic-voice-role': (
        'Confirm an explicit low-semantic or intentionally synthetic artistic goal before considering this creator statement.',
        'Contrast or repetition alone is insufficient; inspect narrative intent and voicebank character separately.',
    ),
    'official-consonant-space-mechanism': (
        'Confirm that the current voicebank exposes the relevant phoneme timing and strength behavior.',
        'Verify whether the phrase needs continuity or an intentional break; the mechanism alone does not decide articulation.',
    ),
    'official-pitch-and-note-texture-mechanism': (
        'Confirm editor generation and voicebank support for the documented note properties.',
        'Audition the existing contour and texture before treating any change as an improvement.',
    ),
    'official-vocal-mode-dimensions': (
        'Check which vocal modes the current voicebank actually exposes and whether their pitch, timbre and pronunciation effects are suitable.',
        'Do not infer a target mood or style from a mode name.',
    ),
    'official-base-parameter-separation': (
        'Check the current voicebank, register and local response for each proposed parameter dimension.',
        'Compare local behavior rather than transferring values or assuming a universal direction.',
    ),
    'official-generated-pitch-context': (
        'Review the current lyric, score, mode and retake context before interpreting or changing a generated contour.',
        'Audition the existing contour; the mechanism does not prescribe naturalness or a particular expression.',
    ),
    'legacy-phonetic-note-migration': (
        'Confirm that an explicit migration review is in scope and preserve the original project first.',
        'Re-audition imported pronunciation, modes, timing and pitch data before any replacement.',
    ),
}


def _applicability(card, language):
    source_language = card['source_language']
    if card['source_id'] == 'R2-ZH-04':
        if language == source_language:
            language_status = 'conditional_worked_example_language'
            language_condition = (
                'The article language is Chinese, but the worked pronunciation example uses English lyrics; Chinese or Japanese transfer is not verified.'
            )
        else:
            language_status = 'conditional_cross_language_and_worked_example'
            language_condition = (
                'The source article is Chinese and its worked example is English, while the requested context is %s; every language-transfer assumption remains conditional.' % language
            )
    elif language == source_language:
        language_status = 'same_source_language_only'
        language_condition = (
            'The requested language matches the source, but that match verifies neither phonetics nor delivery context.'
        )
    else:
        language_status = 'conditional_cross_language_transfer'
        language_condition = (
            'The source method was observed in %s while the requested context is %s; pronunciation and voicebank transfer remain conditional.' % (source_language, language)
        )

    pending = list(_PENDING_CONTEXT_CHECKS[card['id']])
    conditions = [language_condition] + pending
    conditions.append('Required structure features were present only; genre, section role, voicebank, phonetics and acoustic suitability remain unverified.')
    if card['evidence_level'] == 'readable_method':
        conditions.append('The original author case remains conditional on its stated style, voicebank and production context.')
    else:
        conditions.append('This is an official mechanism definition, not a recommended gesture or emotion mapping.')
    return {
        'status': 'structural_match_only',
        'context_status': 'pending_source_context_review',
        'language_status': language_status,
        'language': language,
        'source_language': source_language,
        'pending_context_checks': pending,
        'conditions': conditions,
    }


def _match(card, features, language):
    required = frozenset(card['required_features'])
    if not required <= features:
        return None
    optional = frozenset(card['optional_features'])
    matched_required = sorted(required & features)
    matched_optional = sorted(optional & features)
    same_language = card['source_language'] == language

    result = copy.deepcopy(card)
    result['score'] = (
        2 * len(matched_required)
        + len(matched_optional)
    )
    result['score_kind'] = 'deterministic_match_points'
    reasons = ['required_feature:%s' % name for name in matched_required]
    reasons.extend('optional_feature:%s' % name for name in matched_optional)
    if card['source_id'] == 'R2-ZH-04':
        reasons.append('language:worked_example_is_english')
    elif same_language:
        reasons.append('language:same_source_language_only')
    else:
        reasons.append('language:cross_language_conditional')
    result['match_reasons'] = reasons
    result['applicability'] = _applicability(card, language)
    result['limitations'] = copy.deepcopy(card['limitations'])
    return result


def retrieve_methods(*, language, features, goal='', limit=5):
    """Return enabled cards whose required structure features all match.

    ``score`` is an opaque deterministic match-point total for ordering only.
    It is not an accuracy, confidence, probability or acoustic-quality score.
    ``goal`` is accepted as unparsed caller context and checked only for its
    string type.  It does not affect eligibility, scores, reasons or ordering.
    Scores depend only on explicit required and optional structure overlap.
    """
    language = _normalise_language(language)
    feature_set = _normalise_features(features)
    _validate_goal(goal)
    limit = _normalise_limit(limit)
    if limit == 0:
        return []

    matches = []
    for card in _CARDS:
        if not card['enabled']:
            continue
        match = _match(card, feature_set, language)
        if match is not None:
            matches.append(match)

    matches.sort(key=lambda item: (
        -item['score'],
        0 if item['evidence_level'] == 'readable_method' else 1,
        item['id'],
    ))
    return copy.deepcopy(matches[:limit])


