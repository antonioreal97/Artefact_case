"""Outbound WhatsApp Cloud API client (Meta Graph API ``/{phone_number_id}/messages``).

ADAPTER NOT VALIDATED AGAINST THE LIVE META API: payload shape follows Meta's public
Cloud API docs for text messages; it is exercised only with fakes/mocked transports in
tests. The recipient id and message text are never logged.
"""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

log = get_logger(__name__)

MAX_TEXT_CHARS = 4096  # Cloud API limit for text message bodies


class MetaWhatsAppClient:
    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._token = settings.whatsapp_access_token
        self._base = settings.whatsapp_graph_api_base.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))

    @property
    def configured(self) -> bool:
        return self._token is not None and bool(self._token.get_secret_value())

    async def send_text(self, *, phone_number_id: str, to: str, text: str) -> bool:
        """Send a text reply. Returns True on a 2xx from Meta, False otherwise (logged)."""
        if self._token is None or not self.configured:
            log.warning("reply not sent: whatsapp not configured")
            return False
        try:
            response = await self._http.post(
                f"{self._base}/{phone_number_id}/messages",
                headers={"Authorization": f"Bearer {self._token.get_secret_value()}"},
                json={
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": to,
                    "type": "text",
                    "text": {"preview_url": False, "body": text[:MAX_TEXT_CHARS]},
                },
            )
        except httpx.HTTPError as exc:
            log.error("whatsapp.send_failed", error_type=type(exc).__name__)
            return False
        if response.is_success:
            return True
        log.error("whatsapp.send_failed", status_code=response.status_code)
        return False

    async def aclose(self) -> None:
        await self._http.aclose()
