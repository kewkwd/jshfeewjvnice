"""Paced browser actions and bounded readiness checks.

Pacing controls the requested input tempo; readiness is a separate condition.
Mouse positions use viewport CSS pixels, as in the browser's native input API.
"""
import os
import math
import random
import time
import weakref

_pos = weakref.WeakKeyDictionary()
MAX_SCROLL_DISTANCE = 10_000      # one gesture sequence; callers may request further bounded sequences

# --- Tempo zwischen Abrufen ----------------------------------------------------------
# Zwischen zwei Abrufen derselben Plattform liegt eine zufaellige Pause (kein Burst). Die Verteilung ist
# nach unten betont (meist 2-5 s, selten bis 9 s) und hat ab und zu eine laengere "Lesepause".
PACE_MIN, PACE_MAX = 2.0, 9.0
BREAK_CHANCE, BREAK_RANGE = 0.08, (12.0, 30.0)
PACE_OFF_ENV = "SS_PACE"          # SS_PACE=off schaltet die Pausen ab - nur fuer Tests gedacht
PACE_WINDOW = 600.0               # liegt der letzte Abruf der Plattform laenger zurueck, laeuft es nicht "am Stueck"
_last_fetch = {}                  # Plattform -> Zeitpunkt (monotonic) des letzten Abruf-Beginns

def pace_enabled():
    if os.environ.get("SS_NO_PACING", "").strip().lower() in ("1", "true", "yes"):
        return False
    return os.environ.get(PACE_OFF_ENV, "").strip().lower() not in ("off", "0", "false", "no")

def pace_delay(rng=random, bounds=None):
    """Zufaellige Pause in Sekunden (deterministisch testbar mit einem seeded `random.Random`).

    `bounds` (aus channels.pace_bounds) ersetzt die Standardwerte: {'low', 'high', 'break_chance', 'break_range'}."""
    b = bounds or {}
    low, high = _range(b.get("low", PACE_MIN), b.get("high", PACE_MAX))
    chance = _number(b.get('break_chance', BREAK_CHANCE))
    if not 0 <= chance <= 1:raise ValueError('Pausenwahrscheinlichkeit muss zwischen 0 und 1 liegen.')
    breaks = _range(*b.get('break_range', BREAK_RANGE))
    delay = low + (high - low) * rng.betavariate(1.5, 3.0)
    if rng.random() < chance:
        delay += rng.uniform(*breaks)
    return _number(delay)

def _number(value):
    try:result=float(value)
    except (TypeError,ValueError,OverflowError) as exc:
        raise ValueError('Zeit- und Bewegungswerte müssen Zahlen sein.') from exc
    if not math.isfinite(result):raise ValueError('Zeit- und Bewegungswerte müssen endlich sein.')
    return result

def _range(low, high):
    low, high = _number(low), _number(high)
    if low < 0 or high < low:raise ValueError('Ungültiger Pausenbereich.')
    return low, high

def wait(seconds, *, stop=None, sleep=time.sleep):
    """Interruptible delay; reports the time actually requested from sleep."""
    seconds=_number(seconds)
    if seconds<0:raise ValueError('Negative Pause ist nicht erlaubt.')
    if stop is None:
        if seconds:sleep(seconds)
        return seconds
    waited=0.0
    while waited<seconds and not stop():
        chunk=min(.1,seconds-waited);sleep(chunk);waited+=chunk
    return waited

def _remember_stop(stop):
    """Keep an observed cancellation set for the whole enclosing operation."""
    if stop is None:return None
    stopped=False
    def check():
        nonlocal stopped
        stopped=stopped or bool(stop())
        return stopped
    return check

def pace(platform, rng=random, sleep=time.sleep, clock=time.monotonic, bounds=None, stop=None):
    """Vor einem Abruf aufrufen: wartet, falls kurz zuvor schon ein Abruf derselben Plattform lief.

    Der erste Abruf einer Plattform (und einer nach langer Ruhe, `PACE_WINDOW`) wartet nicht.
    Gibt die gewartete Zeit zurueck.
    """
    stop=_remember_stop(stop)
    if stop and stop():return 0.0
    last = _last_fetch.get(platform)
    waited = 0.0
    if pace_enabled() and last is not None and clock() - last <= PACE_WINDOW:
        waited = wait(pace_delay(rng, bounds),stop=stop,sleep=sleep)
    if not stop or not stop():_last_fetch[platform] = clock()
    return waited

def _viewport_size(value):
    if isinstance(value,dict):
        try:
            width,height=_number(value['width']),_number(value['height'])
            if width>=1 and height>=1:return width,height
        except (ValueError,TypeError,KeyError):pass
    return None

def _viewport(page, stop=None):
    if stop and stop():return None
    try:value=getattr(page,'viewport_size',None)
    except Exception:value=None
    if stop and stop():return None
    size=_viewport_size(value)
    if size is not None:return size
    try:value=page.evaluate('({width:innerWidth,height:innerHeight})')
    except Exception:return None
    if stop and stop():return None
    return _viewport_size(value)

def _clamp(point, viewport):
    px,py=point
    return (max(0,min(viewport[0]-1,px)),max(0,min(viewport[1]-1,py))) if viewport else (max(0,px),max(0,py))

