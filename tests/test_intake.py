import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI, File, UploadFile
from fastapi.testclient import TestClient
from starlette.formparsers import MultiPartParser
import starlette.formparsers

from resource_pipeline.api import create_app
from resource_pipeline.intake import MULTIPART_OVERHEAD_BYTES, UploadBodyLimitMiddleware
from resource_pipeline.security import ArchiveLimits


def multipart(parts):
    body = b""
    for field, content in parts:
        body += (b"--bounded\r\nContent-Disposition: form-data; name=\"" + field.encode()
                 + b"\"; filename=\"input.zip\"\r\nContent-Type: application/zip\r\n\r\n"
                 + content + b"\r\n")
    return body + b"--bounded--\r\n"


def parser_app(maximum):
    app = FastAPI()
    app.state.called = 0

    @app.post("/api/jobs")
    @app.post("/api/raw-jobs")
    async def upload(file: UploadFile = File()):
        app.state.called += 1
        return {"size": file.size}

    app.add_middleware(UploadBodyLimitMiddleware, archive_bytes=maximum, multipart_overhead_bytes=0)
    return app


async def request(app, body, headers=(), path="/api/jobs", end="complete", chunk_bytes=64, http_version="1.1"):
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": http_version,
             "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": b"", "root_path": "", "client": ("127.0.0.1", 1234), "server": ("testserver", 80),
             "headers": [(b"host", b"testserver"), (b"content-type", b"multipart/form-data; boundary=bounded"), *headers]}
    position = received = 0
    sent = []

    async def receive():
        nonlocal position, received
        received += 1
        if position < len(body):
            chunk = body[position:position + chunk_bytes]
            position += len(chunk)
            return {"type": "http.request", "body": chunk,
                    "more_body": position < len(body) or end != "complete"}
        if end == "cancel":
            raise asyncio.CancelledError()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent, received, position


def response(sent):
    start = next(message for message in sent if message["type"] == "http.response.start")
    content = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return start, json.loads(content) if content else None


class UploadBodyLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversized_header_rejects_without_reading_or_parsing(self):
        app = parser_app(400)
        for length in (b"401", b"9" * 5000, b"00000401"):
            with self.subTest(length=length[:20]), patch("starlette.formparsers.SpooledTemporaryFile") as spool:
                sent, received, _ = await request(app, b"unread", headers=[(b"content-length", length)])
                start, _ = response(sent)
                self.assertEqual(413, start["status"])
                self.assertIn((b"connection", b"close"), start["headers"])
                self.assertEqual(0, received)
                spool.assert_not_called()
        self.assertEqual(0, app.state.called)

    async def test_actual_count_rejects_missing_or_untrusted_header_and_closes_spool(self):
        body = multipart([("file", b"a" * 1000)])
        for path in ("/api/jobs", "/api/raw-jobs"):
            for headers in ((), ((b"content-length", b"1"),), ((b"content-length", b"invalid"),)):
                app = parser_app(400)
                opened = []
                original = starlette.formparsers.SpooledTemporaryFile

                def spool(*args, **kwargs):
                    file = original(*args, **kwargs)
                    opened.append(file)
                    return file

                with self.subTest(path=path, headers=headers), patch.object(MultiPartParser, "spool_max_size", 32), \
                        patch("starlette.formparsers.SpooledTemporaryFile", side_effect=spool):
                    sent, _, consumed = await request(app, body, headers=headers, path=path)
                self.assertEqual(413, response(sent)[0]["status"])
                self.assertEqual(0, app.state.called)
                self.assertLess(consumed, len(body))
                self.assertTrue(opened)
                self.assertTrue(all(file.closed for file in opened))
                self.assertTrue(any(file._rolled for file in opened), "Test must exercise actual temporary disk spooling")

    async def test_unexpected_file_parts_count_toward_total(self):
        body = multipart([("file", b"small valid selected part"), ("ignored_extra", b"b" * 1000)])
        app = parser_app(500)
        sent, _, consumed = await request(app, body)
        self.assertEqual(413, response(sent)[0]["status"])
        self.assertEqual(0, app.state.called)
        self.assertLess(consumed, len(body))

    async def test_exact_budget_accepts_and_overhead_is_separate(self):
        body = multipart([("file", b"abc")])
        app = parser_app(len(body))
        sent, _, _ = await request(app, body)
        self.assertEqual(200, response(sent)[0]["status"])
        self.assertEqual({"size": 3}, response(sent)[1])
        guard = UploadBodyLimitMiddleware(app, archive_bytes=3, multipart_overhead_bytes=len(body) - 3)
        self.assertEqual(len(body), guard.maximum)
        sent, _, _ = await request(guard, body)
        self.assertEqual(200, response(sent)[0]["status"])

    async def test_disconnect_and_cancellation_close_partial_spool(self):
        partial = multipart([("file", b"a" * 300)])[:-20]
        for end in ("disconnect", "cancel"):
            opened = []
            original = starlette.formparsers.SpooledTemporaryFile

            def spool(*args, **kwargs):
                file = original(*args, **kwargs)
                opened.append(file)
                return file

            app = parser_app(2000)
            with self.subTest(end=end), patch.object(MultiPartParser, "spool_max_size", 32), \
                    patch("starlette.formparsers.SpooledTemporaryFile", side_effect=spool):
                if end == "cancel":
                    with self.assertRaises(asyncio.CancelledError):
                        await request(app, partial, end=end)
                else:
                    sent, _, _ = await request(app, partial, end=end)
                    self.assertNotEqual(200, response(sent)[0]["status"])
            self.assertEqual(0, app.state.called)
            self.assertTrue(opened)
            self.assertTrue(all(file.closed for file in opened))

    async def test_http2_rejections_do_not_send_connection_header(self):
        app = parser_app(1)
        sent, _, _ = await request(app, b"xx", headers=[(b"content-length", b"2")], http_version="2")
        self.assertEqual(413, response(sent)[0]["status"])
        self.assertNotIn(b"connection", dict(response(sent)[0]["headers"]))

    async def test_real_api_stream_overflow_keeps_413_and_closes_partial_files(self):
        opened = []
        original = starlette.formparsers.SpooledTemporaryFile

        def spool(*args, **kwargs):
            file = original(*args, **kwargs)
            opened.append(file)
            return file

        body = multipart([("file", b"a" * (MULTIPART_OVERHEAD_BYTES + 1000))])
        with tempfile.TemporaryDirectory() as root, \
                patch("resource_pipeline.api.ArchiveLimits", return_value=ArchiveLimits(archive_bytes=64)), \
                patch.object(MultiPartParser, "spool_max_size", 32), \
                patch("starlette.formparsers.SpooledTemporaryFile", side_effect=spool):
            app = create_app(Path(root), synchronous=True)
            for path in ("/api/jobs", "/api/raw-jobs"):
                sent, _, _ = await request(app, body, path=path, chunk_bytes=64 * 1024)
                start, _ = response(sent)
                self.assertEqual(413, start["status"])
                self.assertIn((b"x-content-type-options", b"nosniff"), start["headers"])
            self.assertEqual([], app.state.pipeline.list())
        self.assertTrue(opened)
        self.assertTrue(all(file.closed for file in opened))
        self.assertTrue(any(file._rolled for file in opened))


class UploadGuardIntegrationTests(unittest.TestCase):
    def test_real_api_rejects_entire_multipart_body_before_job_creation(self):
        with tempfile.TemporaryDirectory() as root, \
                patch("resource_pipeline.api.ArchiveLimits", return_value=ArchiveLimits(archive_bytes=64)), \
                TestClient(create_app(Path(root), synchronous=True)) as client:
            for path in ("/api/jobs", "/api/raw-jobs"):
                result = client.post(path, files={"ignored_extra": ("extra.zip", b"x" * MULTIPART_OVERHEAD_BYTES)})
                self.assertEqual(413, result.status_code)
                self.assertEqual("nosniff", result.headers["X-Content-Type-Options"])
            self.assertEqual([], client.get("/api/jobs").json())
            self.assertEqual(403, client.post("/api/jobs", content=b"x", headers={"Origin": "https://evil.invalid"}).status_code)
            self.assertEqual(400, client.get("/api/current", headers={"Host": "evil.invalid"}).status_code)


if __name__ == "__main__":
    unittest.main()
