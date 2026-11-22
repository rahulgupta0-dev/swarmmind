"""Evidence ledger — stable source IDs, citation validation and grounding.

This module is deliberately dependency-free beyond Pydantic (already a
project dependency).  It provides the three public contracts used across
SwarmMind v0.4:

* :class:`SourceRecord` — one citable piece of evidence.
* :class:`Claim` — a statement plus the sources that support it.
* :class:`EvidenceLedger` — assigns stable ``S1``/``S2``... IDs, deduplicates
  sources, renders a bounded prompt block, validates report citations and
  computes a grounding summary.

All public methods are deterministic and safe to call repeatedly.  No LLM
self-judgement is involved.
"""

from __future__ import annotations

import copy
from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = ["SourceRecord", "Claim", "EvidenceLedger"]

_VALID_KINDS = frozenset({"rag", "web", "image", "model"})
_VALID_STATUSES = frozenset({"supported", "unverified", "contradicted"})


class SourceRecord(BaseModel):
    """A single citable source.

    Attributes:
        id: Stable identifier within a run (``"S1"``, ``"S2"``, ...).  Assigned
            by :class:`EvidenceLedger`; callers normally leave it empty.
        kind: Provenance of the source.
        title: Human-readable label (synthesised for RAG sources).
        url: URL, file path, or ``""`` for pure model knowledge.
        snippet: Exact supporting text (capped around 1200 chars upstream).
        doc_id: RAG document identifier.
        chunk: RAG chunk index, or ``-1`` when not applicable.
    """

    id: str = ""
    kind: Literal["rag", "web", "image", "model"]
    title: str
    url: str = ""
    snippet: str = ""
    doc_id: str = ""
    chunk: int = -1


class Claim(BaseModel):
    """A factual claim and the source IDs cited in its support.

    Attributes:
        text: The claim itself.
        source_ids: IDs of :class:`SourceRecord` entries backing the claim.
        status: Grounding status assigned by the worker/synthesiser.
    """

    text: str
    source_ids: list[str] = Field(default_factory=list)
    status: Literal["supported", "unverified", "contradicted"] = "unverified"


