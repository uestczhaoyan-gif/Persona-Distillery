"""Explicit loopback-only Ollama transport. No proxies, redirects or API keys."""
from __future__ import annotations

import ipaddress
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class ProviderError(ValueError):
    pass


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError("Local model endpoint redirected; request refused")


class OllamaProvider:
    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434",
                 timeout: float = 60, num_ctx: int = 32768, max_output_tokens: int = 1600):
        parsed = urlsplit(base_url)
        host = parsed.hostname
        try:
            loopback = host == "localhost" or ipaddress.ip_address(host or "").is_loopback
            port = parsed.port
        except ValueError:
            loopback = False
            port = None
        if (parsed.scheme != "http" or not loopback or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ProviderError("Ollama base_url must be an HTTP loopback origin")
        if not model or "cloud" in model.casefold() or "/" in model or "\\" in model:
            raise ProviderError("Choose an installed local model; cloud models are disabled")
        if not 1 <= timeout <= 60 or not 1024 <= num_ctx <= 262144 or not 1 <= max_output_tokens <= 16000:
            raise ProviderError("Invalid timeout or model capacity limits")
        # Pin localhost to an IP, avoiding DNS/proxy surprises.
        pinned = "127.0.0.1" if host == "localhost" else host
        if ":" in pinned:
            pinned = f"[{pinned}]"
        self.base_url = f"http://{pinned}" + (f":{port}" if port else "")
        self.model, self.timeout = model, timeout
        self.num_ctx, self.max_output_tokens = num_ctx, max_output_tokens
        self.opener = build_opener(ProxyHandler({}), NoRedirects())
        self.supports_tools = False
        self.ready = False

    def post(self, path: str, data: dict) -> dict:
        request = Request(self.base_url + path, data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                payload = response.read(4 * 1024 * 1024 + 1)
            if len(payload) > 4 * 1024 * 1024:
                raise ProviderError("Local model response exceeds size limit")
            value = json.loads(payload)
        except ProviderError:
            raise
        except HTTPError as error:
            raise ProviderError(f"Ollama HTTP {error.code}; check model installation and API compatibility") from None
        except (URLError, TimeoutError, socket.timeout, OSError):
            raise ProviderError("Cannot reach local Ollama within timeout; check service and model") from None
        except (ValueError, UnicodeError):
            raise ProviderError("Local model returned invalid JSON") from None
        if not isinstance(value, dict):
            raise ProviderError("Local model response must be an object")
        if value.get("remote_model") or value.get("remote_host"):
            raise ProviderError("Remote/cloud-backed model refused")
        if value.get("error"):
            raise ProviderError("Local model reported an error; check local service logs")
        return value

    def prepare(self) -> None:
        if not self.ready:
            info = self.post("/api/show", {"model": self.model})
            self.supports_tools = "tools" in info.get("capabilities", [])
            self.ready = True

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        self.prepare()
        request = {"model": self.model, "messages": messages, "stream": False,
                   "options": {"num_ctx": self.num_ctx, "num_predict": self.max_output_tokens}}
        if tools and self.supports_tools:
            request["tools"] = tools
        value = self.post("/api/chat", request)
        message = value.get("message")
        if (value.get("done") is not True or not isinstance(message, dict)
                or message.get("role") != "assistant" or not isinstance(message.get("content", ""), str)):
            raise ProviderError("Local model returned an incomplete or invalid assistant message")
        return message
