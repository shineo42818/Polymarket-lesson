#!/usr/bin/env python3
"""
Local server for mock_trader.html.
Serves the HTML file AND proxies Gamma API calls so the browser
never hits gamma-api.polymarket.com directly (avoids CORS errors).

Usage:
    python server.py
Then open:
    http://localhost:8080/mock_trader.html
"""

import json
import urllib.request
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT      = 8080
GAMMA_API = "https://gamma-api.polymarket.com/markets"


class Handler(SimpleHTTPRequestHandler):

    def do_GET(self):
        # Proxy requests for /api/markets?slug=... to Gamma API
        if self.path.startswith("/api/markets"):
            self._proxy_gamma()
        else:
            super().do_GET()   # serve all other files normally

    def _proxy_gamma(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        slug   = params.get("slug", [""])[0]

        url = f"{GAMMA_API}?slug={urllib.parse.quote(slug)}"
        print(f"  [Proxy] Fetching {url}")
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = resp.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
            print(f"  [Proxy] OK — {len(data)} bytes")
        except Exception as e:
            print(f"  [Proxy] ERROR: {e}")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def log_message(self, fmt, *args):
        # Only print proxy calls above; suppress the normal per-request noise
        pass


if __name__ == "__main__":
    print("=" * 55)
    print("  Polymarket Mock Trader — Local Server")
    print(f"  Open in browser: http://localhost:{PORT}/mock_trader.html")
    print("  Press Ctrl+C to stop")
    print("=" * 55)
    server = HTTPServer(("", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
