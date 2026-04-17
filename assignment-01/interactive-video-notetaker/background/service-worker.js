/**
 * Interactive Video Notetaker — background service worker (MV3)
 */

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});

async function getActiveTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0];
}

async function sendToTab(tabId, message) {
  try {
    return await chrome.tabs.sendMessage(tabId, message);
  } catch {
    return null;
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "GET_VIDEO_STATE") {
    (async () => {
      const tab = await getActiveTab();
      if (!tab?.id) {
        sendResponse({ ok: false, reason: "no_active_tab" });
        return;
      }
      const state = await sendToTab(tab.id, { type: "GET_VIDEO_STATE" });
      if (state && state.ok) {
        sendResponse({ ok: true, tabId: tab.id, ...state });
      } else {
        sendResponse({
          ok: false,
          reason: "no_video_or_script",
          tabId: tab.id,
          pageUrl: tab.url || "",
          title: tab.title || "",
        });
      }
    })();
    return true;
  }

  return false;
});
