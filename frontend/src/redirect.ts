// The script of redirect.html, the page Microsoft sends the browser back to
// after sign-in. MSAL 5 needs such a page for every sign-in flow. It reads the
// answer in its address and hands it to the app: for a full-page sign-in it
// returns the browser to the page the user started from.
//
// It must stay this small. It is not the app and must not load it, and Caddy
// must never serve it with a Cross-Origin-Opener-Policy header.
import { broadcastResponseToMainFrame } from "@azure/msal-browser/redirect-bridge";

broadcastResponseToMainFrame().catch(() => {
  // Opened by hand, or with an answer that cannot be read. The app then shows
  // its sign-in page. Only a top-level page moves on: inside a hidden frame
  // there is nobody to show anything to.
  if (window.self === window.top) {
    window.location.replace("/");
  }
});
