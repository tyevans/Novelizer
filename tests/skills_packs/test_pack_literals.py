"""Guard the literal paths and schema field names embedded in the skills packs.

The packs are agent-visible *instructional* material: eleven tooled agents read
them. A stale literal in a pack does not fail anything -- it quietly teaches
every agent something false while the suite stays green. Rename
``references/arc-invariants.md`` and five packs cite a dead path; rename
``BriefIntent.value_shift`` and three packs tell agents to fill a field that no
longer exists.

Two sweeps, both derived at collection time from disk and from the models --
never from a hand-kept list of the things being guarded, which is the drift
shape this codebase keeps getting bitten by (a hand-listed PACK_NAMES once hid
the Curator; AGENT_NAMES was 9-of-13; the irony-ledger path was hardcoded here
until it was bound to IRONY_LEDGER_PATH).

Sweep A -- every ``references/...`` path a pack cites resolves on disk.
Sweep B -- every schema name a pack cites resolves against the real models.

Ownership resolution, and its deliberate limit
----------------------------------------------
Sweep B has two tiers, because the packs cite schema names two ways:

* **Ownership-pinned**: ``BriefIntent.value_shift``. The pack itself says which
  model owns the field, so the assertion is exact -- the class must exist *and*
  the field must be on it.
* **Union-membership**: a bare ``value_shift``. The pack does not say who owns
  it, so the strongest honest assertion is that *some* real model has such a
  field. Narrowing this to "a model this pack names by class name" was tried
  and rejected on evidence: it flags 19 legitimate fields, because `pacing`
  names no model class at all and `output-conventions` discusses fields
  generically across many. A guard that cries wolf gets muted, so this tier
  stays at union membership and the weaker bite is accepted knowingly.

What the union tier does and does not prove (measured)
------------------------------------------------------
The limit above was also established empirically, by trying to make the union
tier fail rather than by reasoning about it. Renaming
``EditorVerdict.secret_plants`` to ``secret_seeds`` did NOT fail the sweep.
Renaming it on ``EditorVerdict`` *and* ``ChapterDraft`` did not fail either. It
failed only once the name was retired from all THREE carriers -- ``ChatReply``
has it too -- and then it caught TWO packs, including ``output-conventions``,
which had not been checked by hand.

So the guarantee is exactly this: the union tier catches a name **retired from
the codebase**, which is the case that actually bit this project
(``knowledge_intents``, gone in the SecretPlant/SecretCitation split), and NOT
per-model drift. A pack telling the Keeper to fill a field only the Author
carries would pass the union tier. The ownership-pinned tier catches that only
where the prose names the owner, and the Plotter-boundary test in
tests/agents/test_secrets_skill_pack.py is the targeted guard where it matters
most.

Two failed attempts before the third worked is why this is recorded here rather
than in a review comment: anyone trusting this guard needs to know what it does
not prove.

Enum values are excluded structurally, not by taste: ``EXCLUDED_LITERALS`` is
every string in every ``Literal[...]`` on every candidate model. ``yes_but``,
``no_and`` and ``red_herring`` are legitimate pack content and would otherwise
be ~33 phantom failures.

Craft vocabulary that happens to sit in backticks (``decision``, ``disaster``)
cannot be distinguished from a field name by markup -- the packs backtick both.
Those five words are named in ``CRAFT_VOCABULARY`` with the direction of error
chosen on purpose: the sweep fails closed, so a *new* unresolvable token is
loud and a human classifies it, and a companion test fails if one of the five
ever becomes a real field name (which would mean the exception is now hiding a
live citation).
"""

from __future__ import annotations

import importlib
import importlib.resources
import pkgutil
import re
import typing

import pytest
from pydantic import BaseModel

# Packages swept for candidate models. A package list, not a model list: adding
# a model to any of these is covered without touching this file.
MODEL_PACKAGES = ("novelizer.agents", "novelizer.store", "novelizer.canon", "novelizer.brain")

_HIDDEN = {"__pycache__"}

#: Words the packs backtick as *craft* vocabulary, not as schema fields. Each is
#: prose from the technique being taught, verified in context:
#:   decision, reaction, disaster -- Scene/Sequel beats (scene-sequel)
#:   reference                    -- a secret reference in prose (secrets-and-reveals)
#:   delta                        -- the tension tolerance band (pacing)
#: If one of these becomes a real field name, the test below goes red so the
#: exception cannot silently start hiding a live field citation.
CRAFT_VOCABULARY = frozenset({"decision", "reaction", "disaster", "reference", "delta"})

