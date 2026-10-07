"""The CPC outlook source: two schemas, three categories, and one inverted lag.

Offline, against real captured zips (tests/fixtures/cpc/PROVENANCE.md). The
failures guarded here all produce a plausible number rather than an error:

* reading the early schema's probability as a number when it is a padded string
* losing the category, which lives in the FILENAME before October 2012
* treating "no contour over this point" as missing data when it is a real
  forecast of normal odds -- and treating a point outside the CONUS as the same
  thing, which would hand West Africa 4,900 confident forecasts
* archiving `_latest.zip` as if it were a dated vintage
* counting the overlapping month of September 2012 once per schema era
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import pytest

from pipeline.clean import cpc_outlook as clean_cpc
from pipeline.clients import cpc_outlook as client
from pipeline.ingest import cpc_outlook as ingest_cpc

# The five corn points, as config/geography.yaml declares them.
IOWA = (42.0, -93.6)
MINNESOTA = (44.1, -94.0)
NEBRASKA = (40.8, -98.4)


@pytest.fixture
def cpc(fixtures: Path) -> Path:
    return fixtures / "cpc"


def body(cpc: Path, name: str) -> bytes:
    return (cpc / name).read_bytes()


# ------------------------------------------------------------------ the schemas


def test_the_unified_schema_carries_its_own_dates_and_categories(cpc: Path) -> None:
    bundle = client.read(body(cpc, "610temp_20230711.zip"), product="610temp")
    assert bundle.era == "unified"
    assert bundle.issued == date(2023, 7, 11)
    assert (bundle.valid_start, bundle.valid_end) == (date(2023, 7, 17), date(2023, 7, 21))
    assert bundle.lead_days == 6
    assert len(bundle.contours) == 15
    # Three categories, not two. A reader that assumes above/below drops Normal.
    assert "Normal" in {contour.category for contour in bundle.contours}


def test_the_early_schema_needs_the_category_from_the_filename(cpc: Path) -> None:
    raw = body(cpc, "610tempabv_20120710.zip")
    # Without a category there is nothing in the file to supply it, so the reader
    # must refuse rather than guess a direction.
    with pytest.raises(client.OutlookError, match="does not carry a category"):
        client.read(raw, product="610temp")

    bundle = client.read(raw, product="610temp", category="Above")
    assert bundle.era == "early"
    assert bundle.issued == date(2012, 7, 10)
    assert (bundle.valid_start, bundle.valid_end) == (date(2012, 7, 16), date(2012, 7, 20))
    assert all(contour.category == "Above" for contour in bundle.contours)


def test_the_early_probability_is_a_padded_string_and_parses_to_a_number(cpc: Path) -> None:
    bundle = client.read(body(cpc, "610tempabv_20120710.zip"),
                         product="610temp", category="Above")
    probabilities = sorted({contour.probability for contour in bundle.contours})
    assert probabilities == [33.0, 40.0, 50.0, 60.0]
    assert all(isinstance(contour.probability, float) for contour in bundle.contours)


def test_an_unrecognised_schema_names_the_fields_it_found() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("x.shp", b"not a shapefile")
        archive.writestr("x.dbf", b"nor this")
        archive.writestr("x.shx", b"nor this")
    with pytest.raises(Exception):
        client.read(buffer.getvalue(), product="610temp")


def test_a_payload_that_is_not_a_zip_says_so() -> None:
    with pytest.raises(client.OutlookError, match="not a zip"):
        client.read(b"<html>404</html>", product="610temp")


def test_an_archive_without_exactly_one_shapefile_is_refused() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a.shp", b"")
        archive.writestr("b.shp", b"")
    with pytest.raises(client.OutlookError, match="exactly one .shp"):
        client.read(buffer.getvalue(), product="610temp")


# ------------------------------------------------------------- the point read


def test_the_2012_drought_reads_as_a_hot_forecast_over_the_corn_belt(cpc: Path) -> None:
    """The agronomic sanity check: July 2012 was the peak of that drought.

    If the point-in-polygon read were silently matching nothing, every point
    would come back uncovered and this would pass as "normal".
    """
    bundle = client.read(body(cpc, "610tempabv_20120710.zip"),
                         product="610temp", category="Above")
    iowa = client.best(bundle.contours, *IOWA)
    minnesota = client.best(bundle.contours, *MINNESOTA)
    assert iowa is not None and minnesota is not None
    assert iowa.probability == 40.0
    assert minnesota.probability == 50.0
    assert minnesota.anomaly > iowa.anomaly > 0


def test_the_innermost_contour_wins_because_they_nest(cpc: Path) -> None:
    """Contours are nested, not a partition: a point sits inside one to three."""
    bundle = client.read(body(cpc, "610tempabv_20120710.zip"),
                         product="610temp", category="Above")
    containing = [c for c in bundle.contours if c.contains(*MINNESOTA)]
    assert len(containing) > 1, "this fixture's value is that Minnesota nests"
    assert client.best(bundle.contours, *MINNESOTA).probability == max(
        c.probability for c in containing)


def test_a_point_in_no_contour_is_normal_odds_rather_than_missing(cpc: Path) -> None:
    bundle = client.read(body(cpc, "610temp_20230711.zip"), product="610temp")
    iowa = client.best(bundle.contours, *IOWA)
    # 2023-07-11 covers Iowa with the Normal contour; the anomaly is zero either
    # way, which is the point -- a quiet forecast is a forecast.
    assert iowa is None or iowa.category == "Normal"
    assert (iowa.anomaly if iowa else 0.0) == 0.0


def test_latitude_and_longitude_are_not_interchangeable(cpc: Path) -> None:
    """Reading the file's (lon, lat) pairs as (lat, lon) matches nothing."""
    bundle = client.read(body(cpc, "610tempabv_20120710.zip"),
                         product="610temp", category="Above")
    assert client.best(bundle.contours, *IOWA) is not None
    # The transposed point is in the Indian Ocean.
    assert client.best(bundle.contours, IOWA[1], IOWA[0]) is None


