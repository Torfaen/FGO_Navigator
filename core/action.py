import time
from typing import Callable


def wait_until(check: Callable[[], bool], timeout_sec: int, interval_sec: float = 1.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if check():
            return True
        time.sleep(interval_sec)
    return False