class EvidenceLedger:
    """In-memory registry of sources for a single research run.

    IDs are assigned monotonically (``S1``, ``S2``, ...) in insertion order
    and are *never* reassigned.  Web/image sources deduplicate by ``url`` and
    RAG sources by ``doc_id:chunk``; sources without a stable key (e.g. model
    knowledge with an empty URL) always receive a fresh ID.

    The ledger is intentionally stateful per run: one instance should be
    created for one query and reused across that query's workers and report.
    """

    def __init__(self) -> None:
        # id -> record.  Insertion order is the citation order.
        self._sources: dict[str, SourceRecord] = {}
        # dedupe key -> id
        self._by_key: dict[tuple[Any, ...], str] = {}
        self._next = 1

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _dedupe_key(source: SourceRecord) -> tuple[Any, ...] | None:
        """Return the dedupe key for *source*, or ``None`` if it has none."""
        if source.kind == "rag":
            if source.doc_id or source.chunk != -1:
                return ("rag", source.doc_id, source.chunk)
            return None
        if source.url:
            return ("url", source.url)
        return None

    @staticmethod
    def _coerce_source(raw: SourceRecord | dict | str) -> SourceRecord:
        """Build a :class:`SourceRecord` from a model, dict or legacy string.

        Any incoming ``id`` is discarded — the ledger always assigns its own
        stable ID.  Missing/partial fields get safe defaults so legacy mocks
        do not crash validation.  A plain string is treated as a legacy URL.
        """
        if isinstance(raw, SourceRecord):
            return raw.model_copy(deep=True)

        if isinstance(raw, str):
            return SourceRecord(kind="web", title=raw, url=raw, snippet="")

        if isinstance(raw, dict):
            data = dict(raw)
            data.pop("id", None)  # never trust caller-supplied IDs
            kind = data.get("kind")
            if kind not in _VALID_KINDS:
                kind = "model"
            data["kind"] = kind
            if not data.get("title"):
                data["title"] = (
                    data.get("url")
                    or (f"{data.get('doc_id')}:{data.get('chunk')}" if kind == "rag" else "")
                    or "Untitled source"
                )
            # Drop unexpected keys defensively; Pydantic ignores extras by
            # default, but explicit filtering keeps the record clean.
            allowed = {"kind", "title", "url", "snippet", "doc_id", "chunk"}
            return SourceRecord.model_validate({k: v for k, v in data.items() if k in allowed})

        raise TypeError(f"Cannot interpret {type(raw).__name__!r} as a source")

    def _register(self, source: SourceRecord) -> SourceRecord:
        """Register *source* and return the stored (ID-bearing) record."""
        key = self._dedupe_key(source)
        if key is not None and key in self._by_key:
            # Same content: reuse the original ID and keep its original data.
            return self._sources[self._by_key[key]]

        new_id = f"S{self._next}"
        self._next += 1
        source.id = new_id
        self._sources[new_id] = source
        if key is not None:
            self._by_key[key] = new_id
        return source

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, source: SourceRecord | dict | str) -> SourceRecord:
        """Register one source and return its ID-bearing record.

        This is the public way for callers to obtain the stable ID assigned to
        a source (e.g. when building ``sources_cited`` for a worker).
        """
        return self._register(self._coerce_source(source))

    def add_sources(self, sources: list[SourceRecord | dict]) -> list[SourceRecord]:
        """Add sources, assigning stable IDs and deduplicating by content.

        Web/image sources deduplicate by ``url``; RAG sources by
        ``doc_id:chunk``.  A duplicate keeps the ID and content of its first
        occurrence — an existing ID is never reassigned to different content.

        Returns the stored (ID-bearing) records in input order so callers can
        build citation lists without re-querying the ledger.
        """
        return [self.register(raw) for raw in sources]

    def get(self, source_id: str) -> SourceRecord | None:
        """Return a copy of the source with *source_id*, or ``None``.

        A copy is returned so callers cannot mutate ledger state by accident.
        """
        record = self._sources.get(source_id)
        return record.model_copy(deep=True) if record is not None else None

    def as_prompt_block(self, max_chars: int = 6000) -> str:
        """Render all sources as a bounded, citation-labelled text block.

        Each line contains the id, kind, title and snippet.  If the rendered
        text exceeds *max_chars* it is truncated with a clear marker and the
        result is always ``<= max_chars`` characters long.
        """
        if max_chars <= 0:
            return ""

        lines: list[str] = []
        for record in self._sources.values():
            title = " ".join(record.title.split())
            snippet = " ".join(record.snippet.split())
            lines.append(f"[{record.id}|{record.kind}] {title}: {snippet}")

        block = "\n".join(lines)
        if len(block) <= max_chars:
            return block

        marker = "\n... [truncated]"
        keep = max_chars - len(marker)
        if keep <= 0:
            return marker[:max_chars]
        return block[:keep] + marker

    def grounding_summary(self, report: dict) -> dict:
        """Summarise how claims in *report* are grounded.

        ``citation_coverage`` is the fraction of claims carrying at least one
        source ID known to this ledger (or declared in ``report["sources"]``).
        It is ``0.0`` when there are no claims, and always within ``[0, 1]``.
        """
        claims = report.get("claims") or []
        if not isinstance(claims, list):
            claims = [claims]

        # IDs known to be real, from the ledger and the report's own sources.
        known_ids = set(self._sources)
        for src in report.get("sources") or []:
            if isinstance(src, SourceRecord):
                known_ids.add(src.id)
            elif isinstance(src, dict) and src.get("id"):
                known_ids.add(str(src["id"]))
            elif isinstance(src, str):
                known_ids.add(src)

        counts: dict[str, int] = {"supported": 0, "unverified": 0, "contradicted": 0}
        total = 0
        cited = 0

        for claim in claims:
            total += 1
            if isinstance(claim, Claim):
                raw_status: Any = claim.status
                ids: Any = claim.source_ids
            elif isinstance(claim, dict):
                raw_status = claim.get("status", "unverified")
                ids = claim.get("source_ids") or []
            else:
                raw_status = "unverified"
                ids = []

            # Tolerate draft-schema status names from older workers.
            if raw_status in ("verified", "corroborated"):
                raw_status = "supported"
            elif raw_status == "hypothesis":
                raw_status = "unverified"
            if raw_status not in counts:
                raw_status = "unverified"
            counts[raw_status] += 1

            if isinstance(ids, str):
                ids = [ids]
            if any(source_id in known_ids for source_id in ids):
                cited += 1

        coverage = min(1.0, max(0.0, cited / total)) if total else 0.0
        return {
            "total_claims": total,
            "supported": counts["supported"],
            "unverified": counts["unverified"],
            "contradicted": counts["contradicted"],
            "citation_coverage": coverage,
        }

    def validate_citations(self, report: dict) -> dict:
        """Validate a report's source registry and section citations.

        Accepts both full ``SourceRecord`` dicts and legacy URL strings in
        ``report["sources"]``.  Known sources are registered in the ledger and
        rewritten to canonical ``SourceRecord`` dicts; unknown IDs are removed
        from every ``sections[].sources`` list.  The returned report has
        ``"sources"`` (canonical registry) and ``"grounding"`` attached.

        The input *report* is never mutated — a deep copy is validated and
        returned.  Unrelated keys are preserved unchanged.
        """
        result: dict[str, Any] = copy.deepcopy(dict(report))

        incoming = result.get("sources") or []
        if not isinstance(incoming, list):
            incoming = [incoming]

        # Map every original identifier (caller id, URL string, canonical id)
        # to the ledger id actually assigned.
        id_map: dict[str, str] = {sid: sid for sid in self._sources}
        registry: list[SourceRecord] = []

        for raw in incoming:
            original_id: str | None = None
            url: str = ""
            if isinstance(raw, dict):
                if raw.get("id"):
                    original_id = str(raw["id"])
                if raw.get("url"):
                    url = str(raw["url"])
            elif isinstance(raw, str):
                original_id = raw
                url = raw

            record = self._register(self._coerce_source(raw))
            registry.append(record)

            if original_id and original_id not in id_map:
                id_map[original_id] = record.id
            if url and url not in id_map:
                id_map[url] = record.id
            id_map[record.id] = record.id

        # A ledger that already holds sources but a report with no explicit
        # source list should still expose the run's registry.
        if not registry:
            registry = list(self._sources.values())

        known_ids = set(self._sources)

        for section in result.get("sections") or []:
            if not isinstance(section, dict):
                continue
            entries = section.get("sources") or []
            if not isinstance(entries, list):
                entries = [entries]

            filtered: list[str] = []
            for entry in entries:
                value: str | None = None
                if isinstance(entry, SourceRecord):
                    value = entry.id
                elif isinstance(entry, dict):
                    value = entry.get("id") or entry.get("url")
                elif isinstance(entry, str):
                    value = entry

                if not value:
                    continue
                value = str(value)
                if value in known_ids:
                    filtered.append(value)
                elif value in id_map:
                    filtered.append(id_map[value])
                # Otherwise it references an unknown ID -> drop it.
            section["sources"] = filtered

        result["sources"] = [record.model_dump() for record in registry]
        result["grounding"] = self.grounding_summary(result)
        return result
