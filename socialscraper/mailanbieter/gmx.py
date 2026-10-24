from . import unitedinternet
SIGNUP_URL, DOMAENE = 'https://register.gmx.net/?product_id=gmxnetFree', 'gmx.net'
def run(page, ctx):
    return unitedinternet.run(page, ctx)
def wunsch_adresse(vorname, nachname, kennung):
    return unitedinternet.wunsch_adresse(vorname, nachname, kennung, domaene=DOMAENE)
