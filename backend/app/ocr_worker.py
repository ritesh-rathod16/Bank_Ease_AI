"""Single-page OCR worker. The parent process enforces a hard timeout and output cap."""
import json
import re
import sys

import pymupdf


MAX_PAGE_SIDE_PIXELS = 6000
MAX_PAGE_PIXELS = 25_000_000


def main():
    try:
        if len(sys.argv) != 4:
            raise ValueError("invalid arguments")
        page_index = int(sys.argv[1])
        language = sys.argv[2]
        dpi = int(sys.argv[3])
        if page_index < 0 or not re.fullmatch(r"[A-Za-z0-9_+-]{2,64}", language) or not 120 <= dpi <= 300:
            raise ValueError("invalid OCR settings")
        pdf_bytes = sys.stdin.buffer.read(10 * 1024 * 1024 + 1)
        if len(pdf_bytes) > 10 * 1024 * 1024:
            raise ValueError("input limit exceeded")
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
            if page_index >= len(document):
                raise ValueError("page out of range")
            page = document[page_index]
            scale = dpi / 72
            pixel_width = page.rect.width * scale
            pixel_height = page.rect.height * scale
            if (pixel_width > MAX_PAGE_SIDE_PIXELS or pixel_height > MAX_PAGE_SIDE_PIXELS
                    or pixel_width * pixel_height > MAX_PAGE_PIXELS):
                print(json.dumps({"error": True, "error_code": "page_limit"}))
                return 2
            text_page = page.get_textpage_ocr(language=language, dpi=dpi, full=True)
            text = page.get_text("text", textpage=text_page)
            blocks = page.get_text("dict", textpage=text_page).get("blocks", [])
            lines = []
            for block in blocks:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    line_text = "".join(span.get("text", "") for span in spans).strip()
                    if not line_text:
                        continue
                    bounds = [span.get("bbox") for span in spans if span.get("bbox")]
                    if not bounds:
                        bounds = [line.get("bbox", [])]
                    if bounds and len(bounds[0]) == 4:
                        bbox = [min(item[0] for item in bounds), min(item[1] for item in bounds),
                                max(item[2] for item in bounds), max(item[3] for item in bounds)]
                    else:
                        bbox = None
                    lines.append({"text": line_text, "bbox": bbox})
            words = []
            for item in page.get_text("words", textpage=text_page)[:30_000]:
                x0, y0, x1, y1, word, _block_number, _line_number, _word_number = item
                if not word or not all(map(lambda value: isinstance(value, (int, float)), (x0, y0, x1, y1))):
                    continue
                words.append({
                    "text": str(word)[:256],
                    "bbox": [float(x0), float(y0), float(x1), float(y1)],
                })
        print(json.dumps({"text": text[:500_000], "lines": lines[:20_000], "words": words}, ensure_ascii=False))
        return 0
    except Exception as exc:
        message = str(exc).casefold()
        code = "engine_unavailable" if any(part in message for part in ("tesseract", "ocr", "tessdata")) else "processing_failed"
        print(json.dumps({"error": True, "error_code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
