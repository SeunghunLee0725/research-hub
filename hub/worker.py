"""Scheduler process: advances tasks every few seconds and sends Telegram alerts."""
import logging
import sys
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from hub.config import Settings
from hub.db.session import make_sessionmaker
from hub.services import notify, scheduler

TICK_S, NOTIFY_EVERY_S = 5, 30
log = logging.getLogger("research-hub-worker")


def cycle(db: Session, now: datetime, settings: Settings, sender, notify_now: bool) -> None:
    scheduler.tick(db, now)
    if notify_now and sender is not None:
        notify.deliver(db, now, sender, settings.public_url.rstrip("/"), settings.offline_after_seconds)


def _sender(settings: Settings):
    if settings.telegram_bot_token and settings.telegram_chat_id:
        return notify.telegram_sender(settings.telegram_bot_token, settings.telegram_chat_id,
                                      settings.telegram_topic_id)
    log.warning("텔레그램 설정 없음 — 알림을 보내지 않습니다")
    return None


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    sessions, sender, last_notify = make_sessionmaker(settings.database_url), _sender(settings), 0.0
    while True:
        started = time.monotonic()
        notify_now = started - last_notify >= NOTIFY_EVERY_S
        try:
            with sessions() as db:
                cycle(db, datetime.now(timezone.utc), settings, sender, notify_now)
            if notify_now:
                last_notify = started
        except Exception:  # keep the scheduler alive; the next tick retries
            log.exception("scheduler cycle failed")
        time.sleep(TICK_S)


if __name__ == "__main__":
    sys.exit(main())
