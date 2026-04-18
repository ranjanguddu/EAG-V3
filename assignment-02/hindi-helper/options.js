const input = document.getElementById("apiKey");
const status = document.getElementById("status");

chrome.storage.local.get("apiKey", ({ apiKey }) => { if (apiKey) input.value = apiKey; });

document.getElementById("save").onclick = async () => {
  const apiKey = input.value.trim();
  if (!apiKey) { status.textContent = "Key required."; return; }
  await chrome.storage.local.set({ apiKey });
  status.textContent = "Saved.";
  setTimeout(() => status.textContent = "", 2000);
};
