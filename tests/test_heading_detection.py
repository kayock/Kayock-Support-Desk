"""
Regression tests for the component-heading detection fix: recognizing a
component that isn't on the very first line of a chunk (a real problem
in the actual Hyundai Tucson manual, where a page might open with a
broad section title like "Driveshaft and axle" or "4 Wheel Drive (4WD)
System" and only name the actual component -- "Rear differential",
"Transfer oil drain plug" -- a bit further down).

The fix widens where the component name may be found (roughly the
opening 300-500 characters) but keeps matching restricted to short,
label-like lines rather than any substring in the body -- so a component
name mentioned only in passing, deep inside a long explanatory sentence,
still never counts. These tests confirm both halves: previously-rejected
valid passages now verify correctly, and the original cross-component
rejections (ATF capacity for a differential question, a mounting-bolt
torque for a drain-plug question) still hold.
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


# Real-Hyundai-manual-style text: a broad section title on line 1, the
# actual component named on its own short line further down.
HYUNDAI_STYLE_REAR_DIFF = (
    "Driveshaft and axle\n"
    "This section covers the driveshaft, axle assemblies, and differential units used across "
    "AWD and 4WD configurations for this vehicle platform, including inspection intervals and "
    "general service precautions applicable to all drivetrain components covered here.\n\n"
    "Rear differential\n"
    "Oil capacity: 0.58 L. Always check the fluid level with the vehicle on level ground before "
    "refilling with the specified hypoid gear oil to avoid overfilling the differential housing."
)

HYUNDAI_STYLE_TRANSFER_DRAIN = (
    "4 Wheel Drive (4WD) System\n"
    "This system distributes torque between the front and rear axles automatically based on "
    "wheel slip detection and the driver-selected mode setting for the current road conditions "
    "and terrain type being encountered during normal vehicle operation.\n\n"
    "Transfer oil drain plug\n"
    "Tightening torque: 49.0\u201368.6 N\u00b7m. Always install a new sealing washer before "
    "reinstalling the drain plug after draining the transfer case fluid completely."
)

HYUNDAI_STYLE_ATF = (
    "Driveshaft and axle\n"
    "This section covers the driveshaft, axle assemblies, and differential units used across "
    "AWD and 4WD configurations for this vehicle platform, including inspection intervals and "
    "general service precautions applicable to all drivetrain components covered here.\n\n"
    "Automatic Transaxle\n"
    "ATF Quantity A6MF1(4WD): 7.1L. The automatic transaxle uses a different fluid entirely from "
    "the differential and transfer case and must never be mixed with hypoid gear oil or transfer "
    "case fluid under any circumstances, regardless of apparent similarity between the products."
)

HYUNDAI_STYLE_MOUNTING_BOLT = (
    "Driveshaft and axle\n"
    "This section covers the driveshaft, axle assemblies, and differential units used across "
    "AWD and 4WD configurations for this vehicle platform, including inspection intervals and "
    "general service precautions applicable to all drivetrain components covered here.\n\n"
    "Rear differential\n"
    "Rear differential mounting bolt torque: 68.6\u201388.3 N\u00b7m. These bolts secure the "
    "differential carrier to the rear subframe and should be periodically checked for looseness."
)


# --------------------------------------------------- component recognition -

def test_component_recognized_when_not_on_first_line():
    """Direct unit check: 'Rear differential' is on its own short line
    well past line 1 -- must still be recognized."""
    components = vehicle.extract_component_phrases("rear differential capacity")
    verified, other = vehicle.tag_component_verification(
        [{"text": HYUNDAI_STYLE_REAR_DIFF, "distance": 0.2, "meta": {"page": 1, "filename": "m.pdf"}}],
        components,
    )
    assert len(verified) == 1
    assert len(other) == 0


def test_transfer_case_recognized_via_transfer_oil_alias():
    """'Transfer oil drain plug' (not literally 'transfer case') must
    still satisfy a 'transfer case' component request, via the explicit
    heading alias -- and it's also not on line 1."""
    components = vehicle.extract_component_phrases("transfer case drain plug torque")
    assert "transfer case" in components
    verified, other = vehicle.tag_component_verification(
        [{"text": HYUNDAI_STYLE_TRANSFER_DRAIN, "distance": 0.2, "meta": {"page": 2, "filename": "m.pdf"}}],
        components,
    )
    assert len(verified) == 1
    assert len(other) == 0


