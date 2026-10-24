"""One worker/poller implementation; Tk is touched only by its owning thread."""
import threading
import weakref
import tkinter as tk

_ACTIVE = weakref.WeakKeyDictionary()
_ACTIVE_LOCK = threading.Lock()


def background_tasks_running(widget):
    """Whether the Tk owner still has workers that could retain UI objects."""
    try:
        owner = widget.winfo_toplevel()
    except (AttributeError, tk.TclError):
        owner = widget
    try:
        hash(owner)
        weakref.ref(owner)
    except TypeError:
        return False
    with _ACTIVE_LOCK:
        workers = _ACTIVE.get(owner)
        if not workers:
            return False
        for worker in tuple(workers):
            if not worker.is_alive():
                workers.discard(worker)
        if not workers:
            _ACTIVE.pop(owner, None)
            return False
        return True


def run_background(widget, work, done, *, interval, daemon, schedule):
    result = {}
    finished = threading.Event()
    owner = None
    worker = None

    def target():
        try:
            result['value'] = work()
        except BaseException as error:
            # A worker SystemExit must not leave an endless UI poller behind.
            result['error'] = error
        finally:
            finished.set()

    def poll():
        try:
            if not widget.winfo_exists():
                return
            if finished.is_set() and not worker.is_alive():
                if owner is not None:
                    with _ACTIVE_LOCK:
                        workers = _ACTIVE.get(owner)
                        if workers is not None:
                            workers.discard(worker)
                            if not workers:
                                _ACTIVE.pop(owner, None)
                done(result.get('value'), result.get('error'))
            else:
                schedule(widget, interval, poll)
        except tk.TclError:
            pass  # The widget/interpreter can disappear before the callback.

    worker = threading.Thread(target=target, daemon=daemon)
    try:
        owner = widget.winfo_toplevel()
        hash(owner)
        weakref.ref(owner)
    except (AttributeError, tk.TclError, TypeError):
        owner = None
    if owner is not None:
        with _ACTIVE_LOCK:
            _ACTIVE.setdefault(owner, set()).add(worker)
    worker.start()
    try:
        schedule(widget, interval, poll)
    except tk.TclError:
        pass  # File-writing workers still finish when their window closes.
