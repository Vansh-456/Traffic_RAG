"""PDF text extraction with OCR and LLM-vision fallbacks.

1. pymupdf text layer
2. if empty / garbled → Tesseract OCR (``hin+eng``) on 200-dpi page renders
3. if still garbled and a vision-capable model is configured (Gemini) → LLM
   transcription of the page images
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass

from app.config import get_settings
from app.ingestion.parsing.language import looks_garbled
from app.observability.logging import get_logger

log = get_logger(__name__)

MAX_PAGES = 30


@dataclass
class ParsedPdf:
    text: str
    method: str  # text|ocr|vision
    pages: int
    quality_ok: bool


def extract_text_layer(data: bytes) -> tuple[str, int]:
    import fitz  # pymupdf

    with fitz.open(stream=data, filetype="pdf") as doc:
        pages = [page.get_text("text") for page in doc.pages(0, min(len(doc), MAX_PAGES))]
        return "\n\n".join(pages), len(doc)


def render_pages(data: bytes, dpi: int = 200) -> list[bytes]:
    import fitz

    out: list[bytes] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for page in doc.pages(0, min(len(doc), MAX_PAGES)):
            out.append(page.get_pixmap(dpi=dpi).tobytes("png"))
    return out


def ocr_images(images: list[bytes], lang: str = "hin+eng") -> str:
    import io

    import pytesseract
    from PIL import Image

    texts = []
    for png in images:
        with Image.open(io.BytesIO(png)) as img:
            texts.append(pytesseract.image_to_string(img, lang=lang))
    return "\n\n".join(texts)


async def vision_transcribe(images: list[bytes]) -> str | None:
    """Transcribe page images with a vision LLM (Gemini) if configured."""
    settings = get_settings()
    if not settings.gemini_api_key:
        return None
    from app.providers.llm import get_llm

    llm = get_llm()
    parts: list[dict[str, object]] = [
        {
            "type": "text",
            "text": (
                "Transcribe this Delhi Traffic Police document exactly. Keep Hindi in "
                "Devanagari Unicode and English as-is. Preserve headings and numbered "
                "lists. Output only the transcription."
            ),
        }
    ]
    for png in images[:6]:
        b64 = base64.b64encode(png).decode()
        parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    try:
        await llm.limiter.acquire(settings.llm_model_fallback)
        import litellm

        resp = await litellm.acompletion(
            model=settings.llm_model_fallback,
            messages=[{"role": "user", "content": parts}],
            max_tokens=4000,
            timeout=90,
        )
        return str(resp.choices[0].message.content or "")
    except Exception as exc:
        log.warning("vision transcription failed", extra={"err": str(exc)[:200]})
        return None


async def parse_pdf(data: bytes) -> ParsedPdf:
    text, pages = await asyncio.to_thread(extract_text_layer, data)
    if not looks_garbled(text):
        return ParsedPdf(text=text, method="text", pages=pages, quality_ok=True)
    log.info("pdf text layer poor; running OCR", extra={"pages": pages})
    images = await asyncio.to_thread(render_pages, data)
    try:
        ocr_text = await asyncio.to_thread(ocr_images, images)
    except Exception as exc:  # tesseract missing locally
        log.warning("ocr unavailable", extra={"err": str(exc)[:200]})
        ocr_text = ""
    if ocr_text and not looks_garbled(ocr_text):
        return ParsedPdf(text=ocr_text, method="ocr", pages=pages, quality_ok=True)
    vision = await vision_transcribe(images)
    if vision and not looks_garbled(vision):
        return ParsedPdf(text=vision, method="vision", pages=pages, quality_ok=True)
    best = max([text, ocr_text, vision or ""], key=len)
    return ParsedPdf(
        text=best, method="ocr" if best == ocr_text else "text", pages=pages, quality_ok=False
    )
