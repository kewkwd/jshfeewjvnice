# Socialscraper mit PULSE verbinden

Stand: 11.10.2026. Für Entwickler und für Marc.

**Wichtig vorab:** PULSE hat aktuell nur die Oberfläche (UI-Phase, Beispieldaten). Es gibt dort noch keine Datenanbindung. Dieses Dokument beschreibt, was Socialscraper bereitstellt. Das Einbauen in PULSE ist ein späterer Backend-Auftrag; PULSE wurde hier nicht angefasst.

Es gibt zwei Wege. Beide liefern dieselben Daten, die der Beispiel-Client `docs/pulse-client.js` mit derselben Antwortform ansprechen kann.

| | Weg 1: lokal | Weg 2: Cloud |
|---|---|---|
| Was | Socialscraper öffnet eine kleine, nur lesende Schnittstelle auf deinem PC | Socialscraper sendet Kopien der Daten in eine Cloud-Datenbank (Supabase) |
| Wann sinnvoll | Entwicklung, PULSE läuft auf demselben PC | PULSE ist im Netz erreichbar, oder ein externer Server sammelt die Daten |
| Was PULSE braucht | Adresse + Token | Cloud-Adresse + **Lese**-Schlüssel |
| Geprüft | Echt getestet (echte HTTP-Anfragen gegen Wegwerf-Server) | Nur gegen einen selbstgebauten Fake-Server getestet, **gegen echte Cloud nicht geprüft** |

---

## Weg 1: Lokale Schnittstelle

### Einrichten

1. Im Fenster unter Einstellungen die Schnittstelle einschalten (oder Datei `data/integration.json` bearbeiten, siehe unten).
2. Token erzeugen lassen. Er steht danach in `data/integration.json`. Auf der Kommandozeile geht es auch ohne Fenster:
   ```
   python -m socialscraper serve --show-token
   ```
   Das startet die Schnittstelle im Vordergrund (Beenden mit Strg+C). Ohne `--show-token` zeigt das Programm nur, wo der Token steht.
3. Prüfen: `http://127.0.0.1:8765/v1/health` im Browser öffnen. Es muss `"ok": true` erscheinen.
4. Der Hintergrunddienst soll die Schnittstelle beim Start mitstarten, wenn sie aktiviert ist (Funktion `api.start_if_enabled`; Einbau im Dienst ist Sache des Planer-/Dienst-Teams).
5. Für PULSE im Browser (z. B. `http://localhost:5173`) diese Herkunftsadresse in `cors_origins` eintragen, sonst blockiert der Browser die Antworten.

### Die Datei `data/integration.json`

```json
{
  "schema_version": 1,
  "api": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 8765,
    "token": "…wird erzeugt…",
    "cors_origins": ["http://localhost:5173"]
  },
  "sync": {
    "kind": "none",
    "url": "", "key": "", "auto": true,
    "last_push": null, "last_pull": null, "last_error": null
  }
}
```

Die Datei enthält Geheimnisse (Token, Cloud-Schlüssel). Sie nie weitergeben, nie in ein Repository legen, nie im Chat zeigen. Eine beschädigte Datei wird ignoriert (Schnittstelle und Sync bleiben aus); beim nächsten Speichern bleibt eine Kopie als `integration.json.defekt` erhalten.

### Anmeldung

Jede Anfrage außer `/v1/health` braucht den Header

```
Authorization: Bearer <Token>
```

Der Token gehört nur in den Header, nie in die Adresse. Ohne oder mit falschem Token antwortet die Schnittstelle mit 401.

### Gemeinsames Format

