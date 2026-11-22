from __future__ import annotations

from fastapi.testclient import TestClient

from adguard_pipeline.api import app

client = TestClient(app)


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["matcher_version"].startswith("dict:")


def test_version_lists_families():
    response = client.get("/v1/version")
    assert response.status_code == 200
    body = response.json()
    assert body["pipeline_version"]
    assert body["pattern_count"] > 100
    families = {entry["family"] for entry in body["families"]}
    assert {"absolute_terms", "false_advertising"} <= families


def test_normalize_endpoint():
    response = client.post("/v1/normalize", json={"text": "最\u200b佳选择"})
    assert response.status_code == 200
    body = response.json()
    assert body["normalized_text"] == "最佳选择"
    assert "strip-zero-width" in "".join(body["steps"])


def test_match_endpoint_returns_spans():
    text = "本店最😀好，欢迎选购"
    response = client.post("/v1/match", json={"text": text})
    assert response.status_code == 200
    body = response.json()
    assert body["hit"] is True
    detection = body["detections"][0]
    assert detection["family"] == "absolute_terms"
    assert detection["span"] == [2, 5]
    assert text[detection["span"][0] : detection["span"][1]] == "最😀好"


def test_match_endpoint_clean_copy():
    response = client.post("/v1/match", json={"text": "本店商品质量可靠，支持七天无理由退换。"})
    assert response.status_code == 200
    assert response.json()["hit"] is False


def test_invalid_payload_rejected():
    assert client.post("/v1/match", json={}).status_code == 422
    assert client.post("/v1/normalize", json={"text": ""}).status_code == 422
