#!/usr/bin/env python3
"""
Google Drive/Sheets → Confluence attachment sync

Supports:
- source-kind=sheet_values  (pull whole tab by gid or name)
- source-kind=drive_export  (export Google files—Sheets/Docs/Slides—to CSV/PDF/etc)
- source-kind=drive_download (download non-Google files stored in Drive)
- source-kind=drive_list    (list files in a folder → CSV)

Optional write-back (when flags provided):
- --write-back-range "Sync!A1"       -> set that cell with a status line
- --write-back-template "..."        -> customize the written text
- --append-log "SyncLog!A:C"         -> append [timestamp, filename, status]

Auth:
  Google: Service Account JSON (share target Sheet/Folder/File to the SA email)
  Confluence: username + password (or PAT) via env vars

Required env:
  CONF_USER        Confluence username (or email)
  CONF_PASS        Confluence password or Personal Access Token
  GOOGLE_SA_JSON   Path to service-account.json (e.g., /secrets/service-account.json)
"""

import argparse
import csv
import io
import os
import sys
import time
from typing import List, Optional

import requests
from requests.adapters import HTTPAdapter, Retry

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError

# Optional .env support during local/dev runs
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


# -----------------------------
# Google helpers (Sheets + Drive)
# -----------------------------
def build_google_services(google_sa_json: str, scopes: List[str]):
    creds = service_account.Credentials.from_service_account_file(google_sa_json, scopes=scopes)
    sheets = build("sheets", "v4", credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    return sheets, drive


def get_sheet_values(
    sheets,
    spreadsheet_id: str,
    gid: Optional[str],
    tab_name: Optional[str],
    value_render_option: str = "FORMATTED_VALUE",
) -> List[List[str]]:
    """Return values for an entire tab (gid or tab_name)."""
    if tab_name:
        target_title = tab_name
    else:
        # Resolve title from gid
        meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id, includeGridData=False).execute()
        target_title = None
        for sh in meta.get("sheets", []):
            if str(sh["properties"]["sheetId"]) == str(gid):
                target_title = sh["properties"]["title"]
                break
        if not target_title:
            raise RuntimeError(f"Tab with gid={gid} not found in spreadsheet {spreadsheet_id}")

    result = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=target_title,
        valueRenderOption=value_render_option,
    ).execute()
    return result.get("values", [])


# ---- write-back helpers ----
def set_sheet_values(sheets, spreadsheet_id: str, a1_range: str, rows: List[List[str]],
                     value_input_option: str = "USER_ENTERED"):
    body = {"range": a1_range, "majorDimension": "ROWS", "values": rows}
    return sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=a1_range,
        valueInputOption=value_input_option,
        body=body
    ).execute()


def append_sheet_values(sheets, spreadsheet_id: str, a1_range: str, rows: List[List[str]],
                        value_input_option: str = "USER_ENTERED"):
    body = {"values": rows}
    return sheets.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=a1_range,
        valueInputOption=value_input_option,
        insertDataOption="INSERT_ROWS",
        body=body
    ).execute()
# ----------------------------


def drive_export_bytes(drive, file_id: str, export_mime: str) -> bytes:
    """Export a Google-native file (Sheet/Doc/Slide) as bytes (e.g., CSV/PDF)."""
    try:
        req = drive.files().export(fileId=file_id, mimeType=export_mime)
        data = req.execute()  # returns bytes
        if isinstance(data, str):
            data = data.encode("utf-8")
        return data
    except HttpError as e:
        raise RuntimeError(f"Drive export failed for file {file_id}: {e}")


def drive_download_bytes(drive, file_id: str) -> bytes:
    """Download a non-Google file stored in Drive."""
    try:
        req = drive.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, req)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()
    except HttpError as e:
        raise RuntimeError(f"Drive download failed for file {file_id}: {e}")


def drive_list_files(drive, folder_id: str, query: Optional[str] = None) -> List[dict]:
    """List files in a folder (optionally filtered by an additional query)."""
    q_parts = [f"'{folder_id}' in parents", "trashed = false"]
    if query:
        q_parts.append(query)
    q = " and ".join(q_parts)
    try:
        files = []
        page_token = None
        while True:
            results = drive.files().list(
                q=q,
                pageSize=100,
                pageToken=page_token,
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True
            ).execute()
            files.extend(results.get("files", []))
            page_token = results.get("nextPageToken")
            if not page_token:
                break
        return files
    except HttpError as e:
        raise RuntimeError(f"Drive list failed for folder {folder_id}: {e}")