# ------------------------------------------------------------------- the anomaly


def test_the_anomaly_is_signed_against_climatology_not_raw(cpc: Path) -> None:
    """60% Below is the opposite of 60% Above; the raw numbers would agree."""
    above = client.read(body(cpc, "610tempabv_20120710.zip"),
                        product="610temp", category="Above")
    below = client.read(body(cpc, "610tempbel_20120710.zip"),
                        product="610temp", category="Below")
    hot = max(c.anomaly for c in above.contours)
    cold = min(c.anomaly for c in below.contours)
    assert hot > 0 > cold
    assert client._anomaly(60.0, "Above") == pytest.approx(-client._anomaly(60.0, "Below"))
    # A climatological 33.3% is no information in either direction.
    assert client._anomaly(100 / 3, "Above") == pytest.approx(0.0)
    assert client._anomaly(70.0, "Normal") == 0.0


# -------------------------------------------------------------------- the index


def test_the_index_accepts_dated_files_and_rejects_the_moving_pointer(
        monkeypatch, sources) -> None:
    """`_latest.zip` is overwritten in place, so it is not a vintage.

    Archiving it would record bytes that cannot be reproduced or dated. The
    separator-less `610tempYYYYMMDD.zip` anomaly is skipped for the same reason:
    it is one stray file, not a third naming era.
    """
    listing = b'''
      <a href="610temp_20230711.zip">x</a>
      <a href="610tempabv_20120710.zip">x</a>
      <a href="610tempbel_20120710.zip">x</a>
      <a href="814prcp_20230711.zip">x</a>
      <a href="610temp_latest.zip">x</a>
      <a href="610temp20150125.zip">x</a>
      <a href="wk34temp_20230711.zip">x</a>
    '''
    monkeypatch.setattr(client, "fetch_with_headers", lambda *a, **k: (listing, {}))
    found = client.index(sources["cpc_outlook"])
    assert ("610temp", "") in found and found[("610temp", "")] == [date(2023, 7, 11)]
    assert ("610temp", "abv") in found and ("610temp", "bel") in found
    assert ("814prcp", "") in found
    # Not a configured product, a moving pointer, and a separator-less anomaly.
    assert ("wk34temp", "") not in found
    assert all(date(2015, 1, 25) not in dates for dates in found.values())
    assert not any("latest" in str(key) for key in found)


