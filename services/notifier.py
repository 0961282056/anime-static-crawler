"""Operational notifications kept outside crawler and repository logic."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from services.errors import NotificationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Notification:
    status: str
    year: str
    season: str
    count: int = 0
    changed: bool = False
    parse_failures: int = 0
    message: str = ""


class DiscordNotifier:
    def __init__(self, webhook_url: str | None) -> None:
        self.webhook_url = (webhook_url or "").strip()

    def send(self, notification: Notification) -> bool:
        if not self.webhook_url:
            return False

        success = notification.status == "SUCCESS"
        color = 3066993 if success else 15158332
        title = "✅ 動畫爬蟲更新成功" if success else "🚨 動畫爬蟲執行失敗"
        description = (
            f"**季度**：{notification.year} {notification.season}\n"
            f"**有效資料**：{notification.count} 筆\n"
            f"**解析失敗**：{notification.parse_failures} 筆\n"
            f"**資料有變更**：{'是' if notification.changed else '否'}"
        )
        if notification.message:
            description += f"\n**說明**：{notification.message[:1000]}"

        payload = {
            "username": "Anime Crawler Bot",
            "embeds": [
                {
                    "title": title,
                    "description": description,
                    "color": color,
                }
            ],
        }
        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            raise NotificationError(f"Discord notification failed: {exc}") from exc
