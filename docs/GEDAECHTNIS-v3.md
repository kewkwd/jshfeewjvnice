# Gedächtnis-Delta v3 — Stand nach Punkt 4

Marker: [F] = im Code belegt · [A] = unsere Entscheidung · [?] = offen

## 1. Zweck (nicht verhandelbar)
[A] Der Interaktionsgraph darf nicht wie ein Stern um den Influencer aussehen.
Konten müssen VOR dem Like so aussehen, als hätten sie den Influencer organisch
im FYP. Personas = Themen-Cluster, Tiers = Gewichtung im Netz. Seeding nur für
eine Teilmenge, der Rest driftet organisch. Treffer werden geloggt, gute
Drift-Konten werden befördert.

## 2. Fertig und im Repo
[F] socialscraper/personas.py           Persona-Modell, Zuweisung, Jaccard-armes Sampling
[F] socialscraper/fyp/__init__.py       Schema + verbuche()
[F] socialscraper/fyp/konsum.py         FYP-Sitzung im Browser
[F] socialscraper/fyp/seed.py           FYP-Korridor anlegen (nur Teilmenge)
[F] socialscraper/fyp/monitor.py        neue Videos der Persona-Kanäle
[F] socialscraper/commands/personas.py  CLI
[F] socialscraper/commands/fyp.py       CLI
[F] checks/watchtime_check.py           Vorabtest Watch-Time
[F] socialscraper/store.py              _migrate ruft personas/fyp ensure_schema auf
[F] Fan-out + Zwei-Wellen-Kurve und Fingerprint-Diversität waren vorher schon drin

## 3. Neue Tabellen / Spalten
[A] Tabellen: personas, persona_kanaele, fyp_videos, fyp_treffer, konsum_log
[A] accounts: persona_id, tier, folgt_influencer, fyp_status, warmth_score,
    letzter_konsum, konsum_sitzungen

## 4. Verträge (nicht ändern — store._migrate hängt daran)
personas.ensure_schema(db)
personas.anlegen(db, name, *, influencer, kanaele, beschreibung=None,
                 influencer_gewicht=1.0, verhalten=None, now=None)
personas.aktive(db, platform='tiktok')
personas.persona_von(db_path, identity, platform='tiktok')
personas.zuweisen(db_path, platform='tiktok', *, nur_ohne=True)
fyp.ensure_schema(db)
fyp.verbuche(db_path, identity, *, schritte, treffer, now=None)

## 5. Warum so und nicht anders
[A] Persona-Zuweisung deterministisch je Identity (Seed aus der Identity)
    → stabil über Neustarts, keine Neuverteilung bei jedem Lauf.
[A] Jaccard-armes Kanal-Sampling → Personas überlappen schwach → Graph verwischt.
[A] Tier-Gewichte: kern 0.20 / sekundaer 0.40 / rand 0.40.
[A] Seeding nur für eine Teilmenge; der Rest muss den Influencer organisch erwischen.

## 6. Vorabtest bestanden
[F] checks/watchtime_check.py: headless BESTANDEN (6.0s, 14 Telemetrie-Requests),
    headful BESTANDEN (4.0s, 10 Requests).
[F] Verdikt: Headless trägt Watch-Time → FYP-Konsum läuft headless.
    Wichtig, weil eine Maschine (Ryzen 7 1700, 16 GB) sonst keine hunderte
    Fenster gleichzeitig stemmen kann.

## 7. Offen
[?] Timing/Burst (6-8 Aktionen pro Minute, Worker-Pool)
[?] Warm-up-Vertiefung (Tapering, Text-Vielfalt, Interessenprofil)
[?] AVD-Profil-Varianz + Registrierungspipeline