from __future__ import annotations
import asyncio
import copy
from dataclasses import dataclass
from enum import Enum
import chromadb
from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
from novelizer.store.models import (
    WorldEntry, Character, Chapter, ThemeRecord, ThreadRecord, SecretRecord,
    PromiseRecord, ChapterBriefRecord, ArcRecord,
)
from novelizer.text_chunk import chunk_prose


class EmptyIndexError(RuntimeError):
    """The semantic index holds no documents at all, so no query can be answered.

    Raised rather than returned-as-empty because the two outcomes mean opposite
    things to a caller: a populated index that matched nothing is EVIDENCE
    ("canon is silent on this"), while an index with nothing in it is the
    ABSENCE of evidence ("the search is dead"). Collapsing them into `[]` left
    every tooled agent believing canon was empty and retrying rephrasings of a
    query that could never succeed. The store is the only layer that knows its
    own document count, so it is the layer that must say so.
    """


class EmbedProbeFailure(str, Enum):
    """Why the embedding endpoint could not produce a vector.

    Distinguished rather than collapsed into one "embedding failed" because each
    one has a different remedy, and an operator told only that embedding is
    broken has to guess between a host that is down, a model name that does not
    exist there, and a key that was rejected.
    """

    unreachable = "unreachable"        # no HTTP response at all: refused, DNS, TLS
    timeout = "timeout"                # connected, then said nothing in time
    unauthorized = "unauthorized"      # 401/403: the key is wrong or absent
    no_such_model = "no_such_model"    # 404: no such model, or no embeddings route
    http_error = "http_error"          # any other HTTP status
    no_vectors = "no_vectors"          # a clean 200 carrying nothing usable


@dataclass(frozen=True)
class EmbedProbe:
    """The outcome of one embed round-trip against the configured endpoint.

    Facts only -- endpoint, model, what went wrong, the raw error. The REMEDY
    depends on settings this layer deliberately does not read (whether
    embed_base_url was set at all), so composing the operator-facing sentence is
    the caller's job; see novelizer.runtime.embed_probe_message, the single
    formatter both the runtime and `novelizer doctor` share.
    """

    endpoint: str
    model: str
    ok: bool
    failure: EmbedProbeFailure | None = None
    dimensions: int | None = None
    error: str = ""


# One short string: the probe exists to answer "can this endpoint embed at all",
# and a longer input would only cost latency at boot for no extra information.
_PROBE_TEXT = "novelizer embedding endpoint probe"

# Bound for one probe round-trip. An endpoint can accept a TCP connection and
# then never answer, so an unbounded probe would hold start() forever -- strictly
# worse than the dead index it is trying to report. Generous enough that a cold
# local model loading weights on first request still passes.
EMBED_PROBE_TIMEOUT_SECONDS = 15.0


def _single_attempt(ef):
    """The same embedding function, best-effort configured not to retry.

    A probe asks "is this endpoint alive right now", so it wants ONE attempt.
    The openai client chromadb builds retries twice with backoff by default,
    which is right for the indexer -- a transient blip must not cost an
    embedding -- and wrong here: against a refused connection it turns an
    instant answer into ~1.5s of boot latency and learns nothing extra.

    Shallow-copies the function so the store's own embedding function keeps the
    retries real upserts want. Entirely optional: any embedding function that
    does not expose an openai-style `client` (the injected test fake, a future
    chromadb refactor) is probed exactly as it is, because a probe must never
    fail over an attribute it merely hoped for.
    """
    client = getattr(ef, "client", None)
    if client is None or not hasattr(client, "with_options"):
        return ef
    try:
        probe_ef = copy.copy(ef)
        probe_ef.client = client.with_options(max_retries=0)
        return probe_ef
    except Exception:
        return ef


def _vector_width(vectors) -> int:
    """Width of the first returned vector, or 0 if the response carries none.

    Defensive by necessity: an OpenAI-compatible endpoint that is not really an
    embedding endpoint can answer 200 with any shape at all. Measured with len()
    only, never truthiness -- chromadb hands back numpy arrays, whose truth value
    is ambiguous and would raise here rather than report.
    """
    try:
        return len(vectors[0]) if len(vectors) else 0
    except (TypeError, IndexError, KeyError):
        return 0


