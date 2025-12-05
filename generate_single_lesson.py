#!/usr/bin/env python3
import os
import textwrap
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OLLAMA_API_URL = os.environ.get("OLLAMA_API_URL", "https://thehive.tib.ad.ea.com/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "codellama:7b")


def call_ollama(prompt: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False
    }

    resp = requests.post(
        OLLAMA_API_URL,
        json=payload,
        verify=False,
        timeout=240
    )

    if resp.status_code != 200:
        print(f"ERROR: {resp.status_code}")
        print(resp.text)
        resp.raise_for_status()

    return resp.json().get("response", "").strip()


def build_prompt(row: dict) -> str:
    """Builds the prompt that generates a full Confluence lesson page."""
    return textwrap.dedent(f"""
    You are generating a Confluence lesson page for EA Gameplay Engineers.

    Use Confluence markup (h1, h2, bullet lists).
    Do NOT include backticks or code fences.
    Structure the output as follows:

    h1. {row["module"]}.{row["section"]} – {row["section_title"]}

    h2. Learning Objectives
    - 3–5 clear bullets

    h2. Key Concepts
    For each sub-lesson, create a subsection with a clear explanation.

    h2. Teaching Flow
    - Reference the Confluence resource: {row["conf_link"]}
    - Reference the YouTube video: {row["youtube_link"]}

    h2. Example Scenarios
    - 2–3 football teaching scenarios or film reads

    h2. Quiz
    - 5 comprehension questions

    =======
    Topic Metadata
    Module Name: {row["module_name"]}
    Section Title: {row["section_title"]}
    Sub-lessons: {row["sub_lessons"]}
    Examples: {row["examples"]}
    Confluence: {row["conf_link"]}
    YouTube: {row["youtube_link"]}
    =======
    """)


def main():
    row = {
        "module": "1",
        "module_name": "Football Basics / 101",
        "section": "1.1",
        "section_title": "Personnel Identification",
        "sub_lessons": "Offensive personnel groupings (11, 12, 21); Defensive personnel (Nickel, Dime, 3-3-5, 4-2-5); Roles & body types by position",
        "examples": "How to read personnel during the game; Why 11 Personnel dominates modern football",
        "conf_link": "https://confluence.ea.com/display/Football/American+Football+101",
        "youtube_link": "https://www.youtube.com/watch?v=EZrgXXHSaBQ"
    }

    prompt = build_prompt(row)
    result = call_ollama(prompt)

    print("\n================ GENERATED LESSON ================\n")
    print(result)
    print("\n=================================================\n")


if __name__ == "__main__":
    main()
