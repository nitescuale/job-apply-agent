"""DOCX -> PDF conversion utility, shared across CV tailoring and cover letter.

Extracted from cv_tailor so that cover_letter (and any future agent producing
documents) can reuse the same converter without circular imports.

Strategy is unchanged:
    1. docx2pdf (Microsoft Word via pywin32 COM on Windows) — best fidelity,
       PDF is 1:1 with the DOCX.
    2. fallback LibreOffice headless (`soffice --headless --convert-to pdf`).
    3. if neither is available, raise RuntimeError with explicit instructions.

cv_tailor re-exports `convert_docx_to_pdf` under its previous private alias
`_convert_docx_to_pdf` so existing tests that patch
`cv_tailor._convert_docx_to_pdf` keep working.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def convert_docx_to_pdf(docx_path: Path, pdf_path: Path) -> None:
    """Convertit un DOCX en PDF. Essaie docx2pdf (Word COM) puis LibreOffice.

    docx2pdf utilise Microsoft Word via pywin32/COM sur Windows. Si Word
    n'est pas installé, l'appel lève une exception et on bascule sur
    `soffice --headless`. Si ni l'un ni l'autre n'est disponible, on lève
    une RuntimeError avec instructions explicites pour l'utilisateur.
    """
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    # Path 1 : Microsoft Word via docx2pdf (qualité PDF maximale,
    # rendu 1:1 fidèle au DOCX)
    try:
        from docx2pdf import convert as _w_convert

        _w_convert(str(docx_path), str(pdf_path))
        if pdf_path.is_file():
            logger.info("pdf_convert: PDF via docx2pdf -> %s", pdf_path)
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "pdf_convert: docx2pdf indisponible (%s), fallback LibreOffice", exc
        )

    # Path 2 : LibreOffice headless
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        try:
            subprocess.run(
                [
                    soffice,
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(pdf_path.parent),
                    str(docx_path),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            produced = pdf_path.parent / f"{docx_path.stem}.pdf"
            if produced != pdf_path and produced.is_file():
                produced.replace(pdf_path)
            logger.info("pdf_convert: PDF via LibreOffice -> %s", pdf_path)
            return
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
            logger.error("LibreOffice a échoué: %s", stderr)

    raise RuntimeError(
        "Conversion DOCX -> PDF impossible : ni Microsoft Word (via docx2pdf) "
        "ni LibreOffice (soffice) ne sont disponibles. Installe l'un des deux."
    )
