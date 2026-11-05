from __future__ import annotations

pytest_plugins = ["pytester"]


def test_rule_marker_is_registered_for_strict_markers(pytester):
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.rule("SHIP-003")
        def test_x():
            pass
        """
    )
    result = pytester.runpytest("--strict-markers", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
