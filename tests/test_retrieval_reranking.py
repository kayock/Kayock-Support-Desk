"""
Focused tests for app.services.vehicle.rerank_hits -- the lightweight
local reranking pass that boosts exact phrase/OBD-code matches on top of
an already-retrieved semantic candidate set, without replacing Chroma or
building a second search system.
"""
from app.services import vehicle


def test_blower_motor_outranks_generic_motor_passages():
    question = "The blower motor isn't working. What does the service manual say to check?"
    hits = [
        {"text": "Wiper motor replacement: remove the two mounting bolts and disconnect the linkage.",
         "distance": 0.30, "meta": {"filename": "manual.pdf", "page": 50}},
        {"text": "HVAC Blower Motor: if the blower motor does not operate, check fuse 12 and the "
                 "blower motor relay before replacing the blower motor assembly.",
         "distance": 0.35, "meta": {"filename": "manual.pdf", "page": 120}},
        {"text": "4WD hydraulic transfer case motor pressure test procedure and specifications.",
         "distance": 0.28, "meta": {"filename": "manual.pdf", "page": 200}},
        {"text": "Washer motor and reservoir replacement instructions for the windshield washer system.",
         "distance": 0.32, "meta": {"filename": "manual.pdf", "page": 90}},
    ]

    reranked = vehicle.rerank_hits(hits, question)

    assert reranked[0]["meta"]["page"] == 120  # the actual "blower motor" passage
    # It must outrank every passage that only shares the generic word "motor".
    blower_index = next(i for i, h in enumerate(reranked) if h["meta"]["page"] == 120)
    for i, h in enumerate(reranked):
        if h["meta"]["page"] != 120:
            assert i > blower_index or i == blower_index


def test_exact_obd_code_match_receives_strong_boost():
    question = "why does my car keep throwing P0302"
    hits = [
        {"text": "General misfire troubleshooting overview for all cylinders.",
         "distance": 0.20, "meta": {"filename": "manual.pdf", "page": 10}},
        {"text": "P0302: Cylinder 2 misfire detected. Check ignition coil and spark plug for cylinder 2.",
         "distance": 0.40, "meta": {"filename": "manual.pdf", "page": 88}},
    ]
    reranked = vehicle.rerank_hits(hits, question)
    assert reranked[0]["meta"]["page"] == 88  # despite a worse raw semantic distance


def test_rerank_preserves_order_when_no_exact_matches():
    question = "how often should I change the oil"
    hits = [
        {"text": "Unrelated content about tire pressure monitoring.", "distance": 0.20, "meta": {"page": 1}},
        {"text": "More unrelated content about headlight bulbs.", "distance": 0.30, "meta": {"page": 2}},
    ]
    reranked = vehicle.rerank_hits(hits, question)
    assert [h["meta"]["page"] for h in reranked] == [1, 2]  # closer semantic distance still wins


def test_rerank_empty_hits_returns_empty():
    assert vehicle.rerank_hits([], "any question") == []


def test_rerank_handles_hits_without_distance_gracefully():
    hits = [{"text": "blower motor check", "meta": {"page": 1}}]  # no "distance" key
    reranked = vehicle.rerank_hits(hits, "blower motor")
    assert len(reranked) == 1


def test_rerank_explicit_codes_param_overrides_extraction():
    hits = [
        {"text": "P0420: Catalyst system efficiency below threshold.", "distance": 0.3, "meta": {"page": 1}},
        {"text": "P0302: Cylinder 2 misfire detected.", "distance": 0.3, "meta": {"page": 2}},
    ]
    reranked = vehicle.rerank_hits(hits, "some question with no codes in it", obd_codes=["P0302"])
    assert reranked[0]["meta"]["page"] == 2
