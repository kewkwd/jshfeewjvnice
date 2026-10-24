"""Registration form evidence stays in memory and is checked before dispatch."""
import secrets
from urllib.parse import urlsplit
import weakref

from .errors import FinalError

_SESSIONS = weakref.WeakKeyDictionary()
HOSTS = {
    'web.de': {'register.web.de'}, 'gmx.net': {'register.gmx.net'},
    'gmail.com': {'accounts.google.com'}, 'google': {'accounts.google.com'},
    'youtube': {'accounts.google.com'}, 'outlook.com': {'signup.live.com'},
    'instagram': {'instagram.com', 'www.instagram.com'}, 'x': {'x.com', 'www.x.com'},
    'reddit': {'reddit.com', 'www.reddit.com'}, 'trustpilot': {'trustpilot.com', 'www.trustpilot.com'},
    'tiktok': {'tiktok.com', 'www.tiktok.com'},
}


def bind(page, provider):
    if provider not in HOSTS:
        raise FinalError('Für diesen Anbieter fehlt die geprüfte Zuordnung der Registrierungsseiten.')
    _SESSIONS[page] = (frozenset(HOSTS[provider]), '_pw_form_' + secrets.token_hex(16))


def _context(page):
    session = _SESSIONS.get(page)
    try:
        parts = urlsplit(str(page.url))
        valid = (session is not None and parts.scheme == 'https' and parts.hostname in session[0]
                 and parts.port in (None, 443) and parts.username is None and parts.password is None)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise FinalError('Die erwartete Registrierungsseite ist nicht geöffnet. Keine Zugangsdaten eingetragen und kein Formular abgesendet.')
    return session[1], 'https://' + parts.hostname


def require_origin(page):
    return _context(page)[0]


def remember(page, control, value, *, check=None):
    key, origin = _context(page)
    if check:check()
    try:
        result = control.evaluate('''(el, data) => {
          const view = el.ownerDocument.defaultView;
          if (view.location.origin !== data.origin || view !== view.top) return false;
          if (!view[data.key]) Object.defineProperty(view, data.key, {value: {fields: new Map(), sent: new Set()}});
          const read = el => el.isContentEditable ? el.innerText :
            (el.matches('[role="combobox"]') && !('value' in el) ?
             (el.getAttribute('aria-valuetext') || el.innerText).trim() : el.value);
          if (!el.isConnected || read(el) !== data.value) return false;
          view[data.key].fields.set(el, data.value);
          return true;
        }''', {'key': key, 'value': str(value), 'origin': origin}, timeout=1500)
    except Exception:
        if check:check()
        raise FinalError('Die Eingabe konnte nicht für die Abschlussprüfung festgehalten werden. Bitte den Formularstand prüfen.') from None
    if result is not True:
        raise FinalError('Das Eingabefeld wurde zwischenzeitlich verändert. Kein Weiter-Klick ausgeführt.')
    if check:check()


