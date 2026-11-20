"""
Small, focused helpers for the vehicle/backyard-mechanic pass. Nothing
here replaces or reimplements the core RAG pipeline (parsing, chunking,
embedding, Chroma) -- it only adds:

- detecting whether a device is a vehicle (by its free-text device_type)
- normalizing OBD-II trouble codes typed by the user
- building a short vehicle-profile line to include in prompts
- a lightweight local rerank pass over an already-retrieved candidate set,
  boosting exact phrase/code matches (e.g. "blower motor", "P0302") over
  passages that only share a generic word (e.g. "motor")

This module has no dependency on FastAPI, the database, or the LLM client
-- it's plain string/list manipulation, easy to unit test in isolation.
"""
from __future__ import annotations

import re

VEHICLE_TYPE_KEYWORDS = ("car", "vehicle", "truck", "suv", "motorcycle", "van", "auto", "pickup")

# All vehicle profile fields, in display order. Used by device_service and
# main.py so the field list only has to be maintained in one place.
VEHICLE_FIELDS = (
    "vehicle_year", "vehicle_make", "vehicle_model", "vehicle_trim", "vehicle_engine",
    "vehicle_drivetrain", "vin",
)

ANSWER_LEVELS = ("beginner", "experienced")
DEFAULT_ANSWER_LEVEL = "experienced"

OBD_CODE_RE = re.compile(r"^[PBCU]\d{4}[A-Z0-9]*$", re.IGNORECASE)
OBD_CODE_IN_TEXT_RE = re.compile(r"\b[PBCU]\d{4}[A-Z0-9]*\b", re.IGNORECASE)

# VINs are 17 characters, uppercase alphanumeric, excluding I/O/Q (which
# are disallowed by the VIN standard to avoid confusion with 1/0).
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

# Deliberately tiny stopword list -- this is phrase-matching for reranking,
# not real NLP. Just enough to keep "the", "is", "what" etc. from forming
# meaningless boosted phrases.
_STOPWORDS = {
    "the", "a", "an", "is", "isn't", "isnt", "are", "was", "were", "does", "doesn't", "doesnt",
    "do", "did", "to", "of", "for", "and", "or", "in", "on", "about", "how", "what", "why", "when",
    "check", "say", "says", "manual", "service", "my", "i", "it", "this", "that", "with", "at",
}


# ------------------------------------------------------------- vehicles ----

def is_vehicle_device(device_type: str) -> bool:
    """Whether a device's free-text type looks like a vehicle. Deliberately
    simple substring matching -- no vehicle database, no VIN lookups."""
    t = (device_type or "").strip().lower()
    return any(keyword in t for keyword in VEHICLE_TYPE_KEYWORDS)


def vehicle_profile_line(device: dict) -> str:
    """A short human-readable profile line, e.g. '2017 Hyundai Tucson —
    Engine: 2.0L GDI — Drivetrain: AWD'. Empty string if nothing is set."""
    year = (device.get("vehicle_year") or "").strip()
    make = (device.get("vehicle_make") or "").strip()
    model = (device.get("vehicle_model") or "").strip()
    trim = (device.get("vehicle_trim") or "").strip()
    engine = (device.get("vehicle_engine") or "").strip()
    drivetrain = (device.get("vehicle_drivetrain") or "").strip()

    head = " ".join(part for part in (year, make, model, trim) if part)
    extras = []
    if engine:
        extras.append(f"Engine: {engine}")
    if drivetrain:
        extras.append(f"Drivetrain: {drivetrain}")

    if not head and not extras:
        return ""
    return " — ".join([p for p in [head] if p] + extras)


def build_vehicle_context(device: dict) -> str:
    """A short block to prepend to the user prompt so the answering model
    knows which vehicle configuration is being asked about. Empty string
    if no vehicle fields are set (nothing to add)."""
    profile = vehicle_profile_line(device)
    if not profile:
        return ""
    return (
        f"Vehicle: {profile}\n"
        "Only apply information from the manual excerpts that is consistent with this "
        "specific vehicle configuration. If a manual excerpt appears to describe a "
        "different engine, drivetrain, or powertrain type (for example a hybrid/EV/fuel-cell "
        "procedure for a conventional gasoline vehicle), do not apply it -- note the mismatch "
        "instead of silently using it.\n\n"
    )


