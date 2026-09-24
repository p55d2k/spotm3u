import threading
import time

from spotm3u.metadata_cache import MetadataCache


def test_cache_shares_one_inflight_fetch():
    cache = MetadataCache()
    calls = 0
    lock = threading.Lock()

    def fetch():
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.02)
        return "lyrics"

    threads = [threading.Thread(target=lambda: cache.get_or_fetch("song", fetch)) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == 1
    assert cache.metrics["singleflight_hits"] == 4


def test_cache_does_not_retain_missing_results():
    cache = MetadataCache()
    calls = 0

    def fetch():
        nonlocal calls
        calls += 1
        return None

    assert cache.get_or_fetch("missing", fetch) is None
    assert cache.get_or_fetch("missing", fetch) is None
    assert calls == 2