_BARE_TOKEN = re.compile(r"^[a-z][a-z0-9_]*$")
_MODEL_NAME = re.compile(r"^[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+$")
_BACKTICKED = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*)`")
#: Both citation shapes the packs use: cross-pack ``outlining/references/x.md``
#: and self-relative ``references/x.md``.
_REF_PATH = re.compile(r"(?:([a-z0-9-]+)/)?references/([A-Za-z0-9._-]+\.md)")


def _packs_root():
    return importlib.resources.files("novelizer.skills_packs")


PACK_NAMES = sorted(
    entry.name for entry in _packs_root().iterdir() if entry.is_dir() and entry.name not in _HIDDEN
)


def _pack_markdown(pack_name: str) -> dict[str, str]:
    """Every .md file in a pack, keyed by a path readable in an assertion."""
    root = _packs_root() / pack_name
    texts: dict[str, str] = {}
    for entry in root.iterdir():
        if entry.is_file() and entry.name.endswith(".md"):
            texts[f"{pack_name}/{entry.name}"] = entry.read_text(encoding="utf-8")
        elif entry.is_dir() and entry.name not in _HIDDEN:
            for child in entry.iterdir():
                if child.is_file() and child.name.endswith(".md"):
                    texts[f"{pack_name}/{entry.name}/{child.name}"] = child.read_text(
                        encoding="utf-8"
                    )
    return texts


def _walk_models() -> tuple[dict[str, type[BaseModel]], set[str]]:
    """Every pydantic model and every public module-level symbol in MODEL_PACKAGES."""
    models: dict[str, type[BaseModel]] = {}
    symbols: set[str] = set()
    for pkg_name in MODEL_PACKAGES:
        pkg = importlib.import_module(pkg_name)
        modules = [pkg]
        for info in pkgutil.walk_packages(pkg.__path__, pkg_name + "."):
            modules.append(importlib.import_module(info.name))
        for module in modules:
            for name, obj in vars(module).items():
                if not name.startswith("_"):
                    symbols.add(name)
                if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
                    models[obj.__name__] = obj
    return models, symbols


MODELS, CODE_SYMBOLS = _walk_models()


def _literal_strings() -> frozenset[str]:
    """Every string in every Literal[...] anywhere in a candidate model's fields.

    Structural, so enum values can never be mistaken for field names.
    """
    values: set[str] = set()
    for model in MODELS.values():
        for field in model.model_fields.values():
            stack = [field.annotation]
            while stack:
                annotation = stack.pop()
                if typing.get_origin(annotation) is typing.Literal:
                    values.update(a for a in typing.get_args(annotation) if isinstance(a, str))
                else:
                    stack.extend(typing.get_args(annotation))
    return frozenset(values)


EXCLUDED_LITERALS = _literal_strings()

#: field name -> the models that declare it. Union membership for tier 2.
FIELD_OWNERS: dict[str, list[str]] = {}
for _name, _model in sorted(MODELS.items()):
    for _field in _model.model_fields:
        FIELD_OWNERS.setdefault(_field, []).append(_name)


def _cited_reference_paths(pack_name: str) -> list[tuple[str, str, str]]:
    """(citing file, cited pack, cited reference filename) for one pack's text."""
    out: list[tuple[str, str, str]] = []
    for source, text in _pack_markdown(pack_name).items():
        for owner, filename in _REF_PATH.findall(text):
            out.append((source, owner or pack_name, filename))
    return out


def _backticked_tokens(pack_name: str) -> dict[str, str]:
    """token -> the pack file it was seen in."""
    seen: dict[str, str] = {}
    for source, text in _pack_markdown(pack_name).items():
        for token in _BACKTICKED.findall(text):
            seen.setdefault(token, source)
    return seen


# --------------------------------------------------------------------------
# The derivations themselves. A sweep that silently finds nothing is a sweep
# that passes for the wrong reason -- these are the "emptiness is not an
# answer" guards.
# --------------------------------------------------------------------------


def test_derivations_are_not_empty() -> None:
    assert PACK_NAMES, "pack derivation found no packs -- it is broken"
    assert len(MODELS) > 50, f"model walk found only {len(MODELS)} models -- it is broken"
    assert EXCLUDED_LITERALS, "Literal-value extraction found nothing -- it is broken"
    assert FIELD_OWNERS, "field extraction found nothing -- it is broken"
    citations = sum(len(_cited_reference_paths(p)) for p in PACK_NAMES)
    assert citations >= 20, f"reference-path scan found only {citations} citations -- it is broken"
    tokens = sum(len(_backticked_tokens(p)) for p in PACK_NAMES)
    assert tokens >= 100, f"backtick scan found only {tokens} tokens -- it is broken"


# --------------------------------------------------------------------------
# Sweep A -- cited reference paths resolve on disk
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pack_name", PACK_NAMES)
def test_every_cited_reference_path_resolves_on_disk(pack_name: str) -> None:
    for source, owner, filename in _cited_reference_paths(pack_name):
        assert owner in PACK_NAMES, (
            f"{source} cites {owner}/references/{filename} but there is no pack named {owner!r}"
        )
        target = _packs_root() / owner / "references" / filename
        assert target.is_file(), (
            f"{source} cites {owner}/references/{filename}, which does not exist -- "
            "the citing pack is teaching every agent that reads it a dead path"
        )


def test_every_reference_file_on_disk_is_cited_by_some_pack() -> None:
    """The other direction: a reference file nobody points at is guidance the
    agents are shipped and never told to read."""
    cited = {(owner, filename) for p in PACK_NAMES for _, owner, filename in _cited_reference_paths(p)}
    for pack_name in PACK_NAMES:
        refs = _packs_root() / pack_name / "references"
        for entry in refs.iterdir():
            if entry.is_file() and entry.name.endswith(".md"):
                assert (pack_name, entry.name) in cited, (
                    f"{pack_name}/references/{entry.name} exists but no pack cites it"
                )


# --------------------------------------------------------------------------
# Sweep B -- cited schema names resolve against the real models
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pack_name", PACK_NAMES)
def test_every_qualified_field_citation_resolves_to_its_owner(pack_name: str) -> None:
    """Tier 1: `Model.field`. The pack names the owner, so pin it exactly."""
    for token, source in sorted(_backticked_tokens(pack_name).items()):
        head, sep, tail = token.partition(".")
        if not sep or not _MODEL_NAME.match(head):
            continue  # `SKILL.md`, `tension_target.py`, dotted module paths
        assert head in MODELS, (
            f"{source} cites `{token}` but there is no model named {head!r} -- "
            "the class was renamed or removed and the pack still names it"
        )
        assert tail in MODELS[head].model_fields, (
            f"{source} cites `{token}` but {head} has no field {tail!r} "
            f"(it has: {sorted(MODELS[head].model_fields)})"
        )


@pytest.mark.parametrize("pack_name", PACK_NAMES)
def test_every_cited_model_name_is_a_real_model(pack_name: str) -> None:
    for token, source in sorted(_backticked_tokens(pack_name).items()):
        head = token.partition(".")[0]
        if not _MODEL_NAME.match(head):
            continue
        assert head in MODELS, (
            f"{source} cites model `{head}`, which does not exist in {MODEL_PACKAGES}"
        )


@pytest.mark.parametrize("pack_name", PACK_NAMES)
def test_every_bare_schema_token_resolves_to_a_field_or_a_declared_non_field(
    pack_name: str,
) -> None:
    """Tier 2: a bare backticked ``snake_case`` token must be one of four things.

    A real field on some model; a Literal value (legitimate pack content); a
    public novelizer symbol (e.g. ``tension_deviations``, a function the pack
    names); or declared craft vocabulary. Anything else is either a renamed
    field the pack still teaches, or a new craft word that needs one honest
    line in CRAFT_VOCABULARY -- both worth a human's attention.
    """
    for token, source in sorted(_backticked_tokens(pack_name).items()):
        if "." in token or not _BARE_TOKEN.match(token):
            continue
        if token in FIELD_OWNERS or token in EXCLUDED_LITERALS:
            continue
        if token in CODE_SYMBOLS or token in CRAFT_VOCABULARY:
            continue
        pytest.fail(
            f"{source} backticks `{token}`, which is not a field on any model in "
            f"{MODEL_PACKAGES}, not a Literal value, and not a known novelizer symbol. "
            "Either a schema field was renamed and this pack still teaches the old "
            "name, or it is craft vocabulary that belongs in CRAFT_VOCABULARY."
        )


def test_craft_vocabulary_exceptions_are_not_real_field_names() -> None:
    """The negative half, in the spirit of test_outlining_framework_keys.py.

    Each exception exists because the word is prose. If one becomes a real
    field, the exception stops being an exception and starts being a hole: the
    packs' use of it would no longer be checkable against the model.
    """
    for word in sorted(CRAFT_VOCABULARY):
        assert word not in FIELD_OWNERS, (
            f"{word!r} is excused as craft vocabulary but is now a real field on "
            f"{FIELD_OWNERS.get(word)} -- drop it from CRAFT_VOCABULARY so the "
            "packs' citations of it are checked again"
        )


def test_craft_vocabulary_has_no_dead_entries() -> None:
    """An exception for a word no pack uses any more is stale licence."""
    all_tokens = {t for pack_name in PACK_NAMES for t in _backticked_tokens(pack_name)}
    for word in sorted(CRAFT_VOCABULARY):
        assert word in all_tokens, (
            f"{word!r} is excused as craft vocabulary but no pack backticks it any more"
        )