* Alle Antworten sind JSON (UTF-8) und tragen `"schema_version": 1`. Felder werden nur hinzugefügt, nie umbenannt, solange die Version 1 bleibt.
* Zeiten sind UTC im Format `2026-10-11T04:15:33Z`.
* Listen: `{ "items": [...], "total": N, "limit": L, "offset": O }`. `limit` höchstens 500 (Standard 100), `offset` ab 0.
* Fehler: `{ "schema_version": 1, "error": { "code": "...", "message": "..." } }` mit den Codes 400 `bad_request`, 401 `unauthorized`, 403 `bad_host`/`forbidden_origin`, 404 `not_found`, 405 `method_not_allowed`, 500 `internal`, 503 `no_database`/`db_busy`/`db_old`/`db_unreadable` (bei `db_busy` und `db_unreadable` steht `Retry-After` im Antwortkopf: kurz warten und wiederholen). Nie ein Stacktrace.
* Unbekannte oder doppelte Parameter ergeben 400 (so fallen Tippfehler auf).
* `uid` eines Videos ist `plattform:video_id` (z. B. `tiktok:7301234567`). In der Adresse **URL-kodieren**: `tiktok%3A7301234567`.
* `platform` ist einer von `tiktok`, `instagram`, `youtube`, `reddit`, `x`. Bei **Reddit** ist `video_id` die Beitrags-ID ohne `t3_`, `view_count` ist immer `null` (Reddit zeigt keine Aufrufe), `like_count` sind die Punkte, `caption` ist Titel und Text. Bei **X** ist `video_id` die Beitragsnummer, `comment_count` die Zahl der Antworten, `share_count` Weiterleitungen plus Zitate (die Aufteilung steht in `raw`: `retweet_count`, `quote_count`), und die Kommentare eines Beitrags sind seine Antworten. Ein „Video“ ist bei Reddit und X ein Beitrag; Felder, die es dort nicht gibt (`duration_s`, `music`), bleiben leer.
* Nur GET (und OPTIONS für CORS). Alles andere: 405.

### Endpunkte

Adresse der Beispiele: `http://127.0.0.1:8765`.

**`GET /v1/health`** – ohne Token erreichbar. `authenticated` zeigt, ob ein mitgeschickter Token stimmt. `database` sagt, ob die Datenbankdatei existiert.

```json
{ "schema_version": 1, "ok": true, "service": "socialscraper", "authenticated": true,
  "server_time": "2026-10-11T02:52:15Z", "database": true }
```

**`GET /v1/stats`**

```json
{ "schema_version": 1,
  "videos":   { "total": 2, "by_platform": { "tiktok": 2 } },
  "comments": { "total": 4, "by_platform": { "tiktok": 4 } },
  "snapshots": 2, "groups": 1, "accounts": 2,
  "last_fetched_at": "2026-10-02T10:00:00Z" }
```

**`GET /v1/videos`** – Filter: `platform` (`tiktok|instagram|youtube|reddit|x`), `group` (Name oder Zahlen-ID), `q` (Suche in Beschreibung, Autor, Hashtags; höchstens 200 Zeichen), `since` (zuletzt abgerufen seit; ISO-Zeit oder Datum), `sort` (`fetched_at|first_seen_at|created_at|view_count|like_count|comment_count|share_count`, Standard `fetched_at`), `order` (`asc|desc`, Standard `desc`), `limit`, `offset`.

```json
{ "schema_version": 1,
  "items": [ {
    "uid": "tiktok:2", "platform": "tiktok", "video_id": "2", "url": "https://t.example/2",
    "author": "anna", "author_id": null, "caption": "Video 2", "created_at": null, "duration_s": 2.0,
    "view_count": 200, "like_count": null, "comment_count": null, "share_count": null, "save_count": null,
    "hashtags": ["a", "b"], "mentions": [], "music": null, "language": null, "thumbnail": null,
    "first_seen_at": "2026-10-11T02:52:15Z", "fetched_at": "2026-10-02T10:00:00Z",
    "groups": [ { "id": 1, "name": "Sammlung", "color": "#2563eb" } ]
  } ],
  "total": 2, "limit": 1, "offset": 0 }
```

Anmerkung: `created_at` ist die Zeitangabe der Plattform, so gut sie erkannt wurde, und kann `null` sein. Die Rohdaten (`raw`) sind in der Liste nicht enthalten.

**`GET /v1/videos/{uid}`** – ein Video im selben Format unter `"item"`. Mit `?include=raw` kommen zusätzlich die Rohdaten der Plattform (`raw`), sonst nicht. 404, wenn es das Video nicht gibt.

**`GET /v1/videos/{uid}/comments`** – Filter `q`, `since` (Kommentar zum ersten Mal gesehen seit), `parent` (`none` = nur Hauptkommentare, sonst die ID eines Kommentars = dessen Antworten), `sort` (`created_at|first_seen_at|like_count|reply_count`), `order`, `limit`, `offset`.