# ------------------------------------------------------------------- VIN ----

def normalize_vin(raw: str) -> str:
    """Uppercases and strips whitespace. Does not validate -- see is_valid_vin."""
    return (raw or "").strip().upper().replace(" ", "")


def is_valid_vin(vin: str) -> bool:
    """A VIN is exactly 17 characters, uppercase alphanumeric, excluding
    I/O/Q. This is a format check only -- it doesn't guarantee the VIN is
    real or was actually assigned to a vehicle (that's what decode is for)."""
    return bool(VIN_RE.match(vin or ""))


# ------------------------------------------------------------- OBD codes ----

def normalize_obd_codes(raw: str) -> list[str]:
    """Splits user input on commas/whitespace, uppercases each token, and
    de-duplicates while preserving order. 'p0302' -> 'P0302';
    'P0300, P0302, P0420' -> ['P0300', 'P0302', 'P0420']. Does not reject
    malformed tokens -- normalization, not validation -- so a typo still
    gets searched for rather than silently discarded."""
    if not raw or not raw.strip():
        return []
    tokens = re.split(r"[,\s]+", raw.strip())
    codes = []
    seen = set()
    for tok in tokens:
        tok = tok.strip().upper()
        if not tok or tok in seen:
            continue
        seen.add(tok)
        codes.append(tok)
    return codes


def extract_obd_codes_from_text(text: str) -> list[str]:
    """Finds any OBD-II-shaped codes (P/B/C/U + 4+ digits) embedded in a
    free-form question, e.g. 'why is P0302 showing up'."""
    seen = set()
    codes = []
    for match in OBD_CODE_IN_TEXT_RE.findall(text or ""):
        code = match.upper()
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


# ---------------------------------------------- technical spec verification --
# For exact numeric/technical claims (capacity, torque, resistance, etc.), a
# retrieved passage may only be treated as authoritative for a requested
# component if the passage actually mentions that component -- not merely
# because it's from the same vehicle/vehicle system. This is what stops a
# value like "ATF Quantity A6MF1(4WD): 7.1L" (automatic transaxle) from
# being reported as a "rear differential capacity" just because both are
# drivetrain-related and got retrieved together.

SPEC_KEYWORDS = (
    "capacity", "capacities", "torque", "resistance", "voltage", "volt", "volts", "ohm", "ohms",
    "pressure", "psi", "fuse", "amperage", "amp", "amps", "clearance", "gap", "service limit",
    "temperature", "connector", "pin", "pins", "fastener", "spec", "specs", "specification",
    "specifications", "fluid type", "drain plug", "fill plug", "nm", "ft-lb", "ft lb",
)

# A modest, explicit list of common automotive component/subsystem phrases
# -- not an exhaustive ontology, just enough to catch the obvious cases
# from real manuals plus the ones called out in testing. Anything not on
# this list still gets a generic bigram fallback (see extract_component_phrases).
COMPONENT_ALIASES = (
    "rear differential", "front differential", "differential",
    "transfer case", "blower motor", "wiper motor", "washer motor", "hydraulic motor",
    "front brake caliper", "rear brake caliper", "brake caliper",
    "oxygen sensor", "spark plug", "engine oil", "transmission fluid", "power steering",
    "starter motor", "alternator", "fuel pump", "ignition coil", "catalytic converter",
    "cabin air filter", "engine air filter", "wheel bearing", "cv joint", "drive shaft",
    "propeller shaft", "steering rack", "master cylinder", "brake booster", "radiator",
    "thermostat", "water pump", "timing belt", "timing chain", "clutch", "torque converter",
)

