#!/usr/bin/env python3
import os
import csv
import io
import subprocess
from typing import Optional, Any, Dict, List

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from google.oauth2 import service_account
from googleapiclient.discovery import build

app = FastAPI(title="Sheet → Confluence Sync API", version="1.1.0")

# -----------------------------
# Helpers: Google Sheets read
# -----------------------------
def _get_sheets_client():
    sa_path = os.environ.get("GOOGLE_SA_JSON", "/secrets/service-account.json")
    if not os.path.exists(sa_path):
        raise RuntimeError(f"Google service account json not found: {sa_path}")

    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = service_account.Credentials.from_service_account_file(sa_path, scopes=scopes)
    return build("sheets", "v4", credentials=creds)

def _read_tab_as_rows(
    spreadsheet_id: str,
    tab_name: str,
    value_render_option: str = "FORMATTED_VALUE",
) -> List[List[str]]:
    sheets = _get_sheets_client()
    resp = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=tab_name,
        valueRenderOption=value_render_option,
    ).execute()
    return resp.get("values", [])

def _normalize_table(values: List[List[str]]) -> List[List[str]]:
    if not values:
        return values
    max_cols = max(len(r) for r in values)
    return [r + [""] * (max_cols - len(r)) for r in values]

def _rows_to_dicts(values: List[List[str]]) -> List[Dict[str, Any]]:
    values = _normalize_table(values)
    if not values:
        return []

    headers = [h.strip() for h in values[0]]
    out: List[Dict[str, Any]] = []
    for row in values[1:]:
        item = {headers[i]: row[i] for i in range(len(headers))}
        out.append(item)
    return out

def _norm(s: str) -> str:
    return (s or "").strip().lower()

# -----------------------------
# Sync API (your existing flow)
# -----------------------------
class SyncRequest(BaseModel):
    source_kind: Optional[str] = None
    attachment_filename: Optional[str] = None
    sheet_gid: Optional[str] = None
    sheet_tab_name: Optional[str] = None

def _run_sync_blocking(req: SyncRequest):
    env = os.environ.copy()

    if req.source_kind is not None:
        env["SOURCE_KIND"] = req.source_kind
    if req.attachment_filename is not None:
        env["ATTACHMENT_FILENAME"] = req.attachment_filename

    # Prefer tab name unless gid explicitly given
    if req.sheet_gid is not None:
        env["SHEET_GID"] = req.sheet_gid
        env["SHEET_TAB_NAME"] = ""
    if req.sheet_tab_name is not None:
        env["SHEET_TAB_NAME"] = req.sheet_tab_name
        env["SHEET_GID"] = ""

    p = subprocess.run(
        ["bash", "/app/entrypoint.sh"],
        env=env,
        capture_output=True,
        text=True,
        timeout=int(os.getenv("SYNC_TIMEOUT_SECONDS", "600")),
    )
    return p.returncode, p.stdout, p.stderr

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/sync")
async def sync_now(req: SyncRequest = SyncRequest()):
    try:
        code, out, err = await run_in_threadpool(_run_sync_blocking, req)
    except subprocess.TimeoutExpired as e:
        raise HTTPException(
            status_code=504,
            detail={"ok": False, "error": "sync timed out", "stdout": e.stdout, "stderr": e.stderr},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail={"ok": False, "error": f"sync failed: {e}"})

    if code != 0:
        raise HTTPException(status_code=500, detail={"ok": False, "exit_code": code, "stdout": out, "stderr": err})

    return {"ok": True, "stdout": out, "stderr": err}

# -----------------------------
# NEW: Read/query endpoints
# -----------------------------
@app.get("/lesson")
def get_lesson(
    module_name: str = Query(..., description='e.g. "Football Basics / 101"'),
    section: str = Query(..., description='e.g. "1.1"'),
):
    spreadsheet_id = os.getenv("SPREADSHEET_ID")
    tab_name = os.getenv("SHEET_TAB_NAME") or os.getenv("SOURCE_SHEET_TAB_NAME") or "Curriculum"
    render = os.getenv("RENDER_OPT", "FORMATTED_VALUE")

    if not spreadsheet_id:
        raise HTTPException(status_code=500, detail={"ok": False, "error": "SPREADSHEET_ID not set"})

    try:
        rows = _read_tab_as_rows(spreadsheet_id, tab_name, render)
        items = _rows_to_dicts(rows)

        mn = _norm(module_name)
        sec = _norm(section)

        matches = [
            it for it in items
            if _norm(it.get("Module Name", "")) == mn and _norm(it.get("Section", "")) == sec
        ]

        return {"ok": True, "count": len(matches), "data": matches}
    except Exception as e:
        raise HTTPException(status_code=500, detail={"ok": False, "error": str(e)})

@app.get("/lessons")
def list_lessons(
    module_name: str = "",
    section_prefix: str = "",
    author: str = "",
    limit: int = 200,
):
    spreadsheet_id = os.getenv("SPREADSHEET_ID")
    tab_name = os.getenv("SHEET_TAB_NAME") or os.getenv("SOURCE_SHEET_TAB_NAME") or "Curriculum"
    render = os.getenv("RENDER_OPT", "FORMATTED_VALUE")

    if not spreadsheet_id:
        raise HTTPException(status_code=500, detail={"ok": False, "error": "SPREADSHEET_ID not set"})

    try:
        rows = _read_tab_as_rows(spreadsheet_id, tab_name, render)
        items = _rows_to_dicts(rows)

        mn = _norm(module_name)
        sp = _norm(section_prefix)
        au = _norm(author)

        def keep(it: Dict[str, Any]) -> bool:
            if mn and _norm(it.get("Module Name", "")) != mn:
                return False
            if sp and not _norm(it.get("Section", "")).startswith(sp):
                return False
            if au and _norm(it.get("author", "")) != au:
                return False
            return True

        filtered = [it for it in items if keep(it)]
        return {"ok": True, "count": len(filtered), "data": filtered[: max(1, limit)]}
    except Exception as e:
        raise HTTPException(status_code=500, detail={"ok": False, "error": str(e)})

@app.post("/summary")
def summary_placeholder():
    return {"ok": True, "message": "Not implemented yet"}
