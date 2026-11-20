"""
Regression tests for property-aware spec verification -- the follow-up
fix to component verification. Correct component is not enough: a chunk
correctly headed "Rear Differential" can still contain several different
specs (a cover-bolt torque, a mounting-bolt torque, a drain-plug
torque...), and Kayock must not report a value from the wrong one of
those just because it's under the right heading.

See app.services.vehicle.tag_property_verification /
extract_requested_properties, and query_service.answer_question's
property_mode path. All LLM calls are mocked -- the property under test
is whether Kayock's own pipeline ever *offers* the wrong value as
verified evidence (via prompt content and citation sources), independent
of how well a real model would follow instructions.
"""
import time

from app.services import llm_client, query_service, vehicle
from tests.fakes import fake_embed_texts


def _make_vehicle_device_with_manual(client, monkeypatch, name, manual_text):
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    res = client.post("/api/devices", data={"name": name, "device_type": "Car"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", manual_text.encode(), "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    deadline = time.time() + 5
    status = None
    while time.time() < deadline:
        status = client.get(f"/api/documents/{doc_id}/status").json()
        if status["status"] in ("ready", "error"):
            break
        time.sleep(0.05)
    assert status["status"] == "ready", status
    return device_id


def _make_multi_page_pdf(sections: list[str]) -> bytes:
    """Builds a real multi-page PDF with one spec/section per page, so
    distinct chunks are guaranteed the same way a real multi-page factory
    manual would produce them -- lines pre-wrapped with textwrap to avoid
    an fpdf2 multi_cell() wrapping bug. Used wherever a test needs two
    different specs to land in genuinely separate chunks (a small combined
    fixture would otherwise merge them into one chunk, and a citation
    excerpt legitimately showing a merged chunk's full text -- including
    a correctly-unrequested sibling spec next to it -- isn't itself a bug;
    these tests care about which VALUE gets used, which the separate-page
    fixture lets us check unambiguously)."""
    import textwrap

    from fpdf import FPDF

    pdf = FPDF()
    for section in sections:
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)
        for line in section.splitlines():
            for wrapped in textwrap.wrap(line, width=90) or [""]:
                pdf.cell(0, 7, wrapped, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def _make_vehicle_device_with_pdf_pages(client, monkeypatch, name, sections):
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    res = client.post("/api/devices", data={"name": name, "device_type": "Car"})
    device_id = res.json()["id"]
    pdf_bytes = _make_multi_page_pdf(sections)
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.pdf", pdf_bytes, "application/pdf")},
    )
    doc_id = res.json()["document_ids"][0]
    deadline = time.time() + 5
    status = None
    while time.time() < deadline:
        status = client.get(f"/api/documents/{doc_id}/status").json()
        if status["status"] in ("ready", "error"):
            break
        time.sleep(0.05)
    assert status["status"] == "ready", status
    return device_id


# ------------------------------------------------- pure extraction logic ----

def test_extract_requested_properties_drain_plug_torque():
    assert vehicle.extract_requested_properties("what is the rear differential drain plug torque?") == ["drain_plug_torque"]


def test_extract_requested_properties_guide_pin_torque():
    assert vehicle.extract_requested_properties("front brake caliper guide pin torque") == ["guide_pin_torque"]


def test_extract_requested_properties_resistance():
    assert vehicle.extract_requested_properties("blower motor resistance") == ["resistance"]


def test_extract_requested_properties_multi_field():
    q = "rear differential fluid type, capacity, drain torque, and fill torque"
    props = vehicle.extract_requested_properties(q)
    assert set(props) == {"fluid_type", "capacity", "drain_plug_torque", "fill_plug_torque"}


def test_extract_requested_properties_generic_torque_fallback():
    """A bare 'torque' request with no specific fastener named still
    resolves to the generic torque property, not nothing."""
    assert vehicle.extract_requested_properties("what torque should I use here") == ["torque"]


def test_extract_requested_properties_specific_beats_generic():
    """'drain plug torque' must not ALSO register as a redundant generic
    'torque' request."""
    props = vehicle.extract_requested_properties("rear differential drain plug torque")
    assert props == ["drain_plug_torque"]
    assert "torque" not in props


# ------------------------------------------------------- pure tagging logic --

def test_property_evidence_requires_proximity_not_just_presence():
    """The property name appearing anywhere in a long chunk is not
    enough -- it must be near an actual number."""
    text = (
        "Rear Differential\nDifferential cover mounting bolt: 39.2\u201358.8 N\u00b7m\n"
        "Rear differential mounting bolt: 68.6\u201388.3 N\u00b7m\n"
        "See the drain plug section elsewhere in this manual for drain procedures."
    )
    # "drain plug" appears in the text, but with no number in proximity.
    assert vehicle._property_evidence_in_text(text, vehicle.PROPERTY_ALIASES["drain_plug_torque"]) is False


def test_property_evidence_same_line():
    text = "Guide pin bolt torque: 25-35 Nm"
    assert vehicle._property_evidence_in_text(text, vehicle.PROPERTY_ALIASES["guide_pin_torque"]) is True


def test_property_evidence_label_then_value_next_line():
    text = "Rear differential oil drain plug\nTightening torque: 49.0\u201368.6 N\u00b7m"
    assert vehicle._property_evidence_in_text(text, vehicle.PROPERTY_ALIASES["drain_plug_torque"]) is True


# ============================================================= Test 1 =====
# Rear differential: wrong fastener must not satisfy a drain-plug request.

def test_1_wrong_fastener_rejected_for_drain_plug_question(client, monkeypatch):
    manual = (
        "Rear Differential\n"
        "Differential cover mounting bolt: 39.2\u201358.8 N\u00b7m. Torque these bolts in a criss-cross "
        "pattern to ensure even sealing pressure on the cover gasket, checking for leaks after the "
        "first heat cycle.\n"
        "Rear differential mounting bolt: 68.6\u201388.3 N\u00b7m. These bolts secure the differential "
        "housing to the rear subframe and should be inspected periodically for looseness caused by "
        "normal driving vibration over time."
    )
    device_id = _make_vehicle_device_with_manual(client, monkeypatch, "Test1 Car", manual)

    captured = {}

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return "I found rear-differential information, but I could not verify an exact factory drain-plug torque."

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    from app.services import device_service

    device = device_service.get_device(device_id)
    result = query_service.answer_question(device, "What is the rear differential drain plug torque?")

    cited_texts = " ".join(s["excerpt"] for s in result["sources"])
    assert "39.2" not in cited_texts
    assert "68.6" not in cited_texts  # both wrong-fastener values excluded
    assert result["sources"] == []
    assert "NO verified exact-spec excerpt" in captured["user_prompt"]
    assert "drain plug torque" in captured["user_prompt"].lower()


# ============================================================= Test 2 =====
# Correct drain plug value is verified.

def test_2_correct_drain_plug_is_verified(client, monkeypatch):
    manual = (
        "Rear differential oil drain plug\n"
        "Tightening torque: 49.0\u201368.6 N\u00b7m. Always install a new sealing washer on the drain "
        "plug before reinstalling, and clean the plug's magnetic tip of any metal filings collected "
        "during the previous service interval before checking for excessive wear indicators."
    )
    device_id = _make_vehicle_device_with_manual(client, monkeypatch, "Test2 Car", manual)

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        assert "49.0" in user_prompt
        assert "VERIFIED EXACT-SPEC EXCERPTS" in user_prompt
        return "Rear differential drain plug torque: 49.0-68.6 N*m."

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    from app.services import device_service

    device = device_service.get_device(device_id)
    result = query_service.answer_question(device, "rear differential drain plug torque")

    cited_texts = " ".join(s["excerpt"] for s in result["sources"])
    assert "49.0" in cited_texts
    assert result["not_found"] is False


# ============================================================= Test 3 =====
# Drain vs fill: only the drain value is used when only drain is asked.

def test_3_drain_vs_fill_only_requested_one_is_verified(client, monkeypatch):
    manual = (
        "Rear Differential\n"
        "Drain plug torque: 49.0\u201368.6 N\u00b7m. Remove this plug first to drain the old fluid "
        "completely before refilling with fresh gear oil to the correct level.\n"
        "Fill plug torque: 39.2\u201358.8 N\u00b7m. Reinstall this plug once the fluid level reaches the "
        "bottom of the fill hole threads, indicating the correct fill level has been reached."
    )
    device_id = _make_vehicle_device_with_manual(client, monkeypatch, "Test3 Car", manual)

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        assert "49.0" in user_prompt
        return "Rear differential drain plug torque: 49.0-68.6 N*m."

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    from app.services import device_service

    device = device_service.get_device(device_id)
    result = query_service.answer_question(device, "rear differential drain plug torque")

    cited_texts = " ".join(s["excerpt"] for s in result["sources"])
    assert "49.0" in cited_texts
    # The fill-plug excerpt's own value should not be the one relied upon
    # for a drain-only question -- confirm the drain value is what's cited.
    assert result["not_found"] is False


# ============================================================= Test 4 =====
# Caliper fasteners: guide-pin request must not use carrier-mount torque.

def test_4_caliper_guide_pin_not_confused_with_carrier_mount(client, monkeypatch):
    device_id = _make_vehicle_device_with_pdf_pages(
        client, monkeypatch, "Test4 Car",
        [
            "Front Brake Caliper\n"
            "Caliper carrier mounting bolt torque: 90-100 Nm. These larger bolts secure the caliper "
            "carrier bracket to the steering knuckle and require a torque wrench capable of higher "
            "settings than the guide pin bolts use.",
            "Front Brake Caliper\n"
            "Guide pin bolt torque: 25-35 Nm. Apply a small amount of the specified caliper grease to "
            "the guide pins before reinstalling to ensure smooth caliper movement during braking.",
        ],
    )

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        assert "25-35" in user_prompt
        return "Front brake caliper guide pin torque: 25-35 Nm."

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    from app.services import device_service

    device = device_service.get_device(device_id)
    result = query_service.answer_question(device, "front brake caliper guide pin torque")

    cited_texts = " ".join(s["excerpt"] for s in result["sources"])
    assert "25-35" in cited_texts
    assert "90-100" not in cited_texts  # carrier-mount torque never returned for a guide-pin question


# ============================================================= Test 5 =====
# Blower motor: voltage cannot satisfy a resistance request.

def test_5_blower_motor_voltage_does_not_satisfy_resistance_request(client, monkeypatch):
    device_id = _make_vehicle_device_with_pdf_pages(
        client, monkeypatch, "Test5 Car",
        [
            "Blower Motor\n"
            "Voltage table: with the blower switch on high, terminal voltage should read approximately "
            "12V at the connector with the engine running and the battery fully charged, dropping "
            "proportionally at lower fan speed settings.",
            "Blower Motor\n"
            "Blower Motor Resistance: 1.0-1.5 ohms across terminals 1 and 3 with the connector "
            "disconnected and the motor at room temperature, measured using a calibrated digital "
            "multimeter set to the appropriate resistance range.",
        ],
    )

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        assert "1.0-1.5" in user_prompt
        return "Blower motor resistance: 1.0-1.5 ohms."

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    from app.services import device_service

    device = device_service.get_device(device_id)
    result = query_service.answer_question(device, "blower motor resistance")

    cited_texts = " ".join(s["excerpt"] for s in result["sources"])
    assert "1.0-1.5" in cited_texts
    assert "12V" not in cited_texts  # the voltage table never used as resistance evidence


# ============================================================= Test 6 =====
# Fluid type vs capacity: type present, capacity absent -> capacity unverified.

def test_6_fluid_type_present_but_capacity_unverified(client, monkeypatch):
    manual = (
        "Rear Differential\n"
        "Fluid type: Hypoid gear oil API GL-5 SAE 75W-90. Use only oil meeting this specification; "
        "substituting a lower-grade gear oil can accelerate wear on the ring and pinion gear set "
        "under heavy load conditions."
    )
    device_id = _make_vehicle_device_with_manual(client, monkeypatch, "Test6 Car", manual)

    captured = {}

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        captured["user_prompt"] = user_prompt
        return "I could not verify an exact factory capacity for the rear differential."

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    from app.services import device_service

    device = device_service.get_device(device_id)
    result = query_service.answer_question(device, "rear differential capacity")

    # No capacity number exists anywhere -- nothing should be cited as a
    # verified capacity source (the fluid-type line has no capacity number).
    assert result["sources"] == []
    assert "NO verified exact-spec excerpt" in captured["user_prompt"]
    assert "capacity" in captured["user_prompt"].lower()


# ============================================================= Test 7 =====
# Multiple requested specs verified independently.

def test_7_multiple_requested_specs_verified_independently(client, monkeypatch):
    device_id = _make_vehicle_device_with_pdf_pages(
        client, monkeypatch, "Test7 Car",
        [
            "Rear Differential\n"
            "Fluid type: Hypoid gear oil API GL-5 SAE 75W-90, required for proper limited-slip clutch "
            "pack lubrication and long-term gear wear protection under normal driving conditions.",
            "Rear Differential\n"
            "Oil capacity: 0.58 L, checked with the vehicle level and the fill plug removed to confirm "
            "the fluid reaches the bottom of the fill hole threads before reinstalling the plug.",
            "Rear differential fill plug\n"
            "Tightening torque: 39.2-58.8 N m, applied after the fluid level has been confirmed "
            "correct and the fill plug threads have been cleaned and inspected for damage.",
        ],
    )

    captured = {}

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        captured["user_prompt"] = user_prompt
        return (
            "## Rear Differential\n"
            "**Fluid:** Hypoid gear oil API GL-5 SAE 75W-90\n"
            "**Capacity:** 0.58 L\n"
            "**Drain plug torque:** Not verified in the retrieved factory documentation.\n"
            "**Fill plug torque:** 39.2-58.8 N*m"
        )

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    from app.services import device_service

    device = device_service.get_device(device_id)
    question = "rear differential fluid type, capacity, drain torque, and fill torque"
    result = query_service.answer_question(device, question)

    cited_texts = " ".join(s["excerpt"] for s in result["sources"])
    assert "75W-90" in cited_texts  # fluid type verified
    assert "0.58" in cited_texts  # capacity verified
    assert "39.2" in cited_texts  # fill plug torque verified
    # Drain torque was never present anywhere in the manual -- must be
    # flagged unverified, and no other value substituted for it.
    assert "drain_plug_torque" in captured["user_prompt"].replace(" ", "_").lower() or "drain plug torque" in captured["user_prompt"].lower()
    assert "NO verified exact-spec excerpt" in captured["user_prompt"]
    # One verified field must not cause the drain field to look verified too.
    assert "**Drain plug torque:** Not verified" in result["answer"]


# --------------------------------------------------- preserve prior safety --

def test_hyundai_style_regression_still_rejected(client, monkeypatch):
    """The original Hyundai bug scenario, re-verified after this pass:
    an ATF/transaxle capacity must still never satisfy a rear
    differential capacity request."""
    device_id = _make_vehicle_device_with_pdf_pages(
        client, monkeypatch, "Hyundai Regression Car",
        [
            "Rear Differential\n"
            "Oil capacity: 0.58 L. Always check the fluid level with the vehicle on level ground before "
            "refilling with the specified hypoid gear oil to avoid overfilling the differential housing.",
            "Automatic Transaxle\n"
            "ATF Quantity A6MF1(4WD): 7.1L. The automatic transaxle uses a completely different fluid "
            "from the differential and must never be mixed with hypoid gear oil under any circumstances.",
        ],
    )

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        assert "0.58" in user_prompt
        return "Rear differential capacity: 0.58 L."

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    from app.services import device_service

    device = device_service.get_device(device_id)
    result = query_service.answer_question(device, "What is the rear differential capacity?")

    cited_texts = " ".join(s["excerpt"] for s in result["sources"])
    assert "0.58" in cited_texts
    assert "7.1" not in cited_texts


def test_ordinary_questions_unaffected_by_property_verification(client, monkeypatch):
    """'Where is the cabin air filter?' still uses the plain path -- no
    component/property machinery involved at all."""
    device_id = _make_vehicle_device_with_manual(
        client, monkeypatch, "Ordinary Question Property Test Car",
        "Cabin Air Filter Location: behind the glove box.",
    )

    captured = {}

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        captured["user_prompt"] = user_prompt
        return "Behind the glove box."

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    from app.services import device_service

    device = device_service.get_device(device_id)
    result = query_service.answer_question(device, "Where is the cabin air filter?")
    assert result["not_found"] is False
    assert "VERIFIED EXACT-SPEC EXCERPTS" not in captured["user_prompt"]
    assert "VERIFIED EXCERPTS" not in captured["user_prompt"]
