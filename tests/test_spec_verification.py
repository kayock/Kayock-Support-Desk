"""
Regression tests for the technical-specification cross-component bug: a
real value from one component (e.g. an automatic transaxle's ATF
capacity) must never be reported as belonging to a different component
(e.g. a rear differential) just because both were retrieved together as
"drivetrain-related". See app.services.vehicle.tag_component_verification
and app.services.query_service.answer_question's spec_mode path.

These tests mock retrieval and the LLM directly so they're deterministic
and don't depend on real model quality -- the property under test is
whether Kayock's own pipeline ever *offers* an unverified value as if it
were verified (via prompt content and/or citation sources), not whether
a hypothetical real model would follow instructions perfectly.

Note: manual text fixtures below use realistic paragraph-length filler
per section (not just a bare heading + number) so the chunker actually
splits each component into its own chunk, the same way a real multi-
paragraph manual page would -- a two-line fixture would otherwise land
entirely in one chunk and defeat the point of the test.
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
    """Builds a real multi-page PDF with one section per page (same
    cell()-based approach as conftest.py's encrypted-PDF fixture, which
    avoids an fpdf2 multi_cell() wrapping bug -- lines are pre-wrapped
    with textwrap first). Used where a test needs distinct page numbers
    for distinct chunks -- a single-page .txt fixture would put multiple
    chunks under the same page number, colliding in citation dedup the
    way no real multi-page factory manual PDF ever would."""
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


REAR_DIFF_SECTION = (
    "Rear Differential\n"
    "Oil capacity: 0.58 L. Always check the fluid level with the vehicle on level ground and "
    "the drain/fill plugs accessible from underneath the vehicle. Use only the specified hypoid "
    "gear oil for this application; using the wrong fluid type can damage the ring and pinion "
    "gear set over time. Inspect the differential housing for any signs of leakage around the "
    "axle seals before refilling, and torque the fill plug to the specified value after "
    "refilling to avoid stripping the threads in the aluminum housing. Replace the drain plug "
    "gasket whenever the plug is removed, and never reuse a damaged crush washer. "
    "Periodically inspect the ring and pinion backlash and the condition of the limited-slip "
    "clutch packs if equipped, replacing the additive if the differential is refilled with "
    "standard hypoid oil rather than a pre-mixed limited-slip formulation. Road testing after "
    "service should include a check for unusual noise under acceleration and deceleration."
)

TRANSFER_CASE_SECTION = (
    "Transfer Case\n"
    "Oil capacity: 0.65 L. The transfer case is a separate unit from both the differential and "
    "the transaxle, mounted to the rear of the transmission assembly. Check the fluid level "
    "through the fill plug opening with the vehicle level; fluid should be at the bottom edge of "
    "the fill hole. Use only the manufacturer-specified transfer case fluid, which differs from "
    "both the differential gear oil and the automatic transmission fluid used elsewhere in the "
    "drivetrain. Inspect the output shaft seals for leaks during routine service intervals, and "
    "verify the transfer case mounting bolts are torqued to specification. A worn output shaft "
    "seal is a common source of drivetrain fluid leaks and should be replaced with a new seal "
    "and gasket any time the transfer case is removed for service."
)

ATF_SECTION = (
    "Automatic Transaxle\n"
    "ATF Quantity A6MF1(4WD): 7.1L. The automatic transaxle uses a different fluid entirely from "
    "the differential and transfer case and must never be mixed with hypoid gear oil or transfer "
    "case fluid. Always confirm the transaxle identification tag before selecting a replacement "
    "fluid, since different transaxle models used across the model years require different ATF "
    "specifications, and using the wrong one can cause shifting problems or internal damage over "
    "an extended period of normal driving."
)


# ------------------------------------------------- pure component logic ----

def test_is_spec_question_detects_technical_terms():
    assert vehicle.is_spec_question("what is the rear differential capacity") is True
    assert vehicle.is_spec_question("what torque for the caliper bolts") is True
    assert vehicle.is_spec_question("where is the cabin air filter") is False


def test_extract_component_phrases_multi_component():
    q = (
        "For my 2017 Hyundai Tucson SE 2.0L 4WD, what fluid type, capacity, and drain/fill plug "
        "torque specifications does the factory service manual give for the rear differential "
        "and transfer case?"
    )
    components = vehicle.extract_component_phrases(q)
    assert "rear differential" in components
    assert "transfer case" in components


def test_extract_component_phrases_single_component_alias():
    assert vehicle.extract_component_phrases("blower motor resistance") == ["blower motor"]


def test_extract_component_phrases_falls_back_to_bigram_when_no_alias():
    assert "oxygen sensor" in vehicle.extract_component_phrases("oxygen sensor B1S2 resistance spec")


# ---------------------------------------------------- Test A: Hyundai bug --

def test_reject_transaxle_capacity_for_rear_differential_question():
    """The exact real-world failure: an ATF/transaxle capacity value must
    never be classified as verified for a 'rear differential capacity'
    question, even though it's genuinely present and drivetrain-related."""
    question = "rear differential capacity"
    components = vehicle.extract_component_phrases(question)
    hits = [
        {"text": REAR_DIFF_SECTION, "distance": 0.3, "meta": {"page": 10, "filename": "manual.pdf"}},
        {"text": ATF_SECTION, "distance": 0.15, "meta": {"page": 5, "filename": "manual.pdf"}},
    ]
    verified, other = vehicle.tag_component_verification(hits, components)

    verified_pages = {h["meta"]["page"] for h in verified}
    other_pages = {h["meta"]["page"] for h in other}
    assert 10 in verified_pages
    assert 5 not in verified_pages
    assert 5 in other_pages
    assert any("0.58" in h["text"] for h in verified)
    assert not any("0.58" in h["text"] for h in other)


