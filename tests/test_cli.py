from mrc_screener.cli import _warn_on_mode_mismatch, main


def _setup(tmp_path, make_mrc):
    make_mrc("TS_01.st", shape=(3, 16, 16))
    txt = tmp_path / "list.txt"
    txt.write_text("1\tTS_01.tomostar\n")
    return txt


def test_export_ignores_st_without_ext(tmp_path, make_mrc, capsys):
    txt = _setup(tmp_path, make_mrc)
    main(["export", "--txt", str(txt), "--mrc-dir", str(tmp_path)])
    assert "1 rows, 0 resolved" in capsys.readouterr().out


def test_export_finds_st_with_ext(tmp_path, make_mrc, capsys):
    txt = _setup(tmp_path, make_mrc)
    main(["export", "--txt", str(txt), "--mrc-dir", str(tmp_path), "--ext", "st"])
    assert "1 rows, 1 resolved" in capsys.readouterr().out


def test_warns_when_tilt_stacks_scanned_in_tomogram_mode(capsys):
    import argparse

    _warn_on_mode_mismatch(argparse.Namespace(ext=[".st"], mode="tomogram"))
    assert "--mode tiltseries" in capsys.readouterr().err
    _warn_on_mode_mismatch(argparse.Namespace(ext=[".st"], mode="tiltseries"))
    _warn_on_mode_mismatch(argparse.Namespace(ext=None, mode="tomogram"))
    assert capsys.readouterr().err == ""
