"""Exclusive access to browser profiles for the update operations."""
import functools
import math
import time
from . import runlock


def cancelled(stop):
    return stop is not None and stop()


def check_cancelled(stop):
    """Shared cancellation boundary for browser and mailbox operations."""
    if cancelled(stop):
        from .net.errors import FinalError
        raise FinalError('Abgebrochen.')


def positive_timeout(value):
    try:
        valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                 and math.isfinite(value) and value > 0)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError('Das Zeitlimit muss eine endliche positive Zahl sein.')
    return value


def wait(seconds, stop=None):
    """Wait in short intervals, allowing a running task to be cancelled."""
    if stop is None:
        time.sleep(max(0, seconds))
        return True
    end = time.monotonic() + max(0, seconds)
    while not cancelled(stop):
        remaining = end - time.monotonic()
        if remaining <= 0:
            return True
        time.sleep(min(0.2, remaining))
    return False


def exclusive(function):
    @functools.wraps(function)
    def run(first, *args, **kwargs):
        db_path = getattr(first, 'db_path', first)
        with runlock.acquire(runlock.root_for_db(db_path), 'update'):
            return function(first, *args, **kwargs)
    return run


def browser_owned(function):
    @functools.wraps(function)
    def run(cfg, *args, **kwargs):
        from .net.errors import FinalError
        if cancelled(kwargs.get('stop')):
            raise FinalError('Abgebrochen.')
        if 'timeout' in kwargs:
            positive_timeout(kwargs['timeout'])
        from .net import proxymode
        proxymode.require_no_anon(cfg, 'Konto oder Postfach anlegen')
        android_signup = (function.__module__ == 'socialscraper.signup' and
                          function.__name__ == 'create' and
                          (args[0] if args else kwargs.get('platform')) == 'tiktok')
        if kwargs.get('backend') is not None or kwargs.get('route')=='android' or android_signup:
            return function(cfg, *args, **kwargs)
        from .cli import make_runtime, shutdown_runtime
        im, backend, sessions = make_runtime(cfg, need_browser=True)
        kwargs.update(backend=backend, im=kwargs.get('im') or im)
        try:
            return function(cfg, *args, **kwargs)
        finally:
            shutdown_runtime(backend, sessions)
    return run
