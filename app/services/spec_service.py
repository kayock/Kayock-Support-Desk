"""
Structured specification extraction: turns the pattern-shaped values that
hide inside manual text (torque values, fluid types, capacities, fuse
ratings, service intervals) into searchable, sourced rows in the
`spec_values` table.

Honesty rules (mirroring the whole app):
- Nothing is ever invented. Every row keeps the document id, page,
  section, and the excerpt it came from.
- Rows extracted by pattern alone are stored as status='candidate'.
- A row is only 'verified' when it ALSO clears the same component- and
  property-verification layers used for exact-spec answers (component
  named on a label line in the chunk, property name near the number) --
  i.e. the standard the answer pipeline itself enforces.
- Value parsing is deliberately conservative; anything ambiguous stays a
  string in value_text, and non-numeric values are never forced to float.

Multiple manuals per device: when the same category/component/property
appears in several documents, searches prefer documents whose filename
matches the spec_document_preference setting (e.g. service > owner) --
configured in Settings, transparent in every result (the document name is
always shown alongside the value).
"""
from __future__ import annotations

import re

from app.config import DEFAULT_SETTINGS
from app.db import db_cursor
from app.services import settings_service, text_index, vehicle

# ---------------------------------------------------------------- regexes ----
# Each extractor yields (category, property, value_text, value_numeric, unit,
# matched_snippet) tuples from one chunk of text. Keep them modest and
# auditable -- this is a structured-index pass over chunks, not NLP.

_TORQUE_RE = re.compile(
    r"(?P<label>torque|tightening torque|tighten[^\n]{0,30}to)[^\n\d]{0,40}?"
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>N[·\-.\u00b7]?m|ft[·\-.\u00b7]?l?bs?|lb[·\-.\u00b7]?in|kgf[·\-.\u00b7]?m)",
    re.IGNORECASE,
)
_CAPACITY_RE = re.compile(
    r"(?P<label>capacit\w+|quantity|fill amount)[^\n\d]{0,50}?"
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>L|ml|mL|US\s?qt|qt|quarts?|liters?|litres?|Imp\.?\s?pt)\b",
    re.IGNORECASE,
)
_FLUID_RE = re.compile(
    r"(?P<context>fluid type|oil type|specified[^\n]{0,40}oil|gear oil|SAE\s?\d+W-\d+|API\s?GL-?\d|ATF\s?\w*)"
    r"(?P<value>[^\n]{0,60})?",
    re.IGNORECASE,
)
_FUSE_RE = re.compile(
    r"(?P<label>fuse[^\n]{0,30}?)(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>A|amp|amps)\b",
    re.IGNORECASE,
)
_FUSE_TABLE_RE = re.compile(
    r"(?P<id>FUSE\s?\d+)\s*[|\t]|(?P<rating>\d+(?:\.\d+)?)\s*A\s*[|\t]",
    re.IGNORECASE,
)
_SCHEDULE_RE = re.compile(
    r"(?P<context>(?:replace|inspect|check|change|service|lubricate)[^\n]{0,60}?)"
    r"(?:every|at)\s*(?P<num>\d[\d,]*)\s*(?P<unit>km|mi|miles|months?|weeks?|days?)\b",
    re.IGNORECASE,
)

_NUMBER_CLEAN_RE = re.compile(r",")


def _to_float(num: str) -> float | None:
    try:
        return float(_NUMBER_CLEAN_RE.sub("", num))
    except ValueError:
        return None