_SUBMIT = r'''(el, data) => {
 const doc = el.ownerDocument, view = doc.defaultView;
 if (view !== view.top || view.location.origin !== data.origin) return 'origin';
 const target = el.closest('button,input[type="submit"],input[type="image"],[role="button"]') || el;
 const label = (target.innerText || target.getAttribute('aria-label') || target.value || '').trim().toLowerCase();
 const nativeSubmit = target.matches('button,input') && ['submit','image'].includes(target.type);
 const step = /^(weiter|next|fortfahren|continue|registrieren|sign up|absenden|submit|senden|send|fertig|done|bestätigen|confirm|anmelden|log in|jetzt starten|konto erstellen|create account|kostenlos registrieren)$/.test(label);
 if (data.navigate) return 'navigation';
 const scope = target.form || target.closest('form,[role="dialog"],dialog,main,[role="main"]') || doc.body;
 const visible = node => {
   const r = node.getBoundingClientRect(), s = view.getComputedStyle(node);
   return node.isConnected && r.width > 0 && r.height > 0 && s.visibility === 'visible' && s.display !== 'none' && !node.closest('[hidden],[aria-hidden="true"],[inert]');
 };
 const controls = [...new Set([...scope.querySelectorAll('input,textarea,select,[contenteditable="true"],[role="combobox"]'),
     ...(target.form ? [...target.form.elements] : [])])]
   .filter(node => node.matches('input,textarea,select,[contenteditable="true"],[role="combobox"]') &&
     visible(node) && !node.disabled && !node.readOnly && !['hidden','submit','button','reset','image'].includes(node.type));
 if (!nativeSubmit && !step && !controls.length) return 'navigation';
 const state = view[data.key];
 const read = node => node.isContentEditable ? node.innerText :
   (node.matches('[role="combobox"]') && !('value' in node) ? (node.getAttribute('aria-valuetext') || node.innerText).trim() : node.value);
 for (const node of controls) {
   if ((node.validity && !node.validity.valid) || node.getAttribute('aria-invalid') === 'true') return 'invalid';
   if (['checkbox','radio'].includes(node.type)) continue;
   if (!state || !state.fields.has(node)) return 'unknown';
   if (read(node) !== state.fields.get(node)) return 'changed';
 }
 const errors = [...scope.querySelectorAll('[aria-invalid="true"],[role="alert"]')].filter(visible);
 if (errors.some(node => (node.innerText || '').trim() || node.getAttribute('aria-invalid') === 'true')) return 'invalid';
 if ([...scope.querySelectorAll('*')].some(node => node.shadowRoot && visible(node))) return 'unknown';
 // Field names and headings identify the stage; input contents never enter
 // this one-use key. Do not include changing validation or spinner text.
 const shape = controls.map(node => [node.tagName,node.type,node.id,node.name,node.getAttribute('aria-label')]);
 const headings = [...scope.querySelectorAll('h1,h2,[role="heading"]')].filter(visible).map(node => node.innerText);
 const aliasReady = ['register.web.de','register.gmx.net'].includes(view.location.hostname) &&
   scope.matches('form#alias-step') && !!scope.querySelector('.alias-step__form-element--available input#alias');
 const mark = JSON.stringify([view.location.pathname,target.id,label,shape,headings,aliasReady]);
 if (!state) {
   Object.defineProperty(view, data.key, {value: {fields: new Map(), sent: new Set([mark])}});
 } else {
   if (state.sent.has(mark)) return 'sent';
   state.sent.add(mark);
 }
 return 'ready';
}'''


def before_click(page, control, *, navigate=False, check=None):
    key, origin = _context(page)
    if check:check()
    try:
        result = control.evaluate(_SUBMIT, {'key': key, 'navigate': navigate,
            'origin': origin}, timeout=1500)
    except Exception:
        if check:check()
        raise FinalError('Der aktuelle Formularstand ist nicht sicher lesbar. Kein weiterer Klick ausgeführt.') from None
    if result not in ('ready', 'navigation'):
        messages = {'invalid': 'Die Webseite meldet eine ungültige oder fehlende Angabe.',
                    'unknown': 'Ein sichtbares Formularfeld ist noch nicht eindeutig zugeordnet.',
                    'changed': 'Ein eingetragener Wert wurde zwischenzeitlich verändert.',
                    'sent': 'Dieser Formularschritt wurde bereits abgesendet. Seine Antwort ist noch zu prüfen.'}
        raise FinalError(messages.get(result, 'Die erwartete Registrierungsseite ist nicht sicher bestätigt.')
                         + ' Der vorhandene Stand bleibt zur Prüfung erhalten; kein weiterer Klick ausgeführt.')
    if check:check()


def required(result, label):
    if not result:
        raise FinalError(label + ' konnte nicht bestätigt werden. Dieser Schritt wurde nicht abgesendet; bitte den Formularstand prüfen.')
    return result
