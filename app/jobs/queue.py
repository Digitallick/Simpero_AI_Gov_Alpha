from functools import lru_cache

from saq import Queue

from app.core.config import get_settings

settings = get_settings()


@lru_cache
def get_queue() -> Queue:
    # Queue.from_url is lazy — it does not open a connection until the first command is issued,
    # so constructing it here does not violate the no-connections-at-startup rule.
    return Queue.from_url(settings.valkey_url, name="simpero")
