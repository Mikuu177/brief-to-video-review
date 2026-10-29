import os
import sys
from pathlib import Path

from redis import Redis
from rq import Queue, SpawnWorker


def main():
    app_path = str(Path(__file__).resolve().parents[1] / "apps/api")
    sys.path.insert(0, app_path)
    # SpawnWorker starts a fresh interpreter; sys.path alone does not reach that child.
    os.environ["PYTHONPATH"] = os.pathsep.join([app_path, os.environ.get("PYTHONPATH", "")])
    from app.settings import settings

    connection = Redis.from_url(settings.redis_url)
    queue = Queue(settings.queue_name, connection=connection)
    SpawnWorker([queue], connection=connection).work(with_scheduler=True)


if __name__ == "__main__":
    main()
