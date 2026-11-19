"""Interrupt provider sockets when an operation stops; no model-controlled network access."""

import http.client
import socket
import threading
from contextlib import suppress
from urllib.request import HTTPHandler, HTTPSHandler


class CancelScope:
    def __init__(self, cancel, tls):
        self.cancel = cancel
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.sockets = []
        self.thread = None
        scope = self

        def register(connection):
            connection.sock.settimeout(connection.read_timeout)
            with scope.lock:
                scope.sockets.append(connection.sock)
                if cancel.is_set():
                    scope._interrupt()
                    raise OSError("Request stopped.")

        class HTTP(http.client.HTTPConnection):
            def __init__(self, *args, **kwargs):
                self.read_timeout = kwargs.get("timeout", 60)
                kwargs["timeout"] = min(10, self.read_timeout)
                super().__init__(*args, **kwargs)

            def connect(self):
                super().connect()
                register(self)

        class HTTPS(http.client.HTTPSConnection):
            def __init__(self, *args, **kwargs):
                self.read_timeout = kwargs.get("timeout", 60)
                kwargs["timeout"] = min(10, self.read_timeout)
                super().__init__(*args, **kwargs)

            def connect(self):
                super().connect()
                register(self)

        class HTTPRequests(HTTPHandler):
            def http_open(self, request):
                return self.do_open(HTTP, request)

        class HTTPSRequests(HTTPSHandler):
            def https_open(self, request):
                return self.do_open(
                    HTTPS, request, context=self._context, check_hostname=self._check_hostname
                )

        self.handlers = (
            [HTTPSHandler(context=tls)]
            if cancel is None
            else [HTTPRequests(), HTTPSRequests(context=tls)]
        )

    def _interrupt(self):
        for stream in self.sockets:
            with suppress(OSError):
                stream.shutdown(socket.SHUT_RDWR)
            with suppress(OSError):
                stream.close()

    def __enter__(self):
        if self.cancel is not None:
            if self.cancel.is_set():
                raise OSError("Request stopped.")

            def watch():
                while not self.done.wait(0.05):
                    if self.cancel.is_set():
                        with self.lock:
                            self._interrupt()
                        return

            self.thread = threading.Thread(target=watch, daemon=True)
            self.thread.start()
        return self

    def __exit__(self, *_):
        self.done.set()
        if self.thread:
            self.thread.join(timeout=0.2)
