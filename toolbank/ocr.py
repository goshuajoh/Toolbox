"""OCR: Tesseract (pip install pytesseract pillow + Tesseract app), Windows built-in OCR (pip install winocr, nothing
else to install), Azure AI Document Intelligence (ONLY your bank's approved Azure resource). Hybrid PDF reading:
text layer first, OCR only for scanned pages (pip install pypdfium2)."""
import base64
import concurrent.futures
import time
from pathlib import Path

import pandas as pd

from .robust import BusinessError, SystemError_

DOCINTEL_API = "2024-11-30"


def set_tesseract(path):
    """Point pytesseract at tesseract.exe, e.g. r'C:\\Program Files\\Tesseract-OCR\\tesseract.exe'."""
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = str(path)


def _image(img):
    from PIL import Image
    if isinstance(img, (str, Path)):
        im = Image.open(img)
        im.load()
        return im
    return img


def prepare_image(img, scale=2.0, grayscale=True, threshold=None, sharpen=False, border=10):
    """Clean an image for OCR: flatten transparency, grayscale, upscale small text, optional sharpen and
    black/white threshold (0-255), white border. Path or PIL image -> PIL image."""
    from PIL import Image, ImageFilter, ImageOps
    im = _image(img)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        im = Image.alpha_composite(Image.new("RGBA", im.size, (255, 255, 255, 255)), im).convert("RGB")
    if grayscale:
        im = ImageOps.grayscale(im)
    if scale and scale != 1:
        resample = getattr(Image, "Resampling", Image).LANCZOS
        im = im.resize((int(im.width * scale), int(im.height * scale)), resample)
    if sharpen:
        im = im.filter(ImageFilter.SHARPEN)
    if threshold is not None:
        im = im.point(lambda v: 255 if v > threshold else 0)
    if border:
        im = ImageOps.expand(im, border=border, fill="white")
    return im


def _frames(img):
    """Every page of an image (multi-page TIFF / fax) as separate PIL images."""
    from PIL import ImageSequence
    im = _image(img)
    if getattr(im, "n_frames", 1) > 1:
        return [f.copy() for f in ImageSequence.Iterator(im)]
    return [im]


def ocr_image(img, lang="eng", psm=6, prepare=True, config=""):
    """Image -> text with Tesseract (every page of a multi-page TIFF, joined by a form feed).
    psm: 6 one block, 4 columns, 11 sparse text, 7 one line."""
    import pytesseract
    texts = [pytesseract.image_to_string(prepare_image(f) if prepare else f, lang=lang,
                                         config=f"--psm {psm} {config}".strip()) for f in _frames(img)]
    return "\f".join(texts)


def ocr_data(img, lang="eng", psm=6, prepare=True, min_conf=-1):
    """Words with position + confidence -> DataFrame(text, conf, left, top, width, height, page_num, block, par,
    line, word); page_num counts TIFF pages. Coordinates are on the prepared image when prepare=True."""
    import pytesseract
    parts = []
    for page, f in enumerate(_frames(img), 1):
        d = pd.DataFrame(pytesseract.image_to_data(prepare_image(f) if prepare else f, lang=lang,
                                                   config=f"--psm {psm}", output_type=pytesseract.Output.DICT))
        parts.append(d.assign(page_num=page))
    d = pd.concat(parts, ignore_index=True)
    d["conf"] = pd.to_numeric(d["conf"], errors="coerce")
    d["text"] = d["text"].astype(str).str.strip()
    d = d[(d["text"] != "") & (d["conf"] >= min_conf)]
    d = d.rename(columns={"block_num": "block", "par_num": "par", "line_num": "line", "word_num": "word"})
    return d[["text", "conf", "left", "top", "width", "height", "page_num", "block", "par", "line", "word"]] \
        .reset_index(drop=True)


def ocr_lines(words):
    """ocr_data words -> one row per line: text, conf (mean), left, top, right, bottom (top-to-bottom order)."""
    keys = ["page_num", "block", "par", "line"]
    w = words.assign(right=words["left"] + words["width"], bottom=words["top"] + words["height"])
    out = w.groupby(keys, sort=False).agg(text=("text", " ".join), conf=("conf", "mean"), left=("left", "min"),
                                          top=("top", "min"), right=("right", "max"), bottom=("bottom", "max"))
    return out.reset_index().sort_values(["page_num", "top", "left"]).drop(columns=keys[1:]).reset_index(drop=True)


def low_confidence(words, threshold=60):
    """Words Tesseract was unsure about (conf < threshold): route these to a human check."""
    return words[words["conf"] < threshold]


def ocr_regions(img, boxes, lang="eng", psm=7, prepare=True):
    """Fixed-layout forms: {'field': (left, top, right, bottom)} in original image pixels -> {'field': text}."""
    im = _image(img)
    return {name: ocr_image(im.crop(box), lang=lang, psm=psm, prepare=prepare).strip() for name, box in boxes.items()}