def _classify_probe_error(exc: BaseException) -> EmbedProbeFailure:
    """Map an embed exception to a diagnosis using only duck-typed attributes.

    Every openai APIStatusError carries `status_code` (some wrappers carry it on
    `.response` instead), so classification never imports that SDK's exception
    hierarchy -- which is a transitive dependency of chromadb's embedding
    function, not something this store should be pinned to.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        # No HTTP response reached us at all: refused connection, DNS, TLS.
        return EmbedProbeFailure.unreachable
    if status in (401, 403):
        return EmbedProbeFailure.unauthorized
    if status == 404:
        return EmbedProbeFailure.no_such_model
    return EmbedProbeFailure.http_error


@dataclass
class SearchHit:
    kind: str
    id: str
    title: str
    distance: float


# Conservative char cap so embedding input stays under the embed model's
# token context window (e.g. nomic-embed-text's 2048 tokens): ~4 chars/token
# is a safe average for English prose, so 6000 chars stays well under 2048
# tokens even for token-dense text. Without this, any record whose text
# tokenizes past the window makes upsert raise every catch_up retry,
# permanently stalling canon indexing at that event (see seq 65 stall).
# Applies to every non-chapter collection: those records (world entry body,
# character backstory, etc.) are short-form and losing tail content past
# this cap is an acceptable trade for never stalling the indexer.
_MAX_EMBED_CHARS = 6000

# Chapters have no length bound (prose is free-form and routinely runs
# 12,000-24,000+ chars), so a flat cap would silently drop everything past
# the first ~1,500 words from search. Chapters are chunked into overlapping
# windows instead, each embedded and stored as its own vector, so the whole
# chapter stays searchable. Overlap keeps a match from being split across a
# chunk boundary and missed entirely.
_CHAPTER_CHUNK_CHARS = 6000
_CHAPTER_CHUNK_OVERLAP = 500


def _cap(text: str) -> str:
    return text[:_MAX_EMBED_CHARS]


def _chunk_id(chapter_id: str, index: int) -> str:
    return f"{chapter_id}#{index}"


class EmbeddingStore:
    def __init__(
        self,
        path: str,
        embed_model: str = "nomic-embed-text",
        base_url: str = "http://localhost:8080/v1",
        api_key: str = "not-needed",
        embedding_function=None,
    ) -> None:
        self._client = chromadb.PersistentClient(path=path)
        # `embedding_function` is the CI-testability seam: pass a deterministic
        # fake in tests so no suite run depends on a live embed endpoint. When
        # None (default/production), behavior is unchanged.
        ef = embedding_function if embedding_function is not None else OpenAIEmbeddingFunction(
            api_key=api_key, model_name=embed_model, api_base=base_url
        )
        # Kept so probe() can exercise the SAME embedding function the
        # collections use -- probing a separately-built one would be testing a
        # different thing than the indexer actually calls -- and so its report
        # can name the endpoint and model an operator has to go and fix.
        self._ef = ef
        self._embed_model = embed_model
        self._base_url = base_url
        self._world = self._client.get_or_create_collection("world_entries", embedding_function=ef)
        self._chars = self._client.get_or_create_collection("characters", embedding_function=ef)
        self._chapters = self._client.get_or_create_collection("chapters", embedding_function=ef)
        self._themes = self._client.get_or_create_collection("themes", embedding_function=ef)
        self._threads = self._client.get_or_create_collection("threads", embedding_function=ef)
        self._secrets = self._client.get_or_create_collection("secrets", embedding_function=ef)
        self._promises = self._client.get_or_create_collection("promises", embedding_function=ef)
        self._briefs = self._client.get_or_create_collection("briefs", embedding_function=ef)
        self._arcs = self._client.get_or_create_collection("arcs", embedding_function=ef)
        self._entities = self._client.get_or_create_collection("entities", embedding_function=ef)
        # Writes ARE reachable concurrently: agent intent commits
        # (novelizer/agents/intents.py) run as concurrent background tasks,
        # and CanonIndexer.catch_up() runs on every TUI tick. chromadb's
        # persistence is sqlite-backed, and unserialized cross-thread writes
        # risk "database is locked" errors (see commit 17387ac). This lock
        # serializes writes across all callers; asyncio.to_thread is what
        # keeps the event loop responsive while a write is in flight.
        self._write_lock = asyncio.Lock()

    def close(self) -> None:
        """Release the Chroma client this store constructed.

        Writes are already durable (the PersistentClient auto-flushes), so this
        is about the *handle*, not the data. chromadb reference-counts one
        sqlite-backed System per persist path and only tears it down when the
        last client closes; skipping that leaks the sqlite handle plus the
        client's worker threads for the life of the process. Chroma's own
        Client.close() docstring calls this "particularly important for
        PersistentClient to avoid SQLite file locking issues" -- and it is: with
        enough leaked systems in one process, a later client blocks inside
        Chroma's native create_collection and never returns.

        Idempotent, because callers close from `finally` blocks and may close
        twice on an error path.
        """
        client, self._client = self._client, None
        if client is not None:
            client.close()

    async def upsert_world_entry(self, entry: WorldEntry) -> None:
        text = _cap(f"{entry.title}\n{entry.body}")
        async with self._write_lock:
            await asyncio.to_thread(
                self._world.upsert, ids=[entry.id], documents=[text], metadatas=[{"title": entry.title}]
            )

    async def upsert_character(self, char: Character) -> None:
        text = _cap(f"{char.name}\n{char.traits}\n{char.backstory}")
        async with self._write_lock:
            await asyncio.to_thread(
                self._chars.upsert, ids=[char.id], documents=[text], metadatas=[{"name": char.name}]
            )

    async def upsert_chapter(self, chapter: Chapter) -> None:
        chunks = chunk_prose(chapter.prose, _CHAPTER_CHUNK_CHARS, _CHAPTER_CHUNK_OVERLAP)
        ids = [_chunk_id(chapter.id, i) for i in range(len(chunks))]
        metadatas = [
            {"title": chapter.title, "chapter_id": chapter.id, "chunk_index": i}
            for i in range(len(chunks))
        ]
        async with self._write_lock:
            await asyncio.to_thread(self._replace_chapter_chunks_sync, chapter.id, ids, chunks, metadatas)

    def _replace_chapter_chunks_sync(
        self, chapter_id: str, ids: list[str], chunks: list[str], metadatas: list[dict]
    ) -> None:
        # A revision can shrink the chunk count (shorter prose), which would
        # otherwise leave stale trailing chunks from the old, longer version
        # behind after upsert only overwrites the ids it's given.
        existing = self._chapters.get(where={"chapter_id": chapter_id}, include=[])
        stale_ids = [i for i in existing.get("ids", []) if i not in set(ids)]
        if stale_ids:
            self._chapters.delete(ids=stale_ids)
        self._chapters.upsert(ids=ids, documents=chunks, metadatas=metadatas)

    async def upsert_theme(self, theme: ThemeRecord) -> None:
        async with self._write_lock:
            await asyncio.to_thread(
                self._themes.upsert, ids=[theme.id], documents=[_cap(theme.title)], metadatas=[{"title": theme.title}]
            )

    async def upsert_thread(self, thread: ThreadRecord) -> None:
        text = _cap(f"{thread.name}\n{thread.last_note}" if thread.last_note else thread.name)
        async with self._write_lock:
            await asyncio.to_thread(
                self._threads.upsert, ids=[thread.id], documents=[text], metadatas=[{"title": thread.name}]
            )

    async def upsert_secret(self, secret: SecretRecord) -> None:
        async with self._write_lock:
            await asyncio.to_thread(
                self._secrets.upsert, ids=[secret.id], documents=[_cap(secret.title)], metadatas=[{"title": secret.title}]
            )

    async def upsert_promise(self, promise: PromiseRecord) -> None:
        text = _cap(f"{promise.name}\n{promise.description}\n{promise.last_note}\nstate: {promise.state}".strip())
        async with self._write_lock:
            await asyncio.to_thread(
                self._promises.upsert, ids=[promise.id], documents=[text],
                metadatas=[{"title": promise.name}],
            )

    async def upsert_brief(self, brief: ChapterBriefRecord) -> None:
        text = _cap(f"{brief.goal}\n{brief.synopsis}\nstatus: {brief.status}".strip())
        async with self._write_lock:
            await asyncio.to_thread(
                self._briefs.upsert, ids=[brief.id], documents=[text],
                metadatas=[{"title": brief.goal}],
            )

    async def upsert_arc(self, arc: ArcRecord) -> None:
        text = f"{arc.character_id}\n{arc.lie}\n{arc.truth}\n{arc.want}\n{arc.need}".strip()
        if arc.resolved:
            text = f"{text}\nresolved: {arc.outcome}".strip()
        text = _cap(text)
        async with self._write_lock:
            await asyncio.to_thread(
                self._arcs.upsert, ids=[arc.id], documents=[text],
                metadatas=[{"title": f"Arc: {arc.character_id}"}],
            )

    async def upsert_entity(self, entity_id: str, name: str, detail: str) -> None:
        text = _cap(f"{name}\n{detail}")
        async with self._write_lock:
            await asyncio.to_thread(
                self._entities.upsert, ids=[entity_id], documents=[text],
                metadatas=[{"title": name}],
            )

    async def delete_entity(self, entity_id: str) -> None:
        await self.delete(entity_id, "entities")

    async def delete(self, entity_id: str, collection: str) -> None:
        col = {
            "world_entries": self._world,
            "characters": self._chars,
            "chapters": self._chapters,
            "themes": self._themes,
            "threads": self._threads,
            "secrets": self._secrets,
            "promises": self._promises,
            "briefs": self._briefs,
            "arcs": self._arcs,
            "entities": self._entities,
        }[collection]
        # Offload to a thread: this is a synchronous chromadb/HTTP call that
        # would otherwise block the whole asyncio loop (see upsert_* -- same
        # reasoning: CPT-M3 now runs catch-up every TUI tick, so a blocking
        # call here freezes rendering each cycle whenever the embed endpoint
        # is unreachable, not just once at startup). The lock serializes this
        # against concurrent upserts/deletes from agent commits and catch_up.
        async with self._write_lock:
            if collection == "chapters":
                # Chapters are stored as multiple chunk ids ("{id}#0", ...),
                # never as entity_id itself -- delete by the chapter_id
                # metadata all of a chapter's chunks share instead.
                await asyncio.to_thread(self._delete_chapter_chunks_sync, entity_id)
            else:
                await asyncio.to_thread(col.delete, ids=[entity_id])

    def _delete_chapter_chunks_sync(self, chapter_id: str) -> None:
        existing = self._chapters.get(where={"chapter_id": chapter_id}, include=[])
        ids = existing.get("ids", [])
        if ids:
            self._chapters.delete(ids=ids)

    async def query_world_entries(self, query: str, n: int = 5) -> list[WorldEntry]:
        if self._world.count() == 0:
            return []
        results = self._world.query(query_texts=[query], n_results=min(n, self._world.count()))
        entries = []
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            entries.append(WorldEntry(id=doc_id, title=meta.get("title", ""), body=results["documents"][0][i]))
        return entries

    async def query_characters(self, query: str, n: int = 5) -> list[Character]:
        if self._chars.count() == 0:
            return []
        results = self._chars.query(query_texts=[query], n_results=min(n, self._chars.count()))
        chars = []
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            chars.append(Character(id=doc_id, name=meta.get("name", "")))
        return chars

    async def query_themes(self, query: str, n: int = 5) -> list[tuple[str, float]]:
        """Returns (theme_id, distance) pairs, closest first -- distance is
        whatever metric the collection's embedding_function/space uses
        (chromadb default: squared L2 for OpenAIEmbeddingFunction). Callers
        that need a similarity threshold (e.g. near-duplicate suggestion)
        compare against this raw distance rather than a hydrated record,
        since the score itself is the point of this query.
        """
        if self._themes.count() == 0:
            return []
        results = self._themes.query(query_texts=[query], n_results=min(n, self._themes.count()))
        ids = results["ids"][0]
        distances = results["distances"][0]
        return list(zip(ids, distances))

    async def query_chapters(self, query: str, n: int = 5) -> list[Chapter]:
        if self._chapters.count() == 0:
            return []
        # Over-fetch chunks: a chapter's several best-matching chunks would
        # otherwise crowd out other chapters before dedup narrows to n.
        fetch_n = min(n * 4, self._chapters.count())
        results = self._chapters.query(query_texts=[query], n_results=fetch_n)
        best_by_chapter: dict[str, Chapter] = {}
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            chapter_id = meta.get("chapter_id", doc_id)
            if chapter_id not in best_by_chapter:
                best_by_chapter[chapter_id] = Chapter(
                    id=chapter_id, title=meta.get("title", ""), prose=results["documents"][0][i]
                )
        return list(best_by_chapter.values())[:n]

    def _collections_by_kind(self) -> dict:
        return {
            "chapter": self._chapters,
            "character": self._chars,
            "world": self._world,
            "thread": self._threads,
            "secret": self._secrets,
            "theme": self._themes,
            "promise": self._promises,
            "brief": self._briefs,
            "arc": self._arcs,
            "entity": self._entities,
        }

    async def probe(self, timeout: float = EMBED_PROBE_TIMEOUT_SECONDS) -> EmbedProbe:
        """Embed one short string and report whether a usable vector came back.

        The cheapest possible answer to "will this index ever fill up?". Every
        other signal reports a dead embedding endpoint as healthy: upsert
        failures are swallowed by the drain's never-raise contract, the poison
        ladder abandons each event after its budget, the cursor ends up past the
        whole backlog, and lag() then reads 0 forever.

        Never raises -- a probe that crashed the caller would be a worse failure
        than the one it reports -- and never blocks longer than `timeout`.
        """
        try:
            vectors = await asyncio.wait_for(
                asyncio.to_thread(_single_attempt(self._ef), [_PROBE_TEXT]), timeout
            )
        except asyncio.TimeoutError:
            # The abandoned worker thread cannot be killed (asyncio.to_thread
            # threads are not cancellable), but it is detached and daemon-free
            # bookkeeping only: the caller is released on time, which is the
            # property that matters here.
            return self._probe_failed(EmbedProbeFailure.timeout,
                                      f"TimeoutError: no response within {timeout}s")
        except Exception as e:
            return self._probe_failed(_classify_probe_error(e), f"{type(e).__name__}: {e}")
        dimensions = _vector_width(vectors)
        if not dimensions:
            # A clean 200 carrying nothing usable: raises nothing, so no
            # try/except anywhere would ever have caught this one.
            return self._probe_failed(EmbedProbeFailure.no_vectors,
                                      "the endpoint returned no usable vector")
        return EmbedProbe(endpoint=self._base_url, model=self._embed_model,
                          ok=True, dimensions=dimensions)

    def _probe_failed(self, failure: EmbedProbeFailure, error: str) -> EmbedProbe:
        return EmbedProbe(endpoint=self._base_url, model=self._embed_model,
                          ok=False, failure=failure, error=error)

    def _document_count_sync(self) -> int:
        return sum(col.count() for col in self._collections_by_kind().values())

    async def document_count(self) -> int:
        """Total vectors stored across every collection.

        The one number that tells an operator whether the semantic index is
        alive: 0 means every search_canon call is a guaranteed miss, which is
        invisible from the outside (search just answers, wrongly). Offloaded to
        a thread like every other chromadb read -- count() is a sqlite query.
        """
        return await asyncio.to_thread(self._document_count_sync)

    @staticmethod
    def _search_one_collection_sync(col, query: str, n: int) -> list[dict]:
        """Blocking count+query for a single collection -- runs inside
        asyncio.to_thread so query()'s HTTP embed call never blocks the
        event loop (reads are lock-free: no write contention to serialize)."""
        if col.count() == 0:
            return []
        return col.query(query_texts=[query], n_results=min(n, col.count()))

    async def search(self, query: str, kinds: list[str] | None = None, n: int = 8) -> list[SearchHit]:
        by_kind = self._collections_by_kind()
        wanted = list(by_kind) if kinds is None else kinds
        unknown = [k for k in wanted if k not in by_kind]
        if unknown:
            # Kind validation stays ahead of the emptiness check: a bad kind is
            # a caller mistake with actionable feedback, and reporting a dead
            # index instead would hide it.
            raise ValueError(f"Unknown kinds: {unknown}. Valid: {sorted(by_kind)}")
        # Emptiness is measured over the WHOLE index, not over `kinds`: an empty
        # kinds-subset of a populated index is a real, informative miss, whereas
        # a zero-document index cannot answer anything.
        if await self.document_count() == 0:
            raise EmptyIndexError(
                "the semantic index holds no documents; nothing has been indexed yet"
            )
        hits: list[SearchHit] = []
        seen_chapter_ids: set[str] = set()
        for kind in wanted:
            col = by_kind[kind]
            results = await asyncio.to_thread(self._search_one_collection_sync, col, query, n)
            if not results:
                continue
            for i, doc_id in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] or {}
                title = meta.get("title") or meta.get("name") or ""
                # Chapters are stored as multiple chunks per chapter; several
                # chunks of the same chapter can each match, so report the
                # chapter once at its best (lowest-distance, hence first
                # encountered -- chromadb returns results distance-sorted)
                # chunk's score instead of one SearchHit per chunk.
                if kind == "chapter":
                    doc_id = meta.get("chapter_id", doc_id)
                    if doc_id in seen_chapter_ids:
                        continue
                    seen_chapter_ids.add(doc_id)
                hits.append(SearchHit(kind=kind, id=doc_id, title=title,
                                      distance=results["distances"][0][i]))
        hits.sort(key=lambda h: h.distance)
        return hits[:n]