# Real manuals often label a sub-section with a synonym rather than the
# exact component phrase someone would ask about -- e.g. a page titled
# "4 Wheel Drive (4WD) System" containing a sub-heading "Transfer oil
# drain plug" rather than literally "Transfer case". A small, explicit
# synonym map, not a translation engine: only added where testing (or the
# real Hyundai manual) showed it was needed.
COMPONENT_HEADING_ALIASES: dict[str, tuple[str, ...]] = {
    "transfer case": ("transfer case", "transfer oil", "transfer assembly"),
}


def is_spec_question(question: str) -> bool:
    """Whether a question is asking for exact technical specifications
    (as opposed to an ordinary explanatory question like 'where is the
    cabin air filter'), which triggers the stricter component-verification
    path in query_service. Deliberately keyword-based and lightweight."""
    q = (question or "").lower()
    return any(kw in q for kw in SPEC_KEYWORDS)


def extract_component_phrases(question: str) -> list[str]:
    """Best-effort extraction of the component(s) a spec question is
    actually asking about, e.g. 'rear differential capacity...transfer
    case...' -> ['rear differential', 'transfer case']. Prefers known
    aliases (handles multi-component questions naturally, since each
    alias is checked independently); falls back to a generic bigram of
    the question's significant words (with spec keywords stripped out)
    if nothing on the alias list matches."""
    q = (question or "").lower()
    matched = [alias for alias in COMPONENT_ALIASES if alias in q]
    matched = _drop_contained_phrases(matched)
    if matched:
        return matched

    spec_keyword_words = set(" ".join(SPEC_KEYWORDS).split())
    words = [w for w in _significant_words(question) if w not in spec_keyword_words]
    bigrams = [p for p in _ngrams(words, 2) if p]
    return bigrams[:3]


def _drop_contained_phrases(phrases: list[str]) -> list[str]:
    """If both 'differential' and 'rear differential' matched, keep only
    the more specific 'rear differential' -- avoids a generic alias
    swallowing a more precise one that's also present."""
    unique = list(dict.fromkeys(phrases))  # de-dupe, preserve order
    keep = []
    for phrase in sorted(unique, key=len, reverse=True):
        if not any(phrase != kept and phrase in kept for kept in keep):
            keep.append(phrase)
    return [p for p in unique if p in keep]  # restore original relative order


def tag_component_verification(hits: list[dict], components: list[str]) -> tuple[list[dict], list[dict]]:
    """Splits retrieved hits into (verified, other) for the given
    requested component phrases. A hit is 'verified' for this purpose if
    one of the component's heading aliases appears on a short, label-like
    line within the *opening section* of the chunk (roughly its first
    300-500 characters) -- not literally only the first line, and not
    just anywhere in the body.

    Real manuals often open a page with a broader section title --
    'Driveshaft and axle', '4 Wheel Drive (4WD) System' -- and only
    introduce the actual component a bit further down with its own short
    sub-heading ('Rear differential', 'Transfer oil drain plug'). Only
    requiring a match on line 1 rejected those genuinely-correct passages.
    Widening the *window* searched is safe specifically because matching
    still only ever considers short label-like lines (<=100 characters),
    not the full body text -- a long explanatory sentence mentioning a
    component only in passing (e.g. an automatic-transaxle section noting
    its fluid 'must never be mixed with... transfer case fluid' inside a
    multi-sentence paragraph) still never counts, because that sentence
    is far longer than a heading/label line ever is. This is what keeps
    the original Hyundai bug rejected: the ATF/transaxle passage still
    fails this bar for a 'rear differential' question either way -- it
    has no short line naming the differential at all.

    Each verified hit gets a 'verified_components' key listing which
    phrase(s) matched, so a multi-component question (e.g. rear
    differential AND transfer case) keeps each excerpt correctly
    attributed to its own component."""
    if not components:
        return [], list(hits)
    verified, other = [], []
    for hit in hits:
        label_lines = [ln.lower() for ln in _heading_label_lines(hit.get("text") or "")]
        matched = [
            component
            for component in components
            if any(alias in line for line in label_lines for alias in COMPONENT_HEADING_ALIASES.get(component, (component,)))
        ]
        if matched:
            tagged = dict(hit)
            tagged["verified_components"] = matched
            verified.append(tagged)
        else:
            other.append(hit)
    return verified, other


