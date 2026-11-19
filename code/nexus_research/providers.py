"""Narrow, text-only API and CLI adapters. No shell interpolation or model tools."""

import json
import os
import selectors
import shutil
import signal
import ssl
import subprocess
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import certifi

from .transport import CancelScope

KINDS = {"codex", "claude", "openai", "anthropic", "local"}


class ProviderError(ValueError):
    def __init__(self, message, retryable=False, partial=None):
        super().__init__(message)
        self.retryable = retryable
        self.partial = partial


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_endpoint(text):
    url = urlsplit(text)
    if (
        url.scheme != "http"
        or url.hostname not in {"127.0.0.1", "localhost", "::1"}
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError(
            "Local endpoints must use HTTP on localhost, without credentials or query text."
        )
    if url.path.rstrip("/") != "/v1":
        raise ValueError(
            "Use the local server's /v1 base URL, for example http://127.0.0.1:8080/v1."
        )
    return text.rstrip("/")


def cli_search_path():
    return os.pathsep.join(
        [
            os.environ.get("PATH", ""),
            str(Path.home() / ".local/bin"),
            str(Path.home() / ".npm-global/bin"),
            "/opt/homebrew/bin",
            "/usr/local/bin",
        ]
    )


def cli_path(kind):
    return shutil.which(kind, path=cli_search_path())


def availability(connection):
    if connection["kind"] in {"codex", "claude"}:
        return (
            "Installed · login checked on run" if cli_path(connection["kind"]) else "Not installed"
        )
    if connection["kind"] == "local":
        return "Configured · server checked on run"
    return "Key saved · access checked on run" if connection.get("has_key") else "Needs API key"


def request_json(url, body, headers, timeout=60, cancel=None):
    request = Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            **headers,
        },
    )
    try:
        tls = ssl.create_default_context(cafile=certifi.where())
        with CancelScope(cancel, tls) as scope:
            with build_opener(NoRedirect, *scope.handlers).open(
                request, timeout=timeout
            ) as response:
                data = response.read(2_000_001)
                if len(data) > 2_000_000:
                    raise ProviderError("Provider response exceeded the local size limit.")
                return json.loads(data)
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise ProviderError(
                "Provider rejected this connection. Check its login, key and model access."
            ) from None
        raise ProviderError(
            f"Provider returned HTTP {exc.code}. No automatic retry was charged.",
            exc.code in {429, 500, 502, 503, 504},
        ) from None
    except (URLError, TimeoutError, OSError, HTTPException):
        raise ProviderError(
            "Provider connection failed or timed out. Usage may be unreported."
        ) from None
    except (json.JSONDecodeError, UnicodeError):
        raise ProviderError("Provider returned an unreadable response.") from None


def input_count(connection, prompt, key, timeout, cancel=None):
    kind, model = connection["kind"], connection["model"]
    if kind == "openai":
        response = request_json(
            "https://api.openai.com/v1/responses/input_tokens",
            {
                "model": model,
                "input": prompt,
            },
            {"Authorization": f"Bearer {key}"},
            timeout,
            cancel,
        )
    elif kind == "anthropic":
        response = request_json(
            "https://api.anthropic.com/v1/messages/count_tokens",
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
            },
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout,
            cancel,
        )
    else:
        return len(prompt.encode()) + 2048, "estimated"
    count = response.get("input_tokens")
    if not isinstance(count, int) or count < 0:
        raise ProviderError(
            "Provider did not return an input token count; no generation was started."
        )
    return count, "provider_count"


