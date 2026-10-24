"""Profile-scoped input tempo for native browser controls."""
import time
import weakref

from .errors import FinalError

_SETTINGS = weakref.WeakKeyDictionary()


def bind(page, settings):
    try:
        if isinstance(settings, dict) and isinstance(settings.get('tempo'), dict):
            _SETTINGS[page] = dict(settings['tempo'])
        else:_SETTINGS.pop(page, None)
    except TypeError:pass
    return page


def tempo(page):
    try:return _SETTINGS.get(page)
    except TypeError:return None


def pause(page, milliseconds, check=None):
    if check:check()
    while milliseconds > 0:
        chunk = min(100, milliseconds)
        page.wait_for_timeout(chunk)
        if check:check()
        milliseconds -= chunk


def fill(page, control, value, *, check=None, timeout=1500):
    value = str(value)
    if check:check()
    def verified():
        if check:check()
        try:
            correct = control.evaluate("el => el.isContentEditable ? el.innerText : el.value", timeout=timeout) == value
        except Exception:
            if check:check()
            correct = False
        if not correct:
            raise FinalError('Der eingetragene Wert konnte nicht bestätigt werden. Das Formular bleibt zur Prüfung offen; die Eingabe wird nicht wiederholt.')
        if check:check()
    def native_fill(text):
        try:
            control.fill(text, timeout=timeout)
        except FinalError:
            raise
        except Exception as error:
            if check:check()
            raise FinalError('Die Eingabe wurde nicht eindeutig bestätigt. Bitte den Formularstand prüfen; kein weiterer Eingabeversuch ausgeführt.') from error
    settings = tempo(page)
    if settings is None:
        native_fill(value)
        verified()
        return
    if check:check()
    text_field = control.evaluate("el => el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && ['text','password','email','url','search','tel','number'].includes(el.type))", timeout=timeout)
    if text_field is not True:
        # Structured date/time controls need a complete native value.
        native_fill(value)
        verified()
        return
    if not callable(getattr(control, 'press_sequentially', None)):
        raise FinalError('Dieser Browser unterstützt das gespeicherte Profil-Tipptempo nicht.')
    native_fill('')
    try:
        identity = control.evaluate("el => [el.ownerDocument.location.origin, el.tagName, el.type || '', el.id || '', el.name || '']", timeout=timeout)
        for character in value:
            if check:check()
            state = control.evaluate("el => ({identity: [el.ownerDocument.location.origin, el.tagName, el.type || '', el.id || '', el.name || ''], focused: el.ownerDocument.activeElement === el && !el.disabled && !el.readOnly})", timeout=timeout)
            if state.get('identity') != identity or state.get('focused') is not True:
                raise FinalError('Das Eingabefeld hat sich während des Tippens geändert. Die Eingabe wurde angehalten.')
            control.press_sequentially(character, delay=0, timeout=timeout)
            pause(page, 1000 / settings['typing_cps'], check)
    except FinalError:raise
    except Exception as error:
        raise FinalError('Die Eingabe mit Profiltempo wurde unterbrochen; Formular bitte prüfen.') from error
    verified()


def click(page, control, *, check=None, timeout=5000, before_click=None):
    end = time.monotonic() + timeout / 1000
    def boundary():
        if check:check()
        if time.monotonic() >= end:
            raise FinalError('Die Schaltfläche wurde nicht rechtzeitig bedienbar. Kein weiterer Klick ausgeführt.')
    def remaining():
        boundary()
        return max(1, int((end - time.monotonic()) * 1000))
    settings = tempo(page)
    if settings is None:
        if before_click:before_click()
        try:
            control.click(timeout=remaining())
        except FinalError:
            raise
        except Exception as error:
            if check:check()
            raise FinalError('Der Klick wurde nicht eindeutig bestätigt. Bitte den angezeigten Zustand prüfen; kein weiterer Klick ausgeführt.') from error
        if check:check()
        return
    boundary()
    control.scroll_into_view_if_needed(timeout=remaining())
    box = control.bounding_box(timeout=remaining())
    if not box:raise FinalError('Das Klickziel ist nicht sichtbar; kein Klick ausgeführt.')
    from . import humanize
    # Slow configured movement gets its own finite budget. It must not spend
    # the time reserved for waiting until the final control is ready.
    end = time.monotonic() + 30
    if not humanize.move(page, box['x'] + box['width']/2, box['y'] + box['height']/2,
                         stop=lambda: (boundary(), False)[1]):
        raise FinalError('Die Mausbewegung konnte nicht abgeschlossen werden; kein Klick ausgeführt.')
    if check:check()
    pause(page, settings['click_ms'], boundary)
    end = time.monotonic() + timeout / 1000
    previous = None
    while time.monotonic() < end:
        boundary()
        state = control.evaluate("""el => {
          const r = el.getBoundingClientRect();
          const hit = el.ownerDocument.elementFromPoint(r.x + r.width/2, r.y + r.height/2);
          return {box: [r.x,r.y,r.width,r.height], ready: r.width > 0 && r.height > 0 &&
            !el.matches(':disabled,[aria-disabled="true"]') && (hit === el || el.contains(hit))};
        }""", timeout=remaining())
        if state.get('ready') is True and state.get('box') == previous:
            break
        previous = state.get('box') if state.get('ready') is True else None
        pause(page, 100, boundary)
    boundary()
    # A native timeout after dispatch must not lead to a second click.
    if before_click:before_click()
    try:control.click(timeout=remaining())
    except FinalError:raise
    except Exception as error:
        raise FinalError('Der Klick wurde nicht bestätigt; den angezeigten Zustand bitte prüfen.') from error
    if check:check()