```json
{ "schema_version": 1, "video_uid": "tiktok:1",
  "items": [ { "uid": "tiktok:1-1", "platform": "tiktok", "video_id": "1", "comment_id": "1-1",
    "text": "Text 1", "author": "fan", "author_id": null, "created_at": null, "like_count": 1,
    "reply_count": null, "parent_id": null, "is_author_reply": true, "language": null,
    "first_seen_at": "2026-10-11T02:52:15Z", "fetched_at": "2026-10-01T10:00:00Z" } ],
  "total": 2, "limit": 1, "offset": 0 }
```

**`GET /v1/videos/{uid}/history`** – Verlauf der Zahlen, ein Punkt je Abruf, älteste zuerst. Filter `since`, `limit`, `offset`.

```json
{ "schema_version": 1, "video_uid": "tiktok:1",
  "items": [ { "captured_at": "2026-10-01T10:00:00Z", "view_count": 100, "like_count": null,
               "comment_count": null, "share_count": null } ],
  "total": 1, "limit": 100, "offset": 0 }
```

**`GET /v1/comments/new?since=…`** – neue Kommentare über alle Videos, neueste zuerst. `since` ist Pflicht. Optional `platform`, `limit`, `offset`. Zusätzlich zu den Kommentarfeldern: `video_uid`, `video_url`, `video_author`.

**`GET /v1/groups`** – `items` mit `id`, `name`, `color`, `note`, `created_at`, `video_count`. Welche Videos dazugehören, steht in `groups` jedes Videos bzw. über den Filter `group` bei `/v1/videos`.

**`GET /v1/accounts`** – `id`, `platform`, `label`, `identity`, `country`, `is_default`, `note`, `created_at` und der Stand der Konten-Rotation: `rotation_enabled` (Konto nimmt an der Rotation teil), `status` (`bereit`, `abkuehlung`, `tageslimit`, `keine_anmeldung`), `cooldown_until` und `cooldown_reason` (nur bei `abkuehlung`, sonst `null`), `uses_today` (Abrufe heute), `daily_limit` (`null` = unbegrenzt), `use_count`, `last_used_at` und `has_proxy` (hat das Konto einen festen Proxy – **nur ja/nein**, nie die Adresse oder Zugangsdaten). Nie Cookies, Passwörter, Proxys oder Sitzungsdaten.

#### Videolänge und ganze Kanäle

**Länge:** `duration_s` (Sekunden als Zahl, `null` = unbekannt – nie 0 als Platzhalter) steht in jedem Video; dazu `duration_class` (`short` bis 60 s, `medium` bis 10 min, `long` darüber, `null` ohne Länge; die Grenzen stellt man in den Einstellungen ein). `GET /v1/stats` hat `duration` = `{ "avg_s", "min_s", "max_s", "measured", "unmeasured" }` (Mittelwert, kürzestes, längstes; nur gemessene Werte, `unmeasured` = Videos ohne Länge). `GET /v1/groups` hat je Gruppe `duration` = `{ "avg_s", "min_s", "max_s" }`.

**`GET /v1/channels`** (und `GET /v1/channels/{id}`) – ganze Kanäle der Automatik: `id`, `platform`, `channel` (Kanalwert), `name`, `state` (`queued`, `listing`, `fetching`, `paused`, `done`, `watching`), `enabled`, `pace_profile` (`careful`, `normal`, `fast`), `scope`, `scope_value`, `group`, `progress` = `{ "total", "done", "skipped", "pending", "running", "failed" }`, `platform_total` (Gesamtzahl laut Plattform, falls bekannt), `list_complete`, `note` (ehrlicher Satz zur Vollständigkeit der Liste), `paused_until` und `pause_reason` (nur im Zustand `paused`), `next_run_at`, `last_run_at`, `observe_new`, `quiet_hours`, `created_at`. Nie Proxys, Zugangsdaten, Cookies oder Schlüssel. In der Cloud-Variante gibt es keine Kanäle.

#### Stimmung (KI-Einschätzung)