def _extract_from_text(text: str) -> list[dict]:
    """Runs every extractor over one chunk. Returns dicts with category,
    property, value_text, value_numeric, unit, excerpt."""
    found: list[dict] = []
    lines = text.splitlines()

    for m in _TORQUE_RE.finditer(text):
        line = _line_of(lines, m.start())
        found.append(
            {
                "category": "torque",
                "property": "torque",
                "value_text": f"{m.group('num')} {m.group('unit')}",
                "value_numeric": _to_float(m.group("num")),
                "unit": m.group("unit"),
                "excerpt": line,
            }
        )

    for m in _CAPACITY_RE.finditer(text):
        line = _line_of(lines, m.start())
        found.append(
            {
                "category": "capacity",
                "property": "capacity",
                "value_text": f"{m.group('num')} {m.group('unit')}",
                "value_numeric": _to_float(m.group("num")),
                "unit": m.group("unit"),
                "excerpt": line,
            }
        )

    for m in _FUSE_RE.finditer(text):
        line = _line_of(lines, m.start())
        # Fuse-table rows (which carry a FUSE id) are extracted separately
        # below with the id as the property key -- don't also emit a
        # duplicate generic row for the same line.
        if re.search(r"FUSE\s?\d+", line, re.IGNORECASE):
            continue
        found.append(
            {
                "category": "fuse",
                "property": "fuse_amperage",
                "value_text": f"{m.group('num')} {m.group('unit')}",
                "value_numeric": _to_float(m.group("num")),
                "unit": m.group("unit"),
                "excerpt": line,
            }
        )

    # Fuse-table rows: "FUSE 12 | 10A | ..." pairs an id with a rating on
    # one line; both parts live in the same row for reference.
    for line in lines:
        if not _FUSE_TABLE_RE.search(line):
            continue
        id_match = re.search(r"(?P<id>FUSE\s?\d+)", line, re.IGNORECASE)
        rating_match = re.search(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>A|amp|amps)\b", line, re.IGNORECASE)
        if id_match and rating_match:
            found.append(
                {
                    "category": "fuse",
                    "property": f"fuse_{id_match.group('id').upper().replace(' ', '')}",
                    "value_text": f"{rating_match.group('num')} {rating_match.group('unit')}",
                    "value_numeric": _to_float(rating_match.group("num")),
                    "unit": rating_match.group("unit"),
                    "excerpt": line.strip(),
                }
            )

    for m in _FLUID_RE.finditer(text):
        value = (m.group("value") or "").strip()
        if not value:
            continue
        line = _line_of(lines, m.start())
        found.append(
            {
                "category": "fluid",
                "property": "fluid_type",
                "value_text": f"{m.group('context').strip()} {value}"[:200],
                "value_numeric": None,
                "unit": "",
                "excerpt": line,
            }
        )

    for m in _SCHEDULE_RE.finditer(text):
        line = _line_of(lines, m.start())
        found.append(
            {
                "category": "schedule",
                "property": "interval",
                "value_text": f"every {m.group('num')} {m.group('unit')}",
                "value_numeric": _to_float(m.group("num")),
                "unit": m.group("unit"),
                "excerpt": line,
            }
        )

    return found


def _line_of(lines: list[str], pos: int) -> str:
    """The full line containing the character offset `pos` of the source
    text, for a readable excerpt."""
    consumed = 0
    for line in lines:
        if consumed <= pos < consumed + len(line) + 1:
            return line.strip()[:300]
        consumed += len(line) + 1
    return ""


# --------------------------------------------------------------- pipeline ----

def extract_specs_for_document(device_id: int, document_id: int) -> int:
    """Extracts spec candidates from one document's stored chunks. Returns
    the number of new rows stored. Called automatically at the end of
    successful indexing, and re-runnable per device from the API."""
    chunks = text_index.get_document_chunks(document_id)
    rows = 0
    for chunk in chunks:
        for spec in _extract_from_text(chunk["text"]):
            component = _component_for_chunk(chunk, spec)
            verified = _verify_candidate(chunk, component, spec)
            with db_cursor() as cur:
                cur.execute(
                    """INSERT INTO spec_values
                       (device_id, document_id, page, section, category, component, property,
                        value_text, value_numeric, unit, status, excerpt)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(document_id, page, category, component, property, value_text)
                       DO UPDATE SET status = MAX(spec_values.status, excluded.status)""",
                    (
                        device_id,
                        document_id,
                        chunk["page"],
                        chunk["section"],
                        spec["category"],
                        component,
                        spec["property"],
                        spec["value_text"],
                        spec["value_numeric"],
                        spec["unit"],
                        "verified" if verified else "candidate",
                        spec["excerpt"],
                    ),
                )
                if cur.rowcount > 0:
                    rows += 1
    return rows


