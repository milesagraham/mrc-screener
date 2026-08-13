import json

from fastapi.testclient import TestClient

from mrc_screener.server.app import ServerConfig, create_app

from conftest import TEST_MATERIALS, requires_test_materials

pytestmark = requires_test_materials  # every test here drives the real fixture end to end

JPEG_MAGIC = b"\xff\xd8"


def build_client(tmp_path):
    config = ServerConfig(
        txt_path=TEST_MATERIALS / "input_list.txt",
        mrc_dir=TEST_MATERIALS,
        out_stem=tmp_path / "input_list",
    )
    app = create_app(config)
    return TestClient(app), config


def test_index_serves_html(tmp_path):
    client, _ = build_client(tmp_path)
    res = client.get("/")
    assert res.status_code == 200
    assert "mrc-screener" in res.text


def test_state_reports_one_resolved_and_many_unmatched(tmp_path):
    client, _ = build_client(tmp_path)
    res = client.get("/api/state")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["queue"][0]["reference"] == "tomostar/map15_ts_028.tomostar"
    assert data["queue"][0]["decision"] == "undecided"
    assert len(data["issues"]["unmatched"]) == 22


def test_row_detail_and_preview_roundtrip(tmp_path):
    client, _ = build_client(tmp_path)
    row_id = client.get("/api/state").json()["queue"][0]["row_id"]

    detail = client.get(f"/api/rows/{row_id}").json()
    assert detail["n_frames"] == 224
    assert detail["n_previews"] == 20

    res = client.get(f"/api/rows/{row_id}/preview/0")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"
    assert res.content[:2] == JPEG_MAGIC


def test_preview_position_out_of_range_is_404(tmp_path):
    client, _ = build_client(tmp_path)
    row_id = client.get("/api/state").json()["queue"][0]["row_id"]
    res = client.get(f"/api/rows/{row_id}/preview/999")
    assert res.status_code == 404


def test_unresolved_row_is_not_screenable(tmp_path):
    client, _ = build_client(tmp_path)
    unmatched_row_id = client.get("/api/state").json()["issues"]["unmatched"][0]["row_id"]
    res = client.get(f"/api/rows/{unmatched_row_id}")
    assert res.status_code == 404


def test_decision_persists_to_journal_and_survives_new_client(tmp_path):
    client, config = build_client(tmp_path)
    row_id = client.get("/api/state").json()["queue"][0]["row_id"]

    res = client.post(f"/api/rows/{row_id}/decision", json={"decision": "keep"})
    assert res.status_code == 200
    assert res.json()["counts"] == {"keep": 1, "reject": 0}

    assert config.journal_path.exists()
    entry = json.loads(config.journal_path.read_text().splitlines()[0])
    assert entry["decision"] == "keep"
    assert entry["row_id"] == row_id

    # a fresh app pointed at the same journal resumes the decision
    client2, _ = build_client(tmp_path)
    state2 = client2.get("/api/state").json()
    assert state2["queue"][0]["decision"] == "keep"
    assert state2["counts"] == {"keep": 1, "reject": 0}


def test_undo_reverts_decision(tmp_path):
    client, _ = build_client(tmp_path)
    row_id = client.get("/api/state").json()["queue"][0]["row_id"]
    client.post(f"/api/rows/{row_id}/decision", json={"decision": "reject"})

    res = client.post("/api/undo")
    assert res.status_code == 200
    data = res.json()
    assert data["row_id"] == row_id
    assert data["counts"] == {"keep": 0, "reject": 0}


def test_export_writes_screened_and_kept_files(tmp_path):
    client, config = build_client(tmp_path)
    row_id = client.get("/api/state").json()["queue"][0]["row_id"]
    client.post(f"/api/rows/{row_id}/decision", json={"decision": "keep"})

    res = client.post("/api/export")
    assert res.status_code == 200
    data = res.json()
    screened_path = tmp_path / "input_list_screened.txt"
    kept_path = tmp_path / "input_list_kept.txt"
    assert str(screened_path) == data["screened_path"]
    assert str(kept_path) == data["kept_path"]
    assert "keep" in screened_path.read_text()
    assert "map15_ts_028" in kept_path.read_text()
