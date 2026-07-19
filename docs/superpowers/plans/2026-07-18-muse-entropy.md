# Muse Entropy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Break LLM naming/beat convergence (the "Elias Thorne" problem) by injecting PRNG draws from bundled real-world corpora into agent prompts as event-sourced "hands," with uptake tracking.

**Architecture:** A new pure-Python `novelizer/muse/` bounded context (corpora + seeded draw engine) and a no-LLM `Muse` agent that keeps one unconsumed hand ahead of the Author, committed as `inspiration.*` events. The Author renders a binding casting pool + optional inspiration blocks; CharacterKeeper records name uptake at mint time; the ContinuityChecker mining pass records profession/setting/beat uptake; a `:muse` director command shows status and rerolls.

**Tech Stack:** Python ≥3.13, pydantic v2, aiosqlite, hypothesis (already a dependency), tomllib (stdlib). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-18-muse-entropy-design.md`

## Global Constraints

- Python ≥3.13; run tests with `uv run pytest` (asyncio_mode=auto — async tests need no decorator).
- Event sourcing: the log is the truth. Projections must be rebuildable (`Projector._reset_state` + `catch_up` reproduces identical rows). Every new table goes into `_reset_state`.
- First-mint-wins: a second `inspiration.drawn` for an existing hand id is a projection no-op, same as `thread.planted`.
- All agent writes go through `self._committer.commit(agent_name, event_type, aggregate_id, payload)` — never `events.append` directly from an agent.
- `novelizer/muse/` makes no network calls and no LLM calls, ever.
- Determinism: `deal_hand` must be a pure function of `(corpora, seed, era, exclude, hand_id)`. Seeds come from `secrets.randbits(63)` at deal time and are recorded in the event.
- Never introduce names/tropes from the AI-tell ban list (Elias, Elara, Mara, Thorne, lighthouse keeper, clockmaker, etc.) into corpora, tests, or fixtures.
- The test suite is zero-warning. Match existing test style: `FakeRunner` classes, `stack` fixtures with tempfile DBs (see `tests/agents/test_author.py`).

---

### Task 1: Corpora data files + loader

**Files:**
- Create: `novelizer/muse/__init__.py`
- Create: `novelizer/muse/corpus.py`
- Create: `novelizer/muse/data/__init__.py`
- Create: `novelizer/muse/data/given_names.toml`
- Create: `novelizer/muse/data/surnames.toml`
- Create: `novelizer/muse/data/professions.toml`
- Create: `novelizer/muse/data/settings.toml`
- Create: `novelizer/muse/data/beats.toml`
- Modify: `docs/superpowers/specs/2026-07-18-muse-entropy-design.md` (weights → curation wording)
- Test: `tests/muse/__init__.py`, `tests/muse/test_corpus.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces: `load_corpora() -> Corpora`; `Corpora` (pydantic: `version: str`, `given_names: dict[str, list[str]]` era-bucket → names, `surnames: list[str]`, `professions: list[str]`, `settings: list[str]`, `beats: list[str]`); `CorpusError(RuntimeError)`. Era bucket keys: `"victorian"`, `"interwar"`, `"midcentury"`, `"late20th"`, `"modern"`.

Design note: the spec said "frequency weights"; we implement **curated mid-frequency lists sampled uniformly** instead — the curation (real SSA/census names, top-10 megahits and AI-tells excluded) does the weighting's job with zero runtime machinery. Step 7 amends the spec to match.

- [ ] **Step 1: Write the failing tests**

```python
# tests/muse/__init__.py  (empty file)
```

```python
# tests/muse/test_corpus.py
from novelizer.muse.corpus import Corpora, CorpusError, load_corpora

# Words the Cornell study found in 88% of AI stories. Corpora must never
# contain them — they are the exact convergence this feature breaks.
AI_TELLS = {
    "elias", "elara", "mara", "thorne", "lighthouse", "keeper", "baker",
    "mayor", "clockmaker", "fisherman", "librarian", "conductor",
}


def test_load_corpora_returns_populated_buckets():
    corpora = load_corpora()
    assert corpora.version
    assert set(corpora.given_names) == {"victorian", "interwar", "midcentury", "late20th", "modern"}
    for bucket, names in corpora.given_names.items():
        assert len(names) >= 30, f"era bucket {bucket} too thin"
    assert len(corpora.surnames) >= 60
    assert len(corpora.professions) >= 40
    assert len(corpora.settings) >= 35
    assert len(corpora.beats) >= 35


def test_no_ai_tells_in_any_corpus():
    corpora = load_corpora()
    everything = (
        [n for names in corpora.given_names.values() for n in names]
        + corpora.surnames + corpora.professions + corpora.settings + corpora.beats
    )
    for entry in everything:
        for word in entry.lower().replace("-", " ").split():
            assert word not in AI_TELLS, f"AI-tell {word!r} found in corpus entry {entry!r}"


def test_no_duplicates_within_a_corpus():
    corpora = load_corpora()
    for label, entries in (
        ("surnames", corpora.surnames), ("professions", corpora.professions),
        ("settings", corpora.settings), ("beats", corpora.beats),
    ):
        assert len(entries) == len(set(entries)), f"duplicate in {label}"
    for bucket, names in corpora.given_names.items():
        assert len(names) == len(set(names)), f"duplicate in given_names[{bucket}]"


def test_missing_file_raises_corpus_error(monkeypatch):
    import novelizer.muse.corpus as corpus_mod
    monkeypatch.setattr(corpus_mod, "_DATA_PACKAGE", "novelizer.muse")  # package exists, files don't
    try:
        load_corpora()
    except CorpusError:
        pass
    else:
        raise AssertionError("expected CorpusError")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/muse/test_corpus.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.muse'`

- [ ] **Step 3: Create the data files**

`novelizer/muse/__init__.py` and `novelizer/muse/data/__init__.py` are empty files.

```toml
# novelizer/muse/data/given_names.toml
version = "2026.07"

victorian = [
  "Alma", "Augusta", "Bertha", "Chester", "Cordelia", "Dewey", "Edna", "Ephraim",
  "Florence", "Gideon", "Harriet", "Hollis", "Ida", "Jasper", "Lavinia", "Lemuel",
  "Mabel", "Millard", "Myrtle", "Nellie", "Obadiah", "Opal", "Orville", "Pearl",
  "Rufus", "Selma", "Sylvester", "Thaddeus", "Ursula", "Vernon", "Wilhelmina", "Zeb",
]
interwar = [
  "Bernice", "Clarence", "Delbert", "Doris", "Earl", "Eunice", "Ferdinand", "Gladys",
  "Harold", "Hazel", "Homer", "Inez", "Irving", "June", "Lester", "Lois",
  "Marion", "Maxine", "Norma", "Orval", "Phyllis", "Ralph", "Rosalie", "Roscoe",
  "Sheldon", "Thelma", "Velma", "Wallace", "Wilma", "Woodrow", "Yetta", "Opaline",
]
midcentury = [
  "Arlene", "Bruce", "Carol", "Dale", "Dennis", "Diane", "Donna", "Duane",
  "Gary", "Gerald", "Glenn", "Janice", "Jerome", "Joan", "Judith", "Kenneth",
  "Larry", "Linda", "Marcia", "Marvin", "Norman", "Pamela", "Randall", "Rita",
  "Roger", "Roland", "Sandra", "Sharon", "Terrence", "Wanda", "Wayne", "Yvonne",
]
late20th = [
  "Amber", "Brandon", "Chad", "Courtney", "Craig", "Crystal", "Damon", "Dana",
  "Derek", "Erica", "Heather", "Jamal", "Jill", "Keith", "Kelly", "Kristen",
  "Lance", "Latoya", "Melinda", "Misty", "Monique", "Regina", "Scott", "Shanna",
  "Stacy", "Tamika", "Todd", "Tonya", "Travis", "Trevor", "Whitney", "Dwayne",
]
modern = [
  "Aiden", "Alexis", "Brayden", "Brielle", "Camila", "Dakota", "Delaney", "Ezekiel",
  "Genesis", "Harper", "Hudson", "Itzel", "Jaylen", "Kai", "Kinsley", "Landon",
  "Leilani", "Malia", "Mateo", "Nevaeh", "Nolan", "Paisley", "Peyton", "Rowan",
  "Santiago", "Savannah", "Sienna", "Tatum", "Xavier", "Yaretzi", "Zion", "Amaya",
]
```

```toml
# novelizer/muse/data/surnames.toml
version = "2026.07"
entries = [
  "Abernathy", "Acevedo", "Ashworth", "Barlow", "Bautista", "Beckham", "Bellamy", "Boone",
  "Bowden", "Burgos", "Calloway", "Carmichael", "Castellanos", "Chowdhury", "Cisneros", "Coble",
  "Crenshaw", "Delacruz", "Dinh", "Dombrowski", "Eastman", "Escobedo", "Fairbanks", "Fujimoto",
  "Galindo", "Gaskins", "Goldsmith", "Grimes", "Guzman", "Hearn", "Higginbotham", "Holloway",
  "Hutchins", "Ibarra", "Jankowski", "Kimbrough", "Kowalczyk", "Lachance", "Landry", "Ledbetter",
  "Lucero", "Maldonado", "McAllister", "Mercado", "Nakamura", "Nesbitt", "Okafor", "Okonkwo",
  "Palumbo", "Pemberton", "Quezada", "Rafferty", "Renteria", "Saldana", "Sandoval", "Schofield",
  "Singleton", "Stroud", "Tavares", "Thibodeaux", "Ueda", "Vann", "Villanueva", "Whitaker",
  "Winfield", "Yoon", "Zavala", "Vasquez",
]
```

