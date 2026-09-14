#!/usr/bin/env python3
"""Local Grok Studio server.

Cloudflare blocks curl/python and browsers block cross-origin fetches from file://.
This process keeps a real Chromium page on one worker thread and relays API calls.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import queue
import sys
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.abspath(__file__))
HTML_FILE = os.path.join(ROOT, "grok-studio.html")
HOST = "127.0.0.1"
PORT = 8787
ALLOWED_HOSTS = {
    "token.dialoguedui.com",
    "cn.dialoguedui.com",
    "img-api.dialoguedui.com",
}
JOB_TIMEOUT = 180


class BrowserRelay:
    def __init__(self) -> None:
        self.jobs: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.failed: str | None = None
        self.thread = threading.Thread(target=self._run, name="playwright-relay", daemon=True)
        self.thread.start()
        if not self.ready.wait(timeout=30):
            raise RuntimeError(self.failed or "playwright worker failed to start")
        if self.failed:
            raise RuntimeError(self.failed)

    def close(self) -> None:
        self.jobs.put(None)
        self.thread.join(timeout=8)

    def request(self, url: str, method: str, headers: dict, body: str | None) -> dict:
        done = threading.Event()
        slot: dict = {}
        self.jobs.put(("fetch", url, method, headers, body, done, slot))
        if not done.wait(timeout=JOB_TIMEOUT):
            raise TimeoutError("proxy timed out waiting for Chromium")
        if "error" in slot:
            raise RuntimeError(slot["error"])
        return slot["result"]

    def _run(self) -> None:
        play = None
        browser = None
        pages: dict[str, object] = {}
        try:
            play = sync_playwright().start()
            browser = play.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            self.ready.set()
            while True:
                item = self.jobs.get()
                if item is None:
                    break
                kind, url, method, headers, body, done, slot = item
                try:
                    page = self._page(browser, pages, url)
                    slot["result"] = self._fetch(page, url, method, headers, body)
                except Exception as exc:
                    slot["error"] = f"{type(exc).__name__}: {exc}"
                    traceback.print_exc()
                finally:
                    done.set()
        except Exception as exc:
            self.failed = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
            self.ready.set()
        finally:
            try:
                if browser is not None:
                    browser.close()
            except Exception:
                pass
            try:
                if play is not None:
                    play.stop()
            except Exception:
                pass

    def _page(self, browser, pages: dict[str, object], url: str):
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        page = pages.get(origin)
        if page is not None:
            return page
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        )
        page = context.new_page()
        page.set_default_timeout(180000)
        warmup = origin + "/v1/models"
        try:
            page.goto(warmup, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            page.goto(origin + "/", wait_until="domcontentloaded", timeout=30000)
        pages[origin] = page
        return page

    def _fetch(self, page, url: str, method: str, headers: dict, body: str | None) -> dict:
        return page.evaluate(
            """async ({url, method, headers, body}) => {
              const init = { method, headers: headers || {} };
              if (body != null && body !== '') init.body = body;
              const res = await fetch(url, init);
              const buf = await res.arrayBuffer();
              const u8 = new Uint8Array(buf);
              let binary = '';
              const chunk = 0x8000;
              for (let i = 0; i < u8.length; i += chunk) {
                binary += String.fromCharCode.apply(null, u8.subarray(i, i + chunk));
              }
              const outHeaders = {};
              res.headers.forEach((value, key) => { outHeaders[key] = value; });
              return {
                status: res.status,
                headers: outHeaders,
                bodyB64: btoa(binary)
              };
            }""",
            {
                "url": url,
                "method": method or "GET",
                "headers": headers or {},
                "body": body,
            },
        )


RELAY: BrowserRelay | None = None


def allowed_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.hostname in ALLOWED_HOSTS


class Handler(BaseHTTPRequestHandler):
    server_version = "GrokStudio/1.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stdout.write("[studio] " + (fmt % args) + "\n")
        sys.stdout.flush()

    def _send(self, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8", extra=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._send(204, b"", extra={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        })

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in {"/__grok_health", "/health"}:
            payload = json.dumps({"ok": True, "via": "playwright", "port": PORT}).encode("utf-8")
            self._send(200, payload, "application/json; charset=utf-8")
            return
        if path in {"/", "/index.html", "/grok-studio.html"}:
            with open(HTML_FILE, "rb") as handle:
                self._send(200, handle.read(), "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            return
        local = os.path.normpath(os.path.join(ROOT, path.lstrip("/")))
        if local.startswith(ROOT) and os.path.isfile(local):
            mime = mimetypes.guess_type(local)[0] or "application/octet-stream"
            with open(local, "rb") as handle:
                self._send(200, handle.read(), mime)
            return
        self._send(404, b"not found")

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path != "/__grok_proxy":
            self._send(404, b"not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, json.dumps({"error": "invalid json"}).encode("utf-8"), "application/json")
            return
        url = str(payload.get("url") or "")
        if not allowed_url(url):
            self._send(400, json.dumps({"error": "endpoint not allowed"}).encode("utf-8"), "application/json")
            return
        method = str(payload.get("method") or "GET").upper()
        headers = payload.get("headers") or {}
        if not isinstance(headers, dict):
            headers = {}
        clean_headers = {}
        for key, value in headers.items():
            if str(key).lower() in {"host", "content-length", "connection"}:
                continue
            clean_headers[str(key)] = str(value)
        body = payload.get("body")
        if body is not None and not isinstance(body, str):
            body = json.dumps(body, ensure_ascii=False)
        try:
            result = RELAY.request(url, method, clean_headers, body)
        except Exception as exc:
            traceback.print_exc()
            msg = json.dumps({"error": f"proxy failed: {exc}"}).encode("utf-8")
            self._send(502, msg, "application/json")
            return
        upstream_body = base64.b64decode(result.get("bodyB64") or "")
        status = int(result.get("status") or 502)
        upstream_headers = result.get("headers") or {}
        content_type = upstream_headers.get("content-type") or upstream_headers.get("Content-Type") or "application/octet-stream"
        extra = {}
        for key in ("content-disposition", "x-request-id"):
            if key in upstream_headers:
                extra[key] = upstream_headers[key]
        self._send(status, upstream_body, content_type, extra)


def main() -> None:
    global RELAY
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not os.path.isfile(HTML_FILE):
        raise SystemExit(f"missing {HTML_FILE}")
    RELAY = BrowserRelay()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Grok Studio  http://{HOST}:{PORT}")
    print("Keep this window open. Open the URL above in your browser.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()
        if RELAY is not None:
            RELAY.close()


if __name__ == "__main__":
    main()
