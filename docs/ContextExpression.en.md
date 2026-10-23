# Context expression planning

Status, 2026-10-20: the automatic lyric-semantics direction has been retired following user audition feedback. It is not enabled in the desktop workflow; do not configure a new semantic service or extend emotion-to-parameter recipes. Existing imported experiments and their validators remain for historical inspection. Reuse explicit structure, source-linked methods, word alignment and manual local choices for future user-selected styles. The API examples below document the retained experimental tooling, not a recommended automatic tuning workflow.

This optional backend proposes alternative performance plans over existing lyric/score evidence. The offline path combines explicit structure and positions with source-linked conditional expression methods. A provider-neutral interface accepts stanza-level semantic interpretations. It does not configure a language model, synthesize audio, select a voicebank, or write expressive parameters. Existing project and audio export options continue to work independently.

Run the generated module from the application root with the bundled Python; do not launch files inside src/ directly.

```powershell
& '.\dependencies\vocal2midi\python\python.exe' -B '.\context_workflow.py' prepare --job 'C:\BVWA\jobs\example'
& '.\dependencies\vocal2midi\python\python.exe' -B '.\context_workflow.py' propose --job 'C:\BVWA\jobs\example' --goal 'Keep the first phrase restrained; offer a clearer alternative'
& '.\dependencies\vocal2midi\python\python.exe' -B '.\context_workflow.py' export --job 'C:\BVWA\jobs\example'
```

Replace the example task path. An existing per-voice USTX is required. Preparation does not rerun ASR or MIDI inference. To prepare advice as part of ordinary export, use project_exports.py --job TASK --context, or the effective request option context_enabled=true. A context_plan path selects an existing plan; relative paths are resolved inside the task. These CLI overrides do not rewrite the stored task request. No new GUI controls are added in this iteration.

Preparation saves a user-editable draft in context/plan.json. It never replaces an existing plan automatically. After score, lyrics, explicit word spans, or timing evidence changes, old plans fail validation rather than silently rebinding their notes. Explicit prepare --refresh-draft backs up the previous plan and creates an empty one.

Evidence retains verbatim task lyrics with Unicode code-point offsets, current score notes with stable IDs, and overlap links to verified syllable timing. Exact complete lexical agreement supports text links; phonetic text is not converted back into guessed original characters. Unknown associations remain explicit. Hyphen and USTX +~ continuations can inherit an established preceding unit; a bare + is not treated as a reliable continuation.

Characters are not labeled as words. Optional --words FILE accepts a JSON object grouped by voice, containing explicit spans such as {"vocals":[{"span":[0,2],"source":"manual"}]}. Spans use zero-based code points with an exclusive end. Preparation stores this input in context/word-spans.json so subsequent export uses the same segmentation. API callers may instead supply context_word_spans in the effective request.

Timing from a quality.json sidecar is accepted only when both its hash and the current USTX hash match the producer report. Edited or legacy scores still provide current score facts, but unverified acoustic timing is omitted. USTX part offsets, resolution and tempo changes are included in seconds conversion.

The evidence builder and plan validator have no model dependency. The default plan is:

```json
{
  "schema_version": 1,
  "kind": "bvwa.context-plan",
  "evidence_ids": {"vocals": "<current evidence_id>"},
  "candidates": [],
  "selected_candidate": null
}
```

Each candidate contains a unique short id, intent, and gestures. A gesture references a voice, existing note_ids, phase (onset, sustain, release), and a textual direction. Optional rationale records an explanation. Use the IDs actually present in the generated evidence. Stale evidence, unknown notes, duplicate IDs, unrecognized fields, direct SV parameters, and probability fields are rejected. No semantic interpretation is inferred merely because a plan validates.

exports/context contains per-voice evidence, a validated plan snapshot, and preview.json. The preview retains all candidates, the selected ID and a hash-verified baseline SVP reference when available. It explicitly reports advisory_only, adapter_not_implemented, project_written=false and audio_rendered=false. This is a candidate planning artifact, not a newly tuned SVP. A calibrated gesture adapter is future work.

These outputs have independent producer receipts and artifact-index entries. Plan, evidence, baseline or context-code changes invalidate the context products; changing the plan does not change base project signatures or the MIDI inference inputs. Failed/canceled context work preserves committed base outputs. Regeneration backs up edited context products. Original parameters, knots, amplitudes, long-note movement, pitch, unknown channels and manually retained expression are untouched by this module.

When the selected vocal parts change, superseded context evidence is marked inactive in receipts and the artifact index. The files remain on disk as history. Inactive receipts are not revived when reconstructing the index. Disabled context export does not import its optional modules.

Validation separates data-contract/export checks from acoustic measurements and listening. Tests use synthetic local scores and isolated task folders; passing them does not establish lyric interpretation accuracy or improved sound.

