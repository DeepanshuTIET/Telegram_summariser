from __future__ import annotations

import hashlib
import io
from pathlib import Path

import fitz
import pytesseract
from PIL import Image, ImageOps

from .models import ExtractedContent, SkippableItemError


class ContentExtractor:
    def __init__(self, *, max_file_bytes: int, max_pdf_pages: int):
        self.max_file_bytes = max_file_bytes
        self.max_pdf_pages = max_pdf_pages

    def health_check(self) -> None:
        pytesseract.get_tesseract_version()

    def compute_file_hash(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def extract(self, path: Path) -> ExtractedContent:
        if not path.exists():
            raise SkippableItemError(f"Downloaded file is missing: {path.name}")
        file_size = path.stat().st_size
        if file_size > self.max_file_bytes:
            raise SkippableItemError(
                f"File exceeds size limit of {self.max_file_bytes // (1024 * 1024)} MB."
            )

        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self._extract_pdf(path)
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            return self._extract_image(path)
        raise SkippableItemError(f"Unsupported file type: {path.suffix or '[no extension]'}")

    def _extract_pdf(self, path: Path) -> ExtractedContent:
        text_chunks: list[str] = []
        direct_pages = 0
        ocr_pages = 0
        truncated = False
        notes: list[str] = []

        with fitz.open(path) as document:
            total_pages = document.page_count
            page_limit = min(total_pages, self.max_pdf_pages)
            truncated = total_pages > self.max_pdf_pages
            if truncated:
                notes.append(
                    f"Only the first {self.max_pdf_pages} pages were analyzed out of "
                    f"{total_pages} total pages."
                )

            for page_index in range(page_limit):
                page = document.load_page(page_index)
                page_text = page.get_text("text").strip()
                if page_text:
                    direct_pages += 1
                    text_chunks.append(page_text)
                    continue

                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                image_bytes = pix.tobytes("png")
                with Image.open(io.BytesIO(image_bytes)) as page_image:
                    ocr_text = self._ocr_from_image(page_image)
                if ocr_text:
                    ocr_pages += 1
                    text_chunks.append(ocr_text)

        joined = "\n\n".join(chunk for chunk in text_chunks if chunk).strip()
        if not joined:
            raise SkippableItemError("No text could be extracted from this PDF.")

        quality = self._estimate_quality(
            joined,
            used_ocr=ocr_pages > 0,
            has_direct_text=direct_pages > 0,
        )
        return ExtractedContent(
            text=joined,
            ocr_quality=quality,
            source_type="pdf",
            truncated=truncated,
            notes=notes,
        )

    def _extract_image(self, path: Path) -> ExtractedContent:
        with Image.open(path) as image:
            text = self._ocr_from_image(image)
        if not text:
            raise SkippableItemError("No readable text was found in this image.")
        quality = self._estimate_quality(text, used_ocr=True, has_direct_text=False)
        return ExtractedContent(
            text=text,
            ocr_quality=quality,
            source_type="image",
        )

    def _ocr_from_image(self, image: Image.Image) -> str:
        processed = ImageOps.exif_transpose(image)
        processed = ImageOps.grayscale(processed)
        processed = ImageOps.autocontrast(processed)
        return pytesseract.image_to_string(processed).strip()

    @staticmethod
    def _estimate_quality(text: str, *, used_ocr: bool, has_direct_text: bool) -> str:
        cleaned = "".join(character for character in text if character.isalnum() or character.isspace())
        cleaned = " ".join(cleaned.split())
        text_length = len(cleaned)
        if has_direct_text and text_length >= 80:
            return "high"
        if text_length < 40:
            return "low"
        if used_ocr and text_length < 140:
            return "medium"
        return "high" if text_length >= 140 else "medium"
