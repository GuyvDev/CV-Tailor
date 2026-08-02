from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class ProviderQuotaError(RuntimeError):
    def __init__(self, provider: str, message: str) -> None:
        super().__init__(message)
        self.provider = provider


def is_quota_error(status_code: int | None, message: str) -> bool:
    lowered = message.lower()
    return status_code in {402, 429} or any(
        marker in lowered
        for marker in (
            "usage limit",
            "usage_limit",
            "quota",
            "insufficient credit",
            "insufficient_credit",
            "credit balance",
            "credits exhausted",
            "rate limit reached",
            "usagelimitexceeded",
        )
    )


class ProviderUsageStore:
    """Small local ledger for provider calls and observed quota failures."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _empty(self) -> dict[str, Any]:
        return {
            "abacus": {
                "requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "last_success_at": None,
                "quota_blocked_until": None,
                "last_quota_error": None,
            }
        }

    def read(self) -> dict[str, Any]:
        with self._lock:
            return self._read_unlocked()

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty()
        base = self._empty()
        if isinstance(data, dict) and isinstance(data.get("abacus"), dict):
            base["abacus"].update(data["abacus"])
        return base

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def record_abacus_success(self, payload: dict[str, Any]) -> None:
        usage = payload.get("usage") if isinstance(payload, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        with self._lock:
            data = self._read_unlocked()
            item = data["abacus"]
            item["requests"] = int(item.get("requests") or 0) + 1
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                item[field] = int(item.get(field) or 0) + int(usage.get(field) or 0)
            item["last_success_at"] = datetime.now(UTC).isoformat()
            item["quota_blocked_until"] = None
            item["last_quota_error"] = None
            self._write_unlocked(data)

    def record_abacus_quota_error(self, message: str, *, status_code: int | None) -> None:
        # 402 normally means depleted credits. A 429 can be a shorter rate window.
        cooldown = timedelta(hours=24) if status_code == 402 else timedelta(minutes=15)
        with self._lock:
            data = self._read_unlocked()
            item = data["abacus"]
            item["quota_blocked_until"] = (datetime.now(UTC) + cooldown).isoformat()
            item["last_quota_error"] = message[:500]
            self._write_unlocked(data)

    def abacus_quota_blocked(self) -> bool:
        raw = self.read()["abacus"].get("quota_blocked_until")
        if not raw:
            return False
        try:
            return datetime.fromisoformat(str(raw)) > datetime.now(UTC)
        except ValueError:
            return False


class CodexRunner:
    def __init__(
        self,
        *,
        model: str,
        reasoning_effort: str,
        timeout_seconds: int,
        work_dir: Path,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self.work_dir = work_dir
        self._status_lock = threading.Lock()
        self._status_cache: tuple[float, dict[str, Any]] | None = None

    def installed(self) -> bool:
        return shutil.which("codex") is not None

    def auth_file_exists(self) -> bool:
        codex_home = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex")))
        return (codex_home / "auth.json").exists()

    def _app_server_messages(self, methods: list[tuple[int, str, dict[str, Any]]]) -> dict[int, dict[str, Any]]:
        if not self.installed():
            raise RuntimeError("Codex CLI is not installed in the API container.")
        output: queue.Queue[str | None] = queue.Queue()
        process = subprocess.Popen(
            ["codex", "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        def read_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                output.put(line)
            output.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        responses: dict[int, dict[str, Any]] = {}
        transcript: list[str] = []
        deadline = time.monotonic() + min(self.timeout_seconds, 30)

        def send(message: dict[str, Any]) -> None:
            if process.stdin is None or process.poll() is not None:
                raise RuntimeError("Codex app-server exited before account status was available.")
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()

        def wait_for(required_ids: set[int]) -> None:
            while not required_ids.issubset(responses):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Timed out while reading Codex account status.")
                try:
                    line = output.get(timeout=remaining)
                except queue.Empty as exc:
                    raise RuntimeError("Timed out while reading Codex account status.") from exc
                if line is None:
                    detail = "".join(transcript)[-800:].strip()
                    raise RuntimeError(detail or "Codex app-server exited unexpectedly.")
                transcript.append(line)
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                request_id = message.get("id")
                if isinstance(request_id, int):
                    responses[request_id] = message

        try:
            send(
                {
                    "method": "initialize",
                    "id": 0,
                    "params": {
                        "clientInfo": {
                            "name": "cv_docker",
                            "title": "CV Docker",
                            "version": "0.1.0",
                        }
                    },
                }
            )
            wait_for({0})
            send({"method": "initialized", "params": {}})
            for request_id, method, params in methods:
                send({"method": method, "id": request_id, "params": params})
            wait_for({request_id for request_id, _method, _params in methods})
            return {request_id: responses[request_id] for request_id, _method, _params in methods}
        finally:
            if process.stdin is not None:
                process.stdin.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            reader.join(timeout=1)

    def account_status(self, *, force: bool = False) -> dict[str, Any]:
        with self._status_lock:
            now = time.monotonic()
            if not force and self._status_cache and now - self._status_cache[0] < 15:
                return self._status_cache[1]
            try:
                responses = self._app_server_messages(
                [
                    (1, "account/read", {"refreshToken": False}),
                    (2, "account/rateLimits/read", {}),
                ]
                )
            except Exception as exc:
                result = {
                    "installed": self.installed(),
                    "authenticated": False,
                    "available": False,
                    "error": str(exc),
                    "account": None,
                    "rate_limits": None,
                }
                self._status_cache = (now, result)
                return result
            account_response = responses.get(1, {})
            rate_response = responses.get(2, {})
            account = account_response.get("result", {}).get("account")
            rate_limits = rate_response.get("result")
            authenticated = isinstance(account, dict) and account.get("type") == "chatgpt"
            exhausted = self.rate_limits_exhausted(rate_limits)
            error = None
            if "error" in account_response:
                error = account_response["error"].get("message")
            elif "error" in rate_response:
                error = rate_response["error"].get("message")
            result = {
                "installed": True,
                "authenticated": authenticated,
                "available": authenticated and not exhausted and not error,
                "error": error,
                "account": account,
                "rate_limits": rate_limits,
            }
            self._status_cache = (now, result)
            return result

    @staticmethod
    def rate_limits_exhausted(rate_limits: Any) -> bool:
        if not isinstance(rate_limits, dict):
            return False
        buckets = rate_limits.get("rateLimitsByLimitId")
        if not isinstance(buckets, dict):
            single = rate_limits.get("rateLimits")
            buckets = {"codex": single} if isinstance(single, dict) else {}
        for bucket in buckets.values():
            if not isinstance(bucket, dict):
                continue
            if bucket.get("rateLimitReachedType"):
                return True
            for window_name in ("primary", "secondary"):
                window = bucket.get(window_name)
                if isinstance(window, dict) and float(window.get("usedPercent") or 0) >= 100:
                    return True
        return False

    def generate(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        if not self.installed():
            raise RuntimeError("Codex CLI is not installed in the API container.")
        if not self.auth_file_exists():
            raise RuntimeError(
                "Codex is not signed in inside the API container. Run: "
                "docker compose exec api codex login --device-auth"
            )
        status = self.account_status()
        if not status.get("authenticated"):
            detail = str(status.get("error") or "the saved login could not be read")
            raise RuntimeError(
                "Codex account authentication is unavailable inside the API container: "
                f"{detail}. Run: docker compose exec api codex login --device-auth"
            )
        if self.rate_limits_exhausted(status.get("rate_limits")):
            raise ProviderQuotaError("codex", "Codex account usage limit reached.")
        if status.get("error"):
            detail = str(status["error"])
            if is_quota_error(None, detail):
                raise ProviderQuotaError("codex", detail)
            raise RuntimeError(f"Could not verify Codex account availability: {detail}")
        self.work_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="cv-codex-", dir="/tmp") as temp_dir:
            temp_path = Path(temp_dir)
            schema_path = temp_path / "schema.json"
            output_path = temp_path / "output.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            command = [
                "codex",
                "exec",
                "--strict-config",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "-C",
                str(self.work_dir),
                "--model",
                self.model,
                "--config",
                f'model_reasoning_effort="{self.reasoning_effort}"',
                "--config",
                'approval_policy="never"',
                "--config",
                'default_permissions="cv-isolated"',
                "--config",
                (
                    'permissions.cv-isolated.filesystem={":minimal"="read",'
                    '":workspace_roots"={"."="read"},'
                    '"/app/.codex"="deny","/app/data"="deny",'
                    '"/app/outputs"="deny"}'
                ),
                "--config",
                "permissions.cv-isolated.network.enabled=false",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "--json",
                "-",
            ]
            safe_prompt = (
                "Complete this text-generation task using only the evidence in the prompt. "
                "Do not call tools, run commands, browse, or inspect files. Return only the "
                "JSON object required by the output schema.\n\n" + prompt
            )
            try:
                completed = subprocess.run(
                    command,
                    input=safe_prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"Codex generation timed out after {self.timeout_seconds} seconds."
                ) from exc
            combined = "\n".join((completed.stdout, completed.stderr)).strip()
            if completed.returncode != 0:
                if is_quota_error(None, combined):
                    raise ProviderQuotaError("codex", combined[-1200:] or "Codex usage limit reached.")
                raise RuntimeError(combined[-1600:] or "Codex generation failed.")
            if not output_path.exists():
                raise RuntimeError("Codex completed without writing structured output.")
            try:
                result = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError("Codex returned invalid structured JSON.") from exc
            if not isinstance(result, dict):
                raise RuntimeError("Codex structured output was not a JSON object.")
            return result
