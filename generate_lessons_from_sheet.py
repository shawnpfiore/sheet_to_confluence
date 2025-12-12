#!/usr/bin/env python3
import os
import re
import sys
import textwrap
from typing import List, Dict, Optional

import requests
import urllib3
from google.oauth2 import service_account
from googleapiclient.discovery import build

from llm_client import call_llm_with_messages

# Suppress InsecureRequestWarning since we use verify=False for the Hive endpoint
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------- Env / config ----------

OLLAMA_API_URL = os.environ.get("OLLAMA_API_URL", "https://ollama.tib.ad.ea.com/api/generate") #need to update using for MCP
# Use a known-good default; can be overridden via env
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "codellama:7b")

CONF_USER = os.environ.get("CONF_USER")
CONF_PASS = os.environ.get("CONF_PASS")
CONFLUENCE_BASE = os.environ.get("CONFLUENCE_BASE", "https://confluence.ea.com")
PARENT_PAGE_ID = os.environ.get("PARENT_PAGE_ID")  # parent page for child lessons

GOOGLE_SA_JSON = os.environ.get("GOOGLE_SA_JSON", "service-account.json")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")
SHEET_GID = os.environ.get("SHEET_GID")  # expected as string from env

if not (CONF_USER and CONF_PASS and PARENT_PAGE_ID and SPREADSHEET_ID and SHEET_GID):
    print(
        "ERROR: CONF_USER, CONF_PASS, PARENT_PAGE_ID, SPREADSHEET_ID, SHEET_GID must be set.",
        file=sys.stderr,
    )
    sys.exit(1)


# ---------- Google Sheets helpers ----------

def build_sheets():
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = service_account.Credentials.from_service_account_file(GOOGLE_SA_JSON, scopes=scopes)
    return build("sheets", "v4", credentials=creds)


def get_values_by_gid(sheets) -> List[List[str]]:
    meta = sheets.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID,
        includeGridData=False
    ).execute()

    target_title: Optional[str] = None
    for sh in meta.get("sheets", []):
        if str(sh["properties"]["sheetId"]) == str(SHEET_GID):
            target_title = sh["properties"]["title"]
            break

    if not target_title:
        raise RuntimeError(f"Tab with gid={SHEET_GID} not found in spreadsheet {SPREADSHEET_ID}")

    res = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=target_title,
        valueRenderOption="FORMATTED_VALUE",
    ).execute()
    return res.get("values", [])


# ---------- Ollama helper ----------

def call_ollama(prompt: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }

    resp = requests.post(
        OLLAMA_API_URL,
        json=payload,
        verify=False,        # talking to internal Hive/Ollama with custom TLS
        timeout=480,
    )

    if resp.status_code != 200:
        print(f"ERROR: Ollama returned {resp.status_code}", file=sys.stderr)
        print("Response text:", file=sys.stderr)
        print(resp.text, file=sys.stderr)
        resp.raise_for_status()

    return resp.json().get("response", "")


# ---------- Confluence helpers ----------

session = requests.Session()
session.auth = (CONF_USER, CONF_PASS)


def get_page_by_title(title: str, parent_id: str) -> Optional[str]:
    """
    Try to find an existing page with the given title under the given parent.
    Not perfect for huge spaces, but fine for a small lesson tree.
    """
    url = f"{CONFLUENCE_BASE}/rest/api/content"
    params = {
        "title": title,
        "expand": "ancestors",
        "type": "page",
    }
    r = session.get(url, params=params, headers={"Accept": "application/json"}, verify=False)
    r.raise_for_status()

    for page in r.json().get("results", []):
        ancestors = page.get("ancestors", [])
        if any(str(a["id"]) == str(parent_id) for a in ancestors):
            return page["id"]
    return None


def create_or_update_page(title: str, parent_id: str, storage_value: str) -> str:
    existing_id = get_page_by_title(title, parent_id)
    headers = {"Content-Type": "application/json"}

    if existing_id:
        # Get current version
        url_get = f"{CONFLUENCE_BASE}/rest/api/content/{existing_id}"
        r = session.get(
            url_get,
            params={"expand": "version"},
            headers={"Accept": "application/json"},
            verify=False,
        )
        r.raise_for_status()
        current_version = r.json()["version"]["number"]

        payload = {
            "id": existing_id,
            "type": "page",
            "title": title,
            "version": {"number": current_version + 1},
            "ancestors": [{"id": parent_id}],
            "body": {
                "storage": {
                    "value": storage_value,
                    "representation": "storage",
                }
            },
        }
        url_put = f"{CONFLUENCE_BASE}/rest/api/content/{existing_id}"
        resp = session.put(url_put, json=payload, headers=headers, verify=False)
        resp.raise_for_status()
        print(f"Updated page: {title} (id={existing_id})")
        return existing_id
    else:
        payload = {
            "type": "page",
            "title": title,
            "ancestors": [{"id": parent_id}],
            "body": {
                "storage": {
                    "value": storage_value,
                    "representation": "storage",
                }
            },
        }
        url_post = f"{CONFLUENCE_BASE}/rest/api/content"
        resp = session.post(url_post, json=payload, headers=headers, verify=False)
        resp.raise_for_status()
        page_id = resp.json()["id"]
        print(f"Created page: {title} (id={page_id})")
        return page_id