```toml
# novelizer/muse/data/professions.toml
version = "2026.07"
entries = [
  "typesetter", "cooper", "milliner", "wheelwright", "stevedore", "telegraph operator",
  "linotype operator", "cordwainer", "glazier", "chandler", "farrier", "draper",
  "tanner", "sexton", "switchboard operator", "projectionist", "stenographer", "ice cutter",
  "furrier", "locksmith", "saddler", "apiarist", "taxidermist", "midwife",
  "surveyor", "assayer", "teamster", "haberdasher", "ropemaker", "sailmaker",
  "gunsmith", "distiller", "engraver", "bookbinder", "well digger", "actuary",
  "dispatcher", "crane operator", "phlebotomist", "sommelier", "arborist", "auctioneer",
  "bail bondsman", "court stenographer", "elevator mechanic", "mortician", "notary", "parole officer",
  "pit boss", "roughneck", "welder", "wig maker",
]
```

```toml
# novelizer/muse/data/settings.toml
version = "2026.07"
entries = [
  "grain elevator", "salvage yard", "county fairground", "subway maintenance depot",
  "thrift store", "cannery", "quarry", "truck stop diner",
  "cranberry bog", "rooftop apiary", "bowling alley", "hospice ward",
  "greyhound track", "strip-mall dojo", "cold-storage warehouse", "community radio station",
  "courthouse annex", "water treatment plant", "retirement high-rise", "roadside motel",
  "gun range", "laundromat", "food court", "mushroom farm",
  "slaughterhouse", "tannery district", "telephone exchange", "tent revival",
  "union hall", "pawnshop", "stock auction barn", "night market",
  "oil field man-camp", "prison visiting room", "megachurch parking lot", "county records basement",
  "demolition derby pit", "hot spring resort", "mini-golf course", "bus depot",
]
```

```toml
# novelizer/muse/data/beats.toml
version = "2026.07"
entries = [
  "someone's stated motive is false",
  "an object changes hands without its owner noticing",
  "a debt is called in early",
  "two characters discover they share an enemy",
  "a rumor arrives before the person it concerns",
  "an apology makes things worse",
  "a skill learned long ago suddenly matters",
  "someone returns something stolen",
  "a celebration is interrupted by paperwork",
  "the wrong person gets the credit",
  "a promise is kept too literally",
  "an animal behaves as an omen",
  "a stranger knows a private detail",
  "a tool breaks at the worst moment",
  "someone lies to protect a rival",
  "a letter is delivered years late",
  "the weather forces enemies indoors together",
  "a map is wrong on purpose",
  "someone eavesdrops and misunderstands",
  "a gift creates an obligation",
  "the youngest person present is right",
  "a ritual is performed incorrectly",
  "two versions of the same story are told",
  "a door that is always locked stands open",
  "someone pays for silence and gets gossip",
  "an heirloom turns out to be a fake",
  "a truce holds for exactly one scene",
  "someone practices a confession they never give",
  "the power goes out mid-negotiation",
  "a child repeats something overheard",
  "an old injury flares at a telling moment",
  "someone is mistaken for their parent",
  "the price of something doubles overnight",
  "a borrowed item is returned altered",
  "someone counts what they were sure of and comes up short",
  "an outsider wins a local contest",
  "a threat is delivered as a kindness",
  "the second-in-command acts alone",
  "a photograph contradicts a memory",
  "a machine keeps running after it should have stopped",
]
```

- [ ] **Step 4: Write the loader**

```python
# novelizer/muse/corpus.py
from __future__ import annotations
import tomllib
from importlib import resources
from pydantic import BaseModel

_DATA_PACKAGE = "novelizer.muse.data"


class CorpusError(RuntimeError):
    """A bundled corpus file is missing, unparsable, or invalid. Raised at
    Muse construction (Runtime.start) so a bad corpus fails fast, never
    mid-novel."""


class Corpora(BaseModel):
    version: str
    given_names: dict[str, list[str]]
    surnames: list[str]
    professions: list[str]
    settings: list[str]
    beats: list[str]


def _load_toml(filename: str) -> dict:
    try:
        raw = resources.files(_DATA_PACKAGE).joinpath(filename).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, NotADirectoryError) as e:
        raise CorpusError(f"corpus file missing: {filename}") from e
    try:
        return tomllib.loads(raw)
    except tomllib.TOMLDecodeError as e:
        raise CorpusError(f"corpus file unparsable: {filename}: {e}") from e


def load_corpora() -> Corpora:
    given = _load_toml("given_names.toml")
    files = {
        "surnames": _load_toml("surnames.toml"),
        "professions": _load_toml("professions.toml"),
        "settings": _load_toml("settings.toml"),
        "beats": _load_toml("beats.toml"),
    }
    version = str(given.get("version", ""))
    if not version:
        raise CorpusError("given_names.toml must declare a version")
    for name, data in files.items():
        if str(data.get("version", "")) != version:
            raise CorpusError(f"{name}.toml version {data.get('version')!r} != given_names version {version!r}")
        if not data.get("entries"):
            raise CorpusError(f"{name}.toml has no entries")
    buckets = {k: v for k, v in given.items() if k != "version"}
    if not buckets or any(not names for names in buckets.values()):
        raise CorpusError("given_names.toml must have non-empty era buckets")
    return Corpora(
        version=version,
        given_names=buckets,
        surnames=files["surnames"]["entries"],
        professions=files["professions"]["entries"],
        settings=files["settings"]["entries"],
        beats=files["beats"]["entries"],
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/muse/test_corpus.py -v`
Expected: 4 PASS

- [ ] **Step 6: Amend the spec's weights wording**

In `docs/superpowers/specs/2026-07-18-muse-entropy-design.md`, change the given-names corpus row from "bucketed by decade with frequency weights — so draws can be era-coherent with the story's setting and skew toward real-but-not-top-10 names" to "bucketed by era (victorian/interwar/midcentury/late20th/modern); buckets are curated mid-frequency lists sampled uniformly — curation replaces runtime frequency weighting, keeping draws real-but-not-top-10". In the Testing section change "era-bucketed name draws respect the requested decade" to "era-bucketed name draws respect the requested era bucket". In the Muse agent section, change "the decade bucket for name draws comes from the story's world lore / story config when one is stated; otherwise a default-modern bucket is used" to "the era bucket for name draws comes from the `muse_era` story setting (story.toml-overridable); default is the modern bucket".

- [ ] **Step 7: Commit**

```bash
git add novelizer/muse tests/muse docs/superpowers/specs/2026-07-18-muse-entropy-design.md
git commit -m "feat(muse): bundled corpora + validated loader"
```

---

### Task 2: Inspiration events, read models, projection, read store

**Files:**
- Modify: `novelizer/canon/events.py` (4 EventType constants + 4 payload models)
- Modify: `novelizer/store/models.py` (`HandStatus`, `InspirationHandRecord`, `InspirationUptakeRecord`)
- Modify: `novelizer/canon/projector.py` (2 tables, 4 `_apply` branches, `_reset_state`)
- Modify: `novelizer/canon/read_store.py` (4 accessors)
- Test: `tests/canon/test_inspiration_projection.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `EventType.INSPIRATION_DRAWN = "inspiration.drawn"`, `EventType.INSPIRATION_HAND_CONSUMED = "inspiration.hand_consumed"`, `EventType.INSPIRATION_HAND_SUPERSEDED = "inspiration.hand_superseded"`, `EventType.INSPIRATION_UPTAKE_RECORDED = "inspiration.uptake_recorded"`
  - Payloads (canon/events.py): `InspirationDrawn(hand_id, seed: int, corpus_version, era, names: list[str], professions: list[str], settings: list[str], beats: list[str], target_agent: str = "author", authority: dict[str, str])`, `InspirationHandConsumed(hand_id, chapter_id)`, `InspirationHandSuperseded(hand_id)`, `InspirationUptakeRecorded(hand_id, kind, item, chapter_id: str = "")`
  - Read models (store/models.py): `HandStatus` StrEnum (`active`/`consumed`/`superseded`), `InspirationHandRecord(id, seed, corpus_version, era, names, professions, settings, beats, status: HandStatus = active, consumed_chapter_id: str = "")`, `InspirationUptakeRecord(hand_id, kind, item, chapter_id)`
  - ReadStore: `get_active_hand() -> Optional[InspirationHandRecord]`, `get_hand(hand_id) -> Optional[InspirationHandRecord]`, `list_hands(status: Optional[str] = None) -> list[InspirationHandRecord]`, `list_uptake(hand_id: Optional[str] = None) -> list[InspirationUptakeRecord]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/canon/test_inspiration_projection.py
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.events import (
    EventType, InspirationDrawn, InspirationHandConsumed, InspirationHandSuperseded,
    InspirationUptakeRecorded,
)
from novelizer.store.models import HandStatus


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


def _drawn(hand_id="h1", seed=42):
    return InspirationDrawn(
        hand_id=hand_id, seed=seed, corpus_version="2026.07", era="modern",
        names=["Doris Kimbrough", "Mateo Rafferty"], professions=["glazier"],
        settings=["salvage yard"], beats=["a debt is called in early"],
    )


async def test_drawn_projects_active_hand(stack):
    events, proj, read = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    await proj.catch_up()
    hand = await read.get_active_hand()
    assert hand is not None and hand.id == "h1"
    assert hand.status == HandStatus.active
    assert hand.seed == 42 and hand.names[0] == "Doris Kimbrough"