Die Stimmung der Kommentare bestimmt Socialscraper mit der Claude-KI (Anthropic) – **auf Marcs Knopfdruck**, nie von selbst (außer er schaltet die Automatik ein). Das Ergebnis ist eine **KI-Einschätzung**: eine Schätzung eines Sprachmodells, **nicht gemessen** und keine bewiesene Ursache; die Qualität der Einstufung ist nicht geprüft. PULSE soll sie so darstellen: gemessene Werte (Aufrufe, Likes …) und diese Schätzung getrennt kennzeichnen. Jede Antwort trägt `"kind": "ki_einschaetzung"`.

**`GET /v1/sentiment`** – Verteilung und Verlauf. Filter: `platform`, `group` (Name oder Zahlen-ID; die Videos der Gruppe), `video` (`uid`), `since` und `until` (Tag des Kommentars – das Datum der Plattform, sonst der Tag der ersten Sichtung; beide Tage eingeschlossen; nur das Datum zählt), `interval` (`day` oder `week`, Standard `day`; Woche beginnt am Montag). Gezählt werden nur **gültige** Einstufungen: ändert sich der Text eines Kommentars, gilt die alte Einstufung nicht mehr.

```json
{ "schema_version": 1, "kind": "ki_einschaetzung",
  "note": "KI-Einschätzung: eine Schätzung durch ein Sprachmodell, nicht gemessen …",
  "model": "claude-haiku-5-5", "models": { "claude-haiku-5-5": 120 }, "prompt_versions": [1],
  "positive": 70, "neutral": 30, "negative": 20,
  "analyzed": 120, "total_comments": 150, "coverage": 0.8,
  "share": { "positive": 0.5833, "neutral": 0.25, "negative": 0.1667 },
  "score_avg": 0.31, "interval": "day",
  "scope": { "platform": null, "group": null, "video": null, "since": null, "until": null },
  "series": [ { "date": "2026-10-01", "positive": 5, "neutral": 2, "negative": 1, "analyzed": 8,
                "share": { "positive": 0.625, "neutral": 0.25, "negative": 0.125 }, "score_avg": 0.4 } ] }
```

`analyzed` = eingestufte Kommentare, `total_comments` = alle Kommentare im Umfang, `coverage` = `analyzed / total_comments` (`null` ohne Kommentare). `share` und `score_avg` sind `null`, solange nichts eingestuft ist. `score_avg` liegt zwischen -1 (sehr negativ) und 1 (sehr positiv). `model` ist das am häufigsten benutzte Modell. Anteile hängen von der Abdeckung ab: bei niedriger `coverage` sind sie weniger belastbar.

**Kommentare mit Stimmung:** `GET /v1/videos/{uid}/comments` und `GET /v1/comments/new` haben bei jedem Kommentar das Feld `sentiment` – `null` (noch nicht eingestuft) oder
`{ "label": "positiv", "score": 0.9, "model": "…", "prompt_version": 1, "analyzed_at": "2026-10-11T00:00:00Z", "kind": "ki_einschaetzung" }`. `label` ist `positiv`, `neutral` oder `negativ` (deutsch, wie gespeichert); in `/v1/sentiment` heißen die Zähler `positive`, `neutral`, `negative`.

#### Personen und Gruppen

Videos **und Personen** lassen sich in Gruppen einordnen: **Ersteller** (`creator`, aus `videos.author`) und **Kommentarschreiber** (`commenter`, aus `comments.author`). Eine Person ist ein Name aus den gespeicherten Daten: `author_key` ist die Autoren-Kennung (`author_id`), falls vorhanden, sonst der Anzeigename kleingeschrieben und ohne führendes @. Der Schlüssel einer Person ist `platform` + `author_key`; gleiche Namen auf zwei Plattformen sind zwei Personen. Eingeordnet wird nur durch Marcs Handlung im Fenster – nie automatisch.

**`GET /v1/groups`** – zusätzlich `creator_count` und `commenter_count` (zugeordnete Personen je Rolle) neben `video_count`.

**`GET /v1/groups/{id}/people`** – Mitglieder einer Gruppe (`{id}` Zahl oder URL-kodierter Name). Filter `role`, `limit`, `offset`. Antwort: `group` (`id`, `name`), `items` mit `role`, `platform`, `author_key`, `display_name`, `added_at`, `total`.