def _component_for_chunk(chunk: dict, spec: dict) -> str:
    """Best-effort component name for a candidate: prefer the section
    heading (already the chunk's own label line); fall back to a known
    component alias mentioned in the excerpt line."""
    section = (chunk.get("section") or "").strip()
    if section:
        return section.split(" > ")[-1][:100]
    line = (spec.get("excerpt") or "").lower()
    for alias in vehicle.COMPONENT_ALIASES:
        if alias in line:
            return alias
    return ""


def _verify_candidate(chunk: dict, component: str, spec: dict) -> bool:
    """A candidate is 'verified' only when its component appears on a
    label line within the chunk (the same bar component verification
    enforces for exact-spec answers) AND the property alias has numeric
    evidence in the chunk (the same bar property verification enforces).
    Candidates from chunks that merely mention things in body text stay
    'candidate'."""
    if not component:
        return False
    component_lower = component.lower()
    aliases = vehicle.COMPONENT_HEADING_ALIASES.get(component_lower, (component_lower,))
    label_lines = [ln.lower() for ln in vehicle._heading_label_lines(chunk["text"])]
    component_on_label = any(alias in line for line in label_lines for alias in aliases)
    if not component_on_label:
        return False
    prop_aliases = vehicle.PROPERTY_ALIASES.get(spec["property"])
    if prop_aliases is None:
        # Fuse-table rows keyed by fuse id verify against the fuse alias
        if spec["property"].startswith("fuse_"):
            prop_aliases = vehicle.PROPERTY_ALIASES["fuse_amperage"]
        else:
            return False
    return vehicle._property_evidence_in_text(chunk["text"], prop_aliases)


def extract_specs_for_device(device_id: int) -> int:
    """Re-runs extraction over every ready document of a device."""
    total = 0
    with db_cursor() as cur:
        cur.execute("SELECT id FROM documents WHERE device_id = ? AND status = 'ready'", (device_id,))
        doc_ids = [row["id"] for row in cur.fetchall()]
    for document_id in doc_ids:
        total += extract_specs_for_document(device_id, document_id)
    return total


# ----------------------------------------------------------------- query ----

def _document_priority(document_id: int, order: tuple[str, ...]) -> tuple[int, int]:
    """Sort key: documents whose original filename matches an earlier
    preference keyword rank first; ties fall back to document id so output
    is deterministic."""
    with db_cursor() as cur:
        cur.execute("SELECT original_filename FROM documents WHERE id = ?", (document_id,))
        row = cur.fetchone()
    filename = (row["original_filename"] if row else "").lower()
    for i, keyword in enumerate(order):
        if keyword and keyword in filename:
            return (i, document_id)
    return (len(order), document_id)


def _preference_order() -> tuple[str, ...]:
    raw = settings_service.get("spec_document_preference") or DEFAULT_SETTINGS["spec_document_preference"]
    return tuple(k.strip().lower() for k in raw.split(",") if k.strip())


def search_specs(device_id: int, query: str = "", limit: int = 100) -> list[dict]:
    """Searchable structured spec values for a device. Empty query returns
    everything (highest-priority documents first). Every row keeps its
    source document and page; status separates 'verified' from
    'candidate' so a value can never pass for a factory number it isn't."""
    query = (query or "").strip().lower()
    with db_cursor() as cur:
        if query:
            cur.execute(
                """SELECT s.*, d.original_filename FROM spec_values s
                   JOIN documents d ON d.id = s.document_id
                   WHERE s.device_id = ?
                     AND (LOWER(s.component) LIKE ? OR LOWER(s.property) LIKE ?
                          OR LOWER(s.value_text) LIKE ? OR LOWER(s.category) LIKE ?
                          OR LOWER(s.excerpt) LIKE ?)
                   LIMIT ?""",
                (device_id, *(f"%{query}%",) * 5, limit * 4),
            )
        else:
            cur.execute(
                """SELECT s.*, d.original_filename FROM spec_values s
                   JOIN documents d ON d.id = s.document_id
                   WHERE s.device_id = ? LIMIT ?""",
                (device_id, limit * 4),
            )
        rows = [dict(r) for r in cur.fetchall()]

    order = _preference_order()
    rows.sort(key=lambda r: (_document_priority(r["document_id"], order), r["category"], r["page"]))
    return rows[:limit]
