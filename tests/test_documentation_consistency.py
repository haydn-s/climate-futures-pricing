"""Prose must not drift from the committed results it describes.

This project's argument rests on numbers that appear in several places at once:
a report, the README, a model card, and a slide. Nothing stops those copies
diverging when an analysis is rerun, and a deck that contradicts its own
repository is worse than one with no numbers in it.

Each claim below names the committed CSV cell it comes from and the documents
that quote it, in exactly the format each document uses. A rerun that moves a
result fails here, naming the claim and the files to update, rather than
surfacing when somebody reads the deck next to the report.

TWO METRICS COEXIST AND BOTH ARE CORRECT. The original yield study reports
leave-one-year-out R² against the held-out mean (0.73, or 0.46 without 2012);
the forward evaluation reports R² against each fold's own trend forecast
(0.748, or 0.476). They are different denominators on different folds, and the
README says so. Do not "fix" one into the other.
"""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "analysis"


def _normalise(text: str) -> str:
    """Treat the typographic minus as the ASCII one.

    Generated reports format with Python, which emits U+002D; the hand-written
    README and model cards use U+2212 because it reads better beside a plus.
    The values are the same and the distinction is purely typographic, so a
    comparison that cared about it would report drift that does not exist.
    """
    return text.replace("\u2212", "-")


def _cell(csv: str, where: dict, column: str) -> float:
    frame = pd.read_csv(ANALYSIS / csv)
    mask = pd.Series(True, index=frame.index)
    for key, value in where.items():
        assert key in frame.columns, f"{csv} has no column {key!r}"
        mask &= frame[key] == value
    rows = frame.loc[mask]
    assert len(rows) == 1, f"{csv}: {where} matched {len(rows)} rows"
    return float(rows.iloc[0][column])


# Shorthand for the two filters used most often below.
FORWARD = {"sample": "all_years", "feature_set": "counties_drought"}
FORWARD_EX = {"sample": "exclude_2012", "feature_set": "counties_drought"}
NOWCAST_AUG = {"issue_month": 8, "feature_set": "points_drought"}

README = "README.md"
STRATA = "analysis/corn_yield_strata_report.md"
INTERVALS = "analysis/corn_yield_intervals_report.md"
DECISIONS = "analysis/corn_yield_decisions_report.md"
PRICE = "analysis/corn_price_information_report.md"
FORWARD_REPORT = "analysis/corn_yield_forward_validation_report.md"
RISK_CARD = "models/corn-supply-risk-counties-drought-v1/MODEL_CARD.md"
SIGNAL_CARD = "models/corn-wheat-all-public-boosting-v1/MODEL_CARD.md"
DECK = "presentation/final/build_deck.js"

