# app/ocr.py
from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import shutil

def pdf_has_text_layer(pdf_path: Path) -> bool:
    # Heuristic: pypdf will be used by extract.py; this function is optional.
    # Keep simple: try extracting a tiny amount by calling extract via pypdf in extract.py.
    return True

def run_ocr_to_temp(pdf_path: Path, logger) -> Path:
    """
    Creates an OCR'd PDF copy in a temp directory and returns its path.
    Uses ocrmypdf CLI if available. If not present, raises.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="ocrpdf_"))
    out_path = tmpdir / pdf_path.name

    cmd = [
        "ocrmypdf",
        "--skip-text",
        "--force-ocr",
        "--language", "deu",
        "--output-type", "pdf",
        "--rotate-pages",
        "--deskew",
        str(pdf_path),
        str(out_path),
    ]

    logger.info(f"OCR: {pdf_path.name}")
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    except FileNotFoundError as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError("ocrmypdf_not_installed") from e

    if proc.returncode != 0:
        logger.error(f"OCR fehlgeschlagen ({proc.returncode}): {proc.stderr[-1000:]}")
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError("ocr_failed")

    return out_path

def cleanup_temp_ocr(ocr_pdf_path: Path) -> None:
    try:
        parent = ocr_pdf_path.parent
        if parent.name.startswith("ocrpdf_"):
            shutil.rmtree(parent, ignore_errors=True)
    except Exception:
        pass