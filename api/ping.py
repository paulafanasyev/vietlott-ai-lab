"""Kiểm tra tối thiểu: hàm Python trên Vercel có chạy không (không dùng thư viện ngoài)."""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        root = Path(__file__).resolve().parents[1]
        body = json.dumps({
            "ok": True,
            "python": sys.version.split()[0],
            "root_files": sorted(p.name for p in root.iterdir())[:40],
            "db_env": [k for k in ("DATABASE_URL_UNPOOLED", "POSTGRES_URL_NON_POOLING", "DATABASE_URL",
                                   "POSTGRES_URL") if os.environ.get(k)],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
