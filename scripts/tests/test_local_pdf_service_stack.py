from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import unittest

from scripts.local_pdf_service_stack import _wait


class HtmlOnlyHandler(BaseHTTPRequestHandler):
    observed_accept: list[str] = []

    def do_GET(self) -> None:
        accept = self.headers.get("Accept", "")
        self.observed_accept.append(accept)
        self.send_response(200 if "text/html" in accept else 406)
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


class LocalPdfServiceReadinessTests(unittest.TestCase):
    def test_vite_readiness_requests_html(self) -> None:
        HtmlOnlyHandler.observed_accept.clear()
        server = ThreadingHTTPServer(("127.0.0.1", 0), HtmlOnlyHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _wait(f"http://127.0.0.1:{server.server_port}/", [], "Vite viewer", timeout=0.5, accept="text/html")
            self.assertTrue(HtmlOnlyHandler.observed_accept)
            self.assertIn("text/html", HtmlOnlyHandler.observed_accept[-1])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