async def test_consumed_sets_status_and_chapter(stack):
    events, proj, read = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    await events.append(EventType.INSPIRATION_HAND_CONSUMED, "h1",
                        InspirationHandConsumed(hand_id="h1", chapter_id="c9"))
    await proj.catch_up()
    assert await read.get_active_hand() is None
    hand = await read.get_hand("h1")
    assert hand.status == HandStatus.consumed and hand.consumed_chapter_id == "c9"


async def test_superseded_only_flips_active_hands(stack):
    events, proj, read = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    await events.append(EventType.INSPIRATION_HAND_CONSUMED, "h1",
                        InspirationHandConsumed(hand_id="h1", chapter_id="c1"))
    await events.append(EventType.INSPIRATION_HAND_SUPERSEDED, "h1", InspirationHandSuperseded(hand_id="h1"))
    await proj.catch_up()
    # consumed is absorbing: a late supersede never rewrites history
    assert (await read.get_hand("h1")).status == HandStatus.consumed


async def test_second_drawn_for_same_id_is_noop(stack):
    events, proj, read = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn(seed=1))
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn(seed=999))
    await proj.catch_up()
    assert (await read.get_hand("h1")).seed == 1  # first-mint-wins


async def test_uptake_rows_dedupe_on_replay_key(stack):
    events, proj, read = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    up = InspirationUptakeRecorded(hand_id="h1", kind="names", item="Doris Kimbrough", chapter_id="c1")
    await events.append(EventType.INSPIRATION_UPTAKE_RECORDED, "h1", up)
    await events.append(EventType.INSPIRATION_UPTAKE_RECORDED, "h1", up)
    await proj.catch_up()
    rows = await read.list_uptake("h1")
    assert len(rows) == 1 and rows[0].item == "Doris Kimbrough"


async def test_replay_reproduces_identical_rows(stack):
    events, proj, read = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    await events.append(EventType.INSPIRATION_HAND_CONSUMED, "h1",
                        InspirationHandConsumed(hand_id="h1", chapter_id="c1"))
    await events.append(EventType.INSPIRATION_UPTAKE_RECORDED, "h1",
                        InspirationUptakeRecorded(hand_id="h1", kind="beats",
                                                  item="a debt is called in early", chapter_id="c1"))
    await proj.catch_up()
    before = (await read.list_hands(), await read.list_uptake())
    await proj._reset_state()
    await proj.catch_up()
    after = (await read.list_hands(), await read.list_uptake())
    assert before == after
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/canon/test_inspiration_projection.py -v`
Expected: FAIL with `ImportError: cannot import name 'InspirationDrawn'`

- [ ] **Step 3: Add EventType constants and payload models**

In `novelizer/canon/events.py`, append to the `EventType` class:

```python
    INSPIRATION_DRAWN = "inspiration.drawn"
    INSPIRATION_HAND_CONSUMED = "inspiration.hand_consumed"
    INSPIRATION_HAND_SUPERSEDED = "inspiration.hand_superseded"
    INSPIRATION_UPTAKE_RECORDED = "inspiration.uptake_recorded"
```

At the end of `novelizer/canon/events.py`:

```python
def _default_authority() -> dict[str, str]:
    return {"names": "binding", "professions": "inspiration",
            "settings": "inspiration", "beats": "inspiration"}


class InspirationDrawn(BaseModel):
    """Payload for inspiration.drawn — the Muse deals a hand of PRNG draws
    from bundled corpora. `seed` is sourced from OS entropy at deal time and
    recorded here, so replaying the log reproduces the exact draw via
    novelizer.muse.draws.deal_hand (fresh entropy forward, deterministic
    replay back). `authority` carries the per-kind force level so raising
    beat authority later is a settings change, not a schema change.
    """

    hand_id: str
    seed: int
    corpus_version: str
    era: str
    names: list[str] = Field(default_factory=list)
    professions: list[str] = Field(default_factory=list)
    settings: list[str] = Field(default_factory=list)
    beats: list[str] = Field(default_factory=list)
    target_agent: str = "author"
    authority: dict[str, str] = Field(default_factory=_default_authority)


class InspirationHandConsumed(BaseModel):
    """Payload for inspiration.hand_consumed — the Author committed a chapter
    while this hand was live. Consumed is absorbing (like terminal threads):
    a later supersede for a consumed hand is a projection no-op.
    """

    hand_id: str
    chapter_id: str = ""


class InspirationHandSuperseded(BaseModel):
    """Payload for inspiration.hand_superseded — a director reroll discarded
    the hand before any chapter used it. The draw stays in the log as a fact.
    """

    hand_id: str


class InspirationUptakeRecorded(BaseModel):
    """Payload for inspiration.uptake_recorded — one dealt item visibly landed
    in prose. `item` is the dealt item verbatim (never the prose's variant),
    so the projection's (hand_id, kind, item) key dedupes re-mining runs.
    """

    hand_id: str
    kind: str
    item: str
    chapter_id: str = ""
```

- [ ] **Step 4: Add read models**

At the end of `novelizer/store/models.py`:

```python
class HandStatus(StrEnum):
    active = "active"
    consumed = "consumed"
    superseded = "superseded"


class InspirationHandRecord(BaseModel):
    """Read-side row for one dealt Muse hand, built by the Projector from
    inspiration.* events. `consumed` and `superseded` are both absorbing —
    whichever is applied first while the hand is active wins.
    """

    id: str
    seed: int
    corpus_version: str
    era: str
    names: list[str] = Field(default_factory=list)
    professions: list[str] = Field(default_factory=list)
    settings: list[str] = Field(default_factory=list)
    beats: list[str] = Field(default_factory=list)
    status: HandStatus = HandStatus.active
    consumed_chapter_id: str = ""


class InspirationUptakeRecord(BaseModel):
    """Read-side row for one inspiration.uptake_recorded event, deduped by
    (hand_id, kind, item) at the projection so repeated mining runs never
    inflate the uptake rate.
    """

    hand_id: str
    kind: str
    item: str
    chapter_id: str = ""
