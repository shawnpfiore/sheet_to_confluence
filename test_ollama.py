#!/usr/bin/env python3
import os
import requests
import urllib3

# disable the HTTPS warning since you're using verify=False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OLLAMA_API_URL = os.environ.get("OLLAMA_API_URL", "https://ollama.tib.ad.ea.com/api/generate") #need to update using for MCP
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")

def call_ollama(prompt: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    print(f"\nDEBUG: Sending to {OLLAMA_API_URL}")
    print(f"DEBUG: Payload: {payload}\n")

    resp = requests.post(OLLAMA_API_URL, json=payload, verify=False, timeout=120)

    if resp.status_code != 200:
        print(f"ERROR: Status {resp.status_code}")
        print("Response text:")
        print(resp.text)
        resp.raise_for_status()

    data = resp.json()
    return data.get("response", "")

if __name__ == "__main__":
    while True:
        try:
            text = input("You: ")
        except EOFError:
            break
        if not text.strip():
            break
        print("\nModel:\n")
        print(call_ollama(text))
        print()
