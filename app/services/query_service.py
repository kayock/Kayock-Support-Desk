"""
Answers a user's question about a specific device using only the
retrieved chunks from that device's manuals. Refuses to invent an answer
when the documents don't cover it.

Additions for the vehicle/backyard-mechanic passes, all purely in how the
prompt is built, how candidates are reranked/filtered, and which model
process gets marked "in use" -- the core retrieve-then-answer shape is
unchanged:

- For vehicle devices, the vehicle's profile (year/make/model/engine/
  drivetrain) is included as context, manual-backed facts are asked to be
  distinguished from general automotive knowledge, and the answer's
  detail level (Beginner/Experienced) is honored.
- Retrieval pulls a wider semantic candidate set from Chroma and locally
  reranks it to favor chunks containing exact phrases/OBD codes from the
  question (see app.services.vehicle.rerank_hits), so e.g. "blower motor"
  outranks passages that only share the generic word "motor".
- For exact technical-specification questions (capacity, torque,
  resistance, etc.), two verification passes run in sequence:
    1. Component verification (app.services.vehicle.tag_component_verification)
       -- is this passage actually about the requested component at all
       (e.g. "rear differential"), judged from its heading/opening text?
    2. Property verification (app.services.vehicle.tag_property_verification)
       -- within a component-verified passage, is the *specific requested
       property* (drain plug torque vs. fill plug torque vs. mounting
       bolt torque, etc.) actually backed by a number near that property's
       name, not just present somewhere else under the same heading?
  Only passages that clear BOTH checks are ever used as citation sources
  for an exact value -- this is what stops a rear-differential mounting-
  bolt torque from being reported as the rear-differential *drain-plug*
  torque just because both are under the same "Rear Differential" heading.
- Answering activity is reported to ai_manager so the idle-unload timer
  (if enabled) resets on real use rather than unloading a model mid-session.
"""
from time import monotonic

from app.config import KEYWORD_CANDIDATES, RERANK_CANDIDATE_MULTIPLIER, TOP_K
from app.db import bump_metric, db_cursor
from app.services import ai_manager, automotive_sources, llm_client, text_index, vector_store, vehicle
from app.services.llm_client import LLMError

NOT_FOUND_PHRASE = "The uploaded documents don't contain enough information to answer that."
NOT_VERIFIED_PHRASE = (
    "I found related information, but I could not verify an exact factory specification "
    "for this component from the retrieved manual passages."
)

_BASE_RULES = f"""Rules:
- Base your answer strictly on the provided excerpts. Do not use outside knowledge to state
  facts, procedures, specifications, values, or part/code meanings as if the manual said them.
- If the excerpts do not contain enough information to answer, respond with exactly:
  "{NOT_FOUND_PHRASE}"
- Be concise and practical. Include specific steps, error-code meanings, or values from
  the manual when relevant.
- Do not mention "excerpts" or "documents" awkwardly -- just answer naturally, as if you
  had read the manual.
- Citations (which document/page each fact came from) are shown separately below your
  answer by the app itself -- you don't need to list sources yourself."""

_VEHICLE_RULES = """
This is a vehicle service-manual assistant for a backyard mechanic. Additional rules:
- Clearly separate manual-backed facts from general automotive knowledge. If you need to add
  reasonable general explanation that is NOT directly stated in the uploaded manual, put it
  under a heading like "General diagnostic guidance" and keep it clearly distinct from what
  the manual actually says. Never present general automotive knowledge as if it were a
  manufacturer's factory instruction.
- Prefer manual-backed values for torque specs, fluid capacities, connector pins, voltages,
  resistances, fuse ratings, service limits, and DTC procedures, and cite them. If no
  manual-backed value is available, say so plainly rather than estimating one.
- If the retrieved excerpts appear to cover multiple engine, trim, or drivetrain variants and
  you cannot tell which applies to this vehicle, say so explicitly instead of silently picking one.
- If a procedure involves airbags/SRS, brakes, steering, fuel lines, high-voltage hybrid/EV
  systems, vehicle lifting/support, or pressurized systems, include any safety precautions the
  manual states for it."""

