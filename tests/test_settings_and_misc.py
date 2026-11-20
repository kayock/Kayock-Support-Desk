def test_settings_page_loads(client):
    res = client.get("/settings")
    assert res.status_code == 200
    assert "Advanced AI Settings" in res.text


def test_update_and_read_settings(client):
    res = client.post(
        "/api/settings",
        json={"llm_model": "mistral", "embedding_model": "mxbai-embed-large"},
    )
    assert res.status_code == 200

    res = client.get("/settings")
    assert "mistral" in res.text
    assert "mxbai-embed-large" in res.text


def test_network_info(client):
    res = client.get("/api/network")
    assert res.status_code == 200
    data = res.json()
    assert data["lan_url"].startswith("http://")


def test_qr_code_endpoint(client):
    res = client.get("/api/qrcode.png")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"


def test_export_backup_zip(client):
    res = client.get("/api/export")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/zip"


def test_unsupported_file_type_rejected(client):
    res = client.post("/api/devices", data={"name": "Bad File Device"},
                       files={"files": ("manual.exe", b"not a manual", "application/octet-stream")})
    assert res.status_code == 400
