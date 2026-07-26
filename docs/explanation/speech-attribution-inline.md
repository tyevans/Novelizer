# Why attribution is authored, not inferred

Novelizer needs to know, for every sentence of finished prose, which character
is speaking it. That is not a nicety — a text-to-speech pipeline consuming a
chapter needs to assign a voice per line, and there is no way to assign a
voice to a line whose speaker is unknown. The question this document answers
is where that knowledge comes from: does the room infer it after the fact, or
does the Author write it down at the moment of composition?

## The case that kills inference

Consider four lines of dialogue, the kind any dialogue-heavy chapter contains
by the dozen:

> "This is how much?"
> "That's twenty."
> "Dollars?!"
> "Yeah, twenty dollars."

There is not one dialogue tag anywhere in that exchange — no "she said," no
"he asked." This is ordinary, well-written prose; tagging every line of a
rapid back-and-forth is exactly the kind of mechanical over-explanation good
writing avoids once the reader can follow the rhythm of a two-person
exchange. And that is precisely what makes it unrecoverable after the fact.
By the third line, "Dollars?!", nothing in the text says who is speaking. A
human reader tracks it through several cues that are not in the words
themselves — pattern continuation from the scene's speaking order, who is
established as being in the room, who would plausibly react with surprise at
that moment — cues that live in the writer's mental model of the scene, not
on the page. A parser has no such model at all: it can only look at the text.
An LLM asked to infer the speaker afterward is in a scarcely better position:
it is reading the same page the parser is, with none of the scene-construction
context that produced it, and asked to reverse-engineer a fact that was never
recorded. It can guess, and often guess plausibly, but a voicing pipeline that
assigns the wrong voice to "Dollars?!" has silently corrupted the output in a
way nothing downstream will catch, because the wrong assignment reads just as
plausibly as the right one.

The only entity that actually knows who speaks the third line is the Author,
at the moment it writes that line — because the Author is the one deciding,
sentence by sentence, who is talking. That knowledge exists exactly once, at
the moment of authorship, and if it isn't captured then, it's gone. So it has
to be captured then.

## What was rejected, and why

**Post-hoc inference by a dedicated pass.** An agent reads finished prose and
guesses attribution from dialogue tags, alternation patterns, and character
presence. This is the approach the four-line exchange above defeats directly:
it is the common case in dialogue-heavy fiction, not an edge case, and no
amount of heuristic sophistication recovers information the text never
contained.

**Deterministic parsing of unmarked prose.** A rule-based parser (looking for
"she said," proximity to a named character, alternating-speaker heuristics)
fails for the identical reason inference does, plus a second one: pronoun-only
attribution ("he said," with two male characters in the scene) is ambiguous
to a rule as well as to a model. Neither failure is a corner case to be
special-cased away; both are structural.

**Markers left in canonical prose, stripped at render time.** Instead of a
separate attribution pass, the Author's `<speech>`/`<thought>` tags could stay
in the `prose` field permanently, with every reader of prose — the TUI, the
EPUB exporter, `search_canon`, the continuity miner, the Summarizer, any
future consumer — stripping them before use. This was rejected because it
converts a one-time parsing problem into a permanent, distributed obligation:
every current and future prose consumer would need to know about the markup
and remember to strip it, and a single consumer that forgets leaks raw tags
into a search result, a summary, or an exported EPUB. Centralizing the strip
into one pass, after which `chapter.prose` is simply plain text again, means
no consumer downstream of that pass ever has to know markup existed. (Between
the Author writing a chapter and the Attributor processing it, that
obligation does still exist briefly — every other prose consumer's prompt
carries a short marker-awareness note for that window. But it is a window,
not a permanent property of `chapter.prose`.)

**Two prose fields on one event — a marked draft and a clean copy.** The
Author's `chapter.created`/`chapter.revised` could carry both the marked-up
draft and a pre-stripped clean version, generated in the same pass. Rejected
for two reasons: it doubles the storage cost of every chapter's prose, and it
asks a single generation to emit the same text twice, consistently — any
divergence between the two copies (a dropped clause, a reworded sentence in
one but not the other) creates two disagreeing sources of truth for what the
chapter actually says, with no way to tell which one is authoritative.

## The design that was chosen

The Author writes markup inline, at the point of composition, because that is
the only point at which the information exists:
`<speech char="Mira">"Twenty dollars."</speech>` for spoken dialogue,
`<thought char="Mira">…</thought>` for rendered interior thought, narration
left bare. A separate agent, the Attributor, then parses that markup
deterministically, resolves each `char` name to a character id against the
roster's names and aliases, strips the tags, and commits the clean prose
together with a dense segment list in one `chapter.attributed` event (see
[`chapter.attributed` and `speech_segments`](../reference/speech-attribution.md)
for the event shape, and [the Attributor](../reference/attributor-agent.md)
for the agent itself). After that event, `chapter.prose` is ordinary text
again, and the attribution lives entirely in the `speech_segments`
projection.

Clean prose is canon; the annotation is derived from it and stored
separately, never merged back into the prose. This split is what keeps
"markup exists" a fact about one narrow window (draft written, not yet
attributed) instead of a fact every prose consumer must carry forever. It
also means the marked draft is never discarded outright — it survives in the
event log as the original `chapter.created`/`chapter.revised` payload — but no
consumer needs to reach for it, because the clean prose plus the segment list
already contains everything the marked-up version did: every character of
text, which speaker said which span, and where the narration sits between
them. Nothing is thrown away; only the redundant, harder-to-consume encoding
of the same information is retired.

## The annotated view is derived, never stored

The export pipeline (`novelizer/export/voicing.py`) can render chunks back
into the same tag syntax the Author originally wrote — `render_annotated`,
used for a human checking the attribution by eye rather than feeding a voicing
engine. This is worth being explicit about: that rendering is produced on
demand from clean prose plus segments, and it is never written back to
canon or stored as a third copy. Storing it would recreate exactly the
two-copies problem the design rejected above — a second surface that could
drift from the first with no arbiter between them. Because clean prose and
segments together are a complete, lossless record of the marked-up text
(every offset is exact, every gap is an explicit narration segment), there is
nothing the marked-up rendering could carry that regenerating it from the
stored data would ever fail to reproduce.

## Why the design couldn't have been simpler

Every simplification considered above removes a real capability. Skipping the
authoring step and inferring later loses the third line of the four-line
exchange — not as a rare failure, but as the median case in fast dialogue.
Keeping markers in canonical prose removes the stripping pass but pushes an
equivalent obligation onto every future consumer instead, which is strictly
worse: one pass that can be gotten right once versus an indefinitely long list
of call sites that can each independently get it wrong. Storing both a marked
and a clean copy removes the stripping pass too, at the cost of a generation
step that must produce two texts that agree, forever, with no mechanism
enforcing that they do. The chosen design — mark once at the point where the
information exists, formalize once in a narrow deterministic pass, store the
clean result as the single source of truth, derive everything else from it —
is the version of this feature that has exactly one place where the fact "who
is speaking" is decided, and exactly one place where it is recorded. That is
the sense in which it is the simplest design that actually works: not the
smallest number of moving parts, but the smallest number of places the truth
about attribution has to live.