def pdf_to_images(path, dpi=300, pages=None):
    """PDF pages -> PIL images (no Poppler needed). pages = 0-based indexes, None = all."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path))
    try:
        out = []
        for i in (range(len(pdf)) if pages is None else pages):
            page = pdf[i]
            out.append(page.render(scale=dpi / 72).to_pil().copy())
            page.close()
        return out
    finally:
        pdf.close()


def ocr_pdf(path, lang="eng", dpi=300, min_chars=25, pages=None, psm=6, engine=None):
    """PDF -> DataFrame(page, method, text): the text layer where a page has one, OCR only for scanned pages.
    engine = fn(PIL image) -> text to use another OCR (e.g. windows_ocr)."""
    import pypdfium2 as pdfium
    pdf, rows = pdfium.PdfDocument(str(path)), []
    try:
        for i in (range(len(pdf)) if pages is None else pages):
            page = pdf[i]
            tp = page.get_textpage()
            text = tp.get_text_range().replace("\r\n", "\n")
            tp.close()
            if len(text.strip()) >= min_chars:
                rows.append({"page": i + 1, "method": "text", "text": text})
            else:
                img = prepare_image(page.render(scale=dpi / 72).to_pil().copy(), scale=1)
                rows.append({"page": i + 1, "method": "ocr",
                             "text": engine(img) if engine else ocr_image(img, lang=lang, psm=psm, prepare=False)})
            page.close()
    finally:
        pdf.close()
    return pd.DataFrame(rows, columns=["page", "method", "text"])


def windows_ocr(img, lang="en-US"):
    """Windows 10/11 built-in OCR (pip install winocr). Image path or PIL -> text. Runs in a worker thread so it
    also works inside Jupyter."""
    import winocr
    im = _image(img)

    def run():
        if hasattr(winocr, "recognize_pil_sync"):
            return winocr.recognize_pil_sync(im, lang)
        import asyncio
        return asyncio.run(winocr.recognize_pil(im, lang))
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        res = ex.submit(run).result()
    return res["text"] if isinstance(res, dict) else res.text


# ---------- Azure AI Document Intelligence (v4 REST) ----------

def docintel_analyze(path, endpoint, session, model="prebuilt-layout", pages=None, features=(), timeout_s=300,
                     poll_s=2, sleep=time.sleep, clock=time.monotonic):
    """File -> analyzeResult dict. ONLY with your bank's approved resource (the file leaves your PC).
    session = api_key_session(key, header='Ocp-Apim-Subscription-Key') or bearer_session(token).
    model: prebuilt-read (text) | prebuilt-layout (tables) | prebuilt-invoice | your custom model id."""
    from .api import api_call
    url = f"{endpoint.rstrip('/')}/documentintelligence/documentModels/{model}:analyze"
    params = {"api-version": DOCINTEL_API}
    if pages:
        params["pages"] = pages                      # e.g. "1-3,5"
    if features:
        params["features"] = ",".join(features)
    body = {"base64Source": base64.b64encode(Path(path).read_bytes()).decode("ascii")}
    r = api_call(session, "POST", url, params=params, json_body=body, expect_json=False)
    op = r.headers.get("Operation-Location")
    if not op:
        raise SystemError_("Document Intelligence returned no Operation-Location", code="DOCINTEL")
    deadline = clock() + timeout_s
    while True:
        j = api_call(session, "GET", op)
        status = str(j.get("status", "")).lower()
        if status == "succeeded":
            return j.get("analyzeResult", {})
        if status == "failed":
            raise BusinessError(f"analysis failed: {(j.get('error') or {}).get('message', '')}", code="DOCINTEL_FAILED")
        if clock() > deadline:
            raise SystemError_(f"analysis not finished in {timeout_s}s", code="DOCINTEL_TIMEOUT")
        sleep(poll_s)


def docintel_text(result):
    """Full text of an analyzeResult."""
    return result.get("content", "")


def docintel_tables(result):
    """analyzeResult tables -> list of DataFrames (columnHeader rows become column names, spans filled)."""
    from .htmlparse import _unique
    out = []
    for t in result.get("tables", []):
        grid = [[None] * t["columnCount"] for _ in range(t["rowCount"])]
        head = set()
        for c in t.get("cells", []):
            for r in range(c["rowIndex"], c["rowIndex"] + c.get("rowSpan", 1)):
                for k in range(c["columnIndex"], c["columnIndex"] + c.get("columnSpan", 1)):
                    grid[r][k] = c.get("content", "")
            if c.get("kind") == "columnHeader":
                head.add(c["rowIndex"])
        n = 0
        while n in head:
            n += 1
        if n:
            cols = [" | ".join(dict.fromkeys(str(grid[h][i]) for h in range(n) if grid[h][i])) or None
                    for i in range(t["columnCount"])]
            out.append(pd.DataFrame(grid[n:], columns=_unique(cols)))
        else:
            out.append(pd.DataFrame(grid))
    return out


def docintel_fields(result):
    """Fields of prebuilt/custom models -> DataFrame(doc, field, content, value, confidence, type)."""
    rows = []
    for i, d in enumerate(result.get("documents", [])):
        for name, f in (d.get("fields") or {}).items():
            f = f or {}
            value = next((v for k, v in f.items() if k.startswith("value")), None)
            rows.append({"doc": i, "field": name, "content": f.get("content"), "value": value,
                         "confidence": f.get("confidence"), "type": f.get("type")})
    return pd.DataFrame(rows, columns=["doc", "field", "content", "value", "confidence", "type"])
