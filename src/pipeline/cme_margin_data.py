"""Parse CME historical minimum-performance-bond PDFs and attach them to contracts.

CME publishes the public history by business date and *roll product code* (for
example ``C-01``), rather than by an explicit delivery symbol.  The numbered
code is the expiry-ordered position in the listed-contract strip.  This module
therefore assigns the same rank to the LSEG contract panel on each date before
joining.  It never extends a rate outside the PDF's stated dates and never
substitutes a current margin for missing history.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd


CME_HISTORY_PAGE = (
    "https://www.cmegroup.com/solutions/risk-management/"
    "margin-services/historical-margins.html"
)
CME_PDF_URLS = {
    "ZC": "https://www.cmegroup.com/clearing/risk-management/files/C-2020-to-present.pdf",
    "ZW": "https://www.cmegroup.com/clearing/risk-management/files/W-2020-to-present.pdf",
}
ROOT_METADATA = {
    "ZC": {"description": "CORN FUTURES", "exchange": "CBT", "prefix": "C"},
    "ZW": {"description": "WHEAT FUTURES", "exchange": "CBT", "prefix": "W"},
}
MARGIN_COLUMNS = (
    "margin_business_date",
    "root",
    "margin_roll_index",
    "maintenance_margin_usd",
    "margin_roll_product_code",
    "margin_description",
    "margin_exchange",
    "margin_source_url",
)


class MarginDataError(ValueError):
    """A CME margin file or contract-to-roll mapping is invalid."""


@dataclass(frozen=True)
class MarginAttachment:
    frame: pd.DataFrame
    margins: pd.DataFrame
    input_rows: int
    within_source_dates: int
    matched_rows: int
    dropped_unmatched_rows: int
    start_date: str
    end_date: str

    def manifest(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_name": "CME Group historical minimum performance bonds",
            "source_page": CME_HISTORY_PAGE,
            "source_urls": CME_PDF_URLS,
            "interpretation": (
                "minimum performance bond used as maintenance margin; roll product "
                "code joined to expiry-ordered listed-contract rank"
            ),
            "input_rows": self.input_rows,
            "within_source_dates": self.within_source_dates,
            "matched_rows": self.matched_rows,
            "dropped_unmatched_rows": self.dropped_unmatched_rows,
            "margin_start_date": self.start_date,
            "margin_end_date": self.end_date,
        }


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _row_pattern(root: str) -> re.Pattern[str]:
    try:
        metadata = ROOT_METADATA[root]
    except KeyError as exc:
        raise MarginDataError(f"unsupported CME margin root {root!r}") from exc
    return re.compile(
        rf"(?m)^(\d{{4}}-\d{{2}}-\d{{2}})\n"
        rf"({re.escape(metadata['description'])})\n"
        rf"({re.escape(metadata['exchange'])})\n"
        rf"([0-9]+(?:\.[0-9]+)?)\n"
        rf"({re.escape(metadata['prefix'])}-(\d+))$"
    )


def parse_margin_text(text: str, *, root: str) -> pd.DataFrame:
    """Parse the text layer from one or more CME report pages."""

    matches = _row_pattern(root).findall(text)
    if not matches:
        raise MarginDataError(f"{root}: no CME margin rows found in extracted text")
    rows = [
        {
            "margin_business_date": business_date,
            "root": root,
            "margin_roll_index": int(roll_index),
            "maintenance_margin_usd": float(margin),
            "margin_roll_product_code": roll_code,
            "margin_description": description,
            "margin_exchange": exchange,
            "margin_source_url": CME_PDF_URLS[root],
        }
        for business_date, description, exchange, margin, roll_code, roll_index
        in matches
    ]
    return pd.DataFrame(rows, columns=MARGIN_COLUMNS)


def _validate_margins(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        raise MarginDataError("CME margin history is empty")
    clean = frame.copy()
    clean["margin_business_date"] = pd.to_datetime(
        clean["margin_business_date"], errors="coerce"
    ).dt.normalize()
    clean["maintenance_margin_usd"] = pd.to_numeric(
        clean["maintenance_margin_usd"], errors="coerce"
    )
    clean["margin_roll_index"] = pd.to_numeric(
        clean["margin_roll_index"], errors="coerce"
    )
    if clean[[
        "margin_business_date", "maintenance_margin_usd", "margin_roll_index"
    ]].isna().any().any():
        raise MarginDataError("CME margin rows contain missing or invalid values")
    if (clean["maintenance_margin_usd"] <= 0).any():
        raise MarginDataError("CME maintenance margins must be positive")
    if (clean["margin_roll_index"] < 1).any():
        raise MarginDataError("CME roll indexes must be positive")
    clean["margin_roll_index"] = clean["margin_roll_index"].astype(int)
    unknown = sorted(set(clean["root"]) - set(ROOT_METADATA))
    if unknown:
        raise MarginDataError(f"unsupported roots in margin history: {', '.join(unknown)}")
    duplicates = clean.duplicated(
        ["margin_business_date", "root", "margin_roll_index"], keep=False
    )
    if duplicates.any():
        raise MarginDataError(
            f"CME margin history has {int(duplicates.sum())} duplicate date/root/roll rows"
        )
    return clean.sort_values(
        ["margin_business_date", "root", "margin_roll_index"]
    ).reset_index(drop=True)


def parse_cme_margin_pdf(path: str | Path, *, root: str) -> pd.DataFrame:
    """Extract every tabular row from one official CME history PDF."""

    source = Path(path)
    if not source.exists():
        raise MarginDataError(f"CME margin PDF does not exist: {source}")
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise MarginDataError(
            "pypdf is required to parse CME margin files; install requirements.txt"
        ) from exc
    try:
        reader = PdfReader(source)
    except Exception as exc:
        raise MarginDataError(f"cannot open CME margin PDF {source}: {exc}") from exc
    pages: list[pd.DataFrame] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
            pages.append(parse_margin_text(text, root=root))
        except MarginDataError as exc:
            raise MarginDataError(f"{source.name}: page {number}: {exc}") from exc
    return _validate_margins(pd.concat(pages, ignore_index=True))


def parse_cme_margin_pdfs(
    *, corn_pdf: str | Path, wheat_pdf: str | Path
) -> pd.DataFrame:
    return _validate_margins(pd.concat([
        parse_cme_margin_pdf(corn_pdf, root="ZC"),
        parse_cme_margin_pdf(wheat_pdf, root="ZW"),
    ], ignore_index=True))


def attach_margin_history(
    contracts: pd.DataFrame,
    margins: pd.DataFrame,
    *,
    drop_unmatched: bool = True,
) -> MarginAttachment:
    """Join dated roll-position margins to an individual-contract panel.

    Contract ranks are calculated before unmatched rows are removed.  This is
    important when the LSEG panel contains farther expiries than the public CME
    report: deleting them first would renumber the surviving strip.
    """

    required = {"date", "root", "contract", "last_trade_date"}
    missing = sorted(required - set(contracts.columns))
    if missing:
        raise MarginDataError(
            f"contract panel is missing columns: {', '.join(missing)}"
        )
    if "maintenance_margin_usd" in contracts and contracts[
        "maintenance_margin_usd"
    ].notna().any():
        raise MarginDataError(
            "contract panel already contains maintenance margins; refusing to overwrite"
        )
    clean_margins = _validate_margins(margins)
    panel = contracts.drop(columns=[
        column for column in MARGIN_COLUMNS
        if column != "root" and column in contracts.columns
    ], errors="ignore").copy()
    panel["date"] = pd.to_datetime(panel["date"], errors="coerce").dt.normalize()
    panel["last_trade_date"] = pd.to_datetime(
        panel["last_trade_date"], errors="coerce"
    ).dt.normalize()
    if panel[["date", "last_trade_date"]].isna().any().any():
        raise MarginDataError("contract dates must be present and parseable")
    if panel.duplicated(["date", "root", "contract"]).any():
        raise MarginDataError("contract panel has duplicate date/root/contract rows")

    start = clean_margins["margin_business_date"].min()
    end = clean_margins["margin_business_date"].max()
    within = panel["date"].between(start, end)
    covered = panel.loc[within].copy()
    covered = covered.sort_values(
        ["date", "root", "last_trade_date", "contract"]
    )
    covered["margin_roll_index"] = (
        covered.groupby(["date", "root"], sort=False).cumcount() + 1
    )
    joined = covered.merge(
        clean_margins,
        how="left",
        left_on=["date", "root", "margin_roll_index"],
        right_on=["margin_business_date", "root", "margin_roll_index"],
        validate="many_to_one",
    )
    matched = joined["maintenance_margin_usd"].notna()
    output = joined.loc[matched].copy() if drop_unmatched else joined.copy()
    output = output.sort_values(
        ["date", "root", "last_trade_date", "contract"]
    ).reset_index(drop=True)
    return MarginAttachment(
        frame=output,
        margins=clean_margins,
        input_rows=len(panel),
        within_source_dates=int(within.sum()),
        matched_rows=int(matched.sum()),
        dropped_unmatched_rows=int((~matched).sum()) if drop_unmatched else 0,
        start_date=start.date().isoformat(),
        end_date=end.date().isoformat(),
    )


def pdf_manifest(paths: Iterable[str | Path]) -> list[dict[str, object]]:
    return [
        {
            "file": Path(path).name,
            "sha256": sha256_file(path),
            "bytes": Path(path).stat().st_size,
        }
        for path in paths
    ]