def _heading_label_lines(text: str, window: int = 450, max_line_len: int = 100) -> list[str]:
    """The lines within the opening `window` characters of a chunk that
    look like a label/sub-heading (short) rather than full explanatory
    prose. Real manual sub-sections are almost always introduced by a
    short line naming the component/spec, even when the page's very
    first line is a broader section title."""
    stripped = text.strip()
    if not stripped:
        return []
    region = stripped[:window]
    return [ln.strip() for ln in region.splitlines() if ln.strip() and len(ln.strip()) <= max_line_len]


# ------------------------------------------ property-aware spec verification --
# Correct component is not enough: a chunk correctly headed "Rear
# Differential" can still contain several different specs (a cover-bolt
# torque, a mounting-bolt torque, a drain-plug torque...), and a value
# from the wrong one of those must never be reported as the answer to a
# question about a different one. This is a second, narrower check layered
# on top of component verification: it also requires the *requested
# property* (drain plug, fill plug, resistance, gap, etc.) to appear near
# an actual number within the same passage -- not just that the passage
# is generally about the right component.

# A modest alias/phrase map, not an ontology. Order matters for
# extraction (see PROPERTY_EXTRACTION_ORDER below): more specific
# fastener types are checked before the generic "torque" catch-all, so
# "drain plug torque" is recognized as drain_plug_torque, not just torque.
PROPERTY_ALIASES = {
    "drain_plug_torque": ("drain plug", "drain bolt", "drain torque"),
    "fill_plug_torque": ("fill plug", "filler plug", "oil filler plug", "fill bolt", "filler bolt", "fill torque", "filler torque"),
    "mounting_bolt_torque": ("mounting bolt", "mounting bolts"),
    "cover_bolt_torque": ("cover bolt", "cover bolts"),
    "guide_pin_torque": ("guide pin",),
    "fluid_type": ("fluid type", "lubricant", "oil type", "fluid specification"),
    "capacity": ("capacity", "capacities"),
    "resistance": ("resistance", "resistor"),
    "voltage": ("voltage", "volts"),
    "pressure": ("pressure",),
    "fuse_amperage": ("fuse", "amperage"),
    "clearance": ("clearance",),
    "gap": ("gap",),
    "service_limit": ("service limit",),
    "temperature": ("temperature",),
    "connector_pin": ("connector pin", "connector"),
    "torque": ("torque", "tightening torque"),  # generic fallback -- only used if no specific fastener matched
}

# Specific fastener/property types checked before the generic "torque"
# catch-all, so a specific request isn't diluted into a generic one.
_FASTENER_TORQUE_PROPERTIES = (
    "drain_plug_torque", "fill_plug_torque", "mounting_bolt_torque", "cover_bolt_torque", "guide_pin_torque",
)
PROPERTY_EXTRACTION_ORDER = (
    *_FASTENER_TORQUE_PROPERTIES,
    "fluid_type", "capacity", "resistance", "voltage", "pressure", "fuse_amperage",
    "clearance", "gap", "service_limit", "temperature", "connector_pin",
)

_HAS_DIGIT_RE = re.compile(r"\d")


def extract_requested_properties(question: str) -> list[str]:
    """Which specific property/properties a spec question is asking for,
    e.g. 'rear differential drain plug torque' -> ['drain_plug_torque'],
    or 'fluid type, capacity, drain torque, and fill torque' -> all four.
    Returns canonical property keys (see PROPERTY_ALIASES), in the order
    first mentioned would be nice but simplicity wins: extraction order
    below, which just needs to be deterministic."""
    q = (question or "").lower()
    found = [prop for prop in PROPERTY_EXTRACTION_ORDER if any(alias in q for alias in PROPERTY_ALIASES[prop])]
    # Generic "torque" only counts if no specific fastener/torque type was
    # already found -- otherwise "drain plug torque" would also register
    # as a second, redundant generic "torque" request.
    if not any(p in found for p in _FASTENER_TORQUE_PROPERTIES) and any(a in q for a in PROPERTY_ALIASES["torque"]):
        found.append("torque")
    return found


