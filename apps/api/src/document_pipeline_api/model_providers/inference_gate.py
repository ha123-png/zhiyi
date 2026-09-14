"""A shared OS lease for local inference, used by extraction and conversations."""

from contextlib import contextmanager
import hashlib
import os
import time
from urllib.parse import urlparse


@contextmanager
def inference_slot(settings, cancel=None, waiting=None):
    address = urlparse(settings.model_base_url)
    if address.hostname not in {"localhost", "127.0.0.1", "::1"}:
        yield
        return
    directory = settings.storage_dir.parent / "runtime" / "model-locks"
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(f"{address.port or 80}".encode()).hexdigest()[:16]
    deadline = time.monotonic() + settings.model_timeout_seconds
    with (directory / (key + ".lock")).open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        announced = False
        while True:
            if cancel and cancel.is_set():
                raise InterruptedError("已停止生成。")
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if waiting and not announced:
                    waiting()
                    announced = True
                if time.monotonic() > deadline:
                    raise TimeoutError("本地模型仍被其他任务使用，请稍后重试。")
                time.sleep(0.15)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class CoordinatedProvider:
    def __init__(self, provider, settings):
        self.provider, self.settings = provider, settings

    def __getattr__(self, name):
        value = getattr(self.provider, name)
        if name not in {"extract_image", "extract_images", "complete_text"}:
            return value

        def call(*args, **kwargs):
            # Waiting for a busy local model can fail before the HTTP request.
            # Never attribute the previous call's tokens to that failed attempt.
            self.provider.last_usage = {}
            with inference_slot(self.settings):
                return value(*args, **kwargs)

        return call
