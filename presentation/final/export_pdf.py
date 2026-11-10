"""Export the final PPTX to a verified, matching PDF using LibreOffice.

Requires soffice on PATH (or SOFFICE=/path/to/soffice) and pypdf.
A failed conversion never replaces the previous submission PDF.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

from pypdf import PdfReader

HERE = Path(__file__).resolve().parent
PPTX = HERE / "output" / "Final Presentation.pptx"
PDF = PPTX.with_suffix(".pdf")
NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "p": "http://schemas.openxmlformats.org/presentationml/2006/main"}


def normalise(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def verify(pdf: Path, pptx: Path) -> int:
    reader = PdfReader(pdf)
    with zipfile.ZipFile(pptx) as archive:
        slides = sorted(
            (name for name in archive.namelist()
             if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
            key=lambda name: int(re.search(r"slide(\d+)\.xml", name)[1]),
        )
        if len(reader.pages) != len(slides):
            raise ValueError(f"PDF has {len(reader.pages)} pages, PPTX has {len(slides)} slides")
        for index, (page, name) in enumerate(zip(reader.pages, slides), 1):
            if page.mediabox.width <= page.mediabox.height:
                raise ValueError(f"PDF page {index} is not landscape")
            rendered = normalise(page.extract_text() or "")
            if not rendered:
                raise ValueError(f"PDF page {index} has no extractable text")
            root = ET.fromstring(archive.read(name))
            # Check each visible text box independently: PDF reading order can
            # differ between columns, while wrapping can split words anywhere.
            for box in root.findall(".//p:txBody", NS):
                text = "".join(node.text or "" for node in box.findall(".//a:t", NS))
                if text.strip() and normalise(text) not in rendered:
                    raise ValueError(f"PDF page {index} is missing slide text: {text!r}")
    return len(slides)


def main() -> None:
    soffice = os.environ.get("SOFFICE") or shutil.which("soffice")
    if not soffice:
        mac = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        if mac.exists():
            soffice = str(mac)
    if not soffice:
        raise SystemExit("LibreOffice is required. Install it or set SOFFICE to its executable.")
    if not PPTX.exists():
        raise SystemExit("Build Final Presentation.pptx before exporting the PDF.")
    # Same filesystem as output makes the final replacement atomic.
    with tempfile.TemporaryDirectory(prefix=".pdf-export-", dir=HERE) as directory:
        work = Path(directory)
        env = {**os.environ, "XDG_CACHE_HOME": str(work / "cache")}
        result = subprocess.run(
            [soffice, f"-env:UserInstallation={(work / 'profile').as_uri()}",
             "--headless", "--convert-to", "pdf:impress_pdf_Export",
             "--outdir", str(work), str(PPTX)],
            capture_output=True, text=True, timeout=120, env=env,
        )
        candidate = work / PDF.name
        if result.returncode or not candidate.exists():
            raise SystemExit(f"PDF conversion failed:\n{result.stdout}\n{result.stderr}")
        pages = verify(candidate, PPTX)
        candidate.replace(PDF)
    print(f"wrote {PDF} ({pages} pages; all slide text verified)")


if __name__ == "__main__":
    main()