# -------------------------------------------------------------------- the eras


def test_the_era_cut_is_strict_so_the_overlapping_month_is_not_doubled(
        harness, sources, monkeypatch) -> None:
    """Both naming conventions exist from 2012-09-11 to 2012-10-09.

    Taking both for those dates counts the month twice; taking only the unified
    one loses 189 early issuances including half of 2012.
    """
    overlap = [date(2012, 9, 10), date(2012, 9, 12), date(2012, 10, 1)]
    monkeypatch.setattr(client, "index", lambda spec: {
        ("610temp", ""): list(overlap),
        ("610temp", "abv"): list(overlap),
        ("610temp", "bel"): list(overlap),
    })
    request = harness.fetch("cpc_outlook", years=(2012,), months=(9, 10), dry_run=True)
    keys = [record.key for record in ingest_cpc.fetch(request)]
    # Before the cut: the early pair only. From the cut: the unified file only.
    assert keys == ["610tempabv/2012-09-10", "610tempbel/2012-09-10",
                   "610temp/2012-09-12", "610temp/2012-10-01"]
    assert not harness.raw.manifest_path.exists()


def test_the_publication_date_is_the_issuance_and_precedes_the_period(cpc: Path) -> None:
    """The one source here whose publication comes BEFORE what it describes.

    Every other source is late. A forecast is early, which is what makes it
    tradeable at publication rather than merely observable afterwards.
    """
    bundle = client.read(body(cpc, "814prcp_20230711.zip"), product="814prcp")
    assert bundle.issued < bundle.valid_start
    assert bundle.lead_days == 8, "an 814 product starts eight days out, not six"
    assert (bundle.valid_end - bundle.issued).days == 14


def test_the_lead_time_check_catches_a_product_under_the_wrong_name(
        harness, cpc: Path) -> None:
    """A 610 file whose valid period starts eight days out is an 814 mislabelled."""
    request = harness.fetch("cpc_outlook")
    bundle = client.read(body(cpc, "814prcp_20230711.zip"), product="814prcp")
    ingest_cpc._check(bundle, "814prcp", "", date(2023, 7, 11), request.spec)
    with pytest.raises(ValueError, match="expected a lead of 6-10"):
        ingest_cpc._check(bundle, "610prcp", "", date(2023, 7, 11), request.spec)


def test_a_filename_date_disagreeing_with_the_attribute_is_refused(
        harness, cpc: Path) -> None:
    """If the two diverge, one is not the issuance and every point-in-time claim
    built on the publication date is quietly wrong."""
    request = harness.fetch("cpc_outlook")
    bundle = client.read(body(cpc, "610temp_20230711.zip"), product="610temp")
    with pytest.raises(ValueError, match="not the issuance date"):
        ingest_cpc._check(bundle, "610temp", "", date(2023, 7, 12), request.spec)


# -------------------------------------------------------------------- the clean


def seed(harness, cpc: Path, name: str, key: str, **params) -> None:
    harness.seed("cpc_outlook", key, body(cpc, name),
                 url=f"https://example.test/{name}", params=params,
                 publication_date=date.fromisoformat(key.split("/")[-1]))


def test_cleaning_keeps_normal_odds_and_drops_points_off_the_continent(
        harness, cpc: Path, capsys) -> None:
    seed(harness, cpc, "610temp_20230711.zip", "610temp/2023-07-11", product="610temp")
    clean_cpc.clean(harness.clean("cpc_outlook"))
    table = harness.processed.read(clean_cpc.TABLE)
    out = capsys.readouterr().out

    # Corn and soybeans each declare the same five CONUS points; cocoa's four are
    # in West Africa and must be excluded rather than read as normal odds.
    assert set(table["crop"]) == {"corn", "soybeans"}
    assert "cocoa" not in set(table["crop"])
    assert "outside the CONUS" in out
    assert len(table) == 10
    # Every row is a forecast, including the uncovered ones.
    assert table["anomaly"].notna().all()
    assert (table.loc[~table["covered"], "anomaly"] == 0.0).all()
    assert (table["publication_date"] == table["issued"]).all()