def _property_evidence_in_text(text: str, aliases: tuple[str, ...]) -> bool:
    """Whether a numeric value appears close to one of the property's
    alias phrases in this text -- same line, or the line immediately
    after (covers the common 'Rear differential oil drain plug' / next
    line 'Tightening torque: 49.0-68.6 N*m' manual layout). Does not
    accept a number found anywhere else in the passage."""
    lines = [ln.lower() for ln in text.splitlines()]
    for i, line in enumerate(lines):
        if any(alias in line for alias in aliases):
            window = " ".join(lines[i : i + 2])
            if _HAS_DIGIT_RE.search(window):
                return True
    return False


def tag_property_verification(
    component_verified_hits: list[dict], properties: list[str]
) -> tuple[list[dict], list[dict], set[str]]:
    """Second verification pass, applied only to hits that already passed
    component-heading verification. Splits them into:
    - exact_spec_hits: contain numeric evidence, in proximity to the
      property's name, for at least one requested property (tagged with
      'verified_properties' listing which).
    - related_hits: right component, but no requested property has
      numeric evidence in this particular passage -- still useful as
      general context, never as a citable source for an exact value.
    Also returns the set of requested properties that got verified by
    *any* hit, so the caller can tell the model exactly which requested
    values have zero evidence anywhere, rather than a blanket refusal.
    """
    if not properties:
        return [], list(component_verified_hits), set()
    exact_spec_hits, related_hits = [], []
    verified_properties: set[str] = set()
    for hit in component_verified_hits:
        text = hit.get("text") or ""
        matched = [p for p in properties if _property_evidence_in_text(text, PROPERTY_ALIASES[p])]
        if matched:
            tagged = dict(hit)
            tagged["verified_properties"] = matched
            exact_spec_hits.append(tagged)
            verified_properties.update(matched)
        else:
            related_hits.append(hit)
    return exact_spec_hits, related_hits, verified_properties


# ------------------------------------------------------------- reranking ----

def _significant_words(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 2]


def _ngrams(words: list[str], n: int) -> list[str]:
    if len(words) < n:
        return []
    return [" ".join(words[i : i + n]) for i in range(len(words) - n + 1)]


def rerank_hits(hits: list[dict], question: str, obd_codes: list[str] | None = None) -> list[dict]:
    """Reorders an already-retrieved candidate set (from vector_store.query)
    to favor chunks containing exact phrases/codes from the question, on
    top of the existing semantic similarity. This is deliberately simple
    string matching over a small candidate list -- not a search engine.

    Each hit's Chroma cosine *distance* (smaller = more similar) is turned
    into a similarity score, then boosted per exact match found:
      - an OBD code appearing in the chunk: +0.5 (strong signal)
      - an exact 3-word phrase from the question appearing in the chunk: +0.25
      - an exact 2-word phrase from the question appearing in the chunk: +0.15
    This is enough to make a chunk containing the literal phrase
    "blower motor" outrank one that only contains the generic word "motor",
    without discarding genuine semantic matches entirely.
    """
    if not hits:
        return hits

    codes = obd_codes if obd_codes is not None else extract_obd_codes_from_text(question)
    words = _significant_words(question)
    trigrams = [p for p in _ngrams(words, 3) if p]
    bigrams = [p for p in _ngrams(words, 2) if p]

    scored = []
    for hit in hits:
        text_lower = (hit.get("text") or "").lower()
        distance = hit.get("distance")
        similarity = 1.0 - distance if isinstance(distance, (int, float)) else 0.0

        bonus = 0.0
        for code in codes:
            if code.lower() in text_lower:
                bonus += 0.5
        for phrase in trigrams:
            if phrase in text_lower:
                bonus += 0.25
        for phrase in bigrams:
            if phrase in text_lower:
                bonus += 0.15

        scored.append((similarity + bonus, hit))

    # Stable sort preserves original (semantic-distance) order among ties.
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [hit for _, hit in scored]
