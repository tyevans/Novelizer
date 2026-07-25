# Tutorial: your first story

Novelizer is a director's control room for an autonomous writers' room: a cast of AI
agents builds a living world and writes a novel inside it, while you watch the room work
from a terminal UI called Mission Control and steer it with seeds and signals.

In this tutorial you'll go from nothing to a finished first chapter. We'll install
Novelizer, point it at an LLM, create a story, plant a single narrative seed, watch the
room pick it up in Mission Control, and then read the chapter it wrote. Every step shows
you exactly what to type and what you should see, so you can tell at each point that
things are working. You don't need to know anything about Novelizer's internals — or
about how AI agents coordinate — to follow along; by the end you'll have seen the whole
loop once, and the closing sections explain what happened and where to go deeper.

Expect the walk-through itself to take about fifteen minutes of your attention, plus
however long your LLM endpoint takes to draft the chapter while you watch.

## What you'll have at the end

Everything this tutorial builds is a real, inspectable thing on your machine. When you
finish, you will have:

- **A working `novelizer` command on your PATH.** Running `novelizer --help` prints the
  full command list, and running plain `novelizer` opens the terminal UI — a story
  picker with your story preselected, one keypress away from Mission Control.
- **A saved global configuration** at `~/.config/novelizer/config.toml` (or under
  `$XDG_CONFIG_HOME` if you set one), recording your LLM endpoint, API key, and model
  choices — so every later launch skips straight past setup.
- **A self-contained story directory** inside your stories folder, holding everything
  Novelizer knows about your story: a `story.toml` with its title, a `world.db` event log
  where every world fact and chapter lives, and a `chroma/` folder of embeddings. Copy
  that one directory and you've backed up the whole story; delete it and the story is
  gone, nothing else on your machine changes.
- **One finished chapter, written by the room from your seed.** It shows up in
  `novelizer chapters` with an ID, title, and editorial status, and you can print its
  prose any time with `novelizer read <id>` — from a script, a pipe, or just your
  scrollback.
- **A feel for the whole loop.** You'll have planted a seed, watched the agents pick it
  up live in Mission Control, and read the result — which means you'll know what
  "working" looks like at every stage the next time you start a story of your own.

Nothing here is a toy: the story you create in this tutorial is a normal Novelizer story,
and you can keep seeding it and let the room keep writing after the tutorial ends.

## Before you start

You need four things in place. Check each one now — everything after this point assumes
they're ready, and each check takes under a minute.

1. **A terminal you're comfortable in.** Mission Control is a full-screen terminal UI;
   any modern terminal emulator works. All the commands below are typed at your shell
   prompt.

