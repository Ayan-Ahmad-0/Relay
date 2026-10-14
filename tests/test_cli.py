"""Tests for Relay CLI entrypoints."""

import pytest

from relay.cli import main


def test_cli_help(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "Relay: Crash-resilient" in captured.out
