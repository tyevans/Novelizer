# Connect a local LLM

Novelizer talks to a single OpenAI-compatible chat-completions endpoint — it never calls a
hosted provider directly. This guide walks you through pointing Novelizer at a model server
running on your own machine (llama.cpp, Ollama, vLLM, or anything else that speaks the
OpenAI API), verifying the connection with the built-in setup wizard, and tuning the two
settings that matter most for local models: the per-request generation cap
(`llm_max_tokens`) and the per-agent tool-calling switches (`*_tools_enabled`).

You'll finish with a working `~/.config/novelizer/config.toml` and a room full of agents
generating against your local endpoint. If you're setting up Novelizer for the first time
and just want the fastest path to a running story, the wizard steps here are the same ones
[the first-story tutorial](../tutorial/first-story.md) walks through — come back to Steps
4–6 when a local model misbehaves.

*Verified against: `novelizer/settings/models.py` (`EffectiveSettings` defaults),
`novelizer/tui/setup_wizard.py`, `novelizer/settings/setup_core.py` (`probe_endpoint`),
`novelizer/agents/llm.py` (`build_chat_model` binds `langchain_openai.ChatOpenAI` to
`llm_base_url`).*

## Goal

Get Novelizer generating prose against a model server running on your own machine.
Concretely, by the end of this guide you will have:

- an OpenAI-compatible endpoint (llama.cpp, Ollama, vLLM, or similar) reachable at a
  base URL such as `http://localhost:8080/v1`;
- `llm_base_url` (and, if your server requires one, `llm_api_key`) saved to
  `~/.config/novelizer/config.toml` via the setup wizard;
- a passing connectivity test — the wizard probes `GET {llm_base_url}/models` and lists
  the models your server advertises;
- author, agent, and embedding models selected (or deliberately skipped, saving just the
  endpoint);
- `llm_max_tokens` capped and tool calling disabled for any agents your model can't
  serve reliably.

This is a configuration task: no code changes, and nothing story-specific unless you opt
into per-story overrides in Step 6.

## Prerequisites

Before you start, you need:

- **Novelizer installed and on your `PATH`.** Python 3.13+ is required. From a local
  checkout, either `uv sync` (dev environment — run commands as `uv run novelizer`) or
  `uv tool install .` (puts the `novelizer` command on `PATH`). There is no published
  PyPI package yet, so install from the checkout.
- **A local model server that speaks the OpenAI API** — llama.cpp's `llama-server`,
  Ollama, vLLM, LM Studio, or similar. The server must answer two routes under your
  base URL: `GET {base_url}/models` (the exact request the wizard's connection test
  sends) and `POST {base_url}/chat/completions` (generation). Semantic retrieval also
  needs `POST {base_url}/embeddings` — by default Novelizer embeds against the same
  `llm_base_url` it chats against, using `embed_model`. If your chat endpoint serves no
  embedding models, point `embed_base_url` at one that does instead (see the
  [configuration reference](../reference/configuration.md)).
- **Model weights already downloaded** for that server. Choosing and fetching models is
  your server's concern; this guide only covers wiring Novelizer to it. Step 1 notes
  which servers need the embedding model loaded alongside the chat model.
- **An API key only if your server enforces one.** Local servers typically don't;
  Novelizer sends the placeholder `not-needed` as the bearer token when no key is
  configured.

You do **not** need a cloud provider account — Novelizer never calls a hosted provider
directly.

One state check before you begin: the setup wizard runs automatically the first time
you launch `novelizer` and `~/.config/novelizer/config.toml` does not exist (the path
honors `XDG_CONFIG_HOME`). If that file already exists, the wizard will not rerun —
either edit the keys directly per the
[configuration reference](../reference/configuration.md), or use the in-app Settings
screen, which offers the same live connection test.

## Step 1: Start an OpenAI-compatible server (llama.cpp, Ollama, vLLM — base URL per server)

Start your model server and note its **base URL** — the URL prefix under which the
OpenAI routes live. Novelizer appends the route names itself (`/models` for the
connection test, `/chat/completions` for generation, `/embeddings` for retrieval), so
the base URL you give it must **include the `/v1` path segment** and **nothing after
it**. A trailing slash is fine — Novelizer strips it before building URLs.

| Server | Typical launch | Base URL for Novelizer |
| --- | --- | --- |
| llama.cpp | `llama-server -m model.gguf --port 8080` | `http://localhost:8080/v1` |
| Ollama | `ollama serve` (often already running as a service) | `http://localhost:11434/v1` |
| vLLM | `vllm serve <model-id>` | `http://localhost:8000/v1` |

