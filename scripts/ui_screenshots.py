"""Screenshots of every UI page with a local headless Chrome/Edge over the DevTools protocol (no extra installs).

    python scripts/ui_screenshots.py [--ui http://127.0.0.1:8501] [--out docs/screenshots] [--skip-llm]

Uses the synthetic demo profile only (a fresh browser profile, so no saved profile). Pages that call the LLM
(explain, paste, chat) are driven through the UI like a user would; --skip-llm leaves them at their empty state.
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from tornado.ioloop import IOLoop
from tornado.websocket import websocket_connect

BROWSERS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "google-chrome", "chromium", "msedge"]
PORT = 9333
SYNTHETIC_POST = """Sample Fintech Pvt Ltd (demo posting, not a real company) is hiring freshers.

Role 1: Backend Developer Intern
Duration: 6 months internship. Stipend: Rs 25,000 per month.
Skills: Python, SQL, REST APIs.
Location: Bengaluru (hybrid).

Role 2: Data Analyst Trainee
Full-time role. CTC: 6 LPA.
Skills: SQL, Excel, Power BI.
Location: Pune (on-site).

Eligibility for both roles: B.Tech / BE in CSE, IT or ECE, 2026 batch, minimum 7.0 CGPA.
Last date to apply: 31 December 2026.
"""


class CDP:
    def __init__(self, ws: Any) -> None:
        self.ws, self.n = ws, 0

    async def call(self, method: str, **params: Any) -> dict[str, Any]:
        self.n += 1
        mid = self.n
        await self.ws.write_message(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.read_message())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, expr: str) -> Any:
        r = await self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        return r.get("result", {}).get("value")

    async def wait_text(self, text: str, timeout: float = 60) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if await self.js(f"document.body && document.body.innerText.includes({json.dumps(text)})"):
                return True
            await self.sleep(0.5)
        return False

    async def sleep(self, s: float) -> None:
        import asyncio

        await asyncio.sleep(s)

    async def click_text(self, text: str, selector: str = "button") -> bool:
        return bool(await self.js(
            f"(() => {{ const b = [...document.querySelectorAll({json.dumps(selector)})]"
            f".find(e => e.innerText.trim().includes({json.dumps(text)})); if (b) {{ b.scrollIntoView(); b.click(); }}"
            f" return !!b; }})()"))

    async def type_into(self, selector: str, text: str) -> None:
        await self.js(f"document.querySelector({json.dumps(selector)}).focus()")
        await self.call("Input.insertText", text=text)

    async def shot(self, path: Path, width: int = 1400) -> None:
        h = await self.js("Math.max(...[...document.querySelectorAll('[data-testid=\"stMain\"], section.main, "
                          "[data-testid=\"stAppViewContainer\"]')].map(e => e.scrollHeight), 900)")
        h = min(int(h or 900) + 40, 9000)
        await self.call("Emulation.setDeviceMetricsOverride", width=width, height=h, deviceScaleFactor=1, mobile=False)
        await self.sleep(1.5)
        img = await self.call("Page.captureScreenshot", format="png")
        path.write_bytes(base64.b64decode(img["data"]))
        await self.call("Emulation.setDeviceMetricsOverride", width=width, height=1000, deviceScaleFactor=1,
                        mobile=False)
        print("saved", path, f"{width}x{h}")


async def run(ui: str, out: Path, skip_llm: bool, pages: set[str]) -> None:
    targets = httpx.get(f"http://127.0.0.1:{PORT}/json").json()
    page = next(t for t in targets if t["type"] == "page")
    cdp = CDP(await websocket_connect(page["webSocketDebuggerUrl"], max_message_size=200 * 1024 * 1024))
    await cdp.call("Page.enable")
    await cdp.call("Emulation.setDeviceMetricsOverride", width=1400, height=1000, deviceScaleFactor=1, mobile=False)

    async def open_page(path: str, marker: str, timeout: float = 60) -> None:
        await cdp.call("Page.navigate", url=f"{ui}/{path}")
        if not await cdp.wait_text(marker, timeout):
            print(f"WARNING: {path or 'matches'}: '{marker}' did not appear", file=sys.stderr)
        await cdp.sleep(2)

    if pages & {"matches", "job"}:
        await open_page("", "Fit score")
        if "matches" in pages:
            await cdp.shot(out / "matches.png")
    if "job" in pages:  # open the first match like a user, then (optionally) ask for the explanation
        await cdp.click_text("Details")
        await cdp.wait_text("Calls the LLM only when you click", 60)
        await cdp.sleep(2)
        if not skip_llm:
            await cdp.click_text("Explain this match")
            if not await cdp.wait_text("Grounding check", 120):
                print("WARNING: explanation did not appear", file=sys.stderr)
            await cdp.sleep(2)
        await cdp.shot(out / "job_detail.png")
    if "paste" in pages:
        await open_page("paste", "Posting text")
        if not skip_llm:
            await cdp.type_into("textarea", SYNTHETIC_POST)
            await cdp.type_into('input[aria-label="Company (optional)"]', "Sample Fintech (demo)")
            await cdp.sleep(1)
            await cdp.click_text("Extract")
            if not await cdp.wait_text("Extracted facts", 240):
                print("WARNING: paste result did not appear", file=sys.stderr)
            await cdp.sleep(3)
        await cdp.shot(out / "paste.png")
    if "profile" in pages:
        await open_page("profile", "Your details")
        await cdp.shot(out / "profile.png")
    if "chat" in pages:
        await open_page("chat", "Known limitations")
        if not skip_llm:
            await cdp.type_into("textarea", "Which internships in Bengaluru fit my profile?")
            for kind in ("keyDown", "keyUp"):
                await cdp.call("Input.dispatchKeyEvent", type=kind, key="Enter", code="Enter",
                               windowsVirtualKeyCode=13)
            if not await cdp.wait_text("Tool calls", 180):
                print("WARNING: chat answer did not appear", file=sys.stderr)
            await cdp.sleep(2)
        await cdp.shot(out / "chat.png")
    if "evals" in pages:
        await open_page("evals", "Label agreement")
        await cdp.shot(out / "evals.png")
    cdp.ws.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ui", default="http://127.0.0.1:8501")
    ap.add_argument("--out", default="docs/screenshots")
    ap.add_argument("--skip-llm", action="store_true")
    ap.add_argument("--pages", default="matches,job,paste,profile,chat,evals")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    exe = next((b for b in BROWSERS if Path(b).exists() or shutil.which(b)), None)
    if exe is None:
        raise SystemExit("No Chrome or Edge found")
    prof = tempfile.mkdtemp(prefix="joblens-shots-")
    proc = subprocess.Popen([exe, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={prof}",
                             "--no-first-run", "--hide-scrollbars", "--window-size=1400,1000", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                httpx.get(f"http://127.0.0.1:{PORT}/json/version", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.2)
        IOLoop.current().run_sync(lambda: run(args.ui.rstrip("/"), out, args.skip_llm,
                                                   set(args.pages.split(","))), timeout=900)
    finally:
        proc.terminate()
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == "__main__":
    main()
