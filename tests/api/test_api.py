from __future__ import annotations

import pytest
from api.app import create_app
from fastapi.testclient import TestClient
from sparkplan.catalog import load_catalog
from sparkplan.llm import FakeLLM

VALID_SPEC = {
    "idea": "desk weather station",
    "controller": "esp32_devkit_v4",
    "parts": [
        {"instance_id": "env", "part_id": "dht22"},
        {"instance_id": "screen", "part_id": "ssd1306_oled"},
    ],
    "connections": [
        {"instance_id": "env", "peripheral_pin": "DATA", "controller_pin": "GPIO4"},
        {"instance_id": "screen", "peripheral_pin": "SDA", "controller_pin": "GPIO21"},
        {"instance_id": "screen", "peripheral_pin": "SCL", "controller_pin": "GPIO22"},
    ],
    "actions": [
        {
            "action_id": "read_temp",
            "kind": "read",
            "instance_id": "env",
            "api": "read_temperature_c",
        },
        {
            "action_id": "show",
            "kind": "write",
            "instance_id": "screen",
            "api": "print_line1",
            "consumes": "read_temp",
        },
    ],
}


@pytest.fixture()
def client():
    app = create_app(llm=FakeLLM(script=[VALID_SPEC]), catalog=load_catalog())
    return TestClient(app)


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_catalog_endpoint(client):
    data = client.get("/v1/catalog").json()
    ids = {p["part_id"] for p in data["parts"]}
    assert "esp32_devkit_v4" in ids
    assert "dht22" in ids
    assert "display_text" in data["capabilities"]


def test_plan_endpoint_ok(client):
    resp = client.post("/v1/plan", json={"idea": "desk weather station"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["spec"]["parts"][0]["part_id"] == "dht22"


def test_plan_refusal(client):
    resp = client.post("/v1/plan", json={"idea": "a camera drone"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "refused"


def test_build_and_download_artifacts(client):
    resp = client.post("/v1/build", json={"idea": "desk weather station"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    build_id = body["id"]

    detail = client.get(f"/v1/builds/{build_id}").json()
    assert detail["status"] == "ok"

    stl = client.get(f"/v1/builds/{build_id}/enclosure/base.stl")
    assert stl.status_code == 200
    assert len(stl.content) > 1000

    ino = client.get(f"/v1/builds/{build_id}/firmware/sparkplan.ino")
    assert ino.status_code == 200
    assert "#include <DHT.h>" in ino.text

    guide = client.get(f"/v1/builds/{build_id}/guide.md")
    assert guide.status_code == 200
    assert "Bill of materials" in guide.text


def test_unknown_build_404(client):
    assert client.get("/v1/builds/nope").status_code == 404
    assert client.get("/v1/builds/nope/enclosure/base.stl").status_code == 404
