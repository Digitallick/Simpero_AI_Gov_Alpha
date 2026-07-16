from saq.types import SettingsDict

from app.jobs.queue import get_queue
from app.jobs.tasks import functions

settings: SettingsDict = {
    "queue": get_queue(),
    "functions": functions,
    "concurrency": 10,
}
