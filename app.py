"""TxReader web app: upload WeChat screenshots, get transaction rows."""
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from txreader.ocr import decode_image
from txreader.records import build_records
from txreader.wechat import parse_image

app = FastAPI(title="TxReader")
STATIC = Path(__file__).parent / "static"


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/api/parse")
def parse(files: list[UploadFile] = File(...), year: int = Form(date.today().year)):
    per_image, errors = [], []
    for f in files:
        try:
            per_image.append(parse_image(decode_image(f.file.read())))
        except ValueError as e:
            errors.append(f"{f.filename}: {e}")
    if not per_image and errors:
        raise HTTPException(400, "; ".join(errors))
    return {"records": build_records(per_image, year), "errors": errors}
