"""Secret-safe structured logging and Telegram alerting."""

from __future__ import annotations

import logging
from typing import Iterable

import httpx


class SecretRedactingFilter(logging.Filter):
    """Logging filter that strips any known secret value before formatting or emitting."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets: list[str] = [s for s in secrets if s and len(s) >= 6]

    def add_secret(self, value: str) -> None:
        if value and len(value) >= 6 and value not in self._secrets:
            self._secrets.append(value)

    def redact(self, text: str) -> str:
        redacted = text
        for secret in self._secrets:
            if secret in redacted:
                redacted = redacted.replace(secret, "[REDACTED]")
        return redacted

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self.redact(str(v)) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self.redact(a) if isinstance(a, str) else a for a in record.args
                )
        return True


def configure_logging(secrets: Iterable[str] = (), level: int = logging.INFO) -> SecretRedactingFilter:
    """Configure root logger with mandatory secret redaction."""
    root = logging.getLogger()
    root.setLevel(level)
    filt = SecretRedactingFilter(secrets)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)sZ %(levelname)s %(name)s %(message)s")
        )
        root.addHandler(handler)
    for handler in root.handlers:
        handler.addFilter(filt)
    return filt


class TelegramAlerter:
    """Sends risk/crisis/kill-switch alerts to Telegram if bot credentials are provisioned."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        redactor: SecretRedactingFilter | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._bot_token = (bot_token or "").strip() or None
        self._chat_id = (chat_id or "").strip() or None
        self._redactor = redactor or SecretRedactingFilter()
        if self._bot_token:
            self._redactor.add_secret(self._bot_token)
        self._client = http_client
        self.sent_messages: list[str] = []
        self._log = logging.getLogger("ase.alerts")

    @property
    def enabled(self) -> bool:
        return bool(self._bot_token and self._chat_id)

    def send(self, severity: str, message: str) -> bool:
        """Send a redacted alert. Returns True if delivered via Telegram, False if logged locally."""
        clean_msg = self._redactor.redact(f"[ASE:{severity.upper()}] {message}")
        self.sent_messages.append(clean_msg)
        if not self.enabled:
            self._log.warning("Telegram unconfigured; local alert: %s", clean_msg)
            return False

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload = {"chat_id": self._chat_id, "text": clean_msg}
        try:
            if self._client is not None:
                resp = self._client.post(url, json=payload, timeout=10.0)
            else:
                resp = httpx.post(url, json=payload, timeout=10.0)
            resp.raise_for_status()
            return True
        except Exception as exc:
            self._log.error("Telegram delivery failed: %s", self._redactor.redact(str(exc)))
            return False