def test_broad_section_title_alone_does_not_verify_wrong_component():
    """A page titled 'Driveshaft and axle' must not, by itself, verify a
    request for a completely different component that isn't actually
    named anywhere in the passage."""
    components = vehicle.extract_component_phrases("blower motor resistance")
    verified, other = vehicle.tag_component_verification(
        [{"text": HYUNDAI_STYLE_REAR_DIFF, "distance": 0.2, "meta": {"page": 1, "filename": "m.pdf"}}],
        components,
    )
    assert len(verified) == 0
    assert len(other) == 1


# ------------------------------------------- cross-component still rejected

def test_atf_still_rejected_for_rear_differential_with_wider_window():
    """The core safety property must survive the widened search window:
    the ATF passage still has no short line naming the differential, even
    though the differential is mentioned in a long sentence."""
    components = vehicle.extract_component_phrases("rear differential capacity")
    verified, other = vehicle.tag_component_verification(
        [{"text": HYUNDAI_STYLE_ATF, "distance": 0.2, "meta": {"page": 3, "filename": "m.pdf"}}],
        components,
    )
    assert len(verified) == 0
    assert len(other) == 1


def test_mounting_bolt_still_rejected_for_drain_plug_question():
    """Component now correctly verifies (it's a real 'Rear differential'
    passage), but the property layer must still reject the mounting-bolt
    value for a drain-plug question."""
    components = vehicle.extract_component_phrases("rear differential drain plug torque")
    properties = vehicle.extract_requested_properties("rear differential drain plug torque")
    component_verified, _ = vehicle.tag_component_verification(
        [{"text": HYUNDAI_STYLE_MOUNTING_BOLT, "distance": 0.2, "meta": {"page": 4, "filename": "m.pdf"}}],
        components,
    )
    assert len(component_verified) == 1  # component correctly recognized now
    exact_spec_hits, related_hits, verified_properties = vehicle.tag_property_verification(
        component_verified, properties
    )
    assert exact_spec_hits == []  # but the mounting-bolt value still isn't drain-plug evidence
    assert len(related_hits) == 1
    assert verified_properties == set()


# ---------------------------------------------------------- end-to-end HTTP

def test_end_to_end_hyundai_style_capacity_question_now_answers(client, monkeypatch):
    """Before this fix, this exact shape of manual text (component named
    on a later line) incorrectly fell through to 'the uploaded documents
    don't contain enough information' -- confirms it's fixed end-to-end."""
    device_id = _make_vehicle_device_with_manual(
        client, monkeypatch, "Hyundai Heading Test Car", HYUNDAI_STYLE_REAR_DIFF,
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


def test_end_to_end_transfer_case_drain_plug_now_answers(client, monkeypatch):
    device_id = _make_vehicle_device_with_manual(
        client, monkeypatch, "Hyundai Transfer Heading Test Car", HYUNDAI_STYLE_TRANSFER_DRAIN,
    )

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        assert "49.0" in user_prompt
        return "Transfer case drain plug torque: 49.0-68.6 N*m."

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    from app.services import device_service

    device = device_service.get_device(device_id)
    result = query_service.answer_question(device, "What is the transfer case drain plug torque?")

    assert result["not_found"] is False
    cited_texts = " ".join(s["excerpt"] for s in result["sources"])
    assert "49.0" in cited_texts


def test_end_to_end_atf_still_rejected_with_wider_window(client, monkeypatch):
    """Combined manual with both the (later-heading) rear differential
    section and the ATF section -- confirms the wider window doesn't
    reintroduce the original Hyundai bug over real HTTP."""
    from fpdf import FPDF
    import io

    pdf = FPDF()
    for section in (HYUNDAI_STYLE_REAR_DIFF, HYUNDAI_STYLE_ATF):
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)
        import textwrap
        for line in section.splitlines():
            for wrapped in textwrap.wrap(line, width=90) or [""]:
                pdf.cell(0, 7, wrapped, new_x="LMARGIN", new_y="NEXT")
    pdf_bytes = bytes(pdf.output())

    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    res = client.post("/api/devices", data={"name": "Hyundai Combined Heading Test Car", "device_type": "Car"})
    device_id = res.json()["id"]
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

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        assert "0.58" in user_prompt
        return "Rear differential capacity: 0.58 L."

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    from app.services import device_service

    device = device_service.get_device(device_id)
    result = query_service.answer_question(device, "What is the rear differential capacity?")

    cited_texts = " ".join(s["excerpt"] for s in result["sources"])
    assert "0.58" in cited_texts
    assert "7.1" not in cited_texts  # ATF value still rejected despite the wider search window
