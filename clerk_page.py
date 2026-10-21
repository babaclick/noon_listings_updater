"""Clerk SignIn page, the same component Pforge mounts."""

from __future__ import annotations

import base64
import json

from settings import clerk_publishable_key, public_origin


def frontend_api_host(publishable_key: str) -> str:
    raw = publishable_key.strip()
    for prefix in ("pk_test_", "pk_live_"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    padded = raw + "=" * ((4 - len(raw) % 4) % 4)
    decoded = base64.b64decode(padded).decode("utf-8")
    host = decoded.rstrip("$").strip()
    if not host or "/" in host or " " in host:
        raise ValueError("CLERK_PUBLISHABLE_KEY is not a Clerk publishable key.")
    return host


def sign_in_html(publishable_key: str) -> str:
    host = frontend_api_host(publishable_key)
    key_js = json.dumps(publishable_key)
    app_url = json.dumps(public_origin())
    script = f"https://{host}/npm/@clerk/clerk-js@5/dist/clerk.browser.js"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Sign in</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      background: #f6f7f9;
      font-family: "Source Sans 3", "Segoe UI", sans-serif;
      color: #1f2430;
    }}
    main {{ width: min(420px, calc(100vw - 32px)); }}
    h1 {{ font-size: 1.6rem; margin: 0 0 0.25rem; text-align: center; }}
    p {{ margin: 0 0 1rem; text-align: center; color: #4b5565; }}
  </style>
</head>
<body>
  <main>
    <h1>Noon listing update</h1>
    <p>Sign in with your Babaclick Google account</p>
    <div id="sign-in"></div>
  </main>
  <script
    src="{script}"
    data-clerk-publishable-key={key_js}
    crossorigin="anonymous"
  ></script>
  <script>
    const appUrl = {app_url};
    const publishableKey = {key_js};
    window.addEventListener("load", async function () {{
      if (!window.Clerk) {{
        document.getElementById("sign-in").textContent = "Clerk failed to load.";
        return;
      }}
      await Clerk.load({{ publishableKey: publishableKey }});
      const params = new URLSearchParams(window.location.search);
      if (params.get("sign_out") === "1") {{
        await Clerk.signOut();
        window.location.replace(appUrl + "/?clerk_signed_out=1");
        return;
      }}
      if (Clerk.user) {{
        const token = await Clerk.session.getToken();
        const response = await fetch("/sign-in/session", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          credentials: "same-origin",
          body: JSON.stringify({{ token: token }})
        }});
        if (!response.ok) {{
          document.getElementById("sign-in").textContent = "Sign-in could not be saved.";
          return;
        }}
        window.location.replace(appUrl + "/");
        return;
      }}
      Clerk.mountSignIn(document.getElementById("sign-in"), {{
        forceRedirectUrl: window.location.origin + "/sign-in",
        appearance: {{
          elements: {{
            footerAction: {{ display: "none" }},
            headerSubtitle: {{ display: "none" }}
          }}
        }}
      }});
    }});
  </script>
</body>
</html>
"""
