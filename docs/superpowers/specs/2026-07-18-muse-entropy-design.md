# Muse: Event-Sourced Entropy for Names and Story Beats

**Date:** 2026-07-18
**Status:** Approved

## Problem

LLMs converge on a tiny pool of tokens when inventing fiction. A May 2026
Cornell study (Hamilton & Mimno) found that 11 words — the names Elias, Mara,
and Elara; professions like lighthouse keeper, baker, and clockmaker; and the
word "lighthouse" — appeared in 88% of AI-generated stories across GPT, Claude,
and Gemini models. "Elias the lighthouse keeper" alone appeared in two-thirds.
The names have become a cultural tell for AI-generated writing.

Novelizer is fully exposed: character names originate implicitly in the
Author agent's prose, story beats are invented per-chapter by the same prompt,
and the only randomness in the system is sampling temperature — which does not
fix convergence (the Cornell study models ran at normal temperatures).

Prompting the model to "be original" cannot de-bias it. The fix is entropy
injected from outside the model.

## Goal

Break the convergence prior by sampling real-world corpora with a real PRNG
and injecting the draws into agent prompts — binding for character names,
optional inspiration for professions, settings, and beats — with uptake
tracking so ignored draws are visible rather than silent.

## Non-Goals

- No network calls; all corpora are bundled static data.
- No change to the Author's core prose loop beyond added prompt blocks.
- No forced story beats: beat draws are suggestions (director decision), with
  the authority level carried in the event schema so it can be raised later
  via settings, not redesign.

## Design

### New bounded context: `novelizer/muse/`

Pure-Python domain: corpora loading, seeded drawing, hand assembly. No LLM
calls anywhere in this context.

**Corpora** — versioned static data files under `novelizer/muse/data/`:

| Corpus | Source | Notes |
|---|---|---|
| Given names | SSA baby-name data (public domain) | bucketed by era (victorian/interwar/midcentury/late20th/modern); buckets are curated mid-frequency lists sampled uniformly — curation replaces runtime frequency weighting, keeping draws real-but-not-top-10 |
| Surnames | US Census surname list (public domain) | Curated mid-frequency list, sampled uniformly |
| Professions | Historical census occupation titles + modern list | e.g. "cordwainer", "linotype operator" |
| Settings | Curated archetype list (40 curated entries) | Deliberately far from the coastal-village attractor |
| Beat cards | Curated complication deck (40 curated entries, written by us) | Oblique-strategies style: "someone's stated motive is false", "an object changes hands unnoticed" |

Each corpus file carries a version identifier recorded in every draw event.

**Draw model** — `InspirationDraw`:

- `kind`: `names | profession | setting | beat`
- `items`: the drawn entries
- `seed`: PRNG seed, sourced from OS entropy (`secrets`) at draw time and
  recorded in the event — fresh entropy going forward, deterministic replay
  looking back
- `corpus_version`, `target_agent`, `authority` (`binding | inspiration`)

**New event types:**

- `INSPIRATION_DRAWN` — a hand (or single-kind draw) was dealt
- `INSPIRATION_UPTAKE_RECORDED` — a drawn item appeared in a chapter
  (`draw_id`, `item`, `chapter_id`, `used`)

### Muse agent

Joins the roster in `novelizer/runtime.py` like the other agents, but makes
no LLM calls — poll the read projection, top up the hand, commit events.

- **Cadence:** keeps exactly one unconsumed hand ahead of the Author. When a
  chapter draft consumes the current hand, or no hand exists, deal a fresh
  one: ~5 full names (era-coherent given+surname combos), 3 professions,
  2 settings, 2 beat cards.
- **Anti-repetition:** draws exclude items appearing in the last 3 hands
  (configurable via settings), tracked from the event log, so "random" names
  don't recur across a novel.
- **Era coherence:** the era bucket for name draws comes from the `muse_era`
  story setting (story.toml-overridable); default is the modern bucket.
- **Authority split:** the names draw is a binding casting pool; professions,
  settings, and beats are optional inspiration.

### Prompt integration

- `Author._summarize()` gains two blocks:
  - **Casting pool** (binding): "When introducing a new character, take a
    name from: …"
  - **Inspiration hand** (optional): "Optional sparks — use any that fit: …"
- WorldArchitect additionally renders the hand's settings draws in its
  existing seeds section, read-only.
- Consumption follows the existing director-signal pattern: the hand targets
  the Author and is marked consumed when the Author commits a chapter draft.
  WorldArchitect's read-only view never consumes.
- **Defense in depth:** the Author system prompt gains a short static
  ban-list of known AI-tell names and tropes (Elias, Elara, Mara, Thorne,
  lighthouse keepers, clockmakers, coastal villages) covering minor
  characters the casting pool doesn't reach.

### Uptake tracking

- **Names:** measured from `CHARACTER_CREATED` events — CharacterKeeper
  already mints characters from chapter prose, so a minted name matching a
  pool entry is direct evidence of uptake. No prose mining needed.
- **Professions / settings / beats:** the ContinuityChecker's existing
  low-temperature prose-mining pass gains one job: given the chapter text
  and the hand live when it was written, report which items appeared.
  Committed as `INSPIRATION_UPTAKE_RECORDED`.
- Because beat draws are optional (accepted risk: LLMs often ignore
  suggestions), the running uptake rate is the feature's health metric. If
  it trends toward zero, the director can raise authority via settings.

### Director visibility

- `:muse` CLI command — show the current hand and uptake history/rate.
- `:muse reroll` — discard the current hand and deal a new one (supersedes
  the old hand's events; the old draw remains in the log, marked superseded).

### Error handling

- Corpora validated at startup; missing or corrupt data files fail fast.
- If no hand exists when the Author polls, the Author proceeds without the
  blocks — degraded, logged, never blocked.
- Reroll supersedes rather than deletes: the event log keeps every draw.

## Testing

Red/green + property-based, per project principles:

- **Property:** same seed + same corpus version ⇒ identical draw; replaying
  the event log reproduces every draw exactly.
- **Property:** no item repeats within the exclusion window across
  consecutive hands.
- **Property:** era-bucketed name draws respect the requested era bucket.
- **Unit:** prompt-block rendering (casting pool, inspiration hand,
  ban-list), hand consumption semantics, reroll supersession, uptake
  matching from `CHARACTER_CREATED`.

## Decisions log

| Decision | Choice | Alternatives considered |
|---|---|---|
| Entropy source | Real PRNG over real-world corpora | Random constraints w/ LLM inventing; ban-list only |
| Beat authority | Optional inspiration (uptake-tracked) | Hard constraint; must-engage-one |
| Draw scope | Names, professions, settings, beats | Names only |
| Architecture | Event-sourced Muse agent | Draw-at-prompt-build module; director-CLI-only `:draw` |