## Structure, source retrieval and alternatives

propose saves independent context/proposal-input.json and context/proposal.json inputs, a strict context/plan.json, and receipt-backed snapshots in exports/context. Per-voice structure packets and local method-match reasons accompany the proposal. Use --semantic-response FILE to import an explicitly supplied JSON interpretation; --words FILE works for preparation and proposal creation. Inputs validate before proposal word spans are committed.

A repeated propose with the same generated plan reuses valid outputs. Omitting --goal or --semantic-response retains the persisted proposal input. A manually edited nonempty plan requires explicit --refresh-draft, which keeps a backup before replacement. An external --plan stays read-only. Preparing/ready transaction records distinguish interruptions before and after writing the plan; a newer manual edit prevents an automatic replacement. Re-exporting a manually selected or edited strict plan retains the original proposal alternatives.

Structure analysis retains original Unicode spans, stanzas, phrases, repetitions, explicit word positions and score-rest/duration facts. Characters are not relabeled as words. Surface negation, contrast, conditional and addressee cues are lexical hints with unknown semantic scope. Duration/rest thresholds are declared rule choices, not emotional labels. Stanza function, sarcasm, inferred breathing and character relationships require separate evidence.

Method cards retain source IDs/URLs, the author's setting, a short paraphrase, opposite choices and limitations. The current catalog retains 14 cards: five readable method cards participate in retrieval, while nine remain disabled because their context is unsupported or their available evidence is index-only. Disabled retrieval does not judge the musical value of those styles. A match score measures structural support only; it is neither a probability nor an artistic quality rating. Free-text goals do not affect ranking. Cross-language and voicebank transfer remain conditional. Phoneme timing, pitch texture, voice modes and mixing suggestions remain textual and are not applied here.

The planner retains alternative intentions and leaves selected_candidate null. User goals, structural hypotheses, source observations and provider claims remain distinct. The offline baseline does not interpret a free-text goal into gestures; it reports goal_not_used_for_gestures and requires a user choice. Substring matches such as a character inside a negated or unrelated word are not semantic evidence. Score rests do not prove inhalation; repeated lyrics do not prescribe escalation. Unresolved text-to-note mappings cannot create guessed semantic gestures. Coverage limits remain explicit. Mathematical fitting is not used to reject a distinctive reference performance.

## Optional semantic interface

context_semantics provides a Python callable interface with no implicit HTTP client, endpoint, credentials or model download. The Chinese ASR model is not reused as a text planner. No provider means no semantic inference. A caller supplying a provider is responsible for its execution and authorization policy. Lyrics are quoted data, separate from instructions and the user's goal. Both `lyric_data.text` and phrase, stanza and cue text in `structure` contain untrusted lyric data; clearing only `lyric_data` does not remove lyrics from a request. Provider failures retain the exception type without its message, which may contain private lyrics or credentials.

Imported JSON responses must reference exact evidence revisions and provide bounded interpretations with verbatim Unicode spans/quotes, focus roles, intentions, explanations, alternatives and unknowns. Content fingerprints reject edited evidence retaining an old revision; source receipts still determine producer authenticity. The provider name/model is a claim, not proof of execution. A response bound only to evidence has unknown goal provenance and must not be treated as satisfying a different current goal. Stale references, invalid spans, unknown fields, direct parameter arrays and probability fields are rejected. Validation establishes format/reference consistency, not interpretation truth. Hand-authored responses are fixtures rather than model evaluation.

An optional provider failure can retain structural proposals with explicit failure metadata; malformed imported responses are rejected. The built-in CLI does not upload private lyrics to a remote service. Numerical voicebank adaptation still requires a separately verified score/bank/version-appropriate profile. Previous Shian PLUS experiments are not fresh calibration.

Semantic request packets remain bounded to 512 KiB. If a valid score exceeds that optional request limit, the provider is not invoked and the offline planner remains available; request.status explicitly reports request_unavailable. No truncated packet is labeled complete. Imported responses still validate independently against the current evidence. Scope/gesture/note caps are reported as partial or local-subset coverage.

Task JSON records are limited to 8 MiB. A proposal preflight reserves space for commit metadata and accounts for Windows text newlines, so an oversized proposal fails before replacing stored inputs or plans. It is not written as an unreadable record that could break subsequent recovery.

## Explicit SVP audition copies

The separate [context adapter CLI](ContextAdapter.en.md) supports opt-in partial text/reading alignment and reviewed numerical gesture applications on independent A/B projects. Advisory exports remain unapplied. Audition profiles are explicit unrendered experiments, separate from measured voicebank calibration. Context copies add density using the existing piecewise-linear function so unselected continuous values remain unchanged; this does not alter the reference/calibration pipeline's interpolation settings.
