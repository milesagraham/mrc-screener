"""End-to-end server tests on synthetic data (no test_materials needed)."""

from fastapi.testclient import TestClient

from mrc_screener.core.preview import PreviewCache, get_preview_jpeg
from mrc_screener.server.app import ServerConfig, create_app


def _setup(tmp_path, make_mrc):
    make_mrc("TS_01.st", shape=(7, 32, 32))
    make_mrc("TS_01_rec.mrc", shape=(40, 32, 32))  # not scanned with --ext .st
    (tmp_path / "TS_01.rawtlt").write_text("\n".join(str(a) for a in [0, 3, -3, 6, -6, 9, -9]))
    txt = tmp_path / "list.txt"
    txt.write_text("1\tTS_01.tomostar\n")
    return txt


def test_tiltseries_mode_serves_every_tilt_in_angle_order(tmp_path, make_mrc):
    txt = _setup(tmp_path, make_mrc)
    config = ServerConfig(
        txt_path=txt, mrc_dir=tmp_path, out_stem=tmp_path / "out" / "list", extensions=(".st",), mode="tiltseries"
    )
    client = TestClient(create_app(config))

    state = client.get("/api/state").json()
    assert state["mode"] == "tiltseries"
    assert state["extensions"] == [".st"]
    assert state["queue"][0]["filename"] == "TS_01.st"

    detail = client.get("/api/rows/1").json()
    assert detail["n_previews"] == 7
    assert detail["tilt_angles"] == [-9, -6, -3, 0, 3, 6, 9]
    assert detail["start_pos"] == 3
    assert detail["angle_source"] == "TS_01.rawtlt"

    for pos in range(7):
        res = client.get(f"/api/rows/1/preview/{pos}")
        assert res.status_code == 200 and res.content[:2] == b"\xff\xd8"
    assert client.get("/api/rows/1/preview/7").status_code == 404


def test_default_config_still_scans_mrc_in_tomogram_mode(tmp_path, make_mrc):
    txt = _setup(tmp_path, make_mrc)
    client = TestClient(create_app(ServerConfig(txt_path=txt, mrc_dir=tmp_path, out_stem=tmp_path / "out" / "list")))
    state = client.get("/api/state").json()
    assert state["mode"] == "tomogram"
    assert state["queue"][0]["filename"] == "TS_01_rec.mrc"
    detail = client.get("/api/rows/1").json()
    assert detail["n_previews"] == 20 and detail["tilt_angles"] is None


def test_preview_cache_distinguishes_contrast_fraction(tmp_path, make_mrc):
    path = make_mrc("TS_01.st", shape=(8, 32, 32))
    cache_dir = tmp_path / "cache"
    cache = PreviewCache(cache_dir)
    get_preview_jpeg(path, 0, cache, target_long_axis=16, contrast_fraction=0.5)
    get_preview_jpeg(path, 0, cache, target_long_axis=16, contrast_fraction=1.0)
    assert len(list(cache_dir.rglob("*.jpg"))) == 2
