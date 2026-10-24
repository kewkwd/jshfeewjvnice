"""Invalidate generated credentials on manual edits; never collect replacement values."""
import inspect
import json
import secrets


_WATCH = r'''({key, binding, selector, index, expected, origin, check, identity}) => {
    if (location.origin !== origin || window.top !== window) return 'unavailable';
    let state = window[key];
    if (!state) {
        state = {records: new Map(), changed: false, pending: Promise.resolve()};
        Object.defineProperty(window, key, {value: state});
        const invalidate = () => {
            if (state.changed) return;
            state.changed = true;
            // Only a change signal crosses the browser boundary, never an input value.
            state.pending = Promise.resolve(window[binding]()).catch(() => {});
        };
        const checkField = target => {
            if (!(target instanceof HTMLInputElement)) return;
            for (const record of state.records.values()) {
                const field = document.querySelectorAll(record.selector)[record.index];
                const identity = field instanceof HTMLInputElement ? [field.type,field.id,field.name] : null;
                if (field === target && JSON.stringify(identity) === JSON.stringify(record.identity) && target.value !== record.expected) invalidate();
            }
        };
        document.addEventListener('input', event => checkField(event.composedPath()[0]), true);
        document.addEventListener('change', event => checkField(event.composedPath()[0]), true);
        document.addEventListener('submit', () => {
            for (const record of state.records.values()) {
                const field = document.querySelectorAll(record.selector)[record.index];
                // Fields from completed earlier pages need not be present in
                // this form. A new confirmation field is not the old password.
                if (field instanceof HTMLInputElement) checkField(field);
            }
        }, true);
    }
    const current = document.querySelectorAll(selector)[index];
    const fieldIdentity = current instanceof HTMLInputElement ? [current.type,current.id,current.name] : null;
    const recordKey = JSON.stringify([selector, index, check ? fieldIdentity : identity]);
    if (check || !state.records.has(recordKey)) state.records.set(recordKey, {selector, index, expected, identity: check ? fieldIdentity : identity});
    if (check) {
        const field = document.querySelectorAll(selector)[index];
        if (!(field instanceof HTMLInputElement)) return 'unavailable';
        if (field.value !== expected) return 'changed';
    }
    return state.changed ? 'changed' : 'armed';
}'''

_FLUSH = '''async key => {
    const state = window[key];
    if (!state) return true;
    await state.pending;
    return !state.changed;
}'''


class PasswordChangeGuard:
    def __init__(self, on_change):
        self.on_change = on_change
        self.key = '_socialscraper_password_' + secrets.token_hex(12)
        self.binding = self.key + '_changed'
        self.page = None
        self.changed = False
        self.failed = False
        self.armed = False
        self.installed = set()
        self.evaluate_options = {}

    def _evaluate(self, expression, arg=None):
        return self.page.evaluate(expression, arg, **self.evaluate_options)

    def _invalidate(self):
        self.changed = True
        self.on_change()

    def watch(self, page, selector, expected):
        if self.changed or self.failed:
            return False
        try:
            if self.page is None:
                if not all(callable(getattr(page, name, None))
                           for name in ('expose_binding', 'add_init_script', 'evaluate')):
                    self.failed = True
                    return False
                self.page = page
                # Patchright otherwise evaluates in an isolated world where
                # page bindings and document-start scripts are not visible.
                if 'isolated_context' in inspect.signature(page.evaluate).parameters:
                    self.evaluate_options = {'isolated_context': False}

                def changed(source):
                    if source.get('page') is page and source.get('frame') is page.main_frame:
                        self._invalidate()

                page.expose_binding(self.binding, changed)
            if page is not self.page or not isinstance(selector, str):
                self.failed = True
                return False
            # Shared native filling returns an exact locator when a selector
            # matches hidden and visible duplicates. DOM scripts need its CSS
            # part and index separately; Playwright's nth syntax is not CSS.
            base, separator, suffix = selector.rpartition(' >> nth=')
            index = 0
            if separator:
                if not suffix.isascii() or not suffix.isdecimal():
                    self.failed = True
                    return False
                selector, index = base, int(suffix)
            origin = self._evaluate('() => location.origin')
            if not isinstance(origin, str):
                self.failed = True
                return False
            data = dict(key=self.key, binding=self.binding, selector=selector, index=index,
                        expected=expected, origin=origin, check=True)
            data['identity'] = self._evaluate('''({selector,index}) => {
                const field = document.querySelectorAll(selector)[index];
                return field instanceof HTMLInputElement ? [field.type,field.id,field.name] : null;
            }''', {'selector': selector, 'index': index})
            if not isinstance(data['identity'], list) or len(data['identity']) != 3:
                self.failed = True
                return False
            result = self._evaluate(_WATCH, data)
            if result == 'changed':
                self._invalidate()
                return False
            if result != 'armed':
                self.failed = True
                return False
            # Restore the same field guard if the registration document reloads.
            # It is inert on other origins and in frames.
            marker = (origin, selector, index, tuple(data['identity']))
            if marker not in self.installed:
                data['check'] = False
                page.add_init_script(script='(' + _WATCH + ')(' + json.dumps(data) + ')')
                self.installed.add(marker)
            self.armed = True
            return True
        except Exception:
            # Browser exceptions can include script arguments. Keep them private
            # and refuse credential proof when tracking could not be installed.
            self.failed = True
            return False

    def valid(self):
        if not self.armed or self.changed or self.failed:
            return False
        try:
            if self._evaluate(_FLUSH, self.key) is not True:
                self._invalidate()
        except Exception:
            self.failed = True
        return not self.changed and not self.failed