**`GET /v1/people`** – Personen mit Kennzahlen. Filter: `role` (`creator|commenter`; ohne Angabe beide), `platform`, `group` (Name, Zahlen-ID oder `none` = in keiner Gruppe), `q` (Name oder Kennung; Groß-/Kleinschreibung und Umlaute egal), `sort` (`name|videos|views|likes|comments|first|last|positive|negative|platform|seen`), `order`, `limit`, `offset`, `first_seen_since`, `known`.

```json
{ "schema_version": 1, "first_seen_since": null,
  "items": [ { "role": "commenter", "platform": "tiktok", "author_key": "max", "display_name": "Max",
      "first_seen_at": "2026-10-01T10:00:00Z", "is_new": null,
      "videos": 2, "likes": 6, "comments": 3,
      "first_activity": "2026-10-01", "last_activity": "2026-10-03",
      "sentiment": { "kind": "ki_einschaetzung", "analyzed": 3, "positive": 2, "neutral": 0, "negative": 1 },
      "groups": [ { "id": 1, "name": "Beobachtung", "color": "#2563eb" } ] } ],
  "total": 4, "limit": 100, "offset": 0 }
```

Ersteller haben statt `comments`, `first_activity`, `last_activity`, `sentiment` das Feld `views` (Summe der Aufrufe ihrer Videos); `videos` und `likes` zählen ihre Videos. Bei Kommentarschreibern ist `videos` die Zahl der Videos, unter denen sie kommentiert haben.

**Neue Autoren:** `first_seen_at` ist die erste Sichtung der Person in der Datenbank (kleinster `first_seen_at` ihrer Kommentare bzw. Videos). `first_seen_since=<Zeit>` legt fest, ab wann jemand „neu“ heißt; dann steht `is_new` bei jeder Person auf `true` (zum ersten Mal seit dann gesehen) oder `false`. `known=false` liefert nur die neuen, `known=true` nur die bereits bekannten Personen; `known` braucht `first_seen_since` (sonst 400). Ohne `first_seen_since` ist `is_new` immer `null`.

**`GET /v1/stats?since=<Zeit>`** – mit `since` kommt zusätzlich `new_authors`: `{ "since": "…", "creators": N, "commenters": M }` (Personen, die erstmals seit dann gesehen wurden).

**Kommentarendpunkte:** `commenter_group=<Name oder Zahl>` (bei `/v1/videos/{uid}/comments` und `/v1/comments/new`) liefert nur Kommentare von Kommentarschreibern, die der Gruppe angehören. `/v1/comments/new` hat bei jedem Kommentar `author_new`: sein Autor war vor `since` noch nie in der Datenbank.

**Datenschutz-Hinweis:** Das Gruppieren von Kommentarschreibern ist eine Auswertung von Personen. Es werden nur öffentlich sichtbare Namen und Kennungen verwendet, die ohnehin in der Datenbank stehen; Marc entscheidet, wofür sie genutzt werden. PULSE-Regel: personenbezogene Auswertungen nur bewusst und unter Beachtung des Datenschutzes.

### Beispiel

```js
import { createLocalClient } from "./pulse-client.js";
const api = createLocalClient({ baseUrl: "http://127.0.0.1:8765", token: DEIN_TOKEN });
const { items } = await api.listVideos({ platform: "tiktok", limit: 20 });
const verlauf = await api.history(items[0].uid);      // uid wird intern URL-kodiert
const neu = await api.newComments("2026-10-10T00:00:00Z");
```

Mit curl:

```
curl -H "Authorization: Bearer DEIN_TOKEN" "http://127.0.0.1:8765/v1/videos?limit=5"
```

---

## Weg 2: Cloud-Datenbank (Supabase)

Auf diesem Weg sendet Socialscraper Kopien der Daten in eine Cloud-Datenbank. PULSE liest dann direkt von dort, und ein externer Server (Cookies von hier hochladen, siehe Server-Betrieb) kann die Daten sammeln, während dein PC aus ist. **Gegen eine echte Cloud wurde das bisher nicht geprüft**; getestet ist die Logik gegen einen eigenen Fake-Server (Upsert, Pakete, 401, 429, Netzabbruch, Teil-Erfolg).

