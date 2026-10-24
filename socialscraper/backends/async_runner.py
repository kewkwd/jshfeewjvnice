# async_runner.py
import asyncio
import threading
from concurrent.futures import TimeoutError


class AsyncRunner:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._lock = threading.Lock()
        self._stopped = False
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.close()

    def run(self, coro, timeout=180):
        with self._lock:
            if self._stopped or threading.current_thread() is self._t:
                coro.close()
                raise RuntimeError("AsyncRunner ist gestoppt oder wurde aus seinem eigenen Thread aufgerufen")
            future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise

    def stop(self):
        with self._lock:
            if not self._stopped:
                self._stopped = True
                self.loop.call_soon_threadsafe(self.loop.stop)
        if threading.current_thread() is not self._t:
            self._t.join(timeout=5)
