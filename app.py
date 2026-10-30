"""TxReader web app: upload app screenshots, get transaction rows."""
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from txreader.ocr import decode_image
from txreader.parsers import PARSERS, parse_image
from txreader.records import build_records

app = FastAPI(title="TxReader")
STATIC = Path(__file__).parent / "static"


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/api/parse")
def parse(files: list[UploadFile] = File(...),
          year: int = Form(date.today().year),
          source: str = Form("wechat")):
    if source not in PARSERS:
        raise HTTPException(400, f"不支持的来源：{source}")
    per_image, errors = [], []
    for f in files:
        try:
            txs, warnings = parse_image(decode_image(f.file.read()), source)
        except ValueError as e:
            errors.append(f"{f.filename}: {e}")
            continue
        per_image.append(txs)
        errors += [f"{f.filename}: {w}" for w in warnings]
    if not per_image and errors:
        raise HTTPException(400, "; ".join(errors))
    try:
        records = build_records(per_image, year)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"records": records, "errors": errors}