def to_csv_utf8_bom(values: List[List[str]]) -> bytes:
    """Convert a 2D list to CSV bytes with UTF-8 BOM (Excel/Confluence friendly)."""
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\r\n")
    for row in values or []:
        writer.writerow([("" if v is None else str(v)) for v in row])
    data = output.getvalue()
    return ("\ufeff" + data).encode("utf-8")  # prepend BOM


def rows_to_csv_utf8_bom(rows: List[List[str]]) -> bytes:
    return to_csv_utf8_bom(rows)


# -----------------------------
# Confluence helpers
# -----------------------------
def _timed_request(orig_request, timeout):
    def wrapper(method, url, **kwargs):
        if "timeout" not in kwargs:
            kwargs["timeout"] = timeout
        return orig_request(method, url, **kwargs)
    return wrapper


def build_session(user: str, password: str, timeout: int = 30) -> requests.Session:
    s = requests.Session()
    s.auth = (user, password)
    retries = Retry(
        total=5,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.request = _timed_request(s.request, timeout)
    return s


def get_page_check(session: requests.Session, base: str, page_id: str):
    url = f"{base}/rest/api/content/{page_id}"
    r = session.get(url, headers={"Accept": "application/json"})
    r.raise_for_status()
    return r.json()


def find_attachment(session: requests.Session, base: str, page_id: str, filename: str) -> Optional[str]:
    url = f"{base}/rest/api/content/{page_id}/child/attachment"
    r = session.get(url, params={"filename": filename}, headers={"Accept": "application/json"})
    r.raise_for_status()
    js = r.json()
    if js.get("results"):
        return js["results"][0]["id"]
    return None


def create_attachment(session: requests.Session, base: str, page_id: str, filename: str, payload_bytes: bytes):
    url = f"{base}/rest/api/content/{page_id}/child/attachment"
    files = {"file": (filename, payload_bytes, "application/octet-stream")}
    headers = {"X-Atlassian-Token": "no-check"}
    r = session.post(url, files=files, headers=headers)
    if r.status_code >= 300:
        raise RuntimeError(f"Attachment create failed: {r.status_code} - {r.text}")


def update_attachment(session: requests.Session, base: str, page_id: str, att_id: str, filename: str, payload_bytes: bytes):
    url = f"{base}/rest/api/content/{page_id}/child/attachment/{att_id}/data"
    files = {"file": (filename, payload_bytes, "application/octet-stream")}
    headers = {"X-Atlassian-Token": "no-check"}
    r = session.post(url, files=files, headers=headers)
    if r.status_code >= 300:
        raise RuntimeError(f"Attachment update failed: {r.status_code} - {r.text}")


# -----------------------------
# Main
# -----------------------------
def main():
    parser = argparse.ArgumentParser(description="Sync Google Drive/Sheets → Confluence attachment.")
    # Core target
    parser.add_argument("--confluence-base", required=True, help="Confluence base URL")
    parser.add_argument("--page-id", required=True, help="Confluence page ID")
    parser.add_argument("--filename", required=True, help="Attachment filename to create/update")

    # Source selection
    parser.add_argument("--source-kind", default="sheet_values",
                        choices=["sheet_values", "drive_export", "drive_download", "drive_list"])

    # Sheet-specific
    parser.add_argument("--spreadsheet", help="Google Spreadsheet ID (for sheet_values)")
    parser.add_argument("--gid", help="Tab gid (string). Provide gid OR --tab-name for sheet_values.")
    parser.add_argument("--tab-name", help="Tab name (alternative to gid).")
    parser.add_argument("--render", default="FORMATTED_VALUE",
                        choices=["FORMATTED_VALUE", "UNFORMATTED_VALUE", "FORMULA"],
                        help="Google Sheets valueRenderOption")

    # Drive-specific
    parser.add_argument("--drive-file-id", help="Drive file ID (for drive_export/drive_download)")
    parser.add_argument("--drive-folder-id", help="Drive folder ID (for drive_list)")
    parser.add_argument("--drive-query", help="Additional Drive query (for drive_list)")
    parser.add_argument("--export-mime", default="text/csv",
                        help="Export MIME type (e.g., text/csv, application/pdf) for drive_export.")

    # Write-back (optional)
    parser.add_argument("--write-back-range", help='A1 range to write, e.g. "Sync!A1"')
    parser.add_argument("--write-back-template",
                        default="Last sync: {timestamp} | File: {filename} | Status: {status}",
                        help="Text written to --write-back-range (supports {timestamp}, {filename}, {status})")
    parser.add_argument("--append-log", help='A1 range to append rows, e.g. "SyncLog!A:C"')

    args = parser.parse_args()

    conf_user = os.environ.get("CONF_USER")
    conf_pass = os.environ.get("CONF_PASS")
    google_sa_json = os.environ.get("GOOGLE_SA_JSON", "service-account.json")

    if not conf_user or not conf_pass:
        raise SystemExit("ERROR: Set CONF_USER and CONF_PASS environment variables.")
    if not os.path.exists(google_sa_json):
        raise SystemExit(f"ERROR: Google Service Account json not found at {google_sa_json}")

    # Scopes (read-only by default)
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    if args.source_kind in ("drive_export", "drive_download", "drive_list"):
        scopes.append("https://www.googleapis.com/auth/drive.readonly")
    # Escalate Sheets scope to edit only if writing back
    if args.write_back_range or args.append_log:
        scopes = [s for s in scopes if "spreadsheets" not in s]
        scopes.append("https://www.googleapis.com/auth/spreadsheets")

    sheets, drive = build_google_services(google_sa_json, scopes)

    # 1) Acquire payload bytes
    if args.source_kind == "sheet_values":
        if not args.spreadsheet:
            raise SystemExit("ERROR: --spreadsheet is required for source-kind=sheet_values")
        values = get_sheet_values(
            sheets=sheets,
            spreadsheet_id=args.spreadsheet,
            gid=args.gid,
            tab_name=args.tab_name,
            value_render_option=args.render,
        )
        payload_bytes = to_csv_utf8_bom(values)
        final_filename = args.filename

    elif args.source_kind == "drive_export":
        if not args.drive_file_id:
            raise SystemExit("ERROR: --drive-file-id is required for source-kind=drive_export")
        payload_bytes = drive_export_bytes(drive, args.drive_file_id, args.export_mime)
        final_filename = args.filename

    elif args.source_kind == "drive_download":
        if not args.drive_file_id:
            raise SystemExit("ERROR: --drive-file-id is required for source-kind=drive_download")
        payload_bytes = drive_download_bytes(drive, args.drive_file_id)
        final_filename = args.filename

    elif args.source_kind == "drive_list":
        if not args.drive_folder_id:
            raise SystemExit("ERROR: --drive-folder-id is required for source-kind=drive_list")
        files = drive_list_files(drive, args.drive_folder_id, args.drive_query)
        rows = [["id", "name", "mimeType", "modifiedTime", "size"]]
        rows += [[f.get("id"), f.get("name"), f.get("mimeType"), f.get("modifiedTime"), f.get("size")] for f in files]
        payload_bytes = rows_to_csv_utf8_bom(rows)
        final_filename = args.filename

    else:
        raise SystemExit("Unsupported source-kind")

    # 2) Upload to Confluence
    session = build_session(conf_user, conf_pass)
    _ = get_page_check(session, args.confluence_base, args.page_id)  # raises if not reachable

    att_id = find_attachment(session, args.confluence_base, args.page_id, final_filename)
    if att_id:
        update_attachment(session, args.confluence_base, args.page_id, att_id, final_filename, payload_bytes)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Updated attachment: {final_filename}")
    else:
        create_attachment(session, args.confluence_base, args.page_id, final_filename, payload_bytes)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Created attachment: {final_filename}")

    # 3) Optional write-back to the Sheet
    if args.write_back_range or args.append_log:
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            status = "updated" if att_id else "created"

            if args.write_back_range:
                if not args.spreadsheet:
                    raise RuntimeError("--spreadsheet is required when using --write-back-range")
                text = args.write_back_template.format(
                    timestamp=ts, filename=final_filename, status=status
                )
                set_sheet_values(sheets, args.spreadsheet, args.write_back_range, [[text]])

            if args.append_log:
                if not args.spreadsheet:
                    raise RuntimeError("--spreadsheet is required when using --append-log")
                append_sheet_values(sheets, args.spreadsheet, args.append_log, [[ts, final_filename, status]])
        except Exception as e:
            print(f"Write-back warning: {e}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(1)
