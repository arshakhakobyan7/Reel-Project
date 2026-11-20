"""Loopback-only YouTube protocol simulator; no real media, tokens or API calls."""

import copy
import json
import re
import socket
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class YoutubeFake:
    def __init__(self):
        self.calls = []
        self.failures = defaultdict(list)
        self.transfer_faults = []
        self.sessions = {}
        self.videos = {}
        self.order = []
        self.entries = {}
        self.playlists = {}
        self.upload_status = {"uploadStatus": "processed", "privacyStatus": "unlisted"}
        self.page_size = 50
        self.location_override = ""
        self.session_serial = self.video_serial = self.entry_serial = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        )

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def seed(self, tags, *, title="Existing", video_id=None):
        self.video_serial += 1
        video_id = video_id or f"video_{self.video_serial}"
        self.videos[video_id] = {
            "id": video_id,
            "snippet": {"title": title, "tags": tags, "categoryId": "27"},
            "status": copy.deepcopy(self.upload_status),
        }
        self.order.insert(0, video_id)
        return video_id

    def count(self, method, path):
        return sum(c["method"] == method and c["path"] == path for c in self.calls)

    def handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return None

            def reply(self, code=200, payload=None, headers=None):
                data = (
                    json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    if payload is not None
                    else b""
                )
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Type", "application/json; charset=utf-8")
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                try:
                    self.wfile.write(data)
                except BrokenPipeError, ConnectionResetError:
                    return None

            def error(self, code, reason="forbidden", message="fake failure"):
                self.reply(code, {"error": {"message": message, "errors": [{"reason": reason}]}})

            def cut(self):
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    return None
                finally:
                    self.connection.close()

            def dispatch(self):
                parsed = urlsplit(self.path)
                path, query = parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items()}
                call = {
                    "method": self.command,
                    "path": path,
                    "query": query,
                    "headers": dict(self.headers),
                }
                fake.calls.append(call)
                length = int(self.headers.get("Content-Length", 0))
                if path.startswith("/session/") and self.command == "PUT":
                    return self.session(path, length, call)
                raw = self.rfile.read(length)
                call["raw"] = raw
                body = (
                    json.loads(raw)
                    if raw and self.headers.get_content_type() == "application/json"
                    else None
                )
                call["body"] = body
                failures = fake.failures[(self.command, path)]
                if failures:
                    code, reason, message, headers = failures.pop(0)
                    if code in {302, 307, 308}:
                        return self.reply(code, headers=headers)
                    return self.error(code, reason, message)
                if path == "/api/channels" and self.command == "GET":
                    return self.reply(
                        payload={
                            "items": [
                                {"contentDetails": {"relatedPlaylists": {"uploads": "UU_UPLOADS"}}}
                            ]
                        }
                    )
                if path == "/api/videos" and self.command == "GET":
                    ids = query["id"].split(",")
                    return self.reply(
                        payload={"items": [fake.videos[v] for v in ids if v in fake.videos]}
                    )
                if path == "/api/videos" and self.command == "PUT":
                    if body["id"] not in fake.videos:
                        return self.error(404, "videoNotFound")
                    assert "categoryId" in body["snippet"]
                    fake.videos[body["id"]]["snippet"] = body["snippet"]
                    return self.reply(payload=fake.videos[body["id"]])
                if path == "/api/videos" and self.command == "DELETE":
                    if query["id"] not in fake.videos:
                        return self.error(404, "videoNotFound")
                    del fake.videos[query["id"]]
                    return self.reply(204)
                if path == "/upload/videos" and self.command == "POST":
                    assert query["uploadType"] == "resumable"
                    fake.session_serial += 1
                    key = f"/session/{fake.session_serial}"
                    fake.sessions[key] = {
                        "body": body,
                        "total": int(self.headers["X-Upload-Content-Length"]),
                        "data": bytearray(),
                        "video_id": None,
                    }
                    location = (
                        fake.location_override
                        or f"{fake.url}{key}?upload_id=session-secret-{fake.session_serial}"
                    )
                    return self.reply(headers={"Location": location})
                if path == "/upload/thumbnails/set" and self.command == "POST":
                    assert raw
                    return self.reply(payload={"items": [{}]})
                if path == "/api/playlists" and self.command == "GET":
                    values = [
                        {"id": key, "snippet": value["snippet"]}
                        for key, value in fake.playlists.items()
                    ]
                    return self.page(values, query)
                if path == "/api/playlists" and self.command == "POST":
                    key = f"PL_{len(fake.playlists) + 1}"
                    fake.playlists[key] = body
                    return self.reply(payload={"id": key})
                if path == "/api/playlistItems" and self.command == "GET":
                    if query["playlistId"] == "UU_UPLOADS":
                        values = [
                            {"id": "upload_" + key, "contentDetails": {"videoId": key}}
                            for key in fake.order
                            if key in fake.videos
                        ]
                    else:
                        values = [
                            {"id": key}
                            for key, value in fake.entries.items()
                            if value == (query["playlistId"], query.get("videoId"))
                        ]
                    return self.page(values, query)
                if path == "/api/playlistItems" and self.command == "POST":
                    fake.entry_serial += 1
                    key = f"entry_{fake.entry_serial}"
                    fake.entries[key] = (
                        body["snippet"]["playlistId"],
                        body["snippet"]["resourceId"]["videoId"],
                    )
                    return self.reply(payload={"id": key})
                if path == "/api/playlistItems" and self.command == "DELETE":
                    if query["id"] not in fake.entries:
                        return self.error(404, "playlistItemNotFound")
                    del fake.entries[query["id"]]
                    return self.reply(204)
                return self.error(404, "notFound")

            def page(self, values, query):
                start = int(query.get("pageToken", 0))
                end = start + fake.page_size
                body = {"items": values[start:end]}
                if end < len(values):
                    body["nextPageToken"] = str(end)
                return self.reply(payload=body)

            def session(self, path, length, call):
                state = fake.sessions.get(path)
                if state is None:
                    self.rfile.read(length)
                    return self.error(404, "uploadNotFound")
                content_range = self.headers["Content-Range"]
                call["range"] = content_range
                if content_range.startswith("bytes */"):
                    assert length == 0
                    if state["video_id"]:
                        return self.reply(201, fake.videos[state["video_id"]])
                    headers = (
                        {"Range": f"bytes=0-{len(state['data']) - 1}"} if state["data"] else {}
                    )
                    return self.reply(308, headers=headers)
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                assert match and int(match[3]) == state["total"]
                start, end = int(match[1]), int(match[2])
                assert start == len(state["data"]) and end - start + 1 == length
                fault = fake.transfer_faults[0] if fake.transfer_faults else ""
                if fault in {"lost_final", "expired_complete"} and end + 1 != state["total"]:
                    fault = ""
                if fault:
                    fake.transfer_faults.pop(0)
                if fault in {"cut", "503"}:
                    received = self.rfile.read(max(1, length // 2))
                    state["data"].extend(received)
                    call["received"] = len(received)
                    return self.cut() if fault == "cut" else self.error(503, "backendError")
                raw = self.rfile.read(length)
                call["received"] = len(raw)
                if fault in {"404", "410"}:
                    del fake.sessions[path]
                    return self.error(int(fault), "uploadNotFound")
                if fault == "bad_range":
                    return self.reply(308, headers={"Range": "bytes=0-9999999999"})
                if fault == "stall":
                    return self.reply(308)
                state["data"].extend(raw)
                if len(state["data"]) == state["total"]:
                    video = fake.seed(state["body"]["snippet"]["tags"])
                    fake.videos[video]["snippet"] = state["body"]["snippet"]
                    state["video_id"] = video
                    if fault == "lost_final":
                        return self.cut()
                    if fault == "expired_complete":
                        del fake.sessions[path]
                        return self.error(410, "uploadNotFound")
                    return self.reply(201, fake.videos[video])
                return self.reply(308, headers={"Range": f"bytes=0-{len(state['data']) - 1}"})

            do_GET = dispatch
            do_POST = dispatch
            do_PUT = dispatch
            do_DELETE = dispatch

        return Handler