# (claim, csv, filter, column, formatter, documents that must quote it)
CLAIMS = [
    ("forward R2, all years", "corn_yield_forward_validation_results.csv",
     FORWARD, "r2_vs_trend", lambda v: f"{v:+.3f}",
     [README, FORWARD_REPORT, STRATA, RISK_CARD]),
    ("forward R2, excluding 2012", "corn_yield_forward_validation_results.csv",
     FORWARD_EX, "r2_vs_trend", lambda v: f"{v:+.3f}",
     [README, FORWARD_REPORT, STRATA, RISK_CARD]),
    ("influence floor, all years", "corn_yield_strata_influence_summary.csv",
     {**FORWARD, "study": "forward"}, "r2_min_without_one_year",
     lambda v: f"{v:+.3f}", [README, STRATA, RISK_CARD]),
    ("influence ceiling, all years", "corn_yield_strata_influence_summary.csv",
     {**FORWARD, "study": "forward"}, "r2_max_without_one_year",
     lambda v: f"{v:+.3f}", [README, STRATA, RISK_CARD]),
    ("influence floor, excluding 2012", "corn_yield_strata_influence_summary.csv",
     {**FORWARD_EX, "study": "forward"}, "r2_min_without_one_year",
     lambda v: f"{v:+.3f}", [README, STRATA, RISK_CARD]),
    ("severity high tercile", "corn_yield_strata_buckets.csv",
     {**FORWARD, "study": "forward", "dimension": "severity", "bucket": "high"},
     "r2_vs_trend", lambda v: f"{v:+.3f}", [README, STRATA, RISK_CARD]),
    ("severity low tercile, excluding 2012", "corn_yield_strata_buckets.csv",
     {**FORWARD_EX, "study": "forward", "dimension": "severity",
      "bucket": "low"}, "r2_vs_trend", lambda v: f"{v:+.3f}",
     [README, STRATA, RISK_CARD]),
    ("interval width, full season 90%", "corn_yield_intervals_coverage.csv",
     {**FORWARD, "study": "forward", "method": "jackknife_plus",
      "nominal_level": 0.9}, "mean_width_pp_of_trend", lambda v: f"{v:.1f}",
     [README, INTERVALS, RISK_CARD]),
    ("interval width, August nowcast 90%", "corn_yield_intervals_coverage.csv",
     {**NOWCAST_AUG, "sample": "all_years", "study": "nowcast",
      "method": "jackknife_plus", "nominal_level": 0.9},
     "mean_width_pp_of_trend", lambda v: f"{v:.1f}",
     [README, INTERVALS, RISK_CARD]),
    ("provisioning saving, all years", "corn_yield_decisions_scores.csv",
     {**NOWCAST_AUG, "sample": "all_years", "information_set": "nowcast",
      "penalty_ratio": 4.0, "strategy": "model"}, "cost_reduction",
     lambda v: f"{v * 100:.1f}", [README, DECISIONS, RISK_CARD]),
    ("provisioning saving, excluding 2012", "corn_yield_decisions_scores.csv",
     {**NOWCAST_AUG, "sample": "exclude_2012", "information_set": "nowcast",
      "penalty_ratio": 4.0, "strategy": "model"}, "cost_reduction",
     lambda v: f"{v * 100:.1f}", [README, DECISIONS, RISK_CARD]),
    ("horse-race price R2", "corn_price_information_race.csv",
     {**NOWCAST_AUG, "sample": "all_years",
      "price_source": "december_contract"}, "price_r2", lambda v: f"{v:.3f}",
     [README, PRICE, SIGNAL_CARD]),
    ("horse-race incremental R2", "corn_price_information_race.csv",
     {**NOWCAST_AUG, "sample": "all_years",
      "price_source": "december_contract"}, "incremental_r2",
     lambda v: f"{v:+.3f}", [README, PRICE, SIGNAL_CARD, RISK_CARD]),
    ("break-even R2 at 10 bps", "corn_price_information_budget.csv",
     {"horizon_days": 5, "cost_bps": 10.0}, "break_even_r2",
     lambda v: f"{v:.5f}", [README, PRICE, SIGNAL_CARD]),
    ("detectable R2", "corn_price_information_budget.csv",
     {"horizon_days": 5, "cost_bps": 10.0}, "detectable_r2",
     lambda v: f"{v:.5f}", [README, PRICE, SIGNAL_CARD]),
    # Rounding conventions are the quiet way two tables diverge: 70.5% was
    # written as 71% in one place and 70.5% in the other, which reads as a
    # different number. Both now use the report's one-decimal form.
    ("bootstrap probability, excluding 2012 at 8:1",
     "corn_yield_decisions_bootstrap.csv",
     {**NOWCAST_AUG, "sample": "exclude_2012", "information_set": "nowcast",
      "penalty_ratio": 8.0}, "probability_positive",
     lambda v: f"{v * 100:.1f}%", [README, DECISIONS]),
    ("bootstrap probability, all years at 8:1",
     "corn_yield_decisions_bootstrap.csv",
     {**NOWCAST_AUG, "sample": "all_years", "information_set": "nowcast",
      "penalty_ratio": 8.0}, "probability_positive",
     lambda v: f"{v * 100:.1f}%", [README, DECISIONS]),
    ("bootstrap floor, all years at 8:1",
     "corn_yield_decisions_bootstrap.csv",
     {**NOWCAST_AUG, "sample": "all_years", "information_set": "nowcast",
      "penalty_ratio": 8.0}, "bootstrap_p025", lambda v: f"{v:+.3f}",
     [README, DECISIONS]),
    ("interval coverage, August nowcast 90%",
     "corn_yield_intervals_coverage.csv",
     {**NOWCAST_AUG, "sample": "all_years", "study": "nowcast",
      "method": "jackknife_plus", "nominal_level": 0.9}, "covered",
     lambda v: f"{int(v)}/16", [README, INTERVALS, RISK_CARD]),
]


@pytest.mark.parametrize("claim,csv,where,column,fmt,documents", CLAIMS,
                         ids=[claim[0] for claim in CLAIMS])
def test_every_document_quotes_the_committed_value(claim, csv, where, column,
                                                   fmt, documents):
    expected = fmt(_cell(csv, where, column))
    missing = [doc for doc in documents
               if _normalise(expected) not in _normalise((ROOT / doc).read_text())]
    assert not missing, (
        f"{claim}: committed value formats as {expected!r}, which is missing "
        f"from {missing}. Rerun the analysis, then update the prose.")