Notes per server:

- **llama.cpp (`llama-server`)** — serves one model per process. Novelizer's default
  `llm_base_url` is `http://localhost:8080/v1`, which matches `llama-server`'s default
  port, so if this is your server the wizard's pre-filled value in Step 2 already
  points at it. If you also want semantic retrieval, remember that one `llama-server`
  process serves one model — to expose a chat model *and* an embedding model under a
  single base URL you need a router in front (for example `llama-swap`). The simpler
  alternative is to leave `llama-server` for chat and set `embed_base_url` to a second
  endpoint that handles embeddings (a local Ollama, say).
- **Ollama** — serves every pulled model under one base URL and answers all three
  routes Novelizer uses, which makes it the simplest way to get chat and embeddings
  from a single endpoint. Pull your models first (`ollama pull <model>`,
  `ollama pull nomic-embed-text`); the connection test lists whatever `ollama list`
  would show.
- **vLLM** — serves one model per process on port 8000 by default. Chat works out of
  the box; embeddings require a separate embedding-model deployment, so as with
  llama.cpp you need a router if you want retrieval through the same base URL. vLLM's
  reasoning-model output (`reasoning_content` in place of `content`) is handled — see
  [Troubleshooting](#troubleshooting) if you see empty prose from a reasoning model.

Any other server that exposes `GET {base_url}/models` and
`POST {base_url}/chat/completions` (LM Studio, llama-swap, LiteLLM proxy, …) works the
same way — find its base URL in its own docs and carry on to Step 2.

Before leaving this step, confirm the server answers the exact request Novelizer's
connection test will send:

```console
$ curl http://localhost:8080/v1/models
{"object":"list","data":[{"id":"my-chat-model", ...}]}
```

A `200` with a JSON `data` list means Step 2's **Test connection** will pass. The `id`
values in that list are exactly the choices the wizard will offer for author, agent,
and embedding models in Step 3 — if the model you want isn't listed here, load it into
the server before continuing.

## Step 2: Point the setup wizard at the endpoint and test the connection

With the server running, launch Novelizer:

```console
$ novelizer
```

If `~/.config/novelizer/config.toml` does not exist yet, the first-run setup wizard
("Novelizer — First-run setup") opens automatically before anything else. If the file
already exists the wizard is skipped — use the in-app Settings screen (which has the
same live connection test) or edit the file directly, then jump to
[Verify the connection](#verify-the-connection).

In the wizard:

1. **LLM base URL** — the field is pre-filled with `http://localhost:8080/v1`
   (llama.cpp's default). Replace it with the base URL you noted in Step 1, e.g.
   `http://localhost:11434/v1` for Ollama or `http://localhost:8000/v1` for vLLM.
2. **API key** — leave blank for a local server that doesn't enforce auth; Novelizer
   sends the placeholder bearer token `not-needed` on your behalf. Only fill this in
   if your server (or a proxy in front of it) requires a real key.
3. **Stories directory** — where story folders are created; the default `stories` is
   fine for now and has nothing to do with the connection.
4. Press **Test connection**.

The test sends `GET {base_url}/models` with a 5-second timeout and reports the result
inline, without leaving the wizard:

- **`✓ connected — models: …`** — the probe got a `200` and lists every model `id`
  the server advertised. The three model dropdowns below (author, agent, embedding)
  unlock and are populated with exactly this list, and **Save & continue** becomes
  available. Carry on to Step 3.
- **`✓ connected — models: (none reported)`** — the endpoint answered but advertised
  no models. The dropdowns stay disabled and **Save & continue** stays locked; load a
  model into your server (or fix the base URL) and press **Test connection** again,
  or use **Skip model picks — save endpoint only** to save just the endpoint and rely
  on Novelizer's built-in default model names.
- **`✗ …`** — the probe failed. The message is the raw cause: `HTTP 404 from
  http://localhost:8080/v1/models` usually means the base URL is missing its `/v1`
  segment; a connection-refused error means the server isn't listening on that host
  and port. Nothing is saved on failure — fix the URL or the server and test again.
  See [Troubleshooting](#troubleshooting) for more failure signatures.

You can press **Test connection** as many times as you like; each press re-probes and
repopulates the dropdowns from the latest response. Nothing is written to disk until
you choose **Save & continue** or **Skip model picks — save endpoint only** — both are
covered in Step 3. (Pressing `q` quits without saving; the wizard will run again next
launch.)

## Step 3: Pick author, agent, and embedding models (or skip and save endpoint only)

A passing connection test unlocks three dropdowns. All three are populated with the
same list — every model `id` your server advertised in Step 2 — and each is
preselected to the first entry, so review all three before saving. The three picks
map to three settings keys with distinct jobs:

- **author model** (`author_model`) — the model the Author agent writes prose with,
  and the model behind the Author chat persona. Prose quality lives here; give it
  the strongest writer your hardware can serve.
- **agent model** (`agent_model`) — the model every other agent in the room runs on:
  Continuity Checker, World Architect, Character Keeper, Editor, Retconner,
  Structure Analyst, Plotter, triage, the research subagents, and every non-Author
  chat persona. These calls are frequent and analytical rather than literary, so a
  smaller, faster model is a reasonable choice. (See
  [how the room works](../explanation/how-the-room-works.md) for who these agents
  are.)
- **embedding model** (`embed_model`) — used for semantic retrieval via
  `POST {embedding endpoint}/embeddings`. This must be a genuine embedding model (for
  example `nomic-embed-text` on Ollama). The wizard does not filter the list — chat
  models appear in this dropdown too — so it's on you to pick one that actually
  answers the embeddings route.

If your endpoint's `/models` list contains no embedding model at all, it can't do this
job. That's expected of hosted chat routers — **OpenRouter serves no embedding models
whatsoever** — so use the wizard's separate **Embedding base URL** field (written as
`embed_base_url`) to name a provider that does, press **Test embedding connection**, and
pick from *its* list. A local `ollama pull nomic-embed-text` at
`http://localhost:11434/v1` is the cheapest option; OpenAI's `text-embedding-3-small`
also works. Any key you enter there goes to `embed_api_key` and is used only for that
endpoint — your chat key is never forwarded to it.

Picking the same model for author and agent is fine. If a model you want isn't
offered, it wasn't in the server's `/models` response — go back to Step 1, load it,
and re-test.

Then take one of the two exits:

- **Save & continue** — writes `llm_base_url`, the stories directory (pre-filled
  `stories`, so it's saved unless you clear the field), your API key if you entered
  one, and the three model picks to `~/.config/novelizer/config.toml`, then
  continues into the app. Any dropdown you cleared to blank is simply omitted from
  the file and that key falls back to its built-in default — defaults and full key
  semantics live in the [configuration reference](../reference/configuration.md).
- **Skip model picks — save endpoint only** — saves the endpoint (plus stories
  directory and API key, as above) with **no** model keys at all; all three model
  settings fall back to their built-in defaults. This is the right move when your
  server serves a single model and ignores the model name in requests — a lone
  `llama-server` process behaves this way — or when the endpoint reported no models
  in Step 2. Servers that route by model name (Ollama, vLLM) will reject requests
  for a default name they don't serve, so with those either pick models here or set
  `author_model` / `agent_model` / `embed_model` yourself later per the
  [configuration reference](../reference/configuration.md).

Either exit writes the same file, so you can confirm what was saved:

```console
$ cat ~/.config/novelizer/config.toml
llm_base_url = "http://localhost:11434/v1"
default_stories_dir = "stories"
author_model = "qwen3:32b"
agent_model = "llama3.1:8b"
embed_model = "nomic-embed-text"
```

Nothing here is final. The in-app Settings screen edits the same keys with the same
live connection test, editing the file directly works too, and Step 6 covers
overriding models per story or via `NOVELIZER_*` environment variables.

## Step 4: Cap generation with llm_max_tokens to avoid proxy-timeout hangs

`llm_max_tokens` is the per-request generation cap. It is passed as `max_tokens` on
every model request Novelizer's runners make — Author prose drafts, every analytical
agent, chat, triage, research subagents, and knowledge-graph extraction all share the
one cap (the only exception is the Engine Room's internal tool-call summarizer, which
pins its own tiny cap). The default is `4096`, and the setup wizard never asks about
it, so after Steps 2–3 you are already running with that value.

Why this cap exists: an uncapped local model — especially one with server-side
reasoning enabled, where the thinking tokens count as generated output too — can
keep generating past the request timeout of whatever sits between Novelizer and the
model (a reverse proxy, `llama-swap`, a LiteLLM gateway, or the server's own request
timeout). The connection dies at the proxy, no response ever comes back, and in the
app it looks like a hang: an agent pane stuck mid-run forever. Capping generation
bounds the wall-clock time of every request below that timeout, so requests finish
or fail visibly instead of never returning.

**Size the cap from your setup**, in two directions:

- **Upper bound — your slowest timeout.** Estimate your server's generation speed
  (tokens/second, from its own logs or a quick manual request) and multiply by the
  shortest request timeout in the chain. At ~20 tok/s behind a proxy that cuts
  requests at 120 s, anything above ~2400 tokens can hang; set the cap comfortably
  below that.
- **Lower bound — don't truncate the work.** The same cap limits the Author's prose
  and the agents' structured JSON output. Set it too low and chapters stop
  mid-sentence and agent responses truncate mid-JSON (Novelizer chunks its
  fact-dense extraction calls to reduce this risk, but a very small cap can still
  bite). Below ~2048 you are trading hangs for truncation; the `4096` default is a
  reasonable balance for most local setups.

To change it, add the key to `~/.config/novelizer/config.toml`:

```toml
llm_base_url = "http://localhost:11434/v1"
llm_max_tokens = 2048
```

or edit it on the in-app Settings screen. Either way the change takes effect on the
next launch — `llm_max_tokens` is one of the keys the Settings screen flags as
**restart required**, because running agents hold the already-built model client.

Two scope rules to know (details in the
[configuration reference](../reference/configuration.md)):

- `llm_max_tokens` is **global-only**: it belongs in the global config or the
  environment, and is ignored with a logged warning if you put it in a story's
  `story.toml`. Every story on this endpoint shares the cap.
- `NOVELIZER_LLM_MAX_TOKENS` overrides the file for one session — handy for testing
  a value before committing it to the config (see Step 6).

If reasoning output keeps eating the whole cap (visible thinking but empty or
truncated prose), prefer disabling or shortening reasoning on the **server** side
over raising the cap past your timeout budget — see
[Troubleshooting](#troubleshooting).

## Step 5: Disable tools for weak tool-callers (per-agent *_tools_enabled flags)

By default every agent in the room is **tooled**: its runner is built with a
read-only canon filesystem (canon entries, `/outline/`, `/skills/`, a scratch
`/workspace/`) plus a `search_canon` semantic-search tool, and the prose-heavy
agents run in **pull mode** — the prompt hands them a compact chapter index and
trusts them to go read what they need. That division of labor assumes the model can
actually drive tools. Many local models can't, or can't reliably. The telltale
symptoms: an agent alarms with `GraphRecursionError: Recursion limit of 200
reached`, its Engine Room pane shows canon reads looping without ever producing an
answer, or runs die on malformed-output errors because the model mangled the
tool-call JSON.

The escape hatch is per-agent: each agent has its own `*_tools_enabled` flag, all
defaulting to `true`. Turning one off builds that agent's runner without the canon
toolkit, and for the Author, Continuity Checker, Character Keeper, Structure
Analyst, and chat personas it also flips context assembly to **push mode** — the
context they need is pasted into the prompt, so the model can answer in one shot
with no tool round-trips (push mode was Novelizer's original behavior, so it is a
safe, fully supported fallback). The other four flags gate only the toolkit. What
you give up is depth: a push-mode agent sees bounded slices of canon (the Character
Keeper, for example, reads whole chapters in pull mode but only a fixed head-slice
of each recent chapter in push mode), and no untooled agent can search canon
semantically mid-task.

**Match the flags to your models.** The flags map onto the Step 3 split between
`author_model` and `agent_model` (see
[how the room works](../explanation/how-the-room-works.md) for the full fleet and
what each agent does):

- `author_tools_enabled` — the Author, running on `author_model`.
- `checker_tools_enabled`, `world_architect_tools_enabled`,
  `character_keeper_tools_enabled`, `editor_tools_enabled`,
  `retconner_tools_enabled`, `structure_analyst_tools_enabled`,
  `plotter_tools_enabled` — the analytical fleet, all running on `agent_model`.
- `chat_tools_enabled` — one flag for all chat personas (the Author persona chats
  on `author_model`, the rest on `agent_model`).

(`triage_tools_enabled` exists but is fixed at its default — no configuration layer
accepts it. The authoritative flag list, defaults, and per-flag semantics are in the
[configuration reference](../reference/configuration.md).)

Since the analytical fleet shares `agent_model`, a weak tool-caller there usually
means turning off the whole `agent_model` group while a stronger `author_model`
keeps its tools — or vice versa. Disable selectively, starting with the agents you
actually see failing, rather than everything at once.

Add the flags to `~/.config/novelizer/config.toml`:

```toml
# agent_model is a small model that fumbles tool calls; author_model is fine.
checker_tools_enabled = false
world_architect_tools_enabled = false
character_keeper_tools_enabled = false
editor_tools_enabled = false
retconner_tools_enabled = false
structure_analyst_tools_enabled = false
plotter_tools_enabled = false
chat_tools_enabled = false
```

The in-app Settings screen edits the same keys, but mind the scope: these flags are
story-overridable, so a Settings-screen edit writes to the open story's
`story.toml`, not the global file — handy for experiments, but put the flags in the
global config when every story on this endpoint should inherit them.

The flags are read once, when the room's agent runners are built at startup, so
restart Novelizer after changing them — a running room keeps the tooling it
launched with. Note the Settings screen does **not** mark these rows "restart
required" (that badge is reserved for the endpoint and model keys from Steps 2–4),
so don't wait for a prompt: flip the flag, then restart.

Three related notes:

- These flags cannot make requests tool-free. Even an untooled runner carries the
  agent framework's small built-in scratch toolkit (a todo list and an in-memory
  workspace), so every agent request includes a `tools` array. If your server
  rejects tool-bearing requests outright, no flag combination will fix it — use a
  server or proxy that tolerates tool definitions (it need not execute them well;
  push mode removes the need to *use* them).
- The per-agent `*_subagent_enabled` flags (researcher delegation) only have an
  effect when the matching `*_tools_enabled` flag is on — a subagent with no tools
  to read canon with is deliberately not built. They default to `false`, so there
  is nothing to turn off here; see the
  [configuration reference](../reference/configuration.md).
- Every flag is also settable per story or via a `NOVELIZER_*` environment variable
  (for example `NOVELIZER_CHECKER_TOOLS_ENABLED=false` to trial a change without
  editing the file) — that's Step 6.

## Step 6 (optional): Override per story or via NOVELIZER_* environment variables

Everything so far went into the global config, which applies to every story. Two more
layers sit on top of it, and both are useful with local models. Settings merge in
strict precedence order — built-in defaults, then the global config, then the story's
`story.toml`, then `NOVELIZER_*` environment variables, highest last — with each key
resolved independently: set a key in a higher layer and it wins; leave it unset and
the layer below shows through. The full rules live in the
[configuration reference](../reference/configuration.md#configuration-layers-and-precedence).

**Per story: `story.toml`.** Each story directory holds a `story.toml`, and any
*story-overridable* key you add there beats the global config for that story only.
This is how you run one story on a bigger author model, or drop a single
tool-fumbling agent to push mode for the one story that trips it, without touching
your other stories:

```toml
# stories/space-opera/story.toml
title = "Space Opera"
author_model = "qwen3:32b"          # this story gets the big writer
checker_tools_enabled = false        # and a push-mode Continuity Checker
```

The model picks (`author_model`, `agent_model`, `embed_model`), temperatures, and all
the Step 5 tool/subagent flags are story-overridable. The endpoint settings are
deliberately not: `llm_base_url`, `llm_max_tokens`, and `embed_base_url` are
global-only and are **ignored with a logged warning** if you put them in a
`story.toml`, while `llm_api_key` and `embed_api_key` there are a hard error
(`StoryConfigError`) — story directories are shareable and must
never carry secrets. The authoritative per-key scope table is in the
[configuration reference](../reference/configuration.md#key-scoping-story-overridable-global-only-and-forbidden-keys).

You rarely need to edit the file by hand: with a story open, the in-app Settings
screen writes story-overridable keys into that story's `story.toml` (submitting an
empty value removes the key, so the story inherits globally again), and both TOML
files are watched while the app runs, so external edits apply live too.

**Per session: `NOVELIZER_*` environment variables.** Every key an env layer accepts
maps to `NOVELIZER_` plus the key name uppercased — `NOVELIZER_AUTHOR_MODEL`,
`NOVELIZER_LLM_MAX_TOKENS`, `NOVELIZER_CHECKER_TOOLS_ENABLED`, and so on. The
environment beats every file, including `story.toml`, which makes it the right tool
for trialing a value before committing it anywhere:

```console
$ NOVELIZER_LLM_MAX_TOKENS=2048 NOVELIZER_AGENT_MODEL="llama3.1:8b" novelizer
```

Nothing is written to disk; unset the variable and the files are back in charge. A
`.env` file in the directory you launch from is also picked up, using the same
`NOVELIZER_` names. Three behaviors worth knowing before you rely on this:

- While a variable is set, its row on the Settings screen shows source `env` and is
  **read-only** — clear the variable to edit the key from the app again.
- Misspelled `NOVELIZER_*` variables are ignored **silently** (no warning, unlike
  unknown keys in the TOML files), so a typo just quietly does nothing — check the
  Settings screen's source column if an override doesn't seem to take.
- The restart rules from Steps 4–5 still apply regardless of layer: the endpoint,
  key, cap, and model settings (`llm_base_url`, `llm_api_key`, `llm_max_tokens`,
  `author_model`, `agent_model`, `embed_model`, `embed_base_url`, `embed_api_key`)
  only take effect on the next launch,
  and the Step 5 tool/subagent flags likewise don't re-tool a running room — a live
  reload reports them applied, but agent tooling stays as it was built at startup,
  so restart after changing them too, wherever you set them. Other keys apply live
  per the rules in the
  [configuration reference](../reference/configuration.md#settings-that-require-a-restart-restart_required_keys).

## Verify the connection

The wizard's connection test proves exactly one of the three routes Novelizer uses —
`GET {base_url}/models`. A setup can pass that probe and still fail in the room: a
model name the server doesn't route, a chat model in the embedding slot, or an
environment override pointing somewhere else entirely. Run these four checks in
order; each proves strictly more than the last, and each names the evidence to look
for.

**1. Re-probe with the settings Novelizer is actually running on.** With a story
open, press `ctrl+k` for the command palette, run **settings**, then press `t`
(**Test connection**). This is the same `GET {base_url}/models` probe as the
wizard's button, with one difference that matters: it probes the base URL and API
key **the running app resolved at launch** — the global config merged with any
`NOVELIZER_LLM_BASE_URL` / `NOVELIZER_LLM_API_KEY` environment override (the
endpoint keys are global-only, so no story can move them), held pinned since both
are restart-required. That catches the two failure shapes the first-run test never
sees: a stale env override pointing somewhere else entirely, and a config-file edit
made after launch that hasn't taken effect yet. Expect the same
`✓ connected — models: …` result; a `✗ …` here means the endpoint the app is
actually using is wrong, whatever the config file says. The settings table above
the message shows every key's current value and the layer it came from (the
**Source** column), so the culprit is on screen.

**2. Prove the generation and embeddings routes answer for your model names.** The
probe never touches `/chat/completions` or `/embeddings`, and servers that route by
model name (Ollama, vLLM) accept or reject each request by the name it carries. Send
the two requests Novelizer will send, with your configured names:

```console
$ curl -s http://localhost:11434/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{"model": "qwen3:32b", "max_tokens": 16,
         "messages": [{"role": "user", "content": "Say ready."}]}'
```

A JSON body with a `choices` list means generation works for that model name — repeat
with your `agent_model` if it differs from `author_model`. Then embeddings, with your
`embed_model`:

```console
$ curl -s http://localhost:11434/v1/embeddings \
    -H 'Content-Type: application/json' \
    -d '{"model": "nomic-embed-text", "input": "probe"}'
```

A response whose `data[0].embedding` is a list of floats means retrieval will work.
An error body on the first request will break every agent in the room; an error on
the second breaks only indexing — and does so *silently*, which is why check 4
exists.

**3. Watch one real run end to end.** From a second terminal, plant a seed. It
targets your last-opened story; to name one explicitly, the `--story` flag goes
**before** the subcommand (`novelizer --story stories/my-story seed "…"`):

```console
$ novelizer seed "A lighthouse keeper who taxes the tide."
```

Back in the app, press `e` to open the **Engine Room**: each agent pane shows a live
token stream and vitals while that agent runs. Tokens streaming in an agent pane are
the proof that `POST /chat/completions` works under the app's real settings, cap, and
tool flags — not just under curl. Failures are equally visible: every failed agent
run posts one alarm line to THE ROOM feed in the form
`⚠ scheduler error: author: <cause>`, and repeats it once per failed run, so a
connection that dies under load (Step 4) shows up here even after a passing curl.
Within a few agent cycles a drafted chapter should exist — confirm from the second
terminal:

```console
$ novelizer chapters
```

and read the prose in-app with the Reading view (`v`).

**4. Confirm embedding indexing is actually running.** A quiet feed is not proof
here: the canon indexer deliberately never alarms — on an embed failure it logs a
warning, stops at the failing record, and retries on the next cycle, forever. Check
the log file (all Novelizer logging goes to one rotating file, since the TUI owns
the terminal):

```console
$ grep "canon indexing stopped" ~/.config/novelizer/logs/novelizer.log
```

No matches — plus a growing `embeddings/` directory next to the story's `world.db` —
means indexing is healthy. Repeated `canon indexing stopped at seq N (…); will retry`
lines name the exception and the record it stalled on; the usual causes are an
`embed_model` that isn't an embedding model or an endpoint that doesn't serve
`POST {base_url}/embeddings` at all (Step 1's per-server notes) — in which case set
`embed_base_url` to one that does. That log file is also the
first place to look when any of the checks above fails without an obvious on-screen
cause.

If a check fails and the fix isn't obvious from the evidence, the failure signatures
are catalogued in [Troubleshooting](#troubleshooting).

## Troubleshooting

Symptoms first, then cause and fix. Two places surface almost every failure: alarm
lines in THE ROOM feed (`⚠ scheduler error: <agent>: <cause>`, repeated once per
failed run, with a `!` mark on that agent in the status strip), and the log file at
`~/.config/novelizer/logs/novelizer.log` — the TUI owns the terminal, so nothing is
ever printed to stderr. When in doubt, read the log.

**`✗ HTTP 404 from http://…/models` on Test connection.** The base URL is almost
always missing its `/v1` segment (or has extra path after it) — the probe appends
`/models` to exactly what you typed. Use the per-server base URLs from Step 1, e.g.
`http://localhost:11434/v1`, not `http://localhost:11434`.

**`✗ …` with a connection-refused or timeout message on Test connection.** The
server isn't listening at that host and port — it isn't running, is on a different
port, or is bound to another interface. The probe gives up after 5 seconds. Confirm
with `curl {base_url}/models` from the same machine Novelizer runs on. If the
wizard's test passed but the in-app Settings test fails, remember the Settings test
probes the endpoint the running app resolved **at launch** — a
`NOVELIZER_LLM_BASE_URL` environment variable or `.env` file may be overriding your
config file (check the **Source** column in the settings table), and a config edit
made after launch isn't probed until you restart.

**Connection test passes, but every agent alarms with an HTTP 404 or model-not-found
error.** Servers that route by model name (Ollama, vLLM) reject requests for names
they don't serve. This is the classic aftermath of **Skip model picks** in Step 3:
the built-in defaults (`local-model` for chat, `nomic-embed-text` for embeddings)
were sent as literal model names. Set `author_model`, `agent_model`, and
`embed_model` to names your server's `/models` list actually contains — key
semantics in the [configuration reference](../reference/configuration.md).

**Every agent fails immediately with an HTTP 400 mentioning `tools` or
`tool_choice`.** The server rejects requests that carry a `tools` array — and no
Novelizer request is ever tool-free. Even with every Step 5 flag off, the agent
framework's built-in scratch toolkit (a todo list and an in-memory workspace)
still rides along in every request, so no flag combination fixes this. The fix is
on the server side: use a server or proxy that tolerates tool definitions in the
request. It doesn't have to *call* them well — that's what the Step 5 flags are
for — it just has to accept them.

**An agent alarms with `GraphRecursionError: Recursion limit of 200 reached`, or
keeps making tool calls without ever finishing.** The model is a weak tool-caller:
it loops on canon reads instead of producing its answer, until the graph's
200-step recursion limit cuts the run. Disable that agent's `*_tools_enabled` flag
(Step 5) and restart. Since the analytical fleet shares `agent_model`, expect to
disable the whole group if one of them loops.

**An agent pane sits mid-run forever — tokens stopped, no alarm, no error.** The
signature of a proxy or server timeout killing the request after generation exceeded
the timeout budget: the connection dies upstream and no response ever returns. Lower
`llm_max_tokens` below your slowest timeout as sized in Step 4 (trial it with
`NOVELIZER_LLM_MAX_TOKENS` before committing it to the config), and restart — the
cap is a restart-required setting. If the model is a reasoning model, also see the
next entry: thinking tokens count toward the same wall-clock budget.

**Thinking streams in the Engine Room, but the prose is empty or truncated.** A
reasoning model (vLLM's `reasoning_content`, or the `reasoning` key some proxies
use) is spending the `llm_max_tokens` budget thinking — Novelizer surfaces those
deltas as the dim italic "thinking" stream in the agent's pane, but they count as
generated output on the server. Prefer disabling or shortening reasoning **on the
server** (llama.cpp, Ollama, and vLLM each have their own switch) over raising the
cap past your timeout budget.

**Chapters end mid-sentence, or agents alarm with JSON/validation errors.** The
opposite failure: `llm_max_tokens` is too low and output is being cut off — prose
stops abruptly, and agents' structured responses truncate mid-JSON and fail to
parse. Raise the cap (the `4096` default is a sane floor; below ~2048 expect
trouble) and restart.

**The feed shows `RateLimitError: Too many requests` bursts.** The server is
saturated — several agents, chat, embeddings, and the tool-call summarizer all
share one endpoint, and llama.cpp-style servers answer the overflow with 429s.
This is expected under load and self-healing: each request waits out the
saturation with up to `LLM_MAX_RETRIES` (10) exponential-backoff retries before
the run gives up, and an agent whose run does die from a rate limit steps back
three intervals (`RATE_LIMIT_BACKOFF_MULTIPLIER` in `agent_kit`) before rejoining,
so the fleet drains the queue instead of hammering it. If the errors are constant
rather than bursty, the fleet is oversized for the server — lower
`max_concurrent_agents`, or raise the server's parallel-request capacity.

**Prose works, but semantic search finds nothing and nothing ever alarms.** Broken
embeddings are silent by design: the indexer logs a warning, halts at the failing
record, and retries every cycle instead of alarming. Run
`grep "canon indexing stopped" ~/.config/novelizer/logs/novelizer.log` — the line
names the exception. The usual causes are an `embed_model` that isn't an embedding
model (the wizard's dropdown doesn't filter chat models out) or a server that
doesn't answer `POST {base_url}/embeddings` at all (llama.cpp and vLLM need a router
for this, and OpenRouter serves no embedding models whatsoever — Step 1). The fix for
the latter is `embed_base_url`, pointing embeddings at their own endpoint. Fix the model or server and indexing resumes
from the stalled record on its own; nothing is lost.

**A settings change doesn't take effect.** Four causes, in the order to check:

1. **Restart-required key** — the endpoint, key, cap, and model settings only apply
   on the next launch, and the Step 5 tool flags likewise don't re-tool a running
   room. Restart.
2. **Environment variable shadowing the file** — env beats every file. The Settings
   screen shows each key's source; `env` rows are read-only until you unset the
   variable. Don't forget a `.env` file in the launch directory.
3. **Misspelled `NOVELIZER_*` variable** — ignored silently, no warning. Verify the
   spelling against the [configuration reference](../reference/configuration.md).
4. **Key in the wrong layer** — `llm_base_url`, `llm_max_tokens`, and
   `embed_base_url` in a `story.toml` are ignored with a logged warning;
   `llm_api_key` and `embed_api_key` there are a hard error.

**The setup wizard won't reopen.** It only runs when
`~/.config/novelizer/config.toml` doesn't exist. Edit the file, use the in-app
Settings screen (same live connection test), or delete/rename the file to force
the wizard on next launch.

## Related documentation

- [Tutorial: your first story](../tutorial/first-story.md) — the guided first session.
  Its Step 2 walks the same first-run setup wizard this guide covers; start there if
  you've never run Novelizer at all, and come back here when a local model needs
  tuning.
- [Configuration reference](../reference/configuration.md) — the authoritative list of
  every setting this guide touches (`llm_base_url`, `llm_api_key`, `llm_max_tokens`,
  `author_model`, `agent_model`, `embed_model`, the `*_tools_enabled` flags), with
  defaults, per-key scope, layer precedence, `NOVELIZER_*` names, and which keys
  require a restart.
- [Example global config](../examples/config.example.toml) — an annotated
  `~/.config/novelizer/config.toml` starting point, ready to copy and edit if you'd
  rather skip the wizard entirely. It covers the endpoint, model, temperature, and
  cadence keys; the Step 4–5 keys (`llm_max_tokens`, the `*_tools_enabled` flags)
  are not in it — add those per the configuration reference above.
- [novelizer CLI reference](../reference/novelizer-cli.md) — every subcommand and
  flag, including the `novelizer seed` and `novelizer chapters` commands used in
  [Verify the connection](#verify-the-connection) and `--story` resolution.
- [How the room works](../explanation/how-the-room-works.md) — what the agents behind
  `author_model` and `agent_model` actually do, and why; useful background for
  deciding which agents to drop to push mode in Step 5.