_BEGINNER_STYLE = """
Answer detail level: Beginner. Translate automotive terminology into plain language. Prefer,
in this rough order: what the part does, what symptom the procedure addresses, the easiest/
cheapest checks first, tools likely needed, where the manual says the part is located, and what
the result of a check would mean. Don't drown the answer in connector pin numbers or exact
torque/resistance tables unless the user's question specifically needs them. If a step requires
specialized equipment or carries meaningful injury risk, add one short, plain warning -- don't
invent dramatic warnings beyond what the situation calls for."""

_EXPERIENCED_STYLE = """
Answer detail level: Experienced. Preserve full technical detail: factory terminology, exact
DTC information, connector names/pins when available, voltages/resistances, torque values,
service limits, component test steps, and removal/installation sequences, with manual page
references where available. Do not simplify or omit technical detail for the sake of brevity."""

_DIAGNOSTIC_FORMAT = """
Structure this diagnostic answer with only the headings that are actually useful given what the
manual provides (skip any that don't apply):
## Likely area
## Check first
## Factory procedure
## Specifications
Only include values in Specifications that are actually supported by the uploaded documents."""

_SPEC_VERIFICATION_RULES = f"""
This question is asking for exact technical specification(s). Extra rules apply:
- The excerpts below are split into "Verified excerpts" (confirmed to mention the specific
  component/system you were asked about) and "Other retrieved excerpts" (related to the vehicle
  but NOT confirmed to be about that specific component).
- Exact specifications (fluid type, capacity, torque, resistance, voltage, pressure, fuse
  amperage, clearance, gap, service limit, temperature, connector pin, fastener size, etc.) may
  ONLY be reported using values found in the "Verified excerpts". Do not infer that a
  specification from "Other retrieved excerpts" applies merely because it comes from the same
  vehicle, subsystem, or chapter -- a number can be real while belonging to a different
  component (e.g. an automatic transaxle's fluid capacity is not a differential's fluid capacity,
  even though both are drivetrain specs from the same manual).
- If a requested value is not present in the "Verified excerpts" for that component, say exactly:
  "{NOT_VERIFIED_PHRASE}" for that specific value. Do not substitute a related component's value.
- When multiple components were asked about, answer each separately and only use each
  component's own verified excerpts for its values -- do not cross-apply a value from one
  component's verified excerpt to a different requested component.
- Prefer this compact format when multiple specs are requested for one or more components:
  ## <Component Name>
  **Fluid:** ...
  **Capacity:** ...
  **Drain plug torque:** ...
  **Fill plug torque:** ...
  Source: <document>, page <n>
  Use "Not verified in the retrieved factory documentation." for any value that isn't backed by
  that component's verified excerpts, instead of leaving it out or guessing. Omit fields the
  question didn't ask about."""

_PROPERTY_VERIFICATION_RULES = """
This question asks for one or more SPECIFIC properties/specifications (e.g. drain plug torque,
fill plug torque, capacity, resistance) of the requested component -- not just anything about
that component. Correct component is not enough here. Extra rules apply:
- The excerpts are split into "VERIFIED EXACT-SPEC EXCERPTS" (the requested component AND the
  specific requested property both confirmed, with a number in proximity to the property's
  name) and "RELATED EXCERPTS" (right component, but this passage does not itself verify any of
  the specific requested properties).
- A number may only be presented as the requested factory specification when the same source
  text clearly associates that number with BOTH the requested component and the requested
  property. Do not substitute another bolt, plug, fastener, fluid, voltage, resistance, or
  specification from the same component section just because it's nearby or under the same
  heading -- e.g. a differential cover-bolt torque or mounting-bolt torque is not a drain-plug
  torque, even in the same "Rear Differential" section.
- You may use RELATED EXCERPTS for general explanation (what the component is, where it is,
  general context), but never to state an exact numerical specification.
- For each requested property, use its own "VERIFIED EXACT-SPEC EXCERPTS" only. If a requested
  property has no verified exact-spec excerpt, say plainly that the requested specification
  could not be verified for that property -- do not substitute a different property's value and
  do not guess. A response like "I found rear-differential information, but I could not verify
  an exact factory drain-plug torque from the retrieved passages" is correct in that case.
- When several properties were requested, verify and report each one independently -- one
  verified property must never cause you to treat the others as verified too."""


