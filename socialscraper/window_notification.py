"""A short, nonblocking hint for a newly opened interactive window."""
import logging
import os

log=logging.getLogger(__name__)


def window_opened(owner,*,headless=False):
    """Notify once per window/session, without affecting the running task."""
    if headless or os.name!='nt' or getattr(owner,'_window_sound_notified',False) is True:
        return False
    owner._window_sound_notified=True
    try:
        import winsound
        winsound.PlaySound('SystemNotification',
            winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except (ImportError,OSError,RuntimeError):
        log.debug('Fenster-Hinweiston ist nicht verfügbar.',exc_info=True)
        return False
    return True
