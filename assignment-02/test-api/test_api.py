import os, sys, requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.environ.get("MODEL", "gemini-2.0-flash")

url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
body = {"contents": [{"parts": [{"text": "How are you?"}]}]}
r = requests.post(url, headers={"x-goog-api-key": API_KEY}, json=body, timeout=30)

if r.status_code != 200:
    sys.exit(f"{r.status_code}: {r.text}")

print(r.json()["candidates"][0]["content"]["parts"][0]["text"])