### Einrichten, Schritt für Schritt

1. Bei Supabase ein Projekt anlegen (kostenloser Plan genügt zum Ausprobieren).
2. Im Supabase-Dashboard unter **SQL Editor** den Inhalt von `deploy/supabase_schema.sql` ausführen. Das legt die Tabellen an und schaltet Row-Level-Security ein: Der Lese-Schlüssel darf nur lesen. Mehrfaches Ausführen schadet nicht.
3. Unter **Project Settings > API** die Projekt-Adresse (`https://….supabase.co`) und den **service_role**-Schlüssel kopieren. Dieser Schlüssel darf schreiben und umgeht die Zugriffsregeln: **Er ist ein Geheimnis.**
4. Im Fenster unter Einstellungen die Cloud-Datenbank eintragen (Art: Supabase, Adresse, Schlüssel), dann **Verbindung testen**. Oder per Kommandozeile, nachdem die Daten in `data/integration.json` stehen (`sync.kind` = `supabase`, `sync.url`, `sync.key`):
   ```
   python -m socialscraper sync test
   python -m socialscraper sync push
   python -m socialscraper sync pull
   ```
5. Ist „automatisch senden“ an, stößt der Hintergrunddienst nach jedem Lauf einen Push an. Fehler dabei brechen den Lauf nie ab; sie stehen als `last_error` in der Konfiguration und im Protokoll.

### Was passiert beim Senden und Holen

* **Push** schickt nur Zeilen seit dem letzten Stand (Wasserstand je Tabelle in `data/sync-state.json`), in Paketen zu 500 Zeilen, als Upsert (`Prefer: resolution=merge-duplicates`, `on_conflict`). Zweimal senden ändert nichts. Bei Netzfehler, 429 oder 5xx wird bis zu viermal mit Wartezeit wiederholt (ein `Retry-After` der Cloud wird beachtet, höchstens 30 Sekunden). Bei einem Abbruch bleibt der bis dahin erreichte Stand erhalten, der nächste Lauf macht dort weiter. `sync push --full` sendet alles neu.
* **Pull** holt Cloud-Zeilen und führt sie lokal zusammen: `first_seen_at` bleibt lokal unverändert, bei Videos und Kommentaren gewinnt die Zeile mit der neueren `fetched_at`, Verlaufspunkte und Gruppenzuordnungen werden ergänzt. So erscheinen Daten, die ein externer Server gesammelt hat, im Fenster.
* Synchronisiert werden `videos`, `comments`, `video_snapshots`, `groups`, `group_videos` sowie `comment_sentiment` (KI-Einschätzung je Kommentar) und `group_people` (Personen in Gruppen). **Konten, Cookies, Browserprofile und Ziele werden nie hochgeladen.**
* `comment_sentiment` und `group_people` sind neuer: fehlen sie in einer älteren Cloud-Datenbank, werden sie übersprungen und gemeldet (`warning`, „Verbindung testen“ sagt es auch); der Rest läuft wie gewohnt. Dann `deploy/supabase_schema.sql` erneut ausführen. Bei der Stimmung gewinnt beim Holen die neuere `analyzed_at`.
* Gruppen sind in der Cloud über ihren **Namen** verbunden (die lokalen Zahlen-IDs unterscheiden sich je Rechner).
* Grenzen: Gelöschte Videos, Kommentare, Gruppen oder entfernte Gruppenzuordnungen werden in der Cloud **nicht** gelöscht. Wird die Adresse der Cloud geändert, beginnt der Abgleich von vorn.

### Tabellenform in der Cloud

Wie `deploy/supabase_schema.sql` (Postgres). Spalten wie in den Beispielen oben; Listen (`hashtags`, `mentions`) und `raw` sind `jsonb`, Zeiten (`fetched_at`, `first_seen_at`, `captured_at`, `added_at`) sind `timestamptz`.

| Tabelle | Schlüssel |
|---|---|
| `videos` | `uid` |
| `comments` | `uid` |
| `video_snapshots` | `video_uid`, `captured_at` |
| `groups` | `name` |
| `group_videos` | `group_name`, `video_uid` |
| `comment_sentiment` | `comment_uid` |
| `group_people` | `group_name`, `role`, `platform`, `author_key` |