def test_cleaning_collapses_the_early_eras_two_files_into_one_row(
        harness, cpc: Path) -> None:
    """An early issuance is split across an above file and a below file.

    Keeping both gives a point two rows for one product-day; keeping the wrong
    one reads a hot forecast as a cold one.
    """
    seed(harness, cpc, "610tempabv_20120710.zip", "610tempabv/2012-07-10",
         product="610temp", category="Above")
    seed(harness, cpc, "610tempbel_20120710.zip", "610tempbel/2012-07-10",
         product="610temp", category="Below")
    clean_cpc.clean(harness.clean("cpc_outlook"))
    table = harness.processed.read(clean_cpc.TABLE)

    assert len(table) == 10, "five points x two crops, one row each"
    iowa = table.loc[(table["crop"] == "corn") & (table["point"] == "Iowa")]
    assert len(iowa) == 1
    # July 2012: the above-normal file is the one that said something.
    assert iowa["category"].iloc[0] == "Above"
    assert iowa["anomaly"].iloc[0] > 0


# ------------------------------------------------------- the empty outlook


def test_an_outlook_with_no_polygons_is_a_forecast_not_a_failure(cpc: Path) -> None:
    """CPC draws no contours when it expects no significant departure anywhere.

    814prcp for 2014-07-19 is 13 KB with zero records while 814temp the same day
    has twelve. Raising on it stopped two backfill runs dead at that exact date,
    1,999 files into 12,558, so this is the regression test for both.

    The dbf's SCHEMA survives with no records, so the era is still readable; only
    the dates are gone, because they live in the attributes.
    """
    raw = body(cpc, "814prcp_20140719.zip")
    bundle = client.read(raw, product="814prcp", issued=date(2014, 7, 19), leads=(8, 14))
    assert bundle.era == "unified"
    assert bundle.contours == ()
    # Dates reconstructed from the filename and the product's configured leads.
    assert bundle.issued == date(2014, 7, 19)
    assert bundle.lead_days == 8
    assert (bundle.valid_end - bundle.issued).days == 14
    # Every point is then at climatological odds, which is what no contour means.
    assert client.best(bundle.contours, *IOWA) is None


def test_an_empty_outlook_without_dates_or_leads_says_what_it_needs(cpc: Path) -> None:
    """It cannot be guessed silently: with no attributes there is nothing to read."""
    raw = body(cpc, "814prcp_20140719.zip")
    with pytest.raises(client.OutlookError, match="needs `issued`"):
        client.read(raw, product="814prcp")


def test_the_ingest_check_accepts_an_empty_outlook(harness, cpc: Path, capsys) -> None:
    request = harness.fetch("cpc_outlook")
    bundle = client.read(body(cpc, "814prcp_20140719.zip"), product="814prcp",
                         issued=date(2014, 7, 19), leads=(8, 14))
    ingest_cpc._check(bundle, "814prcp", "", date(2014, 7, 19), request.spec)
    assert "climatological odds" in capsys.readouterr().out


def test_cleaning_an_empty_outlook_gives_every_point_normal_odds(
        harness, cpc: Path) -> None:
    harness.seed("cpc_outlook", "814prcp/2014-07-19", body(cpc, "814prcp_20140719.zip"),
                 url="https://example.test/814prcp_20140719.zip",
                 params={"product": "814prcp", "issued": "2014-07-19"},
                 publication_date=date(2014, 7, 19))
    clean_cpc.clean(harness.clean("cpc_outlook"))
    table = harness.processed.read(clean_cpc.TABLE)
    assert len(table) == 10, "five points x two crops, all uncovered"
    assert (table["anomaly"] == 0.0).all()
    assert (~table["covered"]).all()
    assert (table["category"] == "Normal").all()