def _build_system_prompt(
    is_vehicle: bool, answer_level: str, diagnostic: bool, spec_mode: bool = False, property_mode: bool = False
) -> str:
    parts = [
        "You are a support-desk assistant answering questions about a specific "
        + ("vehicle" if is_vehicle else "household device")
        + " using ONLY the excerpts provided below from its manual(s).",
        _BASE_RULES,
    ]
    if is_vehicle:
        parts.append(_VEHICLE_RULES)
        parts.append(_BEGINNER_STYLE if answer_level == "beginner" else _EXPERIENCED_STYLE)
        if diagnostic:
            parts.append(_DIAGNOSTIC_FORMAT)
        if property_mode:
            parts.append(_PROPERTY_VERIFICATION_RULES)
        elif spec_mode:
            parts.append(_SPEC_VERIFICATION_RULES)
    return "\n".join(parts)


def answer_question(
    device: dict,
    question: str,
    *,
    diagnostic: bool = False,
    retrieval_query: str | None = None,
    history: list[dict] | None = None,
) -> dict:
    """Returns {"answer": str, "sources": [...], "not_found": bool, "error": bool}.

    `device` is the full device dict (from device_service.get_device), not
    just an id, so vehicle profile fields and the answer_level preference
    can be read without an extra query.

    `retrieval_query` lets a chat session resolve follow-up wording ("what
    about the fill plug?") into a searchable query while `question` stays
    what is displayed and verified; defaults to `question`.

    `history` (list of {"role", "content"}) is earlier conversation turns
    passed to the model for conversational meaning only -- retrieval,
    reranking, and spec verification always run fresh on the current turn,
    and the prompt tells the model history is never citable evidence."""
    device_id = device["id"]
    ready_document_ids = _ready_document_ids(device_id)
    if not ready_document_ids:
        return {
            "answer": "No manuals have been indexed for this device yet. "
            "Upload a manual first, then ask again.",
            "sources": [],
            "not_found": True,
            "error": False,
        }

    obd_codes = vehicle.extract_obd_codes_from_text(question)

    # The contextualized retrieval query: what search (semantic AND
    # keyword) runs against. For a plain question this equals the question;
    # for a chat follow-up, chat_service resolves wording like "what about
    # the fill plug?" via the previous turn. The ORIGINAL question is kept
    # untouched for response generation, reranking, and verification.
    search_text = (retrieval_query or question).strip() or question

    started = monotonic()
    embedding_error: LLMError | None = None
    # Hybrid retrieval: semantic candidates from Chroma fused with
    # keyword/BM25 candidates from the local text index (reciprocal rank
    # fusion). Both searches receive the same contextualized query. Keyword
    # search is what makes exact trouble codes, fuse numbers, and part
    # names reliable even when embeddings rank a vague semantic neighbor
    # higher. Only 'ready' documents are ever searched.
    candidate_k = TOP_K * RERANK_CANDIDATE_MULTIPLIER
    try:
        [query_embedding] = llm_client.embed_texts([search_text])
    except LLMError as exc:
        # Embedding backend unavailable -- degrade to keyword-only search
        # instead of failing the whole question (the chat model may still
        # be up and perfectly able to answer over keyword-retrieved text).
        query_embedding = None
        embedding_error = exc
        bump_metric("retrieval_embedding_fallbacks")
    candidates = (
        vector_store.query(device_id, query_embedding, candidate_k, allowed_document_ids=ready_document_ids)
        if query_embedding is not None
        else []
    )
    keyword_hits = text_index.search(device_id, search_text, KEYWORD_CANDIDATES, allowed_document_ids=ready_document_ids)
    fused = _fuse_hits(candidates, keyword_hits)
    bump_metric("questions_answered")
    bump_metric("retrieval_latency_ms_sum", (monotonic() - started) * 1000)
    if keyword_hits:
        bump_metric("keyword_hits_returned", len(keyword_hits))
    reranked = vehicle.rerank_hits(fused, question, obd_codes)

    is_vehicle = vehicle.is_vehicle_device(device.get("device_type", ""))
    spec_mode = is_vehicle and vehicle.is_spec_question(question)
    components: list[str] = []
    properties: list[str] = []

    if spec_mode:
        components = vehicle.extract_component_phrases(question)
    if spec_mode and components:
        properties = vehicle.extract_requested_properties(question)

    property_mode = bool(spec_mode and components and properties)

    if property_mode:
        # Two-stage verification: component first, then -- within the
        # component-verified set -- the specific requested property, with
        # its number required to be near the property's name in the text.
        component_verified, other_hits = vehicle.tag_component_verification(reranked, components)
        exact_spec_hits, related_from_component, verified_properties = vehicle.tag_property_verification(
            component_verified, properties
        )
        related_hits = related_from_component + other_hits

        exact_spec_hits = exact_spec_hits[:TOP_K]
        remaining_slots = max(0, TOP_K - len(exact_spec_hits))
        related_hits = related_hits[:remaining_slots]

        hits = exact_spec_hits + related_hits
        citation_pool = exact_spec_hits
        unverified_properties = [p for p in properties if p not in verified_properties]
    elif spec_mode and components:
        # Component-verification only (couldn't identify a specific
        # property from the question -- fall back to the coarser check).
        verified_hits, other_hits = vehicle.tag_component_verification(reranked, components)
        verified_hits = verified_hits[:TOP_K]
        remaining_slots = max(0, TOP_K - len(verified_hits))
        other_hits = other_hits[:remaining_slots]
        hits = verified_hits + other_hits
        citation_pool = verified_hits
    else:
        hits = reranked[:TOP_K]
        citation_pool = hits

    if not hits:
        if embedding_error is not None:
            # Nothing retrievable at all AND the embedding backend is down:
            # report the real problem instead of a misleading "no manuals".
            return {"answer": str(embedding_error), "sources": [], "not_found": False, "error": True}
        return {
            "answer": "No manuals have been indexed for this device yet. "
            "Upload a manual first, then ask again.",
            "sources": [],
            "not_found": True,
            "error": False,
        }

    if property_mode:
        context = _build_exact_spec_context(exact_spec_hits, related_hits, unverified_properties)
    elif spec_mode and components:
        context = _build_verified_context(verified_hits, other_hits)
    else:
        context = _build_plain_context(hits)

    answer_level = (device.get("answer_level") or vehicle.DEFAULT_ANSWER_LEVEL) if is_vehicle else vehicle.DEFAULT_ANSWER_LEVEL
    vehicle_context = vehicle.build_vehicle_context(device) if is_vehicle else ""

    system_prompt = _build_system_prompt(
        is_vehicle, answer_level, diagnostic,
        spec_mode=bool(spec_mode and components), property_mode=property_mode,
    )
    history = history or []
    history_block = _conversation_history_block(history)
    if history_block:
        history_block += "\n\n"
    user_prompt = f"{vehicle_context}{history_block}Question: {question}\n\nManual excerpts:\n\n{context}"

    ai_manager.record_activity()  # counts as answering-model activity for the idle-unload timer

    try:
        if history_block:
            answer = llm_client.chat(system_prompt, user_prompt, messages=_chat_messages(system_prompt, history_block, question, context))
        else:
            answer = llm_client.chat(system_prompt, user_prompt)
    except LLMError as exc:
        return {"answer": str(exc), "sources": [], "not_found": False, "error": True}

    not_found = NOT_FOUND_PHRASE.lower() in answer.lower()

    sources = []
    if not not_found:
        seen = set()
        for hit in citation_pool:
            meta = hit["meta"]
            key = (meta.get("filename"), meta.get("page"))
            if key in seen:
                continue
            seen.add(key)
            sources.append(
                {
                    "document_id": meta.get("document_id"),
                    "filename": meta.get("filename"),
                    "page": meta.get("page"),
                    "section": meta.get("section", ""),
                    "excerpt": hit["text"][:400],
                }
            )

    return {"answer": answer, "sources": sources, "not_found": not_found, "error": False}


