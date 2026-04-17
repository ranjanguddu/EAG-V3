# Interactive Video Notetaker

Chrome extension (Manifest V3): take notes beside YouTube / Coursera videos with **timestamp links** and **screen captures** from the visible tab.

## How to install (development)

1. Open Chrome and go to `chrome://extensions`.
2. Turn **Developer mode** **on** (top right).
3. Click **Load unpacked**.
4. Choose this folder: `interactive-video-notetaker` (the folder that contains `manifest.json`).

## How to use (simple)

1. Open a **video** on **YouTube** (or Coursera on the supported domains).
2. Click the **extension icon** in the toolbar — the **side panel** opens on the right.
3. Type your notes in the panel. After you **pause typing** for about a second, it can **save a screenshot** of the video area (use **theater mode** for a bigger picture).
4. Click **Insert timestamp link** to paste a link that jumps to **that moment** in the video.
5. Click **Capture frame now** anytime to grab the current frame without waiting.
6. Under **Snapshots & links**, click **Jump to mm:ss** to reopen the video at that time.
7. Click **Export JSON** to download your notes and images for backup or other tools.

**Tip:** Keep the **video tab** as the selected tab in the window while you note — the extension reads time and capture from that tab.

## Privacy

- Notes and snapshots are stored **locally** in your browser (`chrome.storage.local`) until you export or clear.
- **Export JSON** includes image data URLs (large). Only share exports you intend to.

## Limits

- Works on pages listed in `manifest.json` **host_permissions** (YouTube + Coursera paths). Other sites can be added later.
- Screenshot is **what you see** in the tab (`captureVisibleTab`), not a raw video frame API.

## Capture not working?

- **Capture frame now** must run from the **side panel** (fixed in v1.0.1). Reload the extension after updating.
- The **video tab** must be the **selected tab** in that window (click the tab once), on **https** (e.g. `youtube.com`), not `chrome://` pages.
- **Auto-capture after typing** is off by default: Chrome often **blocks** tab capture when there is no fresh user click (timer ≠ gesture). Use **Capture frame now** or turn auto on knowing it may fail.

After code changes: open `chrome://extensions` → your extension → **Reload**.

## v1.0.2 capture fix

Tab capture now uses **`chrome.windows.getCurrent`** (same window as the side panel) instead of `tabs.query({ lastFocusedWindow })`, which could pick the wrong window and make `captureVisibleTab` fail. YouTube matches include **all** `https://*.youtube.com/*` and **youtu.be**. If capture still fails, the status line shows **Chrome’s real error message**.
