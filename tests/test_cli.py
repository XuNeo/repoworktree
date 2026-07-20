"""Tests for rwt CLI parser behavior."""

import pytest

import repoworktree.__main__ as rwt_main


def test_help_includes_package_version(monkeypatch):
    monkeypatch.setattr(rwt_main, "_package_version", lambda: "1.2.3-test")

    help_text = rwt_main.build_parser().format_help()

    assert "repoworktree 1.2.3-test" in help_text


def test_version_flag_prints_package_version(monkeypatch, capsys):
    monkeypatch.setattr(rwt_main, "_package_version", lambda: "1.2.3-test")

    with pytest.raises(SystemExit) as exc:
        rwt_main.main(["--version"])

    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "rwt 1.2.3-test"
