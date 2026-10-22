# sc_mp3_proxy.py
# Public HLS -> mp3 transcoder for SaladMenu. The Lua client resolves the SoundCloud stream
# with its own client id and sends only the signed *.sndcdn.com playlist URL here; ffmpeg
# converts it to mp3 (Roblox only decodes mp3/ogg). No yt-dlp, no SoundCloud scraping.
import os
import subprocess
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "8787"))
MAX_TRACK_SECONDS = 900          # cut anything longer than 15 min
FFMPEG_TIMEOUT_S = 150
MAX_CONCURRENT_JOBS = 2
RATE_LIMIT_PER_MINUTE = 12       # per client IP
ALLOWED_SUFFIX = ".sndcdn.com"

jobs = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)
recent_hits = defaultdict(deque)
recent_lock = threading.Lock()


def client_ip(handler: BaseHTTPRequestHandler) -> str:
    # Render puts the real client in X-Forwarded-For.
    forwarded = handler.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() or handler.client_address[0]


def rate_limited(ip: str) -> bool:
    now = time.monotonic()
    with recent_lock:
        hits = recent_hits[ip]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= RATE_LIMIT_PER_MINUTE:
            return True
        hits.append(now)
        return False


def transcode(src: str) -> bytes:
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-protocol_whitelist", "https,tls,tcp,crypto",
            "-i", src, "-vn", "-t", str(MAX_TRACK_SECONDS),
            "-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3", "pipe:1",
        ],
        capture_output=True, timeout=FFMPEG_TIMEOUT_S,
    )
    if result.returncode != 0 or len(result.stdout) < 1000:
        raise RuntimeError(result.stderr.decode(errors="replace").strip()[-120:] or "ffmpeg failed")
    return result.stdout


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if parsed.path != "/mp3":
            self.send_error(404)
            return
        src = parse_qs(parsed.query).get("src", [""])[0]
        url = urlparse(src)
        if url.scheme != "https" or not (url.hostname or "").endswith(ALLOWED_SUFFIX):
            self.send_error(400, "only https *.sndcdn.com playlists")
            return
        if rate_limited(client_ip(self)):
            self.send_error(429, "slow down")
            return
        if not jobs.acquire(timeout=20):
            self.send_error(503, "busy")
            return
        try:
            data = transcode(src)
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            self.send_error(502, str(error).replace("\n", " ")[:120])
            return
        finally:
            jobs.release()
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    print(f"listening on 0.0.0.0:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