def test_answer_question_cites_only_the_verified_component_passage(client, monkeypatch):
    """End-to-end through query_service.answer_question: the ATF/transaxle
    passage must never appear in the returned sources for a rear
    differential question, even though it was retrieved. ('capacity' is a
    recognized property, so this now exercises the property-aware exact-
    spec path, not just component verification -- same safety property,
    stricter mechanism.)"""
    device_id = _make_vehicle_device_with_manual(
        client, monkeypatch, "Spec Verification Test Car",
        REAR_DIFF_SECTION + "\n\n" + ATF_SECTION,
    )

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        assert "VERIFIED EXACT-SPEC EXCERPTS" in user_prompt
        assert "0.58" in user_prompt
        return "Rear differential capacity: 0.58 L."

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    from app.services import device_service

    device = device_service.get_device(device_id)
    result = query_service.answer_question(device, "What is the rear differential capacity?")

    assert result["not_found"] is False
    cited_texts = " ".join(s["excerpt"] for s in result["sources"])
    assert "0.58" in cited_texts
    assert "7.1" not in cited_texts  # the transaxle passage was never cited


# --------------------------------------------- Test B: multi-component -----

def test_multiple_drivetrain_components_kept_separate(client, monkeypatch):
    """Real multi-page PDF (one component per page) -- so each component's
    citation gets its own distinct page number, exactly like a real
    factory manual, rather than colliding under a single page number the
    way a one-page .txt fixture would."""
    device_id = _make_vehicle_device_with_pdf_pages(
        client, monkeypatch, "Multi Component Test Car",
        [REAR_DIFF_SECTION, TRANSFER_CASE_SECTION, ATF_SECTION],
    )

    captured = {}

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        captured["user_prompt"] = user_prompt
        return "## Rear Differential\n**Capacity:** 0.58 L\n## Transfer Case\n**Capacity:** 0.65 L"

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    from app.services import device_service

    device = device_service.get_device(device_id)
    question = "What is the fluid capacity for the rear differential and transfer case?"
    result = query_service.answer_question(device, question)

    assert result["not_found"] is False
    cited_texts = " ".join(s["excerpt"] for s in result["sources"])
    assert "0.58" in cited_texts
    assert "0.65" in cited_texts
    assert "7.1" not in cited_texts
    assert "component: rear differential" in captured["user_prompt"]
    assert "component: transfer case" in captured["user_prompt"]


# ------------------------------------------------------ Test C: wrong motor

def test_blower_motor_question_excludes_other_motor_specs():
    question = "blower motor resistance"
    components = vehicle.extract_component_phrases(question)
    hits = [
        {"text": "Blower Motor Resistor: 1.0-1.5 ohms across terminals 1 and 3", "distance": 0.3, "meta": {"page": 2, "filename": "m.pdf"}},
        {"text": "Wiper Motor resistance test: 3.2 ohms nominal", "distance": 0.2, "meta": {"page": 3, "filename": "m.pdf"}},
        {"text": "Washer Motor circuit resistance: 5.0 ohms", "distance": 0.25, "meta": {"page": 4, "filename": "m.pdf"}},
        {"text": "4WD Hydraulic Motor pressure test procedure", "distance": 0.28, "meta": {"page": 5, "filename": "m.pdf"}},
    ]
    verified, other = vehicle.tag_component_verification(hits, components)
    verified_pages = {h["meta"]["page"] for h in verified}
    assert verified_pages == {2}
    other_pages = {h["meta"]["page"] for h in other}
    assert other_pages == {3, 4, 5}


