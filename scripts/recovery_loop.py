import logging
import time

from recover import recover

if __name__ == "__main__":
    while True:
        try:
            result = recover()
            if any(result.values()):
                logging.warning("Queue recovery: %s", result)
        except Exception:
            logging.exception("Recovery check failed; queued input snapshots remain in PostgreSQL")
        time.sleep(30)
