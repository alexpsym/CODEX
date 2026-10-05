# Local Trading Tools Exit button setup

The red **Exit local tools** control can stop the local worker and close Local Trading Tools tabs in one browser profile. Ordinary page JavaScript cannot close tabs opened manually, so this feature uses the small unpacked Chromium extension in:

`C:\GPT\CODEX-master\tools\browser_extensions\local_tools_exit`

The extension is limited to the local tool server at `http://localhost:8000` and `http://127.0.0.1:8000`. It checks the actual protocol, host and port, and confirms the local worker profile before requesting shutdown. Tabs on other ports, remote/lookalike sites, and tabs that navigate away are retained. The API shutdown must return its accepted response before the extension removes any tabs.

## One-time setup in the existing browser profile

1. Start Chrome or Microsoft Edge using the profile where the Local Trading Tools tabs are opened.
2. Open the browser's extensions page (`chrome://extensions` in Chrome or `edge://extensions` in Edge).
3. Turn on that page's **Developer mode** control, choose **Load unpacked**, and select the exact folder above.
4. Reload any already-open Local Trading Tools pages at either supported address. The red control becomes enabled after it confirms the extension and local worker are available.

This extension controls tabs only in the profile where it is loaded. Install it separately in another profile only if that profile should be controlled. It does not control other browsers or profiles, and Codex does not install it into a live profile.

If the control reports that the helper is unavailable, enable/reload the extension and reload the local page. After updating the repository files, use **Reload** on the extension card and reload the Local Trading Tools pages. The launcher remains unchanged and does not open a browser automatically.

## Later manual acceptance

Start Local Trading Tools normally and confirm its usual ready notification appears without opening a browser. In the supported existing browser profile, open local tool tabs in two windows and leave an unrelated tab in one window; keep a separate unrelated terminal open. Click the red Exit control once from a non-dashboard local tool. The local tabs and owned worker terminal should close, while the unrelated tab and terminal remain. The service should not restart. This check is not performed by this repository change.
