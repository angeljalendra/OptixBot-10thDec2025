from config.settings import Settings
from core.logger import Logger


logger = Logger.get_logger('alerts')


class Alerts:
    def __init__(self):
        self.enabled = Settings.telegram.ENABLED
        self.token = Settings.telegram.TOKEN
        self.chat_id = Settings.telegram.CHAT_ID

    def send_trade(self, text: str):
        logger.info(text)
        if self.enabled and self.token and self.chat_id:
            try:
                import requests
                requests.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
                    timeout=1.5
                )
            except Exception:
                pass