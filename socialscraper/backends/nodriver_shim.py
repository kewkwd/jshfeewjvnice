import asyncio
import json


class _Mouse:
    def __init__(self, runner, tab):
        self._r, self._t = runner, tab
        self._x = self._y = 0
        self._buttons = 0

    async def _dispatch(self, event, **options):
        from nodriver import cdp
        return await self._t.send(cdp.input_.dispatch_mouse_event(
            type_=event, x=self._x, y=self._y, buttons=self._buttons, **options))

    def move(self, x, y):
        self._x, self._y = x, y
        return self._r.run(self._dispatch("mouseMoved"))

    def move_path(self, points):
        async def walk():
            for self._x, self._y in points:
                await self._dispatch("mouseMoved")
        return self._r.run(walk())

    def down(self):
        from nodriver import cdp
        self._buttons = 1
        return self._r.run(self._dispatch("mousePressed", button=cdp.input_.MouseButton.LEFT,
                                          click_count=1))

    def up(self):
        from nodriver import cdp
        self._buttons = 0
        return self._r.run(self._dispatch("mouseReleased", button=cdp.input_.MouseButton.LEFT,
                                          click_count=1))

    def click(self, x=None, y=None):
        if x is not None or y is not None:
            if x is None or y is None:
                raise ValueError("Mausklick benötigt beide Koordinaten")
            self.move(x, y)
        self.down()
        return self.up()

    def wheel(self, dx, dy):
        return self._r.run(self._dispatch("mouseWheel", delta_x=dx, delta_y=dy))


class TabShim:
    def __init__(self, runner, tab):
        self._r, self._t = runner, tab
        self.mouse = _Mouse(runner, tab)

    @property
    def url(self):
        return self._t.url

    async def _evaluate(self, expression, arg=None):
        from nodriver import cdp
        # Playwright invokes function expressions; CDP evaluates their source only.
        expression = expression.strip().rstrip(";")
        code = ("(() => { const value = (" + expression + "); "
                "return typeof value === 'function' ? value(" + json.dumps(arg) + ") : value; })()")
        remote, error = await self._t.send(cdp.runtime.evaluate(
            expression=code, await_promise=True, return_by_value=True))
        if error is not None:
            raise RuntimeError("JavaScript-Auswertung im Browser fehlgeschlagen")
        return remote.value if remote is not None else None

    def evaluate(self, expression, arg=None):
        return self._r.run(self._evaluate(expression, arg))

    async def _wait_loaded(self, wait_until):
        if wait_until in (None, "commit"):
            return
        if wait_until not in ("domcontentloaded", "load"):
            raise ValueError(f"Nicht unterstützter Ladezustand: {wait_until}")
        while True:
            state = await self._evaluate("document.readyState")
            if state == "complete" or (wait_until == "domcontentloaded" and state == "interactive"):
                return
            await asyncio.sleep(0.05)

    def goto(self, url, wait_until="load", timeout=30000, **options):
        async def navigate():
            await self._t.get(url)
            await self._wait_loaded(wait_until)
        return self._r.run(asyncio.wait_for(navigate(), None if timeout == 0 else timeout / 1000))

    def reload(self, wait_until="load", timeout=30000, **options):
        async def refresh():
            await self._t.reload()
            await self._wait_loaded(wait_until)
        return self._r.run(asyncio.wait_for(refresh(), None if timeout == 0 else timeout / 1000))

    def wait_for_timeout(self, ms):
        return self._r.run(asyncio.sleep(ms / 1000))

    def wait_for_selector(self, css, timeout=15000, **options):
        element = self._r.run(self._t.select(css, timeout=timeout / 1000))
        if element is None:
            raise TimeoutError("Browser-Element wurde nicht gefunden")
        return element

    def click(self, selector, timeout=30000, **options):
        async def click_element():
            element = await self._t.select(selector, timeout=timeout / 1000)
            if element is None:
                raise TimeoutError('Anmelde-Schaltfläche wurde nicht gefunden')
            return await element.click()
        return self._r.run(asyncio.wait_for(click_element(), None if timeout == 0 else timeout / 1000))

    def fill(self, selector, value, timeout=30000, **options):
        self.wait_for_selector(selector, timeout=timeout)
        filled = self.evaluate("""([selector, value]) => {
            const element = document.querySelector(selector);
            if (!element) return false;
            element.focus();
            if (element.isContentEditable) {
                element.textContent = value;
            } else {
                const prototype = element.tagName === 'TEXTAREA'
                    ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(prototype, 'value');
                if (!setter) return false;
                setter.set.call(element, value);
            }
            element.dispatchEvent(new Event('input', {bubbles: true}));
            element.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        }""", [selector, str(value)])
        if not filled:
            raise RuntimeError("Browser-Eingabefeld konnte nicht ausgefüllt werden")

    def scroll_down(self, px):
        return self.mouse.wheel(0, px)

    def close(self):
        return self._r.run(self._t.close())