def _conversation_history_block(history: list[dict]) -> str:
    """Renders prior turns for conversational context. Explicitly labeled
    as non-evidence: the model may use it to resolve wording ("it", "that
    component"), never to source a value -- only the fresh excerpts below
    are citable."""
    lines = []
    for m in history:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        who = "User" if m["role"] == "user" else "Assistant"
        content = " ".join(str(m.get("content", "")).split())[:600]
        if content:
            lines.append(f"{who}: {content}")
    if not lines:
        return ""
    return (
        "[Earlier conversation (context for wording only -- do not reuse values, "
        "specifications, or citations from it; answer only from the fresh excerpts below)]\n"
        + "\n".join(lines)
    )


def _chat_messages(system_prompt: str, history_block: str, question: str, context: str):
    """Message list for the chat call. With conversation history, the
    prior turns go in as one clearly-labeled message (proper multi-turn
    framing) and the final user message carries the current question plus
    the fresh retrieval context -- the only citable evidence."""
    if not history_block:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Question: {question}\n\nManual excerpts:\n\n{context}"},
        ]
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": history_block.strip()},
        {
            "role": "user",
            "content": f"Question: {question}\n\nManual excerpts (fresh evidence for this turn):\n\n{context}",
        },
    ]


def _fuse_hits(semantic_hits: list[dict], keyword_hits: list[dict]) -> list[dict]:
    """Reciprocal-rank fusion of the semantic (Chroma) and keyword (BM25)
    candidate lists into one merged candidate set. A chunk found by both
    ranks highest; a chunk found only by one still competes fairly (which
    is what lets an exact-code keyword hit beat a semantically-close but
    wrong-component passage). Dedupe key is (document_id, page, text) so
    the same chunk retrieved by both systems appears once with combined
    score. Keyword-only hits keep distance=None, which rerank_hits already
    handles."""
    K = 60  # standard RRF constant
    scores: dict[tuple, float] = {}
    picked: dict[tuple, dict] = {}

    def key_for(hit: dict) -> tuple:
        meta = hit.get("meta", {})
        return (meta.get("document_id"), meta.get("page"), hit.get("text", ""))

    for rank, hit in enumerate(semantic_hits, start=1):
        key = key_for(hit)
        scores[key] = scores.get(key, 0.0) + 1.0 / (K + rank)
        if key not in picked:
            picked[key] = hit
    for rank, hit in enumerate(keyword_hits, start=1):
        key = key_for(hit)
        scores[key] = scores.get(key, 0.0) + 1.0 / (K + rank)
        if key not in picked:
            picked[key] = hit

    ordered_keys = sorted(scores, key=lambda k: scores[k], reverse=True)
    return [picked[k] for k in ordered_keys]


