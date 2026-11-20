"""Loopback Classroom simulator, including immutable attachments and deleted-post races."""

import copy
import json
import socket
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit


class ClassroomFake:
    def __init__(self):
        self.posts, self.calls = {}, []
        self.failures = defaultdict(list)
        self.responses = defaultdict(list)
        self.serial, self.page_size = 0, 100
        self.cut_after_create = self.cut_after_delete = False
        self.delete_on_patch = None
        self.promote_youtube = False
        self.hide_created_url = False
        self.repeat_page_token = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/"
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

    def seed(self, resource, body, *, course="course-12"):
        self.serial += 1
        post_id = f"post_{self.serial}"
        item = {
            **copy.deepcopy(body),
            "id": post_id,
            "courseId": course,
            "alternateLink": f"https://classroom.google.com/c/{course}/p/{post_id}",
        }
        if self.promote_youtube:
            for material in item.get("materials", []):
                link = material.get("link", {}).get("url", "")
                if "youtube.com/watch?v=" in link:
                    material.clear()
                    material["youtubeVideo"] = {"id": link.split("v=", 1)[1]}
        self.posts[(resource, post_id)] = item
        return item

    def active(self):
        return [item for item in self.posts.values() if item.get("state") != "DELETED"]

    def count(self, method, resource=None):
        return sum(
            c["method"] == method and (resource is None or c["resource"] == resource)
            for c in self.calls
        )

    def handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return None

            def reply(self, status=200, body=None):
                raw = json.dumps({} if body is None else body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except BrokenPipeError, ConnectionResetError:
                    return None

            def error(self, status, reason="PERMISSION_DENIED", message="fake error"):
                self.reply(
                    status,
                    {
                        "error": {
                            "status": reason,
                            "message": message,
                            "errors": [{"reason": reason}],
                        }
                    },
                )

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
                segments = [unquote(s) for s in parsed.path.split("/") if s]
                assert segments[:2] == ["v1", "courses"] and len(segments) in {4, 5}
                course, resource = segments[2:4]
                post_id = segments[4] if len(segments) == 5 else None
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = json.loads(raw) if raw else None
                query = parse_qs(parsed.query)
                call = {
                    "method": self.command,
                    "resource": resource,
                    "course": course,
                    "post_id": post_id,
                    "body": body,
                    "raw": raw,
                    "query": query,
                    "headers": dict(self.headers),
                }
                fake.calls.append(call)
                errors = fake.failures[(self.command, resource)]
                if errors:
                    return self.error(*errors.pop(0))
                responses = fake.responses[(self.command, resource)]
                if responses:
                    return self.reply(body=responses.pop(0))
                key = (resource, post_id)
                if self.command == "POST":
                    assert body["state"] == "PUBLISHED" and body["materials"][0]["link"]["url"]
                    item = fake.seed(resource, body, course=course)
                    if fake.cut_after_create:
                        fake.cut_after_create = False
                        return self.cut()
                    response = copy.deepcopy(item)
                    if fake.hide_created_url:
                        response.pop("alternateLink")
                    return self.reply(body=response)
                if self.command == "GET" and post_id:
                    if key not in fake.posts:
                        return self.error(404, "NOT_FOUND")
                    return self.reply(body=fake.posts[key])
                if self.command == "GET":
                    states = query.get("announcementStates") or query.get(
                        "courseWorkMaterialStates"
                    )
                    assert states == ["PUBLISHED", "DRAFT"]
                    assert query["orderBy"] == ["updateTime desc"]
                    values = [
                        v
                        for (kind, _), v in reversed(list(fake.posts.items()))
                        if kind == resource and v["courseId"] == course and v["state"] in states
                    ]
                    start = int(query.get("pageToken", [0])[0])
                    end = start + fake.page_size
                    list_key = (
                        "announcements" if resource == "announcements" else "courseWorkMaterial"
                    )
                    result = {list_key: values[start:end]} if values else {}
                    if end < len(values):
                        result["nextPageToken"] = "0" if fake.repeat_page_token else str(end)
                    return self.reply(body=result)
                if self.command == "PATCH":
                    if fake.delete_on_patch:
                        status, fake.delete_on_patch = fake.delete_on_patch, None
                        if status == 400:
                            fake.posts[key]["state"] = "DELETED"
                        else:
                            fake.posts.pop(key, None)
                        return self.error(
                            status, "FAILED_PRECONDITION" if status == 400 else "NOT_FOUND"
                        )
                    if key not in fake.posts:
                        return self.error(404, "NOT_FOUND")
                    if fake.posts[key]["state"] == "DELETED":
                        return self.error(400, "FAILED_PRECONDITION")
                    allowed = (
                        {"text", "state"}
                        if resource == "announcements"
                        else {"title", "description", "state"}
                    )
                    fields = query["updateMask"][0].split(",")
                    assert set(fields) == set(body) and set(body).issubset(allowed)
                    fake.posts[key].update(body)
                    return self.reply(body=fake.posts[key])
                if self.command == "DELETE":
                    if key not in fake.posts:
                        return self.error(404, "NOT_FOUND")
                    if fake.posts[key]["state"] == "DELETED":
                        return self.error(400, "FAILED_PRECONDITION")
                    fake.posts[key]["state"] = "DELETED"
                    if fake.cut_after_delete:
                        fake.cut_after_delete = False
                        return self.cut()
                    return self.reply()
                return self.error(405)

            do_GET = dispatch
            do_POST = dispatch
            do_PATCH = dispatch
            do_DELETE = dispatch

        return Handler
