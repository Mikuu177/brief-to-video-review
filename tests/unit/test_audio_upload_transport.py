import base64
import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

from app.services.storage import external_audio_url
from app.settings import settings


def test_audio_upload_source_binding_and_md5_without_environment_proxy(tmp_path, monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_PUT(self):
            content = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.client_address[0], content, self.headers["Content-MD5"]))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    signed = []

    class Signer:
        def pre_signed_url(self, method, bucket, key, **kwargs):
            signed.append((method.value, bucket, key, kwargs))
            return SimpleNamespace(signed_url=f"http://127.0.0.1:{server.server_port}/object")

    monkeypatch.setattr("app.services.storage.tos_client", lambda: Signer())
    monkeypatch.setattr(settings, "tos_local_address", "127.0.0.1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    path = tmp_path / "complete.wav"
    content = b"complete audio test fixture"
    path.write_bytes(content)
    try:
        assert external_audio_url(path).endswith("/object")
        assert received == [("127.0.0.1", content, base64.b64encode(hashlib.md5(content).digest()).decode())]
        assert [s[0] for s in signed] == ["PUT", "GET"]
        assert signed[0][2] == f"creator-review/audio/{hashlib.sha256(content).hexdigest()}.wav"
        assert signed[0][3]["header"]["Content-Length"] == str(len(content))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
