"""Guards the shared contract between this app's enqueue side and
Simpero_Gov_AI_Services' worker side: the queue name. A drift here is
silent — jobs would be enqueued and simply never picked up, with no error
raised on either side — so it's pinned in a test rather than left to only
live in a comment.
"""

from app.jobs.parse_client import PARSE_QUEUE_NAME, get_parse_queue


def test_parse_queue_name_matches_worker_contract():
    # Must equal Simpero_Gov_AI_Services' ParserSettings.queue_name default
    # ("parse") exactly.
    assert PARSE_QUEUE_NAME == "parse"


def test_get_parse_queue_uses_parse_queue_name():
    queue = get_parse_queue()
    assert queue.name == PARSE_QUEUE_NAME
    get_parse_queue.cache_clear()


def test_get_parse_queue_is_separate_from_app_job_queue():
    from app.jobs.queue import get_queue

    assert get_parse_queue().name != get_queue().name
    get_parse_queue.cache_clear()