### PULSE liest aus der Cloud

PULSE verwendet dieselbe Datenbank mit dem **anon**-Schlüssel (nur Lesen) über die Adresse `https://….supabase.co/rest/v1/<tabelle>`:

```js
import { createCloudClient } from "./pulse-client.js";
const cloud = createCloudClient({ url: "https://xyz.supabase.co", readKey: ANON_SCHLUESSEL });
const { items, total } = await cloud.listVideos({ group: "Sammlung", q: "tanz", limit: 20 });
```

Der Cloud-Client liefert dieselbe Antwortform wie der lokale (`items`, `total`, `limit`, `offset`). Nicht enthalten sind dort `accounts` und die Zähler nach Plattform in `stats`.

---

## Sicherheit

* **Token:** wird zufällig erzeugt (mindestens 24 Zeichen), mit konstanter Zeit verglichen und steht nur in `data/integration.json` (Rechte eingeschränkt, soweit Windows das zulässt). Er erscheint nie im Protokoll und nie in Fehlermeldungen. Bei Verdacht einen neuen erzeugen lassen; der alte ist sofort ungültig (die Schnittstelle neu starten).
* **Nur dein PC:** Standard ist `127.0.0.1`. Andere Rechner im Netz erreichen die Schnittstelle nicht. Wer `host` auf eine andere Adresse stellt (z. B. auf einem Server), macht sie im Netz sichtbar; dann unbedingt hinter einer HTTPS-Absicherung (Reverse-Proxy) betreiben, denn der Token wird sonst unverschlüsselt übertragen. Bei `127.0.0.1` prüft die Schnittstelle zusätzlich den `Host`-Header (Schutz gegen DNS-Rebinding).
* **CORS:** Der Browser darf Antworten nur für Herkunftsadressen lesen, die in `cors_origins` stehen (genau, z. B. `http://localhost:5173`, ohne Pfad; `*` ist nicht erlaubt).
* **Nur lesend:** schreibgeschützte Datenbankverbindung je Anfrage, SQL nur mit Parametern, kein Schreib-Endpunkt.
* **Kein Schlüssel im Browser-Code einer öffentlichen Webseite.** Der lokale Token und erst recht der Cloud-Schreibschlüssel (`service_role`) dürfen nie in JavaScript landen, das Fremde laden können. Bei der gehosteten Variante bekommt PULSE nur den Lese-Schlüssel (`anon`) mit Row-Level-Security. Sind die Daten nicht öffentlich gedacht, die Leseregeln `lesen_fuer_pulse` im Schema für `anon` entfernen und PULSE über angemeldete Nutzer (Rolle `authenticated`) oder ein eigenes Backend lesen lassen – dann hält das Backend den Schlüssel.
* Wer den Lese-Schlüssel kennt, kann alles lesen, was die Regeln freigeben, also auch Kommentartexte mit Namen von Dritten. Das beim Veröffentlichen von PULSE mitbedenken.

## Offene Punkte

* PULSE zeigt bisher nur Beispieldaten. Das Einbinden (Client in PULSE einbauen, Ansichten anschließen) ist ein eigener, späterer Backend-Auftrag.
* Cloud-Weg nicht gegen einen echten Supabase-Server geprüft; Postgres-Eigenheiten (z. B. Zeichen `\u0000` in Rohdaten, die `jsonb` ablehnt) können dort erst auffallen.
* Der Token einer laufenden Schnittstelle gilt bis zu ihrem Neustart.
* **Stimmung nicht mit der echten Anthropic-API geprüft** (nur gegen einen selbstgebauten lokalen Testserver); die Qualität der Einstufung ist ungeprüft.
* „Neue Autoren“ meint: noch nie in **dieser** Datenbank gesehen. Eine Liste bekannter Personen aus einer anderen Quelle (z. B. aus PULSE) gibt es nicht.
* Eine Person ist nur über ihre Kennung oder ihren Namen auf einer Plattform erkennbar; ändert jemand seinen Namen (oder fehlt die Kennung), gilt er als neue Person.