2. **[uv](https://docs.astral.sh/uv/)**, the Python package manager we'll install
   Novelizer with. Confirm it's on your PATH:

   ```bash
   uv --version
   ```

   If that prints a version number, you're set. Novelizer itself needs Python 3.13 or
   newer, but you don't have to arrange that separately — uv downloads a suitable Python
   automatically during install if your system doesn't have one.

3. **A checkout of the Novelizer repository.** There's no published PyPI package yet, so
   Step 1 installs from a local copy of this repository. If you're reading this file
   inside a checkout already, just open a shell at its root:

   ```bash
   cd path/to/novelizer
   ```

   Confirm you're in the right place — the repository root contains `pyproject.toml`
   and the `novelizer/` package directory.

4. **An OpenAI-compatible LLM endpoint you can reach.** This is the one prerequisite
   Novelizer can't provide for itself: the writers' room needs a model to write with.
   Any server that speaks the OpenAI API works — llama.cpp's `llama-server`, vLLM,
   LM Studio, Ollama, or a hosted OpenAI-compatible service. It should serve at least
   one chat model and one embedding model; no API key is needed for local servers.
   You'll paste its base URL (something like `http://localhost:8080/v1`) into Novelizer
   in Step 2, so have it running and know the URL before you continue.

   Don't have one yet? Set one up first and come back — the how-to guide
   [Connect a local LLM](../how-to/connect-a-local-llm.md) covers starting a local
   server and the settings that matter for it.

One assumption to be aware of: this tutorial expects a fresh machine with **no existing
Novelizer configuration**. If you've run Novelizer before, `~/.config/novelizer/config.toml`
already exists and Step 2's setup wizard won't appear — you'll land straight in the story
picker, and you can simply skip ahead to Step 3. Nothing in this tutorial touches or
overwrites an existing config or existing stories.

## Step 1 — Install Novelizer

From the root of your repository checkout, install Novelizer as a uv tool:

```bash
uv tool install .
```

This one command does all the setup: uv builds the package, creates an isolated
environment for it (fetching Python 3.13 first if your system doesn't have it), and puts
a `novelizer` binary on your PATH. Because the install is isolated, it won't touch any
Python environment you already use, and the command works from any directory afterwards —
you don't need to stay in the checkout, activate a virtualenv, or prefix anything with
`uv run`.

You'll see uv resolve and install the dependencies, ending with a line like:

```text
Installed 2 executables: novelizer, research-domain
```

(`research-domain` is a companion research tool that ships in the same package — this
tutorial never uses it, and you can ignore it.)

Now confirm the binary works. Ask it for help:

```bash
novelizer --help
```

You should see a usage line followed by the full command list — thirteen subcommands,
including the three this tutorial uses (`seed`, `chapters`, `read`) alongside the rest
(`autonomy`, `voices`, `proposals`, and friends):

```text
Usage: novelizer [OPTIONS] COMMAND [ARGS]...

Options:
  --story PATH  Path to a story directory.
  --help        Show this message and exit.

Commands:
  approve
  autonomy
  chapters
  ...
  read
  ...
  seed
  ...
```

If you see that command list, the install is done — that's the whole step.

Two things worth knowing before moving on:

- **Don't run plain `novelizer` yet.** With no subcommand it launches straight into the
  interactive setup wizard and Mission Control — which is exactly what we want, but
  that's Step 2, and we'll walk through each screen there.
- **If your shell says `novelizer: command not found`**, uv's tool directory isn't on
  your PATH yet. uv prints a warning about this during install; run
  `uv tool update-shell` to add it, then open a new shell and re-run
  `novelizer --help`.

If you later pull new changes into your checkout, the same command with a flag —
`uv tool install . --reinstall` — refreshes the installed binary. And if you ever want
Novelizer gone, `uv tool uninstall novelizer` removes it cleanly.

## Step 2 — First launch: point Novelizer at your LLM

Time to launch. Make sure your LLM endpoint from "Before you start" is running, then
type:

```bash
novelizer
```

Because no configuration exists yet, Novelizer opens a full-screen setup wizard titled
**"Novelizer — First-run setup"** instead of going straight to a story. This wizard
appears exactly once: what you enter here is saved to
`~/.config/novelizer/config.toml`, and every future launch reads it silently and skips
ahead. The whole screen is three text fields, a connection test, and three model picks —
we'll go top to bottom.

**1. Fill in the three fields.**

- **LLM base URL** — pre-filled with `http://localhost:8080/v1`. If your endpoint is
  somewhere else, replace it with your server's base URL. This is the standard
  OpenAI-style base — it usually ends in `/v1`, with no trailing `/chat/completions`.
- **API key** — leave it blank for a local server; Novelizer sends a placeholder key
  local servers ignore. Paste a real key only if your endpoint requires one (the field
  masks what you type).
- **Stories directory** — where story folders will live. The default is `stories`,
  which is a *relative* path — it resolves against whatever directory you launch
  `novelizer` from. That's fine for a first run from your checkout; if you'd rather
  keep stories somewhere fixed, enter an absolute path like `~/novelizer-stories`
  (a `~` is expanded for you).

**2. Press "Test connection."** The wizard makes a live request to
`{base URL}/models` — the same call any OpenAI client makes — and shows the result
right below the button:

```text
✓ connected — models: qwen3-32b, nomic-embed-text
```

(Your model names will differ — the list is whatever your server reports.) If you see
`✗` and an error instead, nothing is saved yet: fix the URL or start your server, then
press **Test connection** again. A connection refused means nothing is listening at
that address; an HTTP 401 means the endpoint wants a real API key.

**3. Pick your three models.** A successful test unlocks the dropdowns beneath the
result line, each filled with the models your server just reported:

- **author model** — writes the actual chapter prose. Give this your strongest chat
  model.
- **agent model** — powers the rest of the writers' room (editors, world-keepers, and
  the other supporting agents). The same model as the author is a perfectly good
  starting choice.
- **embedding model** — builds the semantic memory the room retrieves from. Pick an
  actual embedding model (something like `nomic-embed-text`), not a chat model.

Each dropdown defaults to the first model in the list, so if your server serves only
one chat model and one embedder, correct the embedding pick and you're done.

**If the embedding dropdown has no embedding model in it,** your endpoint doesn't serve
one. This is the normal case for hosted chat routers: **OpenRouter has no embedding
models at all**, so no amount of scrolling that list will turn one up. Novelizer handles
this with a second, independent endpoint:

- Put an embedding provider's URL in **Embedding base URL** — Ollama running locally is
  the easiest (`http://localhost:11434/v1` after `ollama pull nomic-embed-text`), and
  OpenAI's `https://api.openai.com/v1` with `text-embedding-3-small` also works.
- Put that provider's key (if any) in **Embedding API key**. Your LLM key is never sent
  to this endpoint — it's a different provider, so the credential stays separate.
- Press **Test embedding connection**. The embedding dropdown refills from *that*
  endpoint's models, leaving your author and agent picks untouched.

Leave the embedding URL blank whenever one endpoint serves both — a local Ollama or
llama.cpp setup needs nothing extra here.

**4. Press "Save & continue."** The wizard writes your choices to
`~/.config/novelizer/config.toml` and moves on. Any field you left blank is simply
omitted from the file, so Novelizer's built-in defaults keep applying — which is why a
blank API key is fine for local servers.

What you should see next is the **story picker** — a screen listing your stories
(currently none) with the option to create one. That's your confirmation that Step 2
worked, and it's exactly where Step 3 begins.

A few notes before you continue:

- **If you quit the wizard** (press `q`), nothing is written and `novelizer` exits;
  run it again and the wizard reappears fresh.
- **"Skip model picks — save endpoint only"** saves just the URL, key, and stories
  directory without testing the connection, leaving the model choices at their built-in
  defaults. Handy if your server isn't up yet — but for this tutorial, do the full
  test-and-pick so you *know* the room can reach a working model before it starts
  writing.
- **Nothing here is final.** Everything you just entered is plain TOML in
  `~/.config/novelizer/config.toml`, editable by hand or from Mission Control's
  settings screen later.

If your endpoint isn't the simple default case — a different port, a hosted service
with a key, or you're still choosing and starting a local server — the how-to guide
[Connect a local LLM](../how-to/connect-a-local-llm.md) walks through server start
commands, base URLs for the common servers, and the settings that matter.

## Step 3 — Create a story in the story picker

You're now looking at the story picker — a screen titled **"Novelizer — Choose a
story"**. On a fresh machine it shows a single list entry, **"➕ New story"**, already
highlighted. (On later launches your stories appear below it — the one you opened last
comes first and is preselected, the rest follow most-recently-written first — so
returning to a story is just pressing Enter.)

**1. Press Enter on "➕ New story".** An inline form unfolds below the list, and your
cursor lands in the first field.

**2. Type a story name.** This is the only required field. Use whatever title you like —
for this tutorial we'll go with:

```text
The Lighthouse Keeper
```

The name does double duty: it becomes the story's title in `story.toml`, and a
lowercased, hyphenated version of it becomes the folder name — so this story will live
at `stories/the-lighthouse-keeper/` inside the stories directory you chose in Step 2.

**3. Leave the rest of the form alone.** Every other field is optional, and for a first
story the defaults are exactly right — but here's what you're skipping, so nothing on
the screen is a mystery:

- **The premise box** (the large empty text area) — anything you type here is planted
  as a narrative seed the moment the story is created. It's the same mechanism as the
  `novelizer seed` command. Leave it **blank** for this tutorial: in Step 4 we'll plant
  the seed deliberately from the command line, so you can see that moment happen on its
  own.
- **Voice pack** and **prose profile** — two dropdowns controlling the room's writing
  style. They're pre-set to Novelizer's defaults, which is what you want today.
- **"framing (optional)"** — a dropdown of story-structure templates (beat frameworks
  like `six-position`). Leave it unselected; when it's blank, the **target chapters**
  and **genre** fields below it are ignored too. Framing a story is a topic for after
  the tutorial.

**4. Press Enter** (while still in the name field) **or click "Create".**

Three things happen in quick succession: Novelizer creates
`stories/the-lighthouse-keeper/` and writes its `story.toml`, remembers this as your
last-opened story, and then launches straight into **Mission Control** — the
full-screen dashboard titled **"Novelizer — Mission Control"** where the writers' room
does its work. The story's `world.db` event log and `chroma/` embeddings folder are
created as the room boots, so once Mission Control is up, the full story directory
described in "What you'll have at the end" exists on disk.

If Mission Control is on your screen, Step 3 is done. Don't worry about reading the
dashboard yet — it's mostly quiet right now because the room has nothing to write. That
changes in the next step.

A few notes on the picker's edges:

- **The form checks your input before touching disk.** An empty name shows
  `✗ name required`; a name whose folder already exists shows
  `✗ stories/the-lighthouse-keeper already exists` — fix the field and press Create
  again. Nothing half-created is left behind.
- **Escape backs out of the form** without creating anything, returning you to the
  story list. **`q` quits the picker** (and Novelizer) entirely — also without creating
  anything. Run `novelizer` again to come back; your Step 2 configuration is saved, so
  you'll land directly in the picker.
- **The picker is how you'll return to this story.** Next time you run plain
  `novelizer`, "The Lighthouse Keeper" will be in the list and preselected — one Enter
  reopens it. (Scripted commands like the ones in Steps 4 and 6 find the last-opened
  story automatically.)

## Step 4 — Seed the world

Mission Control is open, and the room is waiting. In fact, it's telling you so: on a
brand-new story, the feed pane at the top left — bordered **THE ROOM** — greets you with
two lines from the Director's chair:

```text
★ The room is assembled: Author, Editor, Architect, Keeper, Continuity, Retconner, Analyst.
★ It's quiet. Give them a world:  :seed a lighthouse keeper who taxes the tide
```

The agents have nothing to write about until you give them a starting point. That
starting point is a **seed** — a sentence or two of narrative intent from you, the
Director, that the whole room gets to read. We'll plant one now, from the command line,
so you can watch the exchange happen: you type a sentence in one terminal, and it
arrives in the room in the other.

**1. Open a second terminal, in the same directory you launched `novelizer` from** (the
repository checkout, if you've been following along). Leave Mission Control running in
the first terminal — you want to be watching it when the seed lands.

The same-directory part matters because of a Step 2 choice: `novelizer` finds your
last-opened story automatically, but the default stories directory (`stories`) is a
relative path, so the remembered story only resolves from where you originally
launched. Run the command from somewhere else and Novelizer won't find it — it quietly
falls back to a story named `default`, and your seed lands in the wrong world. If you
do need to seed from elsewhere, point at the story directory explicitly:
`novelizer --story path/to/stories/the-lighthouse-keeper seed "..."`.

**2. Plant the seed.** The seed text is a single argument, so keep it in quotes:

```bash
novelizer seed "A lighthouse keeper who taxes the tide, and a stranger who arrives refusing to pay."
```

The command prints one green confirmation line and exits:

```text
Seed injected: A lighthouse keeper who taxes the tide, and a stranger who arrives refusing to pay.
```

That confirmation means the seed is already durable: it was appended to the story's
`world.db` event log as a `director_signal.created` event, the same way everything the
agents do gets recorded. Even if you quit Novelizer right now, the seed would be waiting
when the room comes back.

**3. Switch back to Mission Control.** Within a moment, your seed appears in THE ROOM as
a line spoken by you:

```text
★ Director    signal: A lighthouse keeper who taxes the tide, and a stranger who
arrives refusing to pay.  ◆ signal
```

If you see that line, Step 4 is done — the room has your seed. It's a broadcast: the
agents that look for director input (the Author and the World Architect among them) all
read pending seeds, and the World Architect treats one as top priority — it wakes on
its next scheduler pass and starts building the world your sentence implies. You may
already see new activity in the feed by the time you look; watching that unfold
properly is the next step.

A few notes before you switch back for good:

- **You could have done this without leaving the TUI.** Press `Ctrl+K` in Mission
  Control to open the command palette, type `seed`, and select it — an input line
  appears at the bottom, pre-filled with `seed `. Type your text, press Enter, and the
  feed prints the same `» Seed injected: ...` confirmation. Both paths append the
  identical event; this tutorial used the CLI so you'd see the two sides of the
  conversation in two windows. (The `:seed` spelling the welcome line advertises works
  too — the colon prefix is accepted, not required.)
- **This is the same mechanism as Step 3's premise box.** Anything you'd typed there
  would have been planted as a seed at story creation. Blank premise plus `novelizer
  seed` afterwards lands the story in exactly the same place.
- **Seeds aren't a one-time thing.** You can plant another whenever you want to steer —
  a new character, a turn of events, a constraint ("no one may speak the keeper's
  name"). The room folds each one into its next pass. For the full command syntax, see
  the [CLI reference](../reference/novelizer-cli.md).

## Step 5 — Watch the first chapter land in Mission Control

This step needs nothing from your keyboard. The room runs itself: a scheduler wakes
agents whenever they have reason to work — up to two at a time — and your seed just
gave two of them a very good reason. Your job now is to watch, and to know what you're
looking at, so you can tell healthy progress from trouble while the first chapter takes
shape.

**What you'll see in THE ROOM.** Every line in the feed starts with a fixed speaker
column — a glyph and a name in that agent's color — so it reads like a screenplay:
`✎ Author`, `§ Editor`, `⌂ Architect`, `♥ Keeper`, `★ Director` (you). Within the
first minute or so, expect the world-building and outlining to start. The World
Architect treats a pending seed as top priority, and the Plotter proposes a first-pass
blueprint from the same seed before any prose exists — the Author holds off until that
blueprint is adopted (or, in an unattended run, until world exists and a blueprint is at
least proposed), so it is normal for the first lines you see to be lore and outline
rather than a drafted chapter. A typical opening run of the feed looks like this (your
titles and names will be your own):

```text
⌂ Architect   lore: The Tide-Tax Ledger  ◆ lore
⌂ Architect   lore: Graywatch Light  ◆ lore
⌂ Architect   💬 "Three entries: the tax, the light, and who collects."
♥ Keeper      new character: Maren Kald  ◆ character
```

Three kinds of line are worth telling apart:

- **Canon lines** — an agent committed a fact to the world: `lore: <title>`,
  `new character: <name>`, `drafted "<title>"`. The dim `◆ lore` / `◆ character` /
  `◆ chapter` chip at the end names the domain the fact landed in. These are the story
  actually growing.
- **Remarks** — dim, italic, quoted lines marked `💬`. That's an agent's one-line note
  about the pass it just finished. Color, not canon.
- **Alarms** — bold red `⚠` lines. On a healthy first run you shouldn't see any; more
  on them under troubleshooting below.

**The two lines at the bottom** are your instrument panel while you wait:

- The **status bar** shows the whole cast as a glyph strip — one glyph per agent, with
  a mark after each: a spinner while that agent is running, a dim `·` when idle, `‖`
  paused, `!` errored. Next to it sits the autonomy dial, which on a new story reads
  `AUTONOMY ▮▮▮▮ full_auto` — the room is fully trusted to commit its own work, which
  is why you don't need to approve anything today.
- The **activity strip** underneath narrates the current run:
  `▶ author · drafting · 12.4k tok · 41s · call 3` while someone is working, and
  `idle · next: author in 42s` between runs. If the strip says idle with a countdown,
  nothing is wrong — agents pace themselves on intervals (the Author's is five minutes
  between passes by default), and the countdown tells you exactly who wakes next.

**The moment you're waiting for.** When the Author finishes a pass, the chapter arrives
in the feed under its own dim rule, so the feed self-organizes by chapter:

```text
── ch 1 · The Stranger at Slack Water ──
✎ Author      drafted "The Stranger at Slack Water"  ◆ chapter
✎ Author      💬 "Opened on the toll bell; left the stranger's refusal hanging."
```

That rule-plus-drafted pair is this step's success line. How long it takes is entirely
your endpoint's speed — the Author reads the world the Architect just built and then
drafts a full chapter of prose, so on a local model this is typically a few minutes of
the activity strip ticking upward. In the **STORY** tree on the right you'll see the
same arrival as a count change: **Chapters (1)**, with your chapter listed as
`◌ The Stranger at Slack Water` — the `◌` dot means editorial status *draft*.

**Want to peek at the prose right now?** Expand **Chapters** in the STORY tree and
select the chapter — the DETAIL pane below shows its title, status and word count, and
the full prose, scrollable. (We'll read it properly from the command line in Step 6.)

**Keep watching for one more beat: the Editor.** A fresh draft is the Editor's cue —
its readiness rises as drafts queue up, and on its next pass it judges the chapter and
either approves it or sends it back:

```text
§ Editor      reviewed "The Stranger at Slack Water" — reviewed  ◆ chapter
```

That line means the chapter was approved: its status moves from *draft* to *reviewed*,
and its dot in the STORY tree turns `◐`. If the Editor asks for a rewrite instead,
you'll see its notes go back as a signal and, a while later, a revised draft — the
loop is bounded (at most two revision rounds per chapter), so it always converges.
Either way you don't need to wait for the review to move on: the chapter exists from
the moment the `drafted` line appears, and that's all Step 6 needs.

**If something looks wrong instead:**

- **Red `⚠` lines in the feed** (or a `!` mark in the roster strip) mean an agent's
  run failed — with a local endpoint the usual causes are the LLM server having gone
  down or a wrong model pick in Step 2. The room is resilient: a failing agent backs
  off and retries on its next interval, so fix the server and the feed will pick back
  up on its own.
- **`✗ author crashed 2m ago (see Engine Room)`** on the activity strip is the same
  story, told by the strip. Press `e` to toggle the **Engine Room** — a single
  chronological stream of every agent's live model calls, token counts, and tool
  activity, each block tagged with that agent's glyph and colour so concurrent work
  stays legible. Click the `author` filter chip to narrow the stream to just that
  agent when you want to see exactly what it was doing; click `all` to widen it back
  out. Press `e` again to come back.
- **Nothing at all for a long time** — check the activity strip. `idle · next: ...`
  with a countdown is normal pacing; no countdown and no activity usually means every
  agent is paused (the roster marks would all show `‖` — press `P`, the pause-all
  toggle, if you've hit it by accident).

One more control worth knowing while the room works: `P` pauses every agent mid-story
and presses play again on a second press — handy when you want to freeze the room and
read the feed at leisure. When your chapter rule is on screen, move on to Step 6.

## Step 6 — Read your chapter

The room has written a chapter. Time to actually read it — twice, in fact: once inside
Mission Control in a view built for reading, and once from the command line, which is
how you'll get prose out of Novelizer whenever you want it in a file, a pipe, or just
your scrollback.

**1. Read it in Mission Control.** Press `v` — the **Reading** toggle. The whole left
column (feed and Story Brain) disappears, and the screen becomes a reading layout: the
STORY tree as a slim column on the left, and the DETAIL pane widened to take most of
the screen. Expand **Chapters (1)** and select your chapter. The DETAIL pane relabels
itself with the chapter's title and shows the title in bold, a dim metadata line —

```text
draft · 2,214 words
```

— and then the full prose, with its paragraphs intact. Scroll with the arrow keys or
PageDown and read the whole thing. (If the metadata line says `reviewed` instead of
`draft`, the Editor from Step 5 already passed the chapter — either status is a
perfectly good place to be.) When you're done, press `v` again and the dashboard comes
back exactly as it was.

**2. Now list it from the command line.** Switch to your second terminal from Step 4 —
the same working-directory rule applies: run from the directory you launched
`novelizer` from, or add `--story path/to/stories/the-lighthouse-keeper` to each
command. Ask for the chapter list:

```bash
novelizer chapters
```

You get a small table — one row per chapter, which today means one row:

```text
                     Chapters
┏━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ ID       ┃ Title                       ┃ Status ┃
┡━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ 3f8a12c9 │ The Stranger at Slack Water │ draft  │
└──────────┴─────────────────────────────┴────────┘
```

The Status column is the chapter's editorial state — `draft`, `reviewed`, or `final` —
the same fact the STORY tree encodes as the `◌` / `◐` / `●` dot. Running this while
Mission Control is still open is fine; headless commands and the TUI are designed to
share the story database.

**3. Grab the chapter's full ID.** One catch before you can print the prose: the ID
column above is shortened to its first 8 characters for display, but `novelizer read`
looks chapters up by **exact full ID** — the prefix won't match. The full ID lives in
the story's `world.db`, which is an ordinary SQLite file — inspecting it directly is
fair game, and the `sqlite3` command ships with macOS and nearly every Linux:

```bash
sqlite3 stories/the-lighthouse-keeper/world.db \
  "SELECT id, json_extract(data, '$.title') FROM chapters;"
```

```text
3f8a12c9-51d0-4c8e-9b7a-2f6e8d4a1c53|The Stranger at Slack Water
```

That long UUID is what `read` wants. Copy yours.

**4. Print the prose.**

```bash
novelizer read 3f8a12c9-51d0-4c8e-9b7a-2f6e8d4a1c53
```

The chapter's title prints as a ruled line, followed by the entire prose:

```text
─────────────────────── The Stranger at Slack Water ───────────────────────
The toll bell rang twice before dawn, which meant the tide had come in owing
money. Maren Kald was on the gallery of Graywatch Light before the second
peal faded...
```

(Your prose will be your own, of course — that's the point.) If you see your chapter's
words scrolling past, Step 6 is done — and so is the loop this tutorial promised: one
sentence in, one chapter out, with everything in between visible along the way.

A few notes on reading, for later:

- **To keep a copy**, redirect it: `novelizer read <full-id> > chapter.txt`. The output
  is plain text — the title rule and the prose, nothing else — so it pipes cleanly into
  `less`, `wc -w`, or anything downstream.
- **A wrong or truncated ID** prints `Chapter <id> not found.` in red — but the command
  still exits 0, so scripts should check the output, not the exit code.
- **`read` always shows the latest canon text.** If the Editor sends the chapter back
  and the Author revises it, the revision replaces the prose in place — re-running the
  same `read` command shows the new text (and `chapters` shows the status back at
  `draft` until it's re-reviewed).
- **When you have more chapters**, Mission Control can bind them into a book: open the
  command palette (`Ctrl+K`) and pick **Export EPUB** — it writes an `.epub` into an
  `export/` folder inside the story directory.

Full syntax for `chapters` and `read` — options, ordering, and edge cases — is in the
[CLI reference](../reference/novelizer-cli.md).

## What just happened

You planted one sentence and got back a chapter. Along the way you watched a lot of
moving parts — feed lines, glyphs, countdowns, status dots — and it's worth one pass
back over the session to name what each of those moments actually was. Nothing here is
new work; it's the same six steps, seen from underneath.

**Everything you did became an event in one log.** The story's `world.db` holds a
single append-only table of events, and every moment of this tutorial landed in it:
your seed was a `director_signal.created` event, the Architect's lore lines and the
Keeper's character were events, the chapter arrived as a `chapter.created` event, and
the Editor's verdict was another event on top. Nothing in that log is ever updated or
deleted — the store has append and read operations and simply no others — which is why
the feed in THE ROOM could narrate the whole session as it happened: the feed is just
the log, printed live. It's also why `novelizer seed` could promise durability the
instant it printed its confirmation line, and why copying the story directory backs up
everything: the log *is* the story.

**The seed was a broadcast, not a command.** When your `director_signal.created` event
landed, no agent was told about it and no pipeline kicked off. Instead, the agents that
poll for director input found it on their next look at the log — and for the World
Architect, a pending seed forces its readiness to maximum, which is why world-building
started within a minute of Step 4. That's the general pattern for how you steer the
room: you append facts, and agents change their own behavior because the log changed.

**The room you watched is bigger than the greeting said.** The welcome line introduced
seven agents by name, but the full roster is ten: alongside the Author, Editor,
Architect, Keeper, Continuity Checker, Retconner, and Structure Analyst you saw in the
feed, a Plotter plans chapter briefs ahead of the Author, a Muse deals hands of raw
inspiration, and a Triage agent audits the others' open flags. The striking part is
what they *don't* do: no agent ever calls another or holds a channel to one. When the
Author drafted your chapter, it read the Architect's lore and the Keeper's character
sheets out of the log — the log is the only shared surface, so everything one agent
knows about another's work is something it read from canon. Even when the Editor
sends a chapter back, its notes travel the same way: appended as a signal addressed
to the Author, waiting in the log until the Author's next pass reads it.

**The pacing you watched was scheduling, not thinking.** The countdowns and spinners
on the activity strip were the scheduler at work: it ticks about once a second, keeps
at most two agents running at a time, and among the agents whose per-agent interval
has elapsed, dispatches whichever reports the highest *readiness* — a score each agent
computes for itself by reading the log. That's why the Editor woke up right after the
draft landed (its readiness rises with the number of drafts waiting) and why `idle ·
next: author in 42s` was healthy rather than stuck: an idle room is agents correctly
concluding there's nothing worth an LLM call yet.

**Everything became canon because you trusted the room to commit.** Agents never write
to the log directly — every result goes through a single committer seam, which checks
the autonomy dial you saw reading `AUTONOMY ▮▮▮▮ full_auto` in the status bar. On a
new story that dial says "append everything," so drafts and lore landed as canon the
moment each agent finished. At stricter settings, the same commits become *proposals*
that queue for your explicit approval instead — same agents, same code, different
answer at the gate. That dial (and the `approve`/`reject` verbs that go with it) is
the main control you haven't used yet.

**And the views you read were projections of the log.** The STORY tree, the DETAIL
pane, `novelizer chapters`, and `novelizer read` all query tables like `chapters` —
which are not the truth but a cache of it, derived by replaying the event log: built
from nothing on the story's first boot, then kept current by a projector that applies
each new event as it lands. That's why a revision "replaces" the prose you read while
the original draft still exists forever as an event, and why the SQLite query in
Step 6 worked: you were reading the same derived tables the room itself reads.

That's the whole machine you operated: an append-only log as the single source of
truth, ten agents coordinating through it without ever speaking to each other, a
scheduler deciding who runs, and a gate deciding what counts. The full story — why
it's a room instead of a pipeline, how readiness scores balance the fleet, and what
each autonomy level gates — is in
[How the room works](../explanation/how-the-room-works.md).

## Where to go next

You've run the whole loop once, and the story you made is not a throwaway — "The
Lighthouse Keeper" is a normal Novelizer story that will keep growing as long as you
keep feeding it. Here's what to do with what you've learned, roughly in the order most
people want it.

**Keep directing this story.** The simplest next move is more of what you just did:
leave Mission Control open, plant another seed — a new arrival, a betrayal, a
constraint the prose must honor — and watch the room fold it into the next chapter.
Each `novelizer seed` is one sentence of steering; the room does the rest.

**Take your hands off `full_auto`.** The autonomy dial is the one control this
tutorial deliberately left alone. Try tightening it:

```bash
novelizer autonomy gated_canon
```

From the next commit on, canon-changing work — new lore, characters, chapter drafts —
queues as proposals instead of landing directly: `novelizer proposals` lists them, and
`novelizer approve` / `novelizer reject` are your verdicts. That turns the room from fully trusted to
editor-in-chief-approves, and it's the fastest way to feel what the gate from "What
just happened" actually does. (`novelizer autonomy full_auto` loosens it again —
changing autonomy is never itself gated.)

**Tune the room for your model.** If your local model rambles past proxy timeouts or
fumbles tool calls, the how-to guide [Connect a local LLM](../how-to/connect-a-local-llm.md)
picks up where Step 2 left off: capping generation with `llm_max_tokens`, switching
off tool-calling per agent for models that can't handle it, and per-server notes for
llama.cpp, Ollama, and vLLM.

**Learn where every knob lives.** Everything you set in the wizard — and dozens of
settings you haven't met, from agent cadence intervals to per-agent tool flags — is
documented in the [configuration reference](../reference/configuration.md). The short
version: built-in defaults, then `~/.config/novelizer/config.toml`, then the story's
own `story.toml`, then `NOVELIZER_*` environment variables, each layer overriding the
last. The Settings screen in Mission Control's command palette shows you which layer
is winning for every row.

**Meet the other ten commands.** This tutorial used `seed`, `chapters`, and `read`;
the [CLI reference](../reference/novelizer-cli.md) documents all thirteen subcommands
with their exact syntax, output, and edge cases — including `retcons` (the story's
open contradiction flags, awaiting the Retconner), `voices` and `voice-scaffold` (the prose-style system
Step 3's dropdowns drew from), and the planning verbs (`plan-resolution`,
`plan-reveal`, `retarget`) for steering structure rather than content.

**Understand the machine you're driving.** When you're ready for the full picture —
why Novelizer is a room instead of a pipeline, how readiness scores keep ten agents
from trampling each other, and exactly which event types each autonomy level gates —
read [How the room works](../explanation/how-the-room-works.md). It's the explanation
this tutorial's "What just happened" was the trailer for.

Wherever you go next, the habit to keep is the one this tutorial taught: plant a
seed, watch the room, read the result. Everything else is refinement.