def api_call(connection, prompt, output_limit, key, timeout, cancel=None):
    kind, model = connection["kind"], connection["model"]
    if kind == "openai":
        data = request_json(
            "https://api.openai.com/v1/responses",
            {
                "model": model,
                "input": prompt,
                "max_output_tokens": output_limit,
                "store": False,
            },
            {"Authorization": f"Bearer {key}"},
            timeout,
            cancel,
        )
        text = "\n".join(
            part.get("text", "")
            for item in data.get("output", [])
            if item.get("type") == "message"
            for part in item.get("content", [])
            if part.get("type") == "output_text"
        )
        usage = data.get("usage") or {}
        used = {
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
            "cached": (usage.get("input_tokens_details") or {}).get("cached_tokens", 0),
            "reasoning": (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
        }
    elif kind == "anthropic":
        data = request_json(
            "https://api.anthropic.com/v1/messages",
            {
                "model": model,
                "max_tokens": output_limit,
                "messages": [{"role": "user", "content": prompt}],
            },
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout,
            cancel,
        )
        text = "\n".join(
            x.get("text", "") for x in data.get("content", []) if x.get("type") == "text"
        )
        usage = data.get("usage") or {}
        cached = usage.get("cache_read_input_tokens", 0)
        written = usage.get("cache_creation_input_tokens", 0)
        used = {
            "input": usage.get("input_tokens", 0) + cached + written,
            "output": usage.get("output_tokens"),
            "cached": cached,
            "reasoning": None,
        }
    else:
        data = request_json(
            local_endpoint(connection["endpoint"]) + "/chat/completions",
            {
                "model": model,
                "max_tokens": output_limit,
                "stream": False,
                "messages": [{"role": "user", "content": prompt}],
            },
            {},
            timeout,
            cancel,
        )
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        usage = data.get("usage") or {}
        used = {
            "input": usage.get("prompt_tokens"),
            "output": usage.get("completion_tokens"),
            "cached": 0,
            "reasoning": None,
        }
    if not isinstance(text, str):
        raise ProviderError("Provider did not return visible text.")
    return {"text": text, "usage": used, "model": data.get("model", model)}


def cli_command(connection):
    kind = connection["kind"]
    executable = cli_path(kind)
    if not executable:
        raise ProviderError(f"Install and sign in to {kind} outside Nexus first.")
    model = connection["model"]
    if kind == "codex":
        args = [
            executable,
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "-c",
            "features.shell_tool=false",
            "-c",
            "features.apps=false",
            "-c",
            "features.skills=false",
            "-c",
            "features.memories=false",
            "-c",
            "features.multi_agent=false",
            "-c",
            "features.code_mode=false",
            "-c",
            "mcp_servers={}",
            "-c",
            'web_search="disabled"',
            "-c",
            "tools.view_image=false",
            "-c",
            "project_doc_max_bytes=0",
        ]
        if model != "default":
            args += ["--model", model]
        return args + ["-"]
    args = [
        executable,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--tools",
        "",
        "--disallowedTools",
        "mcp__*",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--safe-mode",
        "--restricted",
        "--setting-sources",
        "",
        "--no-session-persistence",
        "--disable-slash-commands",
    ]
    if model != "default":
        args += ["--model", model]
    return args


def parse_cli_line(kind, data):
    if kind == "codex":
        item = data.get("item") or {}
        if data.get("type") == "item.completed" and item.get("type") == "agent_message":
            return {"text": item.get("text", "")}
        if data.get("type") == "turn.completed":
            usage = data.get("usage") or {}
            return {
                "usage": {
                    "input": usage.get("input_tokens"),
                    "output": usage.get("output_tokens"),
                    "cached": usage.get("cached_input_tokens", 0),
                    "reasoning": usage.get("reasoning_output_tokens"),
                }
            }
        if data.get("type") in {"turn.failed", "error"}:
            raise ProviderError(
                "Codex could not complete the request. Check its login and model access."
            )
    elif data.get("type") == "result":
        if data.get("is_error"):
            raise ProviderError(
                "Claude Code could not complete the request. Check its login and model access."
            )
        usage = data.get("usage") or {}
        cached = usage.get("cache_read_input_tokens", 0)
        return {
            "text": data.get("result", ""),
            "usage": {
                "input": usage.get("input_tokens", 0)
                + cached
                + usage.get("cache_creation_input_tokens", 0),
                "output": usage.get("output_tokens"),
                "cached": cached,
                "reasoning": None,
            },
            "model": next(iter(data.get("modelUsage", {})), None),
        }
    return {}


def cli_call(connection, prompt, cancel, timeout, directory):
    # Only existing CLI authentication is reused. Research never runs in the user's repo or vault.
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"HOME", "PATH", "TMPDIR", "USER", "LOGNAME", "LANG", "SHELL"}
    }
    env["PATH"] = cli_search_path()
    process = subprocess.Popen(
        cli_command(connection),
        cwd=directory,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    result, raw, started = {"text": "", "usage": {}, "model": None}, b"", time.monotonic()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        process.stdin.write(prompt.encode())
        process.stdin.close()
        total = 0
        while True:
            if cancel.is_set() or time.monotonic() - started >= timeout:
                raise ProviderError(
                    "CLI request stopped. In-flight provider usage may be unreported."
                )
            events = selector.select(0.15)
            for stream, _ in events:
                block = os.read(stream.fileobj.fileno(), 65536)
                if not block:
                    selector.unregister(stream.fileobj)
                    break
                total += len(block)
                if total > 2_000_000:
                    raise ProviderError("CLI response exceeded the local size limit.")
                raw += block
                while b"\n" in raw:
                    line, raw = raw.split(b"\n", 1)
                    try:
                        parsed = parse_cli_line(connection["kind"], json.loads(line))
                    except json.JSONDecodeError:
                        continue
                    if "text" in parsed:
                        result["text"] += parsed.pop("text")
                    result.update({k: v for k, v in parsed.items() if v is not None})
            if not selector.get_map():
                break
        process.wait(timeout=2)
        if process.returncode or not result["text"]:
            raise ProviderError(
                "CLI returned no result. Check its login, model and installed version."
            )
        return result
    except ProviderError as exc:
        if result["text"]:
            exc.partial = dict(result)
        raise
    finally:
        selector.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        process.stdout.close()
        if not process.stdin.closed:
            process.stdin.close()
