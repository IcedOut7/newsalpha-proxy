import aiohttp
import logging
import os
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self):
        # Local load to allow usage in various contexts
        load_dotenv()
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.enabled = bool(self.token and self.chat_id)
        if not self.enabled:
            logger.info("Telegram notification disabled (missing token or chat_id)")

    async def send_message(self, text: str):
        if not self.enabled:
            return

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        err_text = await resp.text()
                        logger.error(f"Telegram API error: {resp.status} - {err_text}")
        except Exception as e:
            logger.error(f"Failed to send telegram message: {e}")

    async def notify_arb(self, result):
        """Notifies about an arbitrage execution result."""
        emoji = "✅" if not result.aborted else "❌"
        status = "Исполнено" if not result.aborted else f"Отменено: {result.abort_reason}"

        msg = (
            f"<b>{emoji} Арбитраж {status}</b>\n"
            f"Событие: {result.event_name}\n"
            f"Профит: <b>{result.estimated_net_profit}$ ({result.estimated_profit_pct}%)</b>\n"
            f"ID: <code>{result.arb_id}</code>"
        )
        await self.send_message(msg)

    async def notify_startup(self, mode, bankroll):
        await self.send_message(f"🚀 <b>Бот запущен</b>\nРежим: <code>{mode}</code>\nБанкролл: <b>${bankroll}</b>")
