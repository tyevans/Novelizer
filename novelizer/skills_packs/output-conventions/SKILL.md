---
name: output-conventions
description: The contract for every structured-output field — what belongs in title vs prose/body, length norms, no invented markup, id citing. Activate before emitting a draft, verdict, amendment, or intent if unsure what belongs in which field.
---

# Output conventions

Your final structured output is parsed by machines, projected into canon
files, and interpolated into other agents' prompts. A malformed field does
not fail loudly — it silently pollutes the canon filesystem and every
downstream context. These are the field-by-field rules.

## Universal rules (every schema)

- **Plain text only in freeform fields.** No invented markup tags
  (`<prose>`, `<title>`, any XML/HTML), no markdown headers, no code
  fences, no JSON serialized into a string field. If you find yourself
  writing a tag inside a field, stop: the schema already separates the
  parts — use the fields.
- **One field, one job.** Content never doubles up across fields. The
  title never contains the body; the body never repeats the title; a
  notes field never carries a second copy of your intents.
- **Titles and names are a single line.** A newline in a title or name
  is always a mistake. Aim under 120 characters, headline-style.
- **Cite only ids you saw.** Every id you emit (thread, secret,
  character, chapter, beat, promise) must appear in your task context or
  in a canon file you actually read this pass. Never mint an id, never
  reconstruct one from memory.
- **Empty defaults are valid answers.** When a field documents an empty
  default (`""`, `[]`), emitting that default is correct when there is
  nothing real to say. Padding a list to look thorough is a failure.

## Per-schema conventions

Read `references/schema-conventions.md` for the field-by-field table of
the major schemas (ChapterDraft, WorldEntryDraft, keeper outputs, flags,
intents). The universal rules above govern any schema not listed there.

The schema that has actually failed in production, in brief:

- `ChapterDraft.title` — one short line, the chapter's name only.
- `ChapterDraft.prose` — the entire chapter body, and nothing else: no
  repeated title line at the top, no tags, no trailing notes.
- `ChapterDraft.feed_note` — a couple of sentences for the feed, not a
  report.
- Thread/theme/promise observations belong in their intent lists, never
  appended to `title` or `prose` as text.

## Degenerate output — wrong vs right

An actual production failure. The drafting model emitted this
`ChapterDraft`:

```json
{
  "title": "The First Change\n<prose>\nThe decision arrived without fanfare. [7,900 more characters of chapter body] </prose>\n[{\"intent\": \"plant\", \"name\": \"uneven reception across places\", \"note\": \"...\"}]",
  "prose": "The decision arrived without fanfare. ...",
  "thread_intents": []
}
```

Everything went into `title`: the real title, an invented `<prose>` tag,
the full chapter body, and a JSON blob of thread notes. Downstream, the
canon file for this chapter became
`/chapters/001-the-first-change-prose-the-decision-arrived-…` — a
10,000-character filename — and every agent's prompt carried the whole
chapter twice.

The same content, emitted correctly:

```json
{
  "title": "The First Change",
  "prose": "The decision arrived without fanfare. ...",
  "thread_intents": [
    {"action": "plant", "name": "uneven reception across places", "note": "Some places welcome Death's transition; others resist it.", "evidence": ""}
  ]
}
```

One field, one job. When in doubt, re-read the schema before you emit.