def test_the_deck_derives_its_numbers_rather_than_hardcoding_them():
    """The builder must read deck_data.json, not carry literals of its own."""
    source = (ROOT / DECK).read_text()
    body = source[source.index("const NOTECARDS"):]
    # Any decimal in the slide or notecard text should come from D.*, so a bare
    # three-or-more-digit decimal is a hardcoded result that can drift.
    literals = re.findall(r"(?<![\w.$])\d+\.\d{3,}(?![\w'])", body)
    assert not literals, (
        f"hardcoded decimals in the deck body: {sorted(set(literals))}. "
        "Numbers belong in deck_data.py so they cannot contradict the repo.")


def test_the_two_r2_metrics_are_both_present_and_distinguished():
    """0.73 and 0.748 are different metrics; the README must not conflate them."""
    readme = (ROOT / README).read_text()
    assert "0.73" in readme and "0.748" in readme, (
        "the README should carry both the leave-one-year-out R² and the "
        "forward R² against trend")
    assert "different metric" in readme or "not be compared" in readme, (
        "the README must say the two R² metrics are not comparable")


def test_locked_trading_results_are_still_negative_everywhere_they_are_quoted():
    """The deck and cards state these as failures; they must still be failures."""
    gefs = _cell("gefs_corn_holdout_results.csv",
                 {"specification": "all_gefs_plus_market_shallow_boosting"},
                 "total_net")
    usda = pd.read_csv(ANALYSIS / "nass_corn_progress_validation_results.csv")
    headline = usda.loc[usda["is_headline"].astype(bool)]
    assert len(headline) == 1, "expected exactly one headline USDA candidate"
    assert gefs < 0 and float(headline.iloc[0]["total_net"]) < 0
    for doc in (README, SIGNAL_CARD):
        text = (ROOT / doc).read_text()
        assert "52.7" in text, f"{doc} no longer quotes the USDA loss"
        assert "7.3" in text, f"{doc} no longer quotes the GEFS holdout loss"


def _built_deck_text() -> str | None:
    """Every word of the committed deck, slides and notecards alike."""
    deck = ROOT / "presentation/final/output/Final Presentation.pptx"
    if not deck.exists():
        return None
    with zipfile.ZipFile(deck) as archive:
        parts = [name for name in archive.namelist()
                 if re.match(r"ppt/(slides|notesSlides)/\w+\d+\.xml$", name)]
        return _normalise(" ".join(
            html.unescape(run)
            for name in parts
            for run in re.findall(r"<a:t>(.*?)</a:t>",
                                  archive.read(name).decode(), re.S)))


# The deck interpolates its numbers at build time, so the source carries none of
# them and only the artifact can be checked. A failure here usually means the
# committed deck is stale rather than that a document is wrong: rebuild it.
DECK_CLAIMS = [
    # The slide stats use the signed three-decimal form; the notecards round
    # the same values to whole percents, so only the slide form is checked.
    ("forward R2, all years", "corn_yield_forward_validation_results.csv",
     FORWARD, "r2_vs_trend", lambda v: f"{v:+.3f}"),
    ("forward R2, excluding 2012", "corn_yield_forward_validation_results.csv",
     FORWARD_EX, "r2_vs_trend", lambda v: f"{v:+.3f}"),
    ("severity high tercile", "corn_yield_strata_buckets.csv",
     {**FORWARD, "study": "forward", "dimension": "severity", "bucket": "high"},
     "r2_vs_trend", lambda v: f"{v:+.3f}"),
    ("interval width, full season 90%", "corn_yield_intervals_coverage.csv",
     {**FORWARD, "study": "forward", "method": "jackknife_plus",
      "nominal_level": 0.9}, "mean_width_pp_of_trend", lambda v: f"{v / 2:.1f}"),
    ("horse-race incremental R2", "corn_price_information_race.csv",
     {**NOWCAST_AUG, "sample": "all_years",
      "price_source": "december_contract"}, "incremental_r2",
     lambda v: f"{v:+.3f}"),
    ("USDA locked loss", "nass_corn_progress_validation_results.csv",
     {"candidate": "progress_plus_market_logistic"}, "total_net",
     lambda v: f"{v * 100:.1f}%"),
]


@pytest.mark.parametrize("claim,csv,where,column,fmt", DECK_CLAIMS,
                         ids=[claim[0] for claim in DECK_CLAIMS])
def test_the_committed_deck_is_not_stale(claim, csv, where, column, fmt):
    text = _built_deck_text()
    if text is None:
        pytest.skip("no committed deck to check")
    expected = _normalise(fmt(_cell(csv, where, column)))
    assert expected in text, (
        f"{claim}: the committed deck does not contain {expected!r}. It is "
        "probably stale — rerun deck_data.py and npm run build.")