def _points(page, x, y, rng=random, stop=None):
    x,y=_number(x),_number(y)
    viewport=_viewport(page,stop)
    if viewport is None:return []
    def clamp(point):
        return _clamp(point,viewport)
    x,y=clamp((x,y))
    try:sx,sy=_pos.get(page,(x/2,y/2))
    except TypeError:sx,sy=x/2,y/2
    sx,sy=clamp((sx,sy))
    c1 = clamp((sx+(x-sx)*.3+rng.randint(-70,70), sy+(y-sy)*.3+rng.randint(-70,70)))
    c2 = clamp((sx+(x-sx)*.7+rng.randint(-70,70), sy+(y-sy)*.7+rng.randint(-70,70)))
    n = rng.randint(18, 36)
    pts = []
    for i in range(1, n+1):
        t = i/n; t=t*t*(3-2*t);m = 1-t
        pts.append((m**3*sx + 3*m*m*t*c1[0] + 3*m*t*t*c2[0] + t**3*x,
                    m**3*sy + 3*m*m*t*c1[1] + 3*m*t*t*c2[1] + t**3*y))
    return pts

def _wait_page(page, milliseconds, stop=None):
    stop=_remember_stop(stop)
    wait(milliseconds/1000,stop=stop,sleep=lambda seconds:page.wait_for_timeout(seconds*1000))
    return not stop or not stop()

def wait_for(page, condition, *, timeout=4.0, interval=0.2, stop=None, clock=time.monotonic):
    """Return the first truthy condition value, or None on timeout/cancellation.

    Times are seconds; timeout=0 performs one check. The condition must be a
    read-only operation with its own bounded transport. Exceptions propagate.
    Browser-call overhead counts against the monotonic budget; requested waits
    also advance the budget when a test double does not actually sleep.
    """
    timeout,interval=_number(timeout),_number(interval)
    if timeout<0 or interval<=0:raise ValueError('Ungültiges Bereitschafts-Zeitlimit oder Prüfintervall.')
    stop=_remember_stop(stop)
    if stop and stop():return None
    started=clock()
    requested=0.0
    first=True
    while True:
        if stop and stop():return None
        elapsed=max(requested,clock()-started)
        if not first and elapsed>=timeout:return None
        value=condition()
        if stop and stop():return None
        elapsed=max(requested,clock()-started)
        if value and (timeout==0 or elapsed<=timeout):return value
        if elapsed>=timeout:return None
        pause_end=min(timeout,elapsed+interval)
        while True:
            if stop and stop():return None
            elapsed=max(requested,clock()-started)
            if elapsed>=pause_end:break
            chunk=min(.1,pause_end-elapsed)
            page.wait_for_timeout(chunk*1000)
            requested=elapsed+chunk
        first=False

def move(page, x, y, *, rng=random, duration=None, stop=None):
    """Move within a readable viewport; False means cancelled or unavailable."""
    stop=_remember_stop(stop)
    if stop and stop():return False
    pts = _points(page, x, y, rng, stop)
    if not pts:return False
    if duration is None:
        from .input_behavior import tempo
        settings = tempo(page)
        if settings:
            try:start = _pos.get(page, (pts[-1][0]/2, pts[-1][1]/2))
            except TypeError:start = (pts[-1][0]/2, pts[-1][1]/2)
            duration = math.dist(start, pts[-1]) / settings['mouse_px_s'] * 1000
        else:duration = rng.uniform(250,650)
    else:duration = _number(duration)
    if duration<0:raise ValueError('Negative Bewegungsdauer ist nicht erlaubt.')
    for px, py in pts:
        if stop and stop():return False
        # Embedded windows and mobile orientation can change during a path.
        viewport=_viewport(page,stop)
        if viewport is None:return False
        px,py=_clamp((px,py),viewport)
        if stop and stop():return False
        page.mouse.move(px, py)
        try:_pos[page]=(px,py)
        except TypeError:pass
        if not _wait_page(page,duration/len(pts),stop):return False
    return True

def scroll(page, total=1200, *, rng=random, stop=None):
    """Send bounded wheel deltas; True does not confirm scrolling or new content.

    The caller must check its content/readiness condition after the gesture.
    """
    total=_number(total);direction=-1 if total<0 else 1
    if abs(total)>MAX_SCROLL_DISTANCE:raise ValueError('Scrollstrecke pro Aufruf ist zu groß.')
    remaining=abs(total)
    while remaining>0:
        if stop and stop():return False
        step=min(remaining,rng.randint(180,420))
        page.mouse.wheel(0,direction*step)
        remaining-=step
        if not _wait_page(page,max(100,min(1500,rng.lognormvariate(6.1,.45))),stop):return False
    return True

def warmup(page, url="https://www.google.com", dwell=2500, *, rng=random, stop=None):
    dwell=_number(dwell)
    if dwell<0:raise ValueError('Negative Aufwärmzeit ist nicht erlaubt.')
    if stop and stop():return False
    try:
        page.goto(url, wait_until="domcontentloaded")
    except Exception:
        return False
    if not _wait_page(page,rng.randint(min(1200,int(dwell)),int(dwell)),stop):return False
    viewport=_viewport(page,stop)
    if viewport is None:return False
    if not move(page,rng.uniform(.2,.8)*viewport[0],rng.uniform(.2,.8)*viewport[1],rng=rng,stop=stop):return False
    return scroll(page,rng.randint(300,800),rng=rng,stop=stop)
