/**
 * Reads video state for supported pages (YouTube, Coursera, generic video).
 */
(function () {
  function getVideoState() {
    const videos = Array.from(document.querySelectorAll("video"));
    let best = null;
    for (const v of videos) {
      const r = v.getBoundingClientRect();
      const area = Math.max(0, r.width) * Math.max(0, r.height);
      if (!best || area > best.area) best = { el: v, area };
    }
    const v = best?.el || videos[0] || null;
    if (!v || Number.isNaN(v.currentTime)) {
      return {
        ok: false,
        reason: "no_video_element",
        href: location.href,
        title: document.title,
      };
    }
    return {
      ok: true,
      currentTime: v.currentTime,
      duration: v.duration,
      paused: v.paused,
      href: location.href,
      title: document.title,
    };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.type === "GET_VIDEO_STATE") {
      sendResponse(getVideoState());
      return true;
    }
    return false;
  });
})();
