# Per-schema field conventions

Field names below are exact (`novelizer/agents/schemas.py`,
`novelizer/agents/base.py`). "One line" means no newlines, headline-style,
aim under 120 characters.

## ChapterDraft (Author)

| Field | Contract |
|---|---|
| `title` | One line: the chapter's name only. Never the body, never tags. |
| `prose` | The entire chapter body, and nothing else. No repeated title line, no markup, no trailing notes. |
| `character_ids` | Ids from the cast block of your task context. |
| `feed_note` | 1–3 sentences for the activity feed. |
| `thread_intents` etc. | Structured observations go here, never as text inside `title`/`prose`. |
| `flags` | Concerns you cannot resolve yourself, as `FlagDraft` items. |

## WorldEntryDraft (World Architect, Retconner)

| Field | Contract |
|---|---|
| `title` | One line, the entry's name. |
| `body` | The entry text, plain prose. |
| `domain` | One of the documented enum values; when unsure, `other`. |
| `tags` | Short lowercase tokens, not sentences. |
| `supersedes_id` | Only an id of an entry you actually read. |

## KeeperOutput (Character Keeper)

| Field | Contract |
|---|---|
| `new_characters[].name` | One line, the character's name only. |
| `new_characters[].traits/motivations/backstory/voice` | Plain prose, each field its own concern — don't repeat one field inside another. |
| `updated_characters[].id` | An existing character id from cast context or a read file. |
| `feed_note` / `no_action` | Stand-asides: `no_action=true`, empty lists, one-line `feed_note`. |

## FlagDraft (all agents)

| Field | Contract |
|---|---|
| `category` | The category your role documents. |
| `description` | 1–3 sentences stating the concern with its evidence handle. |
| `related_entry_ids` | Only ids you saw. |
| `proposed_resolution` | One sentence; empty when you have none. |

## Intents (ThreadIntent, PromiseIntent, ThemeIntent, CausalIntent)

- Minting actions (`plant`, `make`, `introduce`) fill the freeform
  `name`/`title` — one line, a label not a paragraph — and leave `id`
  empty: the system slugs the id.
- Citing actions (`touch`, `pay_off`, `abandon`, `progress`, `pay`,
  `release`, `develop`) fill `id` with an id you saw, and leave
  `name`/`title` empty.

## Secrets (SecretPlant, SecretCitation)

Secrets split minting and citing into two lists instead of one action
enum, so which fields you owe is decided by the list you are in.

- `secret_plants` is for a secret that does not exist yet: a `title` and
  a `note`, nothing else. There is no id field — the system mints it.
  This list is available even when no secret exists in the story.
- `secret_citations` acts on a secret you can already see listed: `id`
  plus `action` (`learn`, `uses`, `reveal`). `learn`/`uses` also name the
  `character_id`; `reveal` leaves it blank, because a reveal makes the
  secret public rather than being one character's act of knowing.
- Never put an id in a plant or a title in a citation. If you have a
  title but no id, it is a plant.
- `note` is one or two sentences of context, not a summary of the
  chapter.
- `evidence` (where present) is a chNNN handle or canon file path you
  actually read — a citing intent without evidence reads as a guess.
- `CausalIntent` cites two existing chapter ids; it never mints anything.

## SummarizerOutput

| Field | Contract |
|---|---|
| `gist` | A single line, ≤ 140 characters, for the chapter map. |
| `summary` | One paragraph. |
