"""Tests for the stderr progress-bar helpers in __main__.py.

__main__.py is the root entry script, not a package module, so it is loaded
from its file path.  Importing it under a non-"__main__" name means the
`if __name__ == "__main__"` guard does not run main()."""

import importlib.util
import pathlib
import time

_spec = importlib.util.spec_from_file_location(
    "rv_main", pathlib.Path(__file__).resolve().parent.parent / "__main__.py"
)
rv_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv_main)


class TestFmtDuration:
    def test_seconds(self):
        assert rv_main._fmt_duration(12.34) == "12.3s"

    def test_minutes(self):
        assert rv_main._fmt_duration(83) == "1m23s"

    def test_hours(self):
        assert rv_main._fmt_duration(3720) == "1h02m"


class TestRenderProgress:
    def test_full_bar_at_completion(self, capsys):
        rv_main._render_progress(100, 100, start=time.monotonic())
        err = capsys.readouterr().err
        assert err.startswith("\r")
        assert "100.0%" in err
        assert "#" * 30 in err          # fully filled bar
        assert "ETA 0.0s" in err

    def test_partial_bar_has_eta(self, capsys):
        rv_main._render_progress(50, 100, start=time.monotonic() - 10)
        err = capsys.readouterr().err
        assert " 50.0%" in err
        assert "#" * 15 in err          # half-filled bar
        assert "ETA" in err

    def test_zero_total_is_safe(self, capsys):
        # No work: treat as complete rather than dividing by zero.
        rv_main._render_progress(0, 0, start=time.monotonic())
        err = capsys.readouterr().err
        assert "100.0%" in err
