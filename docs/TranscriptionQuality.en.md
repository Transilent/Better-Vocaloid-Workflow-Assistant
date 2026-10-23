# Note and pitch handling (development build)

[English](TranscriptionQuality.en.md) | [简体中文](TranscriptionQuality.zh-CN.md)

These changes apply to the Vocal2Midi 2.0 integration. They retain the existing Chinese and Japanese recognition models and require no additional weights. Reference-expression controls are independent of transcription.

## Note verification and curve export

The v2 worker measures pitch for note verification even when producing only MIDI or when **导出连续音高线** is disabled. This enables evidence checks and local recovery without requiring an editor project. A small, hash-verified pitch sidecar is retained with each voice; backing deduplication can use it as well. Export remains independent: a disabled curve option does not write measured pitch or flat replacement presets into editor projects.

Brief melodic steps missed inside an aligned syllable receive a bounded local GAME check. A new short-note boundary requires both stable RMVPE pitch cores and a nearby GAME boundary with the corresponding pitch centers on both sides. A contour change alone is insufficient. Unconfirmed proposals and failed optional checks preserve the existing notes, and cancellation still stops processing. This shares the existing 16-window/24-second per-voice retry budget with collapsed-syllable recovery and reuses the installed model. Smooth periodic vibrato stays in the pitch curve; clear discrete step transitions remain eligible. Attempts and accepted boundaries are recorded in `note_boundary_checks`. This does not recover every short ornament, nor does it repair words absent from recognition.

## Lyrics and syllable boundaries

Chinese and Japanese alignment consume each lyric token even when its phoneme alignment has zero duration or no usable vowel. A failed syllable therefore does not shift later characters onto earlier notes, including kana output. Adjacent aligned words can remain connected through an onset consonant of up to 350 ms. This does not turn a long word-to-word pause into a vowel; general boundary repairs remain limited to 150 ms.

When reference lyrics are selected, matching can restore a small number of internal omitted syllables between strong, ordered reference anchors. The expanded phonetic text is forced-aligned against the audio again. The program does not divide a long note into equally timed characters or invent missing text without a reference. An incorrect reference or a substantially different arrangement can still cause alignment errors.

Chinese HFA results are checked for clusters of collapsed syllables around an unexplained gap. A suspicious window is retried with enough following words to anchor it, reusing the loaded model. A replacement must keep the same word sequence and recover at least two usable syllables. Failed retries preserve the original alignment. Retries are limited to eight windows per voice, recorded in the quality report, and do not introduce another model download.

Japanese phrase-final moras have a separate recovery path when pitch analysis is enabled. A recognized mora compressed to less than 45 ms can trigger local pitch-only GAME inference, including a collapsed final mora immediately following a usable voiced syllable. The candidate window can include up to 250 ms of the following alignment silence. The program retains its existing ASR text only when local notes agree with voiced RMVPE evidence and adequate vocal energy. Unsupported releases are trimmed before verification; a corresponding collapsed placeholder is replaced rather than repeated. The process reuses the loaded model, is bounded to 16 windows and 24 seconds of candidate audio per voice, and records its proposals, accepted intervals and placeholders in the quality report. It does not invent a consonant missing from ASR or recover text from silence.

Use **参考歌词与校准 → 网易云匹配** or import a local lyric file when text accuracy matters. Reference lyrics are applied to the lead by default; backing lyrics remain independent. A reference is especially useful when neighboring phrases contain similar sounds or repeated vowels. Automatic ASR can still omit or mishear words when no reference is supplied.

## Note checks

Algorithm 11 includes an explicitly disabled experiment that treats a recovered stable tail touching an existing note at
the same written pitch, within the same aligned vowel, as a continuation of
that note. The old raw offset alone does not justify another attack. This only
changes newly recovered gaps; existing rearticulations, separate syllables and
pitch steps remain separate. Accepted extensions are recorded under
`aligned_continuations`, separately from new recovered notes and phrase-final
`release_extensions`. The continuous pitch samples are unchanged. The default
keeps separate recovered notes. This is an offline `refine_notes` experiment
(`merge_aligned_continuations=True`), not an enabled application setting: the
observed regression set showed mixed onset and offset results, so it has not
earned promotion into the normal worker.

Chinese local-gap crops use the worker's actual sample rate. The same aligned
interval is therefore retained with 16 kHz and 44.1 kHz input, rather than
truncating a lower-rate waveform using a fixed 44.1 kHz divisor. This is a crop
correctness fix, not a new ASR model or a measured accuracy improvement.

Phrase-final notes can retain a continuous release beyond the aligner's end when stable measured pitch and vocal energy support it. Extension is capped at 600 ms and stops before another note, syllable, pitch change or new energy attack. At most 20 ms of missing pitch is bridged; unsupported silence is excluded. This changes an existing note's end, not its text or onset. The quality report records accepted extensions under `release_extensions`.

The final check retains aligned syllable boundaries even when adjacent notes have the same pitch. Stable new pitch plateaus can split a missed melodic transition. Missing regions are recovered only inside a timed syllable with adequate voiced-pitch evidence; silence and arbitrary text length cannot create notes.

Version 3 preserves explicitly aligned syllables down to 35 ms instead of applying the 70 ms pitch-only split threshold to every lyric. Short neighboring syllables keep their own text. Missing aligned syllables of at least 60 ms can be recovered with stricter short-window pitch stability; unvoiced regions remain excluded. A note spanning two timed phrases can be trimmed around their intervening pause only when low energy or missing voiced evidence also supports that pause.