def _build_plain_context(hits: list[dict]) -> str:
    blocks = []
    for i, hit in enumerate(hits, start=1):
        meta = hit["meta"]
        blocks.append(f"[Excerpt {i} | {meta.get('filename')} | page {meta.get('page')}]\n{hit['text']}")
    return "\n\n".join(blocks)


def _build_verified_context(verified_hits: list[dict], other_hits: list[dict]) -> str:
    parts = []
    if verified_hits:
        blocks = []
        for i, hit in enumerate(verified_hits, start=1):
            meta = hit["meta"]
            components_str = ", ".join(hit.get("verified_components", []))
            blocks.append(
                f"[Verified Excerpt {i} | component: {components_str} | {meta.get('filename')} | "
                f"page {meta.get('page')}]\n{hit['text']}"
            )
        parts.append("VERIFIED EXCERPTS (confirmed to mention the requested component):\n\n" + "\n\n".join(blocks))
    else:
        parts.append("VERIFIED EXCERPTS (confirmed to mention the requested component): none found.")

    if other_hits:
        blocks = []
        for i, hit in enumerate(other_hits, start=1):
            meta = hit["meta"]
            blocks.append(f"[Other Excerpt {i} | {meta.get('filename')} | page {meta.get('page')}]\n{hit['text']}")
        parts.append(
            "OTHER RETRIEVED EXCERPTS (related to the vehicle but NOT confirmed to be about the "
            "specific requested component -- do not use these for exact values):\n\n" + "\n\n".join(blocks)
        )

    return "\n\n".join(parts)


