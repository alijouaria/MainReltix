"""One-click start for the Reltix demo: the app, the phone tunnel and the phone link.

Double-click "Start Reltix.bat" (Windows) or run `python3 launch.py`. It
1. starts the app (main.py),
2. starts a cloudflared tunnel so a phone can open the app over HTTPS (needed for the microphone),
3. finds the public link in cloudflared's output, copies the phone link to the clipboard,
4. opens the staff dashboard, where "Phone" shows a QR code to scan.
Close the window (or press Ctrl+C) to stop everything.
Extra arguments go to main.py, e.g. `launch.py --demo-date 2026-12-01`.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8765
LOCAL = f"http://localhost:{PORT}"
TUNNEL_FILE = ROOT / "data" / "tunnel.json"
LOG_FILE = ROOT / "data" / "cloudflared.log"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def find_cloudflared() -> str | None:
    found = shutil.which("cloudflared")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
        r"C:\Program Files\cloudflared\cloudflared.exe",
        os.path.join(local, "Microsoft", "WinGet", "Links", "cloudflared.exe"),
        *glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "Cloudflare.cloudflared*", "cloudflared*.exe")),
        *glob.glob(os.path.join(os.path.expanduser("~"), "Downloads", "cloudflared*.exe")),
        "/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared",
    ]
    return next((c for c in candidates if c and os.path.isfile(c)), None)


def responds(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as res:
            return res.status < 500
    except Exception:
        return False


def this_version() -> str:
    import hashlib
    digest = hashlib.sha1()
    for name in ("main.py", "web/flow.js", "web/app.js"):
        try:
            digest.update((ROOT / name).read_bytes())
        except OSError:
            pass
    return digest.hexdigest()[:12]


def running_version() -> str | None:
    try:
        with urllib.request.urlopen(LOCAL + "/api/version", timeout=2) as res:
            return json.load(res).get("version")
    except Exception:
        return None   # very old copies don't have /api/version


def stop_running() -> None:
    try:
        req = urllib.request.Request(LOCAL + "/api/shutdown", data=b"{}", headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass
    for _ in range(40):
        if not responds(LOCAL + "/api/portfolio", timeout=0.5):
            return
        time.sleep(0.25)
    print("Couldn't stop it. Close the other Reltix window (or VS Code terminal running main.py), then start again.")
    sys.exit(1)


def copy_to_clipboard(text: str) -> bool:
    try:
        if sys.platform == "win32":
            subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True)
        elif sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
        else:
            return False
        return True
    except Exception:
        return False


def banner(lines: list[str]) -> None:
    width = max(len(line) for line in lines) + 4
    print("\n" + "=" * width)
    for line in lines:
        print("  " + line)
    print("=" * width + "\n", flush=True)


def main() -> int:
    if sys.platform == "win32":
        os.system("")  # lets the Windows console show the banner properly
    TUNNEL_FILE.unlink(missing_ok=True)
    procs: list[subprocess.Popen] = []

    if responds(LOCAL + "/api/portfolio") and running_version() == this_version():
        print("Reltix (this version) is already running on this laptop. Using that one.\n")
    else:
        if responds(LOCAL + "/api/portfolio"):
            # An older copy (another folder or an earlier download) is still running: stop it, or the
            # browser and phone would keep talking to the old code.
            print("An older Reltix is still running. Stopping it...", flush=True)
            stop_running()
        print("Starting Reltix...", flush=True)
        procs.append(subprocess.Popen([sys.executable, str(ROOT / "main.py"), *sys.argv[1:]], cwd=ROOT))
        for _ in range(60):
            if responds(LOCAL + "/api/portfolio"):
                break
            if procs[0].poll() is not None:
                print("The app stopped while starting. Scroll up for the error.")
                return 1
            time.sleep(0.25)

    cloudflared = find_cloudflared()
    phone_url = None
    if not cloudflared:
        print("cloudflared isn't installed, so there's no phone link. The laptop works as usual.")
        print("To get one: run `winget install Cloudflare.cloudflared` once, then start again.\n")
    else:
        print("Opening the phone link (cloudflared)...", flush=True)
        log = open(LOG_FILE, "w", encoding="utf-8")
        tunnel = subprocess.Popen([cloudflared, "tunnel", "--url", LOCAL], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", errors="replace", cwd=ROOT)
        procs.append(tunnel)
        found = threading.Event()
        public = {}

        def read_output() -> None:
            for line in tunnel.stdout:  # keep reading so cloudflared never blocks; everything goes to the log
                log.write(line)
                log.flush()
                match = URL_RE.search(line)
                if match and not found.is_set():
                    public["url"] = match.group(0)
                    found.set()

        threading.Thread(target=read_output, daemon=True).start()
        if not found.wait(45):
            print(f"No link from cloudflared after 45 s. Details: {LOG_FILE}")
        else:
            phone_url = public["url"]
            print("Waiting until the link works...", flush=True)
            for _ in range(30):  # a fresh quick tunnel takes a few seconds before it answers
                if responds(phone_url + "/api/portfolio", timeout=4):
                    break
                time.sleep(1)
            TUNNEL_FILE.write_text(json.dumps({"url": phone_url, "started": time.time()}), encoding="utf-8")

            def heartbeat() -> None:
                # The app only trusts a link that's refreshed every few seconds, so a link from an old run is never shown.
                while tunnel.poll() is None:
                    try:
                        os.utime(TUNNEL_FILE, None)
                    except OSError:
                        pass
                    time.sleep(5)

            threading.Thread(target=heartbeat, daemon=True).start()

    tenant_link = f"{phone_url}/tenant?unit=unit_1a" if phone_url else None
    copied = copy_to_clipboard(tenant_link) if tenant_link else False
    banner([
        "Reltix is running",
        "",
        f"Laptop (staff):  {LOCAL}/reltix",
        f"Phone:           {tenant_link}" if tenant_link else "Phone:           no link (see above)",
        ("                 (copied to the clipboard)" if copied else ""),
        "Easiest: on the dashboard click  [Phone]  and scan the QR code.",
        "",
        "Keep this window open. Close it or press Ctrl+C to stop.",
    ])
    webbrowser.open(LOCAL + "/reltix")

    try:
        while all(p.poll() is None for p in procs) or not procs:
            time.sleep(1)
        print("Something stopped (app or tunnel). Closing the rest.")
    except KeyboardInterrupt:
        print("\nStopping Reltix...")
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(5)
            except Exception:
                p.kill()
        TUNNEL_FILE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
