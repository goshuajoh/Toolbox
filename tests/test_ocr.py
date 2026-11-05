import base64
import json
import shutil

import pytest

from toolbank import *
from tests._server import serve


@pytest.fixture(scope="module")
def text_pdf(tmp_path_factory):
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    p = tmp_path_factory.mktemp("ocr") / "text.pdf"
    c = canvas.Canvas(str(p), pagesize=(595, 300))
    c.setFont("Helvetica", 28)
    c.drawString(40, 220, "Trade Date: 06/03/2026")
    c.drawString(40, 160, "Amount: 1,250.00 USD")
    c.drawString(40, 100, "Counterparty: ACME BANK")
    c.save()
    return p


@pytest.fixture(scope="module")
def tess():
    pytest.importorskip("pytesseract")
    pytest.importorskip("pypdfium2")
    if not shutil.which("tesseract"):
        pytest.skip("tesseract not installed")


def test_pdf_to_images_and_ocr(text_pdf, tess):
    img = pdf_to_images(text_pdf, dpi=200)[0]
    assert abs(img.size[0] - 595 * 200 / 72) <= 1 and abs(img.size[1] - 300 * 200 / 72) <= 1
    text = ocr_image(img)
    assert "Trade Date: 06/03/2026" in text and "1,250.00" in text
    words = ocr_data(img, prepare=False)
    assert {"text", "conf", "left", "top", "width", "height"} <= set(words.columns) and words["conf"].min() >= 0
    lines = ocr_lines(words)
    assert len(lines) == 3 and lines.loc[1, "text"].startswith("Amount")
    assert low_confidence(words, threshold=101).shape[0] == len(words)
    box = lines.loc[1, ["left", "top", "right", "bottom"]].tolist()
    box = (box[0] - 10, box[1] - 10, box[2] + 10, box[3] + 10)
    assert "1,250.00" in ocr_regions(img, {"amount": box})["amount"]
    kv = parse_key_values(text, keys=["Trade Date", "Amount"])
    assert kv["Trade Date"] == "06/03/2026"


def test_ocr_pdf_hybrid(text_pdf, tess, tmp_path):
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    from reportlab.lib.utils import ImageReader
    img = pdf_to_images(text_pdf, dpi=200)[0]
    scanned = tmp_path / "mixed.pdf"
    c = canvas.Canvas(str(scanned), pagesize=(595, 300))
    c.drawImage(ImageReader(img), 0, 0, width=595, height=300)       # page 1: picture only (a scan)
    c.showPage()
    c.setFont("Helvetica", 20)
    c.drawString(40, 150, "This page has a real text layer for extraction")
    c.save()
    out = ocr_pdf(scanned, dpi=200)
    assert out["method"].tolist() == ["ocr", "text"]
    assert "ACME BANK" in out.loc[0, "text"] and "real text layer" in out.loc[1, "text"]
    seen = []
    out2 = ocr_pdf(scanned, pages=[0], engine=lambda im: seen.append(im.size) or "custom")
    assert out2["text"].tolist() == ["custom"] and seen


def test_multipage_tiff(text_pdf, tess, tmp_path):
    img = pdf_to_images(text_pdf, dpi=150)[0].convert("L")
    second = img.transpose(0).transpose(0)                           # same page again
    tif = tmp_path / "fax.tif"
    img.save(tif, save_all=True, append_images=[second])
    text = ocr_image(tif)
    assert text.count("ACME BANK") == 2 and "\f" in text
    assert sorted(ocr_data(tif)["page_num"].unique().tolist()) == [1, 2]


def test_prepare_image_transparency():
    Image = pytest.importorskip("PIL.Image")
    im = Image.new("RGBA", (50, 20), (0, 0, 0, 0))
    out = prepare_image(im, scale=2, threshold=128)
    assert out.mode == "L" and out.size == (120, 60) and out.getpixel((5, 5)) == 255


def test_docintel_analyze_mock(tmp_path):
    pytest.importorskip("requests")
    doc = tmp_path / "inv.pdf"
    doc.write_bytes(b"%PDF-fake")
    state = {"polls": 0}
    result = {"content": "Invoice 42", "tables": [{"rowCount": 3, "columnCount": 2, "cells": [
        {"rowIndex": 0, "columnIndex": 0, "content": "Item", "kind": "columnHeader"},
        {"rowIndex": 0, "columnIndex": 1, "content": "Amount", "kind": "columnHeader"},
        {"rowIndex": 1, "columnIndex": 0, "content": "Fee", "rowSpan": 2},
        {"rowIndex": 1, "columnIndex": 1, "content": "10"},
        {"rowIndex": 2, "columnIndex": 1, "content": "20"}]}],
        "documents": [{"fields": {"InvoiceTotal": {"type": "currency", "content": "USD 30.00",
                                                   "valueCurrency": {"amount": 30.0, "currencyCode": "USD"},
                                                   "confidence": 0.97}}}]}

    def analyze(req):
        body = json.loads(req.body)
        assert base64.b64decode(body["base64Source"]) == b"%PDF-fake"
        assert req.query["api-version"] == "2024-11-30" and req.headers["Ocp-Apim-Subscription-Key"] == "k"
        return 202, {"Operation-Location": f"http://127.0.0.1:{req.server.server_address[1]}/ops/1"}, b""

    def op(req):
        state["polls"] += 1
        return 200, {}, ({"status": "running"} if state["polls"] < 3 else
                         {"status": "succeeded", "analyzeResult": result})

    routes = {("POST", "/documentintelligence/documentModels/prebuilt-invoice:analyze"): analyze,
              ("GET", "/ops/1"): op,
              ("POST", "/documentintelligence/documentModels/bad:analyze"): lambda r: (
                  202, {"Operation-Location": f"http://127.0.0.1:{r.server.server_address[1]}/ops/2"}, b""),
              ("GET", "/ops/2"): lambda r: (200, {}, {"status": "failed", "error": {"message": "corrupt"}})}
    with serve(routes) as (base, log):
        s = api_key_session("k", header="Ocp-Apim-Subscription-Key")
        slept = []
        res = docintel_analyze(doc, base, s, model="prebuilt-invoice", sleep=slept.append)
        assert state["polls"] == 3 and slept == [2, 2]
        assert docintel_text(res) == "Invoice 42"
        t = docintel_tables(res)[0]
        assert t.columns.tolist() == ["Item", "Amount"] and t.values.tolist() == [["Fee", "10"], ["Fee", "20"]]
        f = docintel_fields(res)
        assert f.loc[0, "field"] == "InvoiceTotal" and f.loc[0, "value"] == {"amount": 30.0, "currencyCode": "USD"}
        with pytest.raises(BusinessError) as e:
            docintel_analyze(doc, base, s, model="bad", sleep=lambda x: None)
        assert e.value.code == "DOCINTEL_FAILED"