# ---------- Prompt + page generation ----------

def build_prompt_from_row(row: Dict[str, str]) -> str:
    """
    Build the LLM prompt from a normalized row dict.

    Expected keys in `row` (mapped below):
      - Module
      - Module Name
      - Section
      - Section Title
      - Sub-lessons
      - Examples
      - Confluence Link
      - YouTube Link
    """
    return textwrap.dedent(f"""
    You are helping build an internal American Football curriculum for EA Gameplay engineers.

    Generate a Confluence lesson page for this topic. Use Confluence storage/markup-style headings and lists.
    Do NOT include backticks or code fences.

    Module: {row["Module"]}
    Module Name: {row["Module Name"]}
    Section: {row["Section"]}
    Section Title: {row["Section Title"]}
    Sub-lessons: {row["Sub-lessons"]}
    Examples: {row.get("Examples", "")}
    Confluence Reference: {row.get("Confluence Link", "")}
    YouTube Reference: {row.get("YouTube Link", "")}

    Page structure:
    - h1: "<Module>.<Section> – <Section Title>"
    - h2: Learning Objectives (3–5 bullets)
    - h2: Key Concepts (expand each sub-lesson as h3 with explanation)
    - h2: Teaching Flow (bulleted steps using the links if provided)
    - h2: Example Scenarios (2–3 football situations)
    - h2: Quiz (5 questions)

    Again, output ONLY the Confluence markup.
    """)


def main():
    if not os.path.exists(GOOGLE_SA_JSON):
        print(f"ERROR: Google SA JSON not found at {GOOGLE_SA_JSON}", file=sys.stderr)
        sys.exit(1)

    sheets = build_sheets()
    values = get_values_by_gid(sheets)
    if not values:
        print("No values found in sheet.")
        return

    header = values[0]
    rows = values[1:]

    # Normalize header names (collapse whitespace)
    # Expecting something like:
    #   Module | Module Name | Section | Section Title | Sub-lessons | Examples | External Links | Zoom Links
    def normalize(name: str) -> str:
        return re.sub(r"\s+", " ", name).strip()

    header = [normalize(h) for h in header]

    for row_vals in rows:
        if not any(cell.strip() for cell in row_vals):
            continue  # skip completely blank rows

        row_dict = {
            header[i]: row_vals[i].strip() if i < len(row_vals) and isinstance(row_vals[i], str) else (row_vals[i] if i < len(row_vals) else "")
            for i in range(len(header))
        }

        # Map to friendly keys; adapt the source names if your sheet headers differ
        mapped = {
            "Module": row_dict.get("Module", ""),
            "Module Name": row_dict.get("Module Name", ""),
            "Section": row_dict.get("Section", ""),
            "Section Title": row_dict.get("Section Title", ""),
            "Sub-lessons": row_dict.get("Sub-lessons", ""),
            "Examples": row_dict.get("Examples", ""),
            # Assuming:
            #   External Links = Confluence page URL
            #   Zoom Links     = YouTube or session URL
            "Confluence Link": row_dict.get("External Links", ""),
            "YouTube Link": row_dict.get("Zoom Links", ""),
        }

        if not mapped["Section"] or not mapped["Section Title"]:
            continue  # skip rows without key info

        # Example title: "1.1 – Personnel Identification"
        title = f'{mapped["Module"]}.{mapped["Section"]} – {mapped["Section Title"]}'.strip()

        print(f"\n=== Generating lesson for: {title} ===")
        prompt = build_prompt_from_row(mapped)

        messages = [
            {
                "role": "system",
                "content": "You are generating Confluence lesson pages for an internal American Football curriculum for EA Gameplay engineers."
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]

        content = call_llm_with_messages(messages)
        create_or_update_page(title, PARENT_PAGE_ID, content)


if __name__ == "__main__":
    main()
