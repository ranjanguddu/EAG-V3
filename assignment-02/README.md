# Assignment 02 — Hindi Helper (Chrome + Gemini 2.0 Flash)

Select text on any page:
- **One word** → Hindi meaning.
- **Multiple words/lines** → Hindi translation.

This is the demo of our plugin, refer: https://youtu.be/PqQLQoMkosU

## 1. Test the API key

```bash
cd test-api
pip install requests python-dotenv
# put GEMINI_API_KEY=... in .env
python3 test_api.py
```

If you see an English reply, the key works.

## 2. Install the extension

1. Open `chrome://extensions` → enable **Developer mode**.
2. **Load unpacked** → select `hindi-helper/`.
3. **Details → Extension options** → paste your Gemini API key → **Save**.

## 3. Use

Select text on any page → click the popup button.