Short articulation gaps inside a continuous aligned phrase are closed by extending the preceding note to the next onset. The next note's onset and lyric remain unchanged. Longer rests, missing intervening syllables and unaligned pitch-only passages are not joined automatically.

Small note-center errors can be corrected from a stable measured pitch. Larger changes require a second, independent YIN estimate from the audio. Normal vibrato and continuous slides remain pitch curves. Weak or unstable pitch-only candidates can be excluded. With a substantial reference selected, quiet filler sounds absent from that reference can also be excluded; strong ad-libs and explicitly referenced fillers remain. These checks are conservative heuristics, not a general singing-versus-instrument classifier.

Center checks compare the integer MIDI key actually written to the editor, rather than a fractional model value that can round to a different key. Brief notes can qualify with fewer frames only under tighter stability and voiced-coverage requirements. A lyric split alone does not authorize a large pitch change: it still needs an independent check or a detected new melodic plateau.

Each voice's `midi/<voice>/<voice>.quality.json` records raw candidates, exclusions, pitch changes, note splits, recovered notes and timed syllables. Original source audio and previous tasks are preserved.

`aligned_coverage` also lists substantial regions inside already timed
syllables that the final notes do not cover. A `voiced_uncovered` entry has
measured voiced-pitch support but remains an inspection candidate: ornaments,
alignment errors and notation choices still need review. The report retains
the lyric, exact interval and pitch evidence; the worker prints a coverage
notice without fabricating notes. Deliberately excluded quiet reference
fillers and shared-lead residuals are not relabelled as omissions. Missing ASR words without a timed
syllable remain outside this check.

## Rechecking voiced passages marked as silence

With continuous-pitch analysis enabled, Chinese alignment is checked against the measured voicing mask. If a short phrase contains a sustained audible vowel that the aligner labelled as silence, the assistant retries that phrase with its existing recognized phonemes and a small amount of surrounding audio. It reuses the loaded alignment model. A retry must preserve the word sequence, phrase boundaries and usable syllables, and reduce the unexplained voiced gap before it replaces the first result. Real rests remain rests. Attempts are bounded and recorded in `voiced_pause_retries`.

Long reference matches can also restore a missing internal pair near the beginning of a phrase, even when only one preceding character survived recognition. Ordered anchors and limits on the number and length of omissions still apply. Text before or after the matched span is not added. The expanded text is aligned to audio; it is not assigned guessed, equal-length notes. A stable aligned vowel narrowly below the normal voiced-frame threshold can qualify for note recovery only with a compact, well-supported pitch core.

If phrase re-alignment still leaves one recognized internal syllable collapsed between intact words, local GAME extraction can propose a note in that bounded gap. Independent f0 and audio level must support it. It cannot supply text absent from the recognized/reference-matched sequence, and it does not recover a whole missing sentence. These decisions are recorded in `internal_syllable_repairs`.

## Avoiding duplicate lead notes in backing

Backing transcription can still hear a quiet lead residual after audio cleanup. When the separation check has recorded shared components, the assistant compares eligible backing candidates with the current lead's verified fine-grained pitch analysis and audio. Exclusion requires close contour agreement, sufficient voiced frames, positive waveform correlation and a weaker backing signal. Two notes having the same MIDI key is not sufficient. Independent harmony contours remain eligible, including ordinary unison notes with different pitch variation.

The backing quality report retains raw candidates and records any `cross_voice_exclusions`. All requested native outputs receive the same final note list. Cached backing results also depend on the lead analysis and separation report, so changing the lead cannot silently reuse a previous deduplication decision. This heuristic can still confuse intentionally phase-locked doubles with leakage and can leave uncertain residuals intact.

Backing deduplication also checks the first and last 120 ms of a mostly shared note. At least 60 ms of an independent voiced contour protects that candidate; a single-frame spike does not. This prevents a genuinely different release from being discarded solely because most of the preceding note resembled the lead.

## Continuous pitch and project export

Measured absolute pitch and its voicing mask are retained in a compressed `<voice>.pitch.npz` sidecar. Project export uses this data directly instead of adding note-relative offsets to USTX's simulated default pitch. Unsupported frames and rests become explicit breaks in the curve, preventing a default pitch floor from extending through instrumental regions.

The native USTX offset also uses the rounded written key, avoiding detuning caused by subtracting the model's fractional note pitch. Isolated one- or two-octave inference jumps are repaired only when they last at most 20 ms and their immediate neighbors agree. Sustained octave changes, ordinary ornaments and vibrato remain intact. Invalid pitch values are excluded. This does not resolve every sustained disagreement between transcription and pitch models.

Version 4 also uses the existing Chinese/Japanese phoneme alignment to screen pitch inside known unvoiced consonants. Only points more than three semitones from the written note are excluded, leaving a 10 ms margin at both phoneme boundaries. Vowels, voiced or unknown consonants and unaligned material keep their existing pitch handling. Notes can remain connected through a consonant without following an unreliable pitch there. The filtered pitch is used consistently for note checks, the saved sidecar and project export. The quality report records the excluded frame indices and original values under `pitch_phoneme_guard`; alignment mistakes can still affect this heuristic.

The sidecar is verified against the extraction receipt. A missing or changed sidecar requires re-extraction instead of silently reverting to a different pitch method. A manually edited native project keeps its own tuning. Disabling pitch export still removes explicit pitch presets rather than adding flat replacement curves.

The sidecar is normally tens of KiB per voice for a short song. It is part of the task data; preserve the complete task folder for reproducible re-export. Reference text, model output and export validation do not establish perceptual transcription accuracy. Audition the result in the target editor and report problematic times or bars.
