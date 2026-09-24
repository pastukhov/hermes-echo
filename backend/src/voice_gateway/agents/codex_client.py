"""Authenticated HTTP client for the host-side Codex voice agent."""

from __future__ import annotations

import asyncio
import ipaddress
import json
from urllib.parse import urlsplit

import httpx

from .base import AgentClientError, AgentReply, AgentRequest


class CodexAgentClient:
    _SAFE_ERRORS = {
        "agent_unavailable": ("Codex agent is unavailable", True),
        "agent_auth_required": ("Codex authentication is required", False),
        "agent_rate_limited": ("Codex account quota is unavailable", True),
        "agent_timeout": ("Codex agent request timed out", True),
        "agent_invalid_response": ("Codex returned an invalid response", False),
        "permission_required": ("Codex request requires permission", False),
        "interrupted": ("Codex request was interrupted", True),
        "idempotency_conflict": ("Request ID was reused with different input", False),
        "device_busy": ("This device already has an active request", True),
        "agent_busy": ("Codex agent queue is full", True),
        "invalid_request": ("Codex request is invalid", False),
        "request_not_found": ("Codex request was not found", False),
    }
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        client: httpx.AsyncClient | None = None,
        deadline_seconds: float = 180.0,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        parsed = urlsplit(base_url)
        hostname = (parsed.hostname or "").lower()
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = hostname == "localhost"
        if (
            parsed.scheme != "http"
            or not is_loopback
            or not parsed.port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Codex agent URL must be an explicit loopback HTTP URL")
        if not token.strip():
            raise ValueError("Codex agent token is required")
        if deadline_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("deadline and poll interval must be positive")
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client or httpx.AsyncClient(timeout=5.0)
        self._owns_client = client is None
        self.deadline_seconds = deadline_seconds
        self.poll_interval_seconds = poll_interval_seconds

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def complete(self, request: AgentRequest) -> AgentReply:
        try:
            return await self._complete(request)
        except asyncio.CancelledError:
            await asyncio.shield(self.cancel(request.request_id))
            raise

    async def _complete(self, request: AgentRequest) -> AgentReply:
        deadline = asyncio.get_running_loop().time() + self.deadline_seconds
        path = f"{self.base_url}/v1/agent/turns"
        body = {
            "request_id": request.request_id,
            "device_id": request.device_id,
            "transcript": request.transcript,
        }
        response = None
        # Re-submit only the identical request ID/body if the 202 was lost.
        for _ in range(2):
            try:
                response = await self._client.post(
                    path, json=body, headers=self._headers, timeout=5.0
                )
                break
            except httpx.HTTPError as exc:
                del exc
        if response is None:
            raise AgentClientError("agent_unavailable", "Codex agent is unreachable", True) from None
        if response.status_code not in (200, 202):
            self._raise_response(response)

        while True:
            payload = self._json(response)
            status = payload.get("status")
            if status == "completed":
                return self._reply(payload.get("reply"))
            if status in {"failed", "interrupted", "cancelled"}:
                error = payload.get("error") or {}
                code = str(error.get("code") or "agent_unavailable")
                message = str(error.get("message") or "Codex agent request failed")
                raise AgentClientError(code, message, bool(error.get("retryable", False)))
            if status not in {"queued", "running"}:
                raise AgentClientError("agent_invalid_response", "Invalid Codex agent status")
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                await self.cancel(request.request_id)
                raise AgentClientError("agent_timeout", "Codex agent request timed out", True)
            await asyncio.sleep(min(self.poll_interval_seconds, remaining))
            try:
                response = await self._client.get(
                    f"{path}/{request.request_id}", headers=self._headers, timeout=5.0
                )
            except httpx.HTTPError:
                continue
            if response.status_code != 200:
                self._raise_response(response)

    async def cancel(self, request_id: str) -> None:
        try:
            response = await self._client.post(
                f"{self.base_url}/v1/agent/turns/{request_id}/cancel",
                headers=self._headers,
                timeout=5.0,
            )
        except httpx.HTTPError:
            return
        if response.status_code not in (200, 404):
            self._raise_response(response)

    async def reset(self, device_id: str) -> None:
        try:
            response = await self._client.post(
                f"{self.base_url}/v1/agent/sessions/reset",
                json={"device_id": device_id},
                headers=self._headers,
                timeout=5.0,
            )
        except httpx.HTTPError:
            raise AgentClientError("agent_unavailable", "Codex agent is unreachable", True) from None
        if response.status_code != 200:
            self._raise_response(response)

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            raise AgentClientError("agent_invalid_response", "Invalid Codex agent response") from None
        if not isinstance(payload, dict):
            raise AgentClientError("agent_invalid_response", "Invalid Codex agent response")
        return payload

    @classmethod
    def _reply(cls, value: object) -> AgentReply:
        if not isinstance(value, dict):
            raise AgentClientError("agent_invalid_response", "Missing Codex reply")
        reply = value.get("reply")
        thread_id = value.get("thread_id")
        if not isinstance(reply, str) or not reply.strip() or not isinstance(thread_id, str):
            raise AgentClientError("agent_invalid_response", "Invalid Codex reply")
        note = value.get("note")
        if note is not None and not isinstance(note, dict):
            raise AgentClientError("agent_invalid_response", "Invalid Codex note")
        return AgentReply(
            reply=reply.strip(),
            note=note,
            thread_id=thread_id,
            model=value.get("model") if isinstance(value.get("model"), str) else None,
            provider="codex",
        )

    @classmethod
    def _raise_response(cls, response: httpx.Response) -> None:
        code = "agent_unavailable"
        if response.status_code == 401:
            code = "agent_auth_required"
        try:
            detail = response.json().get("detail", {})
            if isinstance(detail, dict):
                proposed = detail.get("code")
                if proposed in cls._SAFE_ERRORS:
                    code = proposed
        except (ValueError, AttributeError, TypeError):
            pass
        message, default_retryable = cls._SAFE_ERRORS[code]
        retryable = default_retryable or response.status_code >= 500
        raise AgentClientError(code, message, retryable)