def _build_exact_spec_context(exact_spec_hits: list[dict], related_hits: list[dict], unverified_properties: list[str]) -> str:
    parts = []
    if exact_spec_hits:
        blocks = []
        for i, hit in enumerate(exact_spec_hits, start=1):
            meta = hit["meta"]
            components_str = ", ".join(hit.get("verified_components", []))
            properties_str = ", ".join(p.replace("_", " ") for p in hit.get("verified_properties", []))
            blocks.append(
                f"[Verified Exact-Spec Excerpt {i} | component: {components_str} | property: {properties_str} | "
                f"{meta.get('filename')} | page {meta.get('page')}]\n{hit['text']}"
            )
        parts.append("VERIFIED EXACT-SPEC EXCERPTS (component AND requested property both confirmed):\n\n" + "\n\n".join(blocks))
    else:
        parts.append("VERIFIED EXACT-SPEC EXCERPTS (component AND requested property both confirmed): none found.")

    if related_hits:
        blocks = []
        for i, hit in enumerate(related_hits, start=1):
            meta = hit["meta"]
            blocks.append(f"[Related Excerpt {i} | {meta.get('filename')} | page {meta.get('page')}]\n{hit['text']}")
        parts.append(
            "RELATED EXCERPTS (right component, but do not directly verify any requested property -- "
            "usable only for general explanation, never for an exact numerical claim):\n\n" + "\n\n".join(blocks)
        )

    if unverified_properties:
        friendly = ", ".join(p.replace("_", " ") for p in unverified_properties)
        parts.append(
            f"NOTE: the following requested propert{'y has' if len(unverified_properties) == 1 else 'ies have'} "
            f"NO verified exact-spec excerpt anywhere in the retrieved passages: {friendly}. "
            f"State plainly that these could not be verified -- do not substitute a related value or guess."
        )

    return "\n\n".join(parts)


def diagnose_codes(device: dict, codes: list[str]) -> list[dict]:
    """Runs one structured diagnostic answer per OBD-II code, scoped to
    this device's manuals, alongside the standard generic DTC definition
    (a separate, clearly-labeled source -- see automotive_sources.lookup_dtc).
    Returns a list of {"code": ..., "dtc_lookup": {...}, **answer_question result}.
    The generic definition and the manual-backed answer are kept as
    separate fields rather than blended into one string, so the UI can
    label them distinctly and never imply a standardized SAE definition
    came from the vehicle's manufacturer."""
    results = []
    for code in codes:
        dtc_lookup = automotive_sources.lookup_dtc(code)
        question = (
            f"Diagnostic trouble code {code}. What does the manual say this code means, "
            f"and what is the factory diagnostic procedure to check it?"
        )
        result = answer_question(device, question, diagnostic=True)
        results.append({"code": code, "dtc_lookup": dtc_lookup, **result})
    return results


def _ready_document_ids(device_id: int) -> list[int]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT id FROM documents WHERE device_id = ? AND status = 'ready'",
            (device_id,),
        )
        return [row["id"] for row in cur.fetchall()]
