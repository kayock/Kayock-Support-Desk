def test_home_page_loads(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Kayock Support Desk" in res.text


def test_create_and_get_device(client):
    res = client.post(
        "/api/devices",
        data={"name": "Kitchen Dishwasher", "manufacturer": "Bosch", "model_number": "SHPM88Z75N"},
    )
    assert res.status_code == 200
    device_id = res.json()["id"]

    res = client.get(f"/devices/{device_id}")
    assert res.status_code == 200
    assert "Kitchen Dishwasher" in res.text
    assert "Bosch" in res.text


def test_create_device_requires_name(client):
    res = client.post("/api/devices", data={"name": "  "})
    assert res.status_code == 400


def test_update_device(client):
    res = client.post("/api/devices", data={"name": "Router"})
    device_id = res.json()["id"]

    res = client.put(f"/api/devices/{device_id}", json={"name": "Living Room Router", "notes": "2.4GHz only upstairs"})
    assert res.status_code == 200

    res = client.get(f"/devices/{device_id}")
    assert "Living Room Router" in res.text
    assert "2.4GHz only upstairs" in res.text


def test_delete_device(client):
    res = client.post("/api/devices", data={"name": "Old TV"})
    device_id = res.json()["id"]

    res = client.delete(f"/api/devices/{device_id}")
    assert res.status_code == 200

    res = client.get(f"/devices/{device_id}")
    assert res.status_code == 404


def test_get_missing_device_404(client):
    res = client.get("/devices/999999")
    assert res.status_code == 404
