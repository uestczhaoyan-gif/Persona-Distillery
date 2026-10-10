"""Explicit loopback-only Ollama transport. No proxies, redirects or API keys."""
from __future__ import annotations

import ipaddress
import hashlib
import json
import math
import re
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
                 timeout: float = 60, num_ctx: int = 32768, max_output_tokens: int = 1600,
                 *, temperature: float | None = None, seed: int | None = None):
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
        if temperature is not None and (type(temperature) not in {int, float}
                or not math.isfinite(temperature) or not 0 <= temperature <= 2):
            raise ProviderError("temperature must be finite and between 0 and 2")
        if seed is not None and (type(seed) is not int or not 0 <= seed <= 2147483647):
            raise ProviderError("seed must be an integer between 0 and 2147483647")
        # Pin localhost to an IP, avoiding DNS/proxy surprises.
        pinned = "127.0.0.1" if host == "localhost" else host
        if ":" in pinned:
            pinned = f"[{pinned}]"
        self.base_url = f"http://{pinned}" + (f":{port}" if port else "")
        self.model, self.timeout = model, timeout
        self.num_ctx, self.max_output_tokens = num_ctx, max_output_tokens
        self.temperature, self.seed = temperature, seed
        self.metadata_sha256 = None
        self.last_metrics = {}
        self.opener = build_opener(ProxyHandler({}), NoRedirects())
        self.supports_tools = False
        self.ready = False

    def post(self, path: str, data: dict) -> dict:
        return self._request(path, data)

    def _request(self, path: str, data: dict | None = None) -> dict:
        request = Request(self.base_url + path,
                          data=None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="GET" if data is None else "POST")
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
            capabilities = info.get("capabilities", [])
            if not isinstance(capabilities, list) or any(not isinstance(x, str) for x in capabilities):
                raise ProviderError("Invalid model capabilities")
            self.supports_tools = "tools" in capabilities
            self.metadata_sha256 = hashlib.sha256(json.dumps(
                info, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            self.ready = True

    def options(self) -> dict:
        result = {"num_ctx": self.num_ctx, "num_predict": self.max_output_tokens}
        if self.temperature is not None:
            result["temperature"] = self.temperature
        if self.seed is not None:
            result["seed"] = self.seed
        return result

    def inspect_identity(self) -> dict:
        """Fresh metadata-only inspection for experiments; no private prompt or model download."""
        self.ready = False
        self.prepare()
        models = self._request("/api/tags").get("models")
        if not isinstance(models, list) or any(not isinstance(x, dict) for x in models):
            raise ProviderError("Invalid installed model inventory")
        names = {self.model}
        if ":" not in self.model:
            names.add(self.model + ":latest")
        matches = [x for x in models if isinstance(x.get("name"), str) and x["name"] in names]
        if len(matches) != 1:
            raise ProviderError("Model identity missing or ambiguous")
        match = matches[0]
        digest = match.get("digest")
        if (match.get("remote_host") or match.get("remote_model") or not isinstance(digest, str)
                or re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", digest) is None):
            raise ProviderError("Model digest missing, invalid or remote-backed")
        return {"model": match["name"], "digest": digest.removeprefix("sha256:"),
                "metadata_sha256": self.metadata_sha256, "supports_tools": self.supports_tools,
                "options": self.options()}

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        self.last_metrics = {}
        self.prepare()
        request = {"model": self.model, "messages": messages, "stream": False,
                   "options": self.options()}
        if tools and self.supports_tools:
            request["tools"] = tools
        value = self.post("/api/chat", request)
        message = value.get("message")
        if (value.get("done") is not True or not isinstance(message, dict)
                or message.get("role") != "assistant" or not isinstance(message.get("content", ""), str)):
            raise ProviderError("Local model returned an incomplete or invalid assistant message")
        self.last_metrics = {key: value[key] for key in (
            "total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration",
            "eval_count", "eval_duration") if type(value.get(key)) is int and value[key] >= 0}
        if isinstance(value.get("done_reason"), str) and value["done_reason"] in {"stop", "length", "load", "unload"}:
            self.last_metrics["done_reason"] = value["done_reason"]
        return message
