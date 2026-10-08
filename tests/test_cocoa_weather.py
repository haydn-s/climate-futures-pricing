"""Regression tests for the cocoa analysis's named-country diagnostics."""

from __future__ import annotations

import pandas as pd
import pytest

from analysis.cocoa_weather import correlate, paired_shortfalls


def test_primary_pair_is_selected_by_name_not_mapping_order() -> None:
    years = [2020, 2021, 2022, 2023]
    shortfalls = {
        "Cameroon": pd.Series([1.0, 2.0, 3.0, 4.0], index=years),
        "Cote d'Ivoire": pd.Series([-1.0, -2.0, -3.0, -4.0], index=years),
        "Ghana": pd.Series([1.0, 2.0, 3.0, 4.0], index=years),
    }

    pair, correlation = paired_shortfalls(shortfalls)

    assert list(pair.columns) == ["Cote d'Ivoire", "Ghana"]
    assert correlation == pytest.approx(-1.0)


def test_primary_pair_fails_loudly_when_a_named_series_is_missing() -> None:
    with pytest.raises(ValueError, match="Ghana"):
        paired_shortfalls({"Cote d'Ivoire": pd.Series([1.0, 2.0, 3.0])})


def test_constant_measure_is_uninformative_without_emitting_a_correlation() -> None:
    constant = pd.Series([0.0] * 10)
    varying = pd.Series(range(10), dtype="float64")

    correlation, count, p_value = correlate(constant, varying)

    assert pd.isna(correlation)
    assert count == 10
    assert pd.isna(p_value)