```

- [ ] **Step 5: Add projection tables and apply branches**

In `novelizer/canon/projector.py`, append to `_CREATE`:

```sql
CREATE TABLE IF NOT EXISTS inspiration_hands (
    id TEXT PRIMARY KEY, data TEXT NOT NULL, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inspiration_uptake (
    hand_id TEXT NOT NULL, kind TEXT NOT NULL, item TEXT NOT NULL,
    chapter_id TEXT NOT NULL DEFAULT '', PRIMARY KEY (hand_id, kind, item)
);
```

Add `"inspiration_hands", "inspiration_uptake"` to the table tuple in `_reset_state`.

Import the new read models at the top (extend the existing `novelizer.store.models` import): `HandStatus, InspirationHandRecord`.

Add `_apply` branches before the `AUTONOMY_CHANGED` branch:

```python
        elif t == EventType.INSPIRATION_DRAWN:
            cur = await self._conn.execute("SELECT id FROM inspiration_hands WHERE id=?", (p["hand_id"],))
            existing = await cur.fetchone()
            if existing is None:
                record = InspirationHandRecord(
                    id=p["hand_id"], seed=p["seed"], corpus_version=p["corpus_version"],
                    era=p["era"], names=p.get("names", []), professions=p.get("professions", []),
                    settings=p.get("settings", []), beats=p.get("beats", []),
                )
                await self._conn.execute(
                    "INSERT OR REPLACE INTO inspiration_hands (id, data, status) VALUES (?,?,?)",
                    (record.id, record.model_dump_json(), record.status.value),
                )
            # else: a hand id is minted exactly once — first-mint-wins, same
            # rule as thread.planted/secret.created/theme.introduced.
        elif t in (EventType.INSPIRATION_HAND_CONSUMED, EventType.INSPIRATION_HAND_SUPERSEDED):
            cur = await self._conn.execute("SELECT data FROM inspiration_hands WHERE id=?", (p["hand_id"],))
            row = await cur.fetchone()
            if row is not None:
                record = InspirationHandRecord.model_validate_json(row[0])
                if record.status == HandStatus.active:
                    if t == EventType.INSPIRATION_HAND_CONSUMED:
                        updated = record.model_copy(update={
                            "status": HandStatus.consumed,
                            "consumed_chapter_id": p.get("chapter_id", ""),
                        })
                    else:
                        updated = record.model_copy(update={"status": HandStatus.superseded})
                    await self._conn.execute(
                        "INSERT OR REPLACE INTO inspiration_hands (id, data, status) VALUES (?,?,?)",
                        (updated.id, updated.model_dump_json(), updated.status.value),
                    )
                # else: consumed/superseded are absorbing — the event is a fact
                # in the log, but the projection does not change.
            # else: no row for this id (shouldn't happen under correct Muse
            # behavior) — nothing to project, no error raised.
        elif t == EventType.INSPIRATION_UPTAKE_RECORDED:
            await self._conn.execute(
                "INSERT OR IGNORE INTO inspiration_uptake (hand_id, kind, item, chapter_id) VALUES (?,?,?,?)",
                (p["hand_id"], p["kind"], p["item"], p.get("chapter_id", "")),
            )
```

- [ ] **Step 6: Add ReadStore accessors**

In `novelizer/canon/read_store.py`, extend the `novelizer.store.models` import with `InspirationHandRecord, InspirationUptakeRecord`, and append:

```python
    async def get_active_hand(self) -> Optional[InspirationHandRecord]:
        cur = await self._conn.execute(
            "SELECT data FROM inspiration_hands WHERE status='active' ORDER BY rowid DESC LIMIT 1"
        )
        row = await cur.fetchone()
        return InspirationHandRecord.model_validate_json(row[0]) if row else None

    async def get_hand(self, hand_id: str) -> Optional[InspirationHandRecord]:
        cur = await self._conn.execute("SELECT data FROM inspiration_hands WHERE id=?", (hand_id,))
        row = await cur.fetchone()
        return InspirationHandRecord.model_validate_json(row[0]) if row else None

    async def list_hands(self, status: Optional[str] = None) -> list[InspirationHandRecord]:
        if status:
            cur = await self._conn.execute(
                "SELECT data FROM inspiration_hands WHERE status=? ORDER BY rowid", (status,)
            )
        else:
            cur = await self._conn.execute("SELECT data FROM inspiration_hands ORDER BY rowid")
        return [InspirationHandRecord.model_validate_json(r[0]) for r in await cur.fetchall()]

    async def list_uptake(self, hand_id: Optional[str] = None) -> list[InspirationUptakeRecord]:
        query = "SELECT hand_id, kind, item, chapter_id FROM inspiration_uptake"
        params: tuple = ()
        if hand_id is not None:
            query += " WHERE hand_id=?"
            params = (hand_id,)
        query += " ORDER BY rowid"
        cur = await self._conn.execute(query, params)
        return [
            InspirationUptakeRecord(hand_id=r[0], kind=r[1], item=r[2], chapter_id=r[3])
            for r in await cur.fetchall()
        ]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/canon/test_inspiration_projection.py -v`
Expected: 6 PASS

- [ ] **Step 8: Commit**

```bash
git add novelizer/canon/events.py novelizer/store/models.py novelizer/canon/projector.py novelizer/canon/read_store.py tests/canon/test_inspiration_projection.py
git commit -m "feat(muse): inspiration.* events, projection, and read accessors"
```

---

### Task 3: Deterministic draw engine

**Files:**
- Create: `novelizer/muse/draws.py`
- Test: `tests/muse/test_draws_property.py`

**Interfaces:**
- Consumes: `Corpora` from Task 1; `InspirationDrawn` from Task 2.
- Produces: `deal_hand(corpora: Corpora, seed: int, era: str, exclude: set[str], hand_id: str) -> InspirationDrawn` (pure; unknown era falls back to `DEFAULT_ERA` and records the bucket actually used); constants `HAND_NAMES = 5`, `HAND_PROFESSIONS = 3`, `HAND_SETTINGS = 2`, `HAND_BEATS = 2`, `DEFAULT_ERA = "modern"`.

- [ ] **Step 1: Write the failing property tests**

```python
# tests/muse/test_draws_property.py
from hypothesis import given, strategies as st
from novelizer.muse.corpus import load_corpora
from novelizer.muse.draws import (
    DEFAULT_ERA, HAND_BEATS, HAND_NAMES, HAND_PROFESSIONS, HAND_SETTINGS, deal_hand,
)

CORPORA = load_corpora()
SEEDS = st.integers(min_value=0, max_value=2**63 - 1)


@given(seed=SEEDS)
def test_same_seed_deals_identical_hand(seed):
    a = deal_hand(CORPORA, seed, "modern", set(), "h1")
    b = deal_hand(CORPORA, seed, "modern", set(), "h1")
    assert a == b  # this is what makes event-log replay deterministic


@given(seed=SEEDS)
def test_hand_sizes(seed):
    hand = deal_hand(CORPORA, seed, "modern", set(), "h1")
    assert len(hand.names) == HAND_NAMES
    assert len(hand.professions) == HAND_PROFESSIONS
    assert len(hand.settings) == HAND_SETTINGS
    assert len(hand.beats) == HAND_BEATS
    assert len(set(hand.names)) == HAND_NAMES  # no repeats within a hand


@given(seed=SEEDS, era=st.sampled_from(sorted(CORPORA.given_names)))
def test_given_names_come_from_requested_era_bucket(seed, era):
    hand = deal_hand(CORPORA, seed, era, set(), "h1")
    bucket = set(CORPORA.given_names[era])
    surnames = set(CORPORA.surnames)
    for full in hand.names:
        given_part, surname_part = full.rsplit(" ", 1)
        assert given_part in bucket and surname_part in surnames
    assert hand.era == era


@given(seed=SEEDS)
def test_unknown_era_falls_back_to_default(seed):
    hand = deal_hand(CORPORA, seed, "jurassic", set(), "h1")
    assert hand.era == DEFAULT_ERA


@given(seed=SEEDS)
def test_exclusion_respected_when_corpus_ample(seed):
    exclude = set(CORPORA.beats[:5]) | set(CORPORA.professions[:5]) | set(CORPORA.settings[:5])
    hand = deal_hand(CORPORA, seed, "modern", exclude, "h1")
    assert not (set(hand.beats) & exclude)
    assert not (set(hand.professions) & exclude)
    assert not (set(hand.settings) & exclude)


@given(seed=SEEDS)
def test_excluded_name_components_not_redealt(seed):
    excluded_full_names = {f"{CORPORA.given_names['modern'][0]} {CORPORA.surnames[0]}"}
    hand = deal_hand(CORPORA, seed, "modern", excluded_full_names, "h1")
    for full in hand.names:
        given_part, surname_part = full.rsplit(" ", 1)
        assert given_part != CORPORA.given_names["modern"][0]
        assert surname_part != CORPORA.surnames[0]


@given(seed=SEEDS)
def test_exhausted_exclusion_reuses_corpus_instead_of_failing(seed):
    hand = deal_hand(CORPORA, seed, "modern", set(CORPORA.beats), "h1")
    assert len(hand.beats) == HAND_BEATS  # falls back to the full pool
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/muse/test_draws_property.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.muse.draws'`

- [ ] **Step 3: Write the draw engine**

```python
# novelizer/muse/draws.py
from __future__ import annotations
import random
from novelizer.canon.events import InspirationDrawn
from novelizer.muse.corpus import Corpora

HAND_NAMES = 5
HAND_PROFESSIONS = 3
HAND_SETTINGS = 2
HAND_BEATS = 2
DEFAULT_ERA = "modern"


def _sample(rng: random.Random, pool: list[str], exclude: set[str], count: int) -> list[str]:
    fresh = [entry for entry in pool if entry not in exclude]
    if len(fresh) < count:
        # The exclusion window exhausted the corpus: reuse is better than an
        # empty draw (spec: degraded, never blocked).
        fresh = list(pool)
    return rng.sample(fresh, min(count, len(fresh)))


def deal_hand(corpora: Corpora, seed: int, era: str, exclude: set[str], hand_id: str) -> InspirationDrawn:
    """Deal one hand. Pure: identical (corpora, seed, era, exclude) always
    deal the identical hand — the property event-log replay relies on.
    `exclude` holds items dealt in the recent-hand window verbatim; for names
    ("Given Surname") both components are individually excluded.
    """
    rng = random.Random(seed)
    bucket_era = era if era in corpora.given_names else DEFAULT_ERA
    excluded_givens = {entry.rsplit(" ", 1)[0] for entry in exclude if " " in entry}
    excluded_surnames = {entry.rsplit(" ", 1)[1] for entry in exclude if " " in entry}
    givens = _sample(rng, corpora.given_names[bucket_era], excluded_givens, HAND_NAMES)
    surnames = _sample(rng, corpora.surnames, excluded_surnames, HAND_NAMES)
    return InspirationDrawn(
        hand_id=hand_id,
        seed=seed,
        corpus_version=corpora.version,
        era=bucket_era,
        names=[f"{g} {s}" for g, s in zip(givens, surnames)],
        professions=_sample(rng, corpora.professions, exclude, HAND_PROFESSIONS),
        settings=_sample(rng, corpora.settings, exclude, HAND_SETTINGS),
        beats=_sample(rng, corpora.beats, exclude, HAND_BEATS),
    )
```

Note: `excluded_givens`/`excluded_surnames` split multi-word non-name items too ("salvage yard" → given "salvage"); that only ever over-excludes a stray token from the name pools, never under-excludes, so it's harmless.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/muse/test_draws_property.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/muse/draws.py tests/muse/test_draws_property.py
git commit -m "feat(muse): seeded deterministic draw engine with exclusion window"
```

---

### Task 4: Muse agent, settings keys, runtime wiring

**Files:**
- Create: `novelizer/agents/muse.py`
- Modify: `novelizer/settings/models.py` (3 new keys + STORY_OVERRIDABLE_KEYS)
- Modify: `novelizer/runtime.py` (construct Muse, roster, interval_map, live-apply era/window)
- Test: `tests/agents/test_muse.py`

**Interfaces:**
- Consumes: `load_corpora`/`CorpusError` (Task 1), `deal_hand`/`DEFAULT_ERA` (Task 3), events + ReadStore accessors (Task 2).
- Produces: `Muse(read_store, committer, interval=60, era=DEFAULT_ERA, exclusion_hands=3, personality="")` — a `BaseAgent` with `name="muse"`, no runner, no LLM calls; `Muse.deal_fresh_hand() -> InspirationDrawn` (public: the `:muse reroll` command in Task 8 calls it); settings keys `muse_interval: int = 60`, `muse_era: str = "modern"`, `muse_exclusion_hands: int = 3`; `runtime.muse` attribute.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_muse.py
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, InspirationHandConsumed
from novelizer.agents.muse import Muse


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def test_run_once_deals_a_hand_when_none_active(stack):
    events, proj, read, committer = stack
    muse = Muse(read, committer)
    await muse.run_once()
    await proj.catch_up()
    hand = await read.get_active_hand()
    assert hand is not None
    assert len(hand.names) == 5 and len(hand.beats) == 2
    assert hand.era == "modern"


async def test_run_once_skips_when_hand_already_active(stack):
    events, proj, read, committer = stack
    muse = Muse(read, committer)
    await muse.run_once()
    await proj.catch_up()
    first = await read.get_active_hand()
    await muse.run_once()
    await proj.catch_up()
    assert [h.id for h in await read.list_hands()] == [first.id]


async def test_new_hand_excludes_recent_hand_items(stack):
    events, proj, read, committer = stack
    muse = Muse(read, committer)
    await muse.run_once()
    await proj.catch_up()
    first = await read.get_active_hand()
    await events.append(EventType.INSPIRATION_HAND_CONSUMED, first.id,
                        InspirationHandConsumed(hand_id=first.id, chapter_id="c1"))
    await proj.catch_up()
    await muse.run_once()
    await proj.catch_up()
    second = await read.get_active_hand()
    assert second.id != first.id
    for kind in ("professions", "settings", "beats"):
        assert not (set(getattr(second, kind)) & set(getattr(first, kind)))


async def test_readiness_reflects_hand_presence(stack):
    events, proj, read, committer = stack
    muse = Muse(read, committer)
    assert await muse.readiness() == 0.9
    await muse.run_once()
    await proj.catch_up()
    assert await muse.readiness() == 0.0


async def test_era_setting_is_respected(stack):
    events, proj, read, committer = stack
    muse = Muse(read, committer, era="victorian")
    await muse.run_once()
    await proj.catch_up()
    assert (await read.get_active_hand()).era == "victorian"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_muse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.agents.muse'`

- [ ] **Step 3: Write the Muse agent**

```python
# novelizer/agents/muse.py
from __future__ import annotations
import logging
import secrets
import uuid
from novelizer.agents.base import BaseAgent
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, InspirationDrawn
from novelizer.canon.read_store import ReadStore
from novelizer.muse.corpus import load_corpora
from novelizer.muse.draws import DEFAULT_ERA, deal_hand

logger = logging.getLogger(__name__)


class Muse(BaseAgent):
    """Deals seeded hands of corpus draws as inspiration.* events.

    The one agent with no LLM: poll the read projection, top up the hand,
    commit. Corpora load at construction so a bad data file fails Runtime
    startup, never mid-novel. Keeps exactly one unconsumed hand ahead of the
    Author; the Author consumes it at chapter commit, which makes readiness
    flip back to 0.9 and the next cycle deal a fresh hand.
    """

    def __init__(
        self,
        read_store: ReadStore,
        committer: Committer,
        interval: int = 60,
        era: str = DEFAULT_ERA,
        exclusion_hands: int = 3,
        personality: str = "",
    ) -> None:
        super().__init__(None, read_store, committer, interval, name="muse", personality=personality)
        self._corpora = load_corpora()
        self._era = era
        self._exclusion_hands = exclusion_hands

    async def readiness(self) -> float:
        return 0.0 if await self._read.get_active_hand() is not None else 0.9

    async def run_once(self) -> None:
        if await self._read.get_active_hand() is not None:
            return
        await self.deal_fresh_hand()

    async def deal_fresh_hand(self) -> InspirationDrawn:
        """Deal and commit a new hand unconditionally. Public: the director's
        `:muse reroll` calls this right after superseding the active hand,
        without waiting for the projector to catch up."""
        recent = (await self._read.list_hands())[-self._exclusion_hands:]
        exclude = {
            item
            for hand in recent
            for item in (*hand.names, *hand.professions, *hand.settings, *hand.beats)
        }
        seed = secrets.randbits(63)
        hand = deal_hand(self._corpora, seed, self._era, exclude, str(uuid.uuid4()))
        await self._committer.commit(self.name, EventType.INSPIRATION_DRAWN, hand.hand_id, hand)
        await self._remark(f"a fresh hand on the table: {', '.join(hand.names)}")
        logger.info("muse dealt hand %s (seed=%d, era=%s)", hand.hand_id, seed, hand.era)
        return hand
```

- [ ] **Step 4: Add settings keys**

In `novelizer/settings/models.py`, add to `EffectiveSettings` after `structure_analyst_interval`:

```python
    muse_interval: int = 60
```

and after `sag_spike_delta`:

```python
    # Muse: era bucket for name draws (victorian/interwar/midcentury/late20th/modern)
    # and how many recent hands' items are excluded from a fresh deal.
    muse_era: str = "modern"
    muse_exclusion_hands: int = 3
```

Extend `STORY_OVERRIDABLE_KEYS` with `"muse_interval", "muse_era", "muse_exclusion_hands"` (in the interval group and the tuning group respectively).

- [ ] **Step 5: Wire into Runtime**

In `novelizer/runtime.py`:
- Import: `from novelizer.agents.muse import Muse`
- In `__init__`: add `self.muse = None` beside the other agent attributes.
- In `start()`, after `self.structure_analyst = ...`:

```python
        self.muse = Muse(
            self.read, self.committer,
            interval=s.muse_interval, era=s.muse_era,
            exclusion_hands=s.muse_exclusion_hands, personality=personalities.get("muse", ""),
        )
```

- Add `self.muse` to the `self.agents` list (before `self.author`, so a hand exists by the Author's first poll in a fresh story).
- In `apply_settings`, add `"muse_interval": [self.muse]` to `interval_map`, and after the `max_concurrent_agents` branch add:

```python
            elif key == "muse_era":
                self.muse._era = new.muse_era
                applied.append(key)
            elif key == "muse_exclusion_hands":
                self.muse._exclusion_hands = new.muse_exclusion_hands
                applied.append(key)
```

- [ ] **Step 6: Run tests to verify they pass (plus regressions)**

Run: `uv run pytest tests/agents/test_muse.py tests/test_runtime.py tests/test_apply_settings.py tests/settings -v`
Expected: all PASS (runtime/settings suites confirm the new keys and roster entry break nothing)

- [ ] **Step 7: Commit**

```bash
git add novelizer/agents/muse.py novelizer/settings/models.py novelizer/runtime.py tests/agents/test_muse.py
git commit -m "feat(muse): no-LLM Muse agent dealing hands, wired into runtime + settings"
```

---

### Task 5: Author + WorldArchitect prompt integration and hand consumption

**Files:**
- Create: `novelizer/muse/prompts.py`
- Modify: `novelizer/agents/author.py` (ban note, hand in poll/_summarize/commit)
- Modify: `novelizer/agents/world_architect.py` (settings sparks, read-only)
- Test: `tests/muse/test_prompts.py`, `tests/agents/test_author_muse.py`

**Interfaces:**
- Consumes: `InspirationHandRecord` (Task 2), `EventType.INSPIRATION_HAND_CONSUMED` + `InspirationHandConsumed` (Task 2).
- Produces: `casting_pool_note(hand) -> str`, `inspiration_note(hand) -> str`, `architect_settings_note(hand) -> str` (all return `""` for `None`/empty), `AI_TELL_BAN_NOTE: str`, `NAME_UPTAKE_HAND_WINDOW = 3` (Task 6 uses it), `name_uptake_matches(name: str, hands) -> tuple[str, str] | None` returning `(hand_id, dealt_item)` (Task 6 uses it). Author consumes the active hand on new-chapter commit only (never on revision).

- [ ] **Step 1: Write the failing tests**

```python
# tests/muse/test_prompts.py
from novelizer.muse.prompts import (
    AI_TELL_BAN_NOTE, architect_settings_note, casting_pool_note,
    inspiration_note, name_uptake_matches,
)
from novelizer.store.models import InspirationHandRecord


def _hand(hand_id="h1", **kw):
    defaults = dict(
        id=hand_id, seed=1, corpus_version="2026.07", era="modern",
        names=["Doris Kimbrough", "Mateo Rafferty"], professions=["glazier"],
        settings=["salvage yard"], beats=["a debt is called in early"],
    )
    defaults.update(kw)
    return InspirationHandRecord(**defaults)


def test_casting_pool_note_lists_names_and_is_binding():
    note = casting_pool_note(_hand())
    assert "Doris Kimbrough" in note and "Mateo Rafferty" in note
    assert "NEW named character" in note


def test_inspiration_note_is_marked_optional():
    note = inspiration_note(_hand())
    assert "optional" in note.lower()
    assert "glazier" in note and "salvage yard" in note and "a debt is called in early" in note


def test_notes_are_empty_without_a_hand():
    assert casting_pool_note(None) == ""
    assert inspiration_note(None) == ""
    assert architect_settings_note(None) == ""
    assert inspiration_note(_hand(professions=[], settings=[], beats=[])) == ""


def test_architect_note_only_carries_settings():
    note = architect_settings_note(_hand())
    assert "salvage yard" in note and "glazier" not in note


def test_ban_note_names_the_tells():
    for tell in ("Elias", "Elara", "Mara", "Thorne", "lighthouse"):
        assert tell in AI_TELL_BAN_NOTE


def test_name_uptake_matches_full_and_given_name():
    hands = [_hand("h1"), _hand("h2", names=["Wanda Okafor"])]
    assert name_uptake_matches("Wanda Okafor", hands) == ("h2", "Wanda Okafor")
    # prose often drops the surname: given-name-token match still counts,
    # and the most recent hand wins
    assert name_uptake_matches("doris", hands) == ("h1", "Doris Kimbrough")
    assert name_uptake_matches("Prudence", hands) is None
    assert name_uptake_matches("", hands) is None
```

```python
# tests/agents/test_author_muse.py
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, InspirationDrawn
from novelizer.agents.author import Author, AUTHOR_SYSTEM_PROMPT, ChapterDraft
from novelizer.agents.world_architect import WorldArchitect
from novelizer.agents.schemas import WorldEntriesDraft
from novelizer.store.models import Chapter, DirectorSignal, HandStatus, SignalKind


class FakeRunner:
    def __init__(self, draft): self._draft = draft; self.calls = []
    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._draft}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


def _drawn(hand_id="h1"):
    return InspirationDrawn(
        hand_id=hand_id, seed=7, corpus_version="2026.07", era="modern",
        names=["Doris Kimbrough"], professions=["glazier"],
        settings=["salvage yard"], beats=["a debt is called in early"],
    )


async def test_author_prompt_carries_pool_and_sparks(stack):
    events, proj, read, committer = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    await proj.catch_up()
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    await Author(runner, read, committer).run_once()
    content = runner.calls[0]["messages"][0]["content"]
    assert "Doris Kimbrough" in content and "a debt is called in early" in content


async def test_author_consumes_hand_on_new_chapter(stack):
    events, proj, read, committer = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    await proj.catch_up()
    await Author(FakeRunner(ChapterDraft(title="T", prose="P")), read, committer).run_once()
    await proj.catch_up()
    hand = await read.get_hand("h1")
    chapter = (await read.list_chapters())[0]
    assert hand.status == HandStatus.consumed and hand.consumed_chapter_id == chapter.id


async def test_author_revision_does_not_consume_hand(stack):
    events, proj, read, committer = stack
    await events.append(EventType.CHAPTER_CREATED, "c1", Chapter(id="c1", title="One", prose="orig"))
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    await events.append(EventType.DIRECTOR_SIGNAL_CREATED, "s1",
                        DirectorSignal(id="s1", kind=SignalKind.revise, body="fix",
                                        target_agent="author", target_entity="c1"))
    await proj.catch_up()
    await Author(FakeRunner(ChapterDraft(title="One", prose="new")), read, committer).run_once()
    await proj.catch_up()
    assert (await read.get_hand("h1")).status == HandStatus.active


async def test_author_works_without_a_hand(stack):
    events, proj, read, committer = stack
    runner = FakeRunner(ChapterDraft(title="T", prose="P"))
    await Author(runner, read, committer).run_once()
    await proj.catch_up()
    assert len(await read.list_chapters()) == 1
    assert "Casting pool" not in runner.calls[0]["messages"][0]["content"]


async def test_system_prompt_bans_ai_tells():
    assert "Elias" in AUTHOR_SYSTEM_PROMPT and "lighthouse" in AUTHOR_SYSTEM_PROMPT


async def test_world_architect_sees_setting_sparks_but_never_consumes(stack):
    events, proj, read, committer = stack
    await events.append(EventType.INSPIRATION_DRAWN, "h1", _drawn())
    await proj.catch_up()
    runner = FakeRunner(WorldEntriesDraft(entries=[]))
    await WorldArchitect(runner, read, committer).run_once()
    await proj.catch_up()
    assert "salvage yard" in runner.calls[0]["messages"][0]["content"]
    assert (await read.get_hand("h1")).status == HandStatus.active
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/muse/test_prompts.py tests/agents/test_author_muse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.muse.prompts'`

- [ ] **Step 3: Write the prompt helpers**

```python
# novelizer/muse/prompts.py
from __future__ import annotations
from typing import Iterable, Optional
from novelizer.store.models import InspirationHandRecord

# Static defense in depth for minor characters the casting pool doesn't cover.
# These exact words appeared in 88% of AI-generated stories (Hamilton & Mimno,
# Cornell 2026); the corpora exclude them and the Author is told to as well.
AI_TELL_BAN_NOTE = (
    "Never name characters Elias, Elara, Mara, Thorne, or Voss, and avoid stock figures "
    "like lighthouse keepers, clockmakers, bakers, or quaint coastal villages — these are "
    "convergent AI cliches. Avoid near-variants of them too."
)

# How many recent consumed hands CharacterKeeper scans when matching a freshly
# minted character name to a dealt name (Task 6).
NAME_UPTAKE_HAND_WINDOW = 3


def casting_pool_note(hand: Optional[InspirationHandRecord]) -> str:
    if hand is None or not hand.names:
        return ""
    return (
        "\n\nCasting pool (binding): when you introduce a NEW named character, take their "
        "name from this list, exactly as written: " + "; ".join(hand.names)
    )


def inspiration_note(hand: Optional[InspirationHandRecord]) -> str:
    if hand is None:
        return ""
    parts = []
    if hand.professions:
        parts.append("professions: " + "; ".join(hand.professions))
    if hand.settings:
        parts.append("settings: " + "; ".join(hand.settings))
    if hand.beats:
        parts.append("story sparks: " + "; ".join(hand.beats))
    if not parts:
        return ""
    return (
        "\n\nInspiration hand (optional — weave in any that genuinely fit this chapter, "
        "ignore the rest): " + " | ".join(parts)
    )


def architect_settings_note(hand: Optional[InspirationHandRecord]) -> str:
    if hand is None or not hand.settings:
        return ""
    return "\n\nDrawn setting sparks (optional): " + "; ".join(hand.settings)


def name_uptake_matches(
    name: str, hands: Iterable[InspirationHandRecord]
) -> tuple[str, str] | None:
    """Match a minted character name against dealt names in `hands`.

    Returns (hand_id, dealt_item) for the most recent hand containing a match,
    or None. A match is the full dealt name, or its given-name token — prose
    routinely drops surnames, and a dropped surname is still uptake.
    """
    lowered = name.strip().lower()
    if not lowered:
        return None
    first_token = lowered.split(" ")[0]
    for hand in reversed(list(hands)):
        for dealt in hand.names:
            dealt_lower = dealt.lower()
            if dealt_lower == lowered or dealt_lower.split(" ")[0] == first_token:
                return (hand.id, dealt)
    return None
```

- [ ] **Step 4: Integrate into the Author**

In `novelizer/agents/author.py`:

- Add imports: `from novelizer.canon.events import InspirationHandConsumed` (extend the existing `novelizer.canon.events` import) and `from novelizer.muse.prompts import AI_TELL_BAN_NOTE, casting_pool_note, inspiration_note`.
- Change the system prompt constant to append the ban note:

```python
AUTHOR_SYSTEM_PROMPT = """You are the Author of a living fictional world. Write the next prose chapter.
You receive world lore, active characters, previous chapter summaries, and director notes.
Write a self-contained chapter with a clear narrative beat, 2-5 paragraphs.
Return a title, the full prose, and the ids of characters who appear.
""" + AI_TELL_BAN_NOTE
```

- In `_summarize`, before the `return`, add:

```python
    pool = casting_pool_note(ctx.get("hand"))
    sparks = inspiration_note(ctx.get("hand"))
```

and change the returned f-string's middle from `...Director notes:\n{notes}{voice}...` to `...Director notes:\n{notes}{pool}{sparks}{voice}...`.

- In `poll()`, add to the returned dict:

```python
            "hand": await self._read.get_active_hand(),
```

- In `commit()`, in the **new-chapter** branch only (the `else` that commits `CHAPTER_CREATED`), after `valid_chapter_ids = ...`, add:

```python
            hand = ctx.get("hand")
            if hand is not None:
                await self._committer.commit(
                    self.name, EventType.INSPIRATION_HAND_CONSUMED, hand.id,
                    InspirationHandConsumed(hand_id=hand.id, chapter_id=chapter.id),
                )
```

- [ ] **Step 5: Integrate into the WorldArchitect (read-only)**

In `novelizer/agents/world_architect.py`:

- Add import: `from novelizer.muse.prompts import architect_settings_note`
- In `poll()`, add `"hand": await self._read.get_active_hand(),`
- In `work()`, add `sparks = architect_settings_note(ctx.get("hand"))` and change the msg line to:

```python
        msg = f"Existing world entries:\n{existing}\n\nDirector seeds:\n{seeds}{sparks}{cast}\n\nGenerate new world entries."
```

`commit()` is untouched — the WorldArchitect never consumes the hand.

- [ ] **Step 6: Run tests to verify they pass (plus author regressions)**

Run: `uv run pytest tests/muse/test_prompts.py tests/agents/test_author_muse.py tests/agents/test_author.py tests/agents/test_world_architect.py tests/agents/test_guarded_line_adoption.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add novelizer/muse/prompts.py novelizer/agents/author.py novelizer/agents/world_architect.py tests/muse/test_prompts.py tests/agents/test_author_muse.py
git commit -m "feat(muse): casting pool + inspiration hand in Author prompt, ban-list, WA sparks"
```

---

### Task 6: Name uptake at CharacterKeeper mint time

**Files:**
- Modify: `novelizer/agents/character_keeper.py`
- Test: `tests/agents/test_character_keeper_uptake.py`

**Interfaces:**
- Consumes: `name_uptake_matches`, `NAME_UPTAKE_HAND_WINDOW` (Task 5); `EventType.INSPIRATION_UPTAKE_RECORDED` + `InspirationUptakeRecorded` (Task 2); `ReadStore.list_hands` (Task 2).
- Produces: on every successful `CHARACTER_CREATED` mint whose name matches a dealt name in the last `NAME_UPTAKE_HAND_WINDOW` consumed hands, one `inspiration.uptake_recorded` commit with `kind="names"`, `item=<dealt name verbatim>`, `chapter_id=<the hand's consumed_chapter_id>`. Mint-time is one-shot, so this is naturally idempotent; the projection's (hand_id, kind, item) key catches any replays.

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_character_keeper_uptake.py
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, InspirationDrawn, InspirationHandConsumed
from novelizer.agents.character_keeper import CharacterKeeper
from novelizer.agents.schemas import KeeperOutput, NewCharacter
from novelizer.store.models import Chapter


class FakeRunner:
    def __init__(self, out): self._out = out
    async def ainvoke(self, inputs):
        return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def _seed_consumed_hand(events, proj):
    await events.append(EventType.CHAPTER_CREATED, "c1",
                        Chapter(id="c1", title="One", prose="Doris crossed the yard."))
    await events.append(EventType.INSPIRATION_DRAWN, "h1", InspirationDrawn(
        hand_id="h1", seed=7, corpus_version="2026.07", era="modern",
        names=["Doris Kimbrough"], professions=["glazier"], settings=["salvage yard"],
        beats=["a debt is called in early"],
    ))
    await events.append(EventType.INSPIRATION_HAND_CONSUMED, "h1",
                        InspirationHandConsumed(hand_id="h1", chapter_id="c1"))
    await proj.catch_up()


async def test_minting_a_dealt_name_records_uptake(stack):
    events, proj, read, committer = stack
    await _seed_consumed_hand(events, proj)
    out = KeeperOutput(new_characters=[NewCharacter(name="Doris Kimbrough")])
    await CharacterKeeper(FakeRunner(out), read, committer).run_once()
    await proj.catch_up()
    rows = await read.list_uptake("h1")
    assert [(r.kind, r.item, r.chapter_id) for r in rows] == [("names", "Doris Kimbrough", "c1")]


async def test_minting_an_undealt_name_records_nothing(stack):
    events, proj, read, committer = stack
    await _seed_consumed_hand(events, proj)
    out = KeeperOutput(new_characters=[NewCharacter(name="Prudence Vann")])
    await CharacterKeeper(FakeRunner(out), read, committer).run_once()
    await proj.catch_up()
    assert await read.list_uptake() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_character_keeper_uptake.py -v`
Expected: `test_minting_a_dealt_name_records_uptake` FAILS (no uptake rows); the other may pass — that's fine.

- [ ] **Step 3: Implement**

In `novelizer/agents/character_keeper.py`:

- Add imports: extend the `novelizer.canon.events` import with `InspirationUptakeRecorded`, and add `from novelizer.muse.prompts import NAME_UPTAKE_HAND_WINDOW, name_uptake_matches`.
- In `poll()`, add to the returned dict:

```python
            "hands": (await self._read.list_hands(status="consumed"))[-NAME_UPTAKE_HAND_WINDOW:],
```

- In `commit()`, inside the `for new in out.new_characters:` loop, immediately after the `await self._committer.commit(self.name, EventType.CHARACTER_CREATED, char_id, character)` line, add:

```python
            match = name_uptake_matches(new.name, ctx.get("hands", []))
            if match is not None:
                hand_id, dealt_item = match
                hand = next(h for h in ctx["hands"] if h.id == hand_id)
                await self._committer.commit(
                    self.name, EventType.INSPIRATION_UPTAKE_RECORDED, hand_id,
                    InspirationUptakeRecorded(hand_id=hand_id, kind="names", item=dealt_item,
                                              chapter_id=hand.consumed_chapter_id),
                )
```

- [ ] **Step 4: Run tests to verify they pass (plus keeper regressions)**

Run: `uv run pytest tests/agents/test_character_keeper_uptake.py tests/agents/test_character_keeper.py tests/agents/test_character_keeper_property.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add novelizer/agents/character_keeper.py tests/agents/test_character_keeper_uptake.py
git commit -m "feat(muse): record name uptake when CharacterKeeper mints a dealt name"
```

---

### Task 7: Profession/setting/beat uptake via the mining pass

**Files:**
- Modify: `novelizer/agents/schemas.py` (`MinedInspirationFact`, field on `MinedFactsOutput`)
- Modify: `novelizer/agents/continuity_checker.py` (prompt lines, hands in poll, commit path)
- Test: `tests/agents/test_continuity_uptake.py`

**Interfaces:**
- Consumes: hands-by-chapter from `ReadStore.list_hands(status="consumed")` (Task 2); `EventType.INSPIRATION_UPTAKE_RECORDED` + `InspirationUptakeRecorded` (Task 2).
- Produces: `MinedInspirationFact(kind: Literal["professions", "settings", "beats"], item: str)`; `MinedFactsOutput.inspiration_facts: list[MinedInspirationFact]` (default empty — existing callers unaffected); mining prompt lists the dealt items for chapters that consumed a hand; validated facts commit `inspiration.uptake_recorded` with `item` = the dealt entry verbatim (case-insensitive match against the hand; non-matching facts are logged and dropped, never a retcon).

- [ ] **Step 1: Write the failing tests**

```python
# tests/agents/test_continuity_uptake.py
import os
import tempfile
import pytest
from novelizer.canon.event_store import EventStore
from novelizer.canon.projector import Projector
from novelizer.canon.read_store import ReadStore
from novelizer.canon.committer import Committer
from novelizer.canon.events import EventType, InspirationDrawn, InspirationHandConsumed
from novelizer.agents.continuity_checker import ContinuityChecker
from novelizer.agents.schemas import ContinuityOutput, MinedFactsOutput, MinedInspirationFact
from novelizer.store.models import Chapter


class FakeRunner:
    def __init__(self, out): self._out = out; self.calls = []
    async def ainvoke(self, inputs):
        self.calls.append(inputs)
        return {"structured_response": self._out}


@pytest.fixture
async def stack():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    events = EventStore(path); await events.init()
    proj = Projector(events, path); await proj.init()
    read = ReadStore(path); await read.init()
    yield events, proj, read, Committer(events)
    await read.close(); await proj.close(); await events.close(); os.unlink(path)


async def _seed(events, proj):
    await events.append(EventType.CHAPTER_CREATED, "c1",
                        Chapter(id="c1", title="One", prose="The glazier waited in the salvage yard."))
    await events.append(EventType.INSPIRATION_DRAWN, "h1", InspirationDrawn(
        hand_id="h1", seed=7, corpus_version="2026.07", era="modern",
        names=["Doris Kimbrough"], professions=["glazier"], settings=["salvage yard"],
        beats=["a debt is called in early"],
    ))
    await events.append(EventType.INSPIRATION_HAND_CONSUMED, "h1",
                        InspirationHandConsumed(hand_id="h1", chapter_id="c1"))
    await proj.catch_up()


def _checker(read, committer, events, mining_out):
    return ContinuityChecker(
        FakeRunner(ContinuityOutput()), FakeRunner(mining_out), read, committer, events,
    )


async def test_mining_prompt_lists_dealt_items(stack):
    events, proj, read, committer = stack
    await _seed(events, proj)
    mining_runner = FakeRunner(MinedFactsOutput())
    checker = ContinuityChecker(FakeRunner(ContinuityOutput()), mining_runner, read, committer, events)
    await checker.run_once()
    prompt = mining_runner.calls[0]["messages"][0]["content"]
    assert "glazier" in prompt and "a debt is called in early" in prompt


async def test_valid_inspiration_fact_records_uptake_verbatim(stack):
    events, proj, read, committer = stack
    await _seed(events, proj)
    out = MinedFactsOutput(inspiration_facts=[
        MinedInspirationFact(kind="professions", item="GLAZIER"),  # case-insensitive match
    ])
    await _checker(read, committer, events, out).run_once()
    await proj.catch_up()
    rows = await read.list_uptake("h1")
    assert [(r.kind, r.item, r.chapter_id) for r in rows] == [("professions", "glazier", "c1")]


async def test_undealt_item_is_dropped_not_retconned(stack):
    events, proj, read, committer = stack
    await _seed(events, proj)
    out = MinedFactsOutput(inspiration_facts=[
        MinedInspirationFact(kind="beats", item="a completely invented beat"),
    ])
    await _checker(read, committer, events, out).run_once()
    await proj.catch_up()
    assert await read.list_uptake() == []
    assert await read.list_retcon_requests(status="open") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agents/test_continuity_uptake.py -v`
Expected: FAIL with `ImportError: cannot import name 'MinedInspirationFact'`

- [ ] **Step 3: Extend the mining schema**

In `novelizer/agents/schemas.py`, before `MinedFactsOutput`:

```python
class MinedInspirationFact(BaseModel):
    """A dealt Muse inspiration item the prose visibly uses, extracted by the
    mining pass. `item` must repeat a dealt entry from the chapter's hand (the
    prompt lists them); a non-matching item is dropped at commit time with a
    logged info line, never a retcon — uptake is a health metric, not canon.
    Name uptake is NOT mined: it's recorded mechanically at CharacterKeeper
    mint time, so `kind` has no "names" arm.
    """

    kind: Literal["professions", "settings", "beats"]
    item: str
```

Add to `MinedFactsOutput`:

```python
    inspiration_facts: list[MinedInspirationFact] = Field(default_factory=list)
```

- [ ] **Step 4: Extend the ContinuityChecker**

In `novelizer/agents/continuity_checker.py`:

- Extend the schemas import with `MinedInspirationFact` and the events import with `InspirationUptakeRecorded`.
- Append to `MINING_SYSTEM_PROMPT` (inside the string, as a new final sentence):

```
If the prompt lists dealt inspiration items for this chapter, also report inspiration_facts:
each dealt item the prose visibly uses, with its kind and the item exactly as listed. Only
items from the dealt list are legal; never invent inspiration_facts.
```

- In `poll()`, add to the returned dict:

```python
            "hands_by_chapter": {
                h.consumed_chapter_id: h
                for h in await self._read.list_hands(status="consumed")
                if h.consumed_chapter_id
            },
```

- In `_mining_prompt`, before the `return`, add:

```python
        hand = ctx.get("hands_by_chapter", {}).get(chapter.id)
        dealt = ""
        if hand is not None:
            dealt = (
                f"\n\nDealt inspiration items for this chapter:\n"
                f"professions: {', '.join(hand.professions) or '(none)'}\n"
                f"settings: {', '.join(hand.settings) or '(none)'}\n"
                f"beats: {', '.join(hand.beats) or '(none)'}"
            )
```

and append `{dealt}` to the end of the returned f-string.

- In `_commit_mined_facts`, before the final `CHAPTER_MINED` commit, add:

```python
        hand = ctx.get("hands_by_chapter", {}).get(chapter_id)
        for fact in mined_out.inspiration_facts:
            if hand is None:
                logger.info(
                    "%s: mined inspiration fact %r for chapter %r with no consumed hand, dropped",
                    self.name, fact.item, chapter_id,
                )
                continue
            dealt_pool = {"professions": hand.professions, "settings": hand.settings,
                          "beats": hand.beats}[fact.kind]
            match = next((d for d in dealt_pool if d.lower() == fact.item.strip().lower()), None)
            if match is None:
                logger.info(
                    "%s: mined inspiration fact %r not in the dealt %s for chapter %r, dropped",
                    self.name, fact.item, fact.kind, chapter_id,
                )
                continue
            await self._committer.commit(
                self.name, EventType.INSPIRATION_UPTAKE_RECORDED, hand.id,
                InspirationUptakeRecorded(hand_id=hand.id, kind=fact.kind, item=match,
                                          chapter_id=chapter_id),
            )
```

- [ ] **Step 5: Run tests to verify they pass (plus checker regressions)**

Run: `uv run pytest tests/agents/test_continuity_uptake.py tests/agents/test_continuity_checker.py tests/agents/test_schemas.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add novelizer/agents/schemas.py novelizer/agents/continuity_checker.py tests/agents/test_continuity_uptake.py
git commit -m "feat(muse): mine profession/setting/beat uptake from chapter prose"
```

---

### Task 8: `:muse` director command, status report, full-suite verification

**Files:**
- Create: `novelizer/muse/report.py`
- Modify: `novelizer/director/commands.py` (`muse` branch in `dispatch`)
- Test: `tests/muse/test_report.py`, `tests/director/test_muse_command.py`

**Interfaces:**
- Consumes: `runtime.muse.deal_fresh_hand()` (Task 4), ReadStore accessors (Task 2), `EventType.INSPIRATION_HAND_SUPERSEDED` + `InspirationHandSuperseded` (Task 2).
- Produces: `muse_status_report(active, hands, uptake) -> str`; `uptake_summary(hands, uptake) -> str`; director commands `:muse` (status) and `:muse reroll` (supersede active hand + deal immediately).

- [ ] **Step 1: Write the failing tests**

```python
# tests/muse/test_report.py
from novelizer.muse.report import muse_status_report, uptake_summary
from novelizer.store.models import HandStatus, InspirationHandRecord, InspirationUptakeRecord


def _hand(hand_id, status=HandStatus.consumed):
    return InspirationHandRecord(
        id=hand_id, seed=1, corpus_version="2026.07", era="modern", status=status,
        names=["Doris Kimbrough"], professions=["glazier"], settings=["salvage yard"],
        beats=["a debt is called in early"],
    )


def test_uptake_summary_counts_landed_items():
    hands = [_hand("h1"), _hand("h2")]
    uptake = [
        InspirationUptakeRecord(hand_id="h1", kind="names", item="Doris Kimbrough"),
        InspirationUptakeRecord(hand_id="h2", kind="professions", item="glazier"),
    ]
    summary = uptake_summary(hands, uptake)
    assert "2/8" in summary and "25%" in summary  # 4 items per hand x 2 consumed hands


def test_uptake_summary_without_consumed_hands():
    assert "No consumed hands" in uptake_summary([_hand("h1", HandStatus.active)], [])


def test_status_report_shows_active_hand_and_uptake():
    active = _hand("h9", HandStatus.active)
    report = muse_status_report(active, [active, _hand("h1")], [])
    assert "Doris Kimbrough" in report and "glazier" in report
    assert "0/4" in report


def test_status_report_without_active_hand():
    assert "No active hand" in muse_status_report(None, [], [])
```

```python
# tests/director/test_muse_command.py
import os
import tempfile
import pytest
from novelizer.director.commands import dispatch
from novelizer.runtime import Runtime
from novelizer.settings import EffectiveSettings
from novelizer.store.models import HandStatus


@pytest.fixture
async def runtime():
    tmp = tempfile.mkdtemp()
    settings = EffectiveSettings(db_path=os.path.join(tmp, "world.db"),
                                 chroma_path=os.path.join(tmp, "chroma"))
    rt = Runtime(settings, runners={})
    await rt.start()
    yield rt
    await rt.close()


async def test_muse_status_before_any_hand(runtime):
    out = await dispatch(runtime, ":muse")
    assert "No active hand" in out


async def test_muse_reroll_supersedes_and_redeals(runtime):
    first = await runtime.muse.deal_fresh_hand()
    await runtime.projector.catch_up()
    out = await dispatch(runtime, ":muse reroll")
    await runtime.projector.catch_up()
    assert "Rerolled" in out
    assert (await runtime.read.get_hand(first.hand_id)).status == HandStatus.superseded
    active = await runtime.read.get_active_hand()
    assert active is not None and active.id != first.hand_id


async def test_muse_status_shows_active_hand(runtime):
    hand = await runtime.muse.deal_fresh_hand()
    await runtime.projector.catch_up()
    out = await dispatch(runtime, ":muse")
    assert hand.names[0] in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/muse/test_report.py tests/director/test_muse_command.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'novelizer.muse.report'`

- [ ] **Step 3: Write the report helpers**

```python
# novelizer/muse/report.py
from __future__ import annotations
from typing import Iterable, Optional
from novelizer.store.models import HandStatus, InspirationHandRecord, InspirationUptakeRecord


def _dealt_count(hand: InspirationHandRecord) -> int:
    return len(hand.names) + len(hand.professions) + len(hand.settings) + len(hand.beats)


def uptake_summary(
    hands: Iterable[InspirationHandRecord], uptake: Iterable[InspirationUptakeRecord]
) -> str:
    """The feature's health metric: beat draws are optional inspiration, so if
    this trends toward zero the director raises authority via settings rather
    than the feature silently doing nothing (spec: accepted risk, tracked)."""
    consumed = [h for h in hands if h.status == HandStatus.consumed]
    dealt = sum(_dealt_count(h) for h in consumed)
    if dealt == 0:
        return "No consumed hands yet — uptake unknown."
    used = len({(u.hand_id, u.kind, u.item) for u in uptake})
    return f"Uptake: {used}/{dealt} dealt items landed in prose across {len(consumed)} consumed hands ({100 * used // dealt}%)."


def muse_status_report(
    active: Optional[InspirationHandRecord],
    hands: Iterable[InspirationHandRecord],
    uptake: Iterable[InspirationUptakeRecord],
) -> str:
    if active is None:
        head = "No active hand (the Muse deals within its next cycle)."
    else:
        head = (
            f"Active hand [{active.id[:8]}] era={active.era}:\n"
            f"  names: {'; '.join(active.names)}\n"
            f"  professions: {'; '.join(active.professions)}\n"
            f"  settings: {'; '.join(active.settings)}\n"
            f"  beats: {'; '.join(active.beats)}"
        )
    return f"{head}\n{uptake_summary(hands, uptake)}"
```

- [ ] **Step 4: Add the dispatch branch**

In `novelizer/director/commands.py`:

- Add imports: extend the `novelizer.canon.events` import with `InspirationHandSuperseded` (the module already imports `EventType`), and add `from novelizer.muse.report import muse_status_report`.
- In `dispatch()`, before the final `return f"Unknown command: ..."`:

```python
    if cmd == "muse":
        if rest and rest[0].lower() == "reroll":
            active = await runtime.read.get_active_hand()
            if active is not None:
                await runtime.events.append(
                    EventType.INSPIRATION_HAND_SUPERSEDED, active.id,
                    InspirationHandSuperseded(hand_id=active.id),
                )
            # Deal without waiting for the projector: deal_fresh_hand doesn't
            # check for an active hand, and the projection sorts itself out
            # (the superseded event lands before the new drawn event).
            hand = await runtime.muse.deal_fresh_hand()
            return f"Rerolled. New hand: {'; '.join(hand.names)}"
        return muse_status_report(
            await runtime.read.get_active_hand(),
            await runtime.read.list_hands(),
            await runtime.read.list_uptake(),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/muse/test_report.py tests/director/test_muse_command.py -v`
Expected: all PASS

- [ ] **Step 6: Full-suite verification**

Run: `uv run pytest`
Expected: entire suite PASSES with zero warnings. If any pre-existing test asserts on the Author prompt string or the runtime roster, fix the assertion to include the new content — never weaken the new behavior to satisfy an old assertion.

- [ ] **Step 7: Commit**

```bash
git add novelizer/muse/report.py novelizer/director/commands.py tests/muse/test_report.py tests/director/test_muse_command.py
git commit -m "feat(muse): :muse status/reroll director command with uptake reporting"
```
