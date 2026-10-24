REGISTER_URL = 'https://www.reddit.com/register/'
SEL = {'email': ['input[name="email"]', '#regEmail', 'input[type="email"]'],
       'user': ['input[name="username"]', '#regUsername'],
       'pass': ['input[name="password"]', '#regPassword'],
       'submit': ['button[type="submit"]', 'button[data-testid="submit-button"]']}
CAPTCHA = ['iframe[title*="hCaptcha" i]', 'iframe[src*="hcaptcha" i]', '.h-captcha']

def run(page, ctx):
    if not ctx.fill(SEL['email'], ctx.email):
        raise RuntimeError('Reddit: E-Mail-Feld nicht gefunden')
    if not ctx.fill(SEL['user'], ctx.username):
        raise RuntimeError('Reddit: Benutzernamen-Feld nicht gefunden')
    if not ctx.fill(SEL['pass'], ctx.password):
        raise RuntimeError('Reddit: Passwort-Feld nicht gefunden')
    ctx.warte(600)
    if not ctx.click(SEL['submit']):
        raise RuntimeError('Reddit: Anmelde-Schaltfläche nicht gefunden')
    ctx.warte(3000)
    if any(page.query_selector(s) for s in CAPTCHA):
        ctx.melden('Reddit zeigt eine Sicherheitsabfrage – bitte im Fenster bestätigen.')
    return ctx.warte_auf_cookies(still=False)