# ---------------------------------------------------- Test D: wrong fastener

def test_brake_caliper_torque_excludes_unrelated_fastener():
    question = "front brake caliper mounting torque"
    components = vehicle.extract_component_phrases(question)
    hits = [
        {"text": "Front Brake Caliper mounting bolts: 90-100 Nm", "distance": 0.3, "meta": {"page": 1, "filename": "m.pdf"}},
        {"text": "Rear Differential mounting bolt torque: 60-70 Nm", "distance": 0.2, "meta": {"page": 8, "filename": "m.pdf"}},
        {"text": "Suspension control arm bolt torque: 110 Nm", "distance": 0.25, "meta": {"page": 9, "filename": "m.pdf"}},
    ]
    verified, other = vehicle.tag_component_verification(hits, components)
    verified_pages = {h["meta"]["page"] for h in verified}
    assert verified_pages == {1}
    assert 8 not in verified_pages
    assert 9 not in verified_pages


# ------------------------------------------------ Test E: missing exact value

def test_missing_exact_value_reports_not_verified_not_a_guess(client, monkeypatch):
    """A passage mentions the rear differential exists (as part of an AWD
    overview) but gives no capacity number; the only number in the
    manual is the (wrong-component) ATF value in its own separate chunk.
    The pipeline must never let that wrong number reach the citable
    sources for this question, and the prompt must instruct the model to
    say it couldn't verify rather than guess. ('capacity' is a recognized
    property, so this exercises the property-aware exact-spec path.)"""
    overview_section = (
        "Drivetrain Overview\n"
        "This AWD system uses a rear differential and a transfer case to distribute power to all "
        "four wheels. Torque is sent from the transmission through the transfer case to both the "
        "front and rear axles. For detailed service specifications, refer to the individual "
        "component sections. General inspection intervals for the drivetrain are listed in the "
        "maintenance schedule section of this manual, along with recommended fluid change "
        "intervals for each major drivetrain component. The AWD control module monitors wheel "
        "speed sensors at all four corners and can vary torque distribution between the front and "
        "rear axles automatically depending on road conditions and detected wheel slip, without "
        "any manual intervention required from the driver under normal operating conditions."
    )
    device_id = _make_vehicle_device_with_manual(
        client, monkeypatch, "Missing Spec Test Car",
        overview_section + "\n\n" + ATF_SECTION,
    )

    captured = {}

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        return "I found rear-differential information, but I could not verify an exact factory capacity."

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    from app.services import device_service

    device = device_service.get_device(device_id)
    result = query_service.answer_question(device, "What is the rear differential capacity?")

    # The wrong-component (ATF/transaxle) value must never be cited here.
    cited_texts = " ".join(s["excerpt"] for s in result["sources"])
    assert "7.1" not in cited_texts
    assert result["sources"] == []  # no passage verified both component AND capacity
    assert "could not be verified" in captured["system_prompt"].lower()
    assert "capacity" in captured["user_prompt"].lower()
    assert "NO verified exact-spec excerpt" in captured["user_prompt"]


# --------------------------------------------------- normal questions intact

def test_ordinary_explanatory_question_not_forced_through_spec_mode(client, monkeypatch):
    """'Where is the cabin air filter?' must NOT trigger the strict
    component-verification path -- it's not a spec question."""
    assert vehicle.is_spec_question("Where is the cabin air filter?") is False

    device_id = _make_vehicle_device_with_manual(
        client, monkeypatch, "Ordinary Question Test Car",
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
    assert "VERIFIED EXCERPTS" not in captured["user_prompt"]


def test_non_vehicle_device_never_uses_spec_verification(client, monkeypatch, sample_txt_bytes):
    """Appliance devices must behave exactly as before -- spec_mode only
    ever applies to vehicle devices."""
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        assert "VERIFIED EXCERPTS" not in user_prompt
        assert "component-verification" not in system_prompt.lower()
        return "The drain pump is blocked."

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    res = client.post("/api/devices", data={"name": "Regression Appliance Spec Test"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", sample_txt_bytes, "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    deadline = time.time() + 5
    status = None
    while time.time() < deadline:
        status = client.get(f"/api/documents/{doc_id}/status").json()
        if status["status"] in ("ready", "error"):
            break
        time.sleep(0.05)
    assert status["status"] == "ready"

    res = client.post(f"/api/devices/{device_id}/ask", json={"question": "What torque should I use for E24?"})
    assert res.status_code == 200
    assert res.json()["not_found"] is False
