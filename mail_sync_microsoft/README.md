# Mail Sync for Microsoft 365 (`mail_sync_microsoft`)

Spiegelt ausgewählte Microsoft-365-Postfächer über Microsoft Graph nach Odoo Community und
hängt jede geschäftliche Konversation an den passenden Kontakt bzw. dessen Firma. Odoo liest
nur; es schreibt nie in das Postfach zurück.

* Odoo 18.0 Community, Lizenz LGPL-3
* Abhängigkeiten: `mail`, `contacts`, OCA `queue_job`; Python `msal`, `requests`
* Erweiterung für Verkaufschancen: [`crm_mail_sync`](../crm_mail_sync/README.md)
* Einrichtung in Microsoft Entra und Exchange: [docs/konfiguration-microsoft.md](../docs/konfiguration-microsoft.md)
* Datenschutz-Vorlage: [docs/datenschutz.md](../docs/datenschutz.md)

## 1. Funktionsweise

### 1.1 Ablauf

1. **Ordner-Discovery** (`job_discover_folders`): `mailFolders` rekursiv einlesen, je Ordner
   eine Zeile `mail.sync.folder` mit `include`-Vorbelegung (siehe 2.3). Da Graph v1.0 kein
   `wellKnownName` liefert, werden die Well-known-Ordner (inbox, sentitems, drafts,
   deleteditems, junkemail, archive, outbox, …) vorab über ihre Alias-URLs aufgelöst und den
   IDs zugeordnet. In Outlook gelöschte Ordner werden nicht entfernt, sondern auf
   `include = False` gesetzt.
2. **Backfill** (`job_backfill`, je eingeschlossenem Ordner): erste Delta-Abfrage
   `messages/delta` mit `$filter=receivedDateTime ge <start_date>`; alle Seiten werden
   verarbeitet, der zurückgegebene `deltaLink` wird am Ordner gespeichert.
3. **Delta** (`job_delta`, alle 5 Minuten je Ordner): `messages/delta` mit gespeichertem
   `deltaLink`. Fehlt der Link oder ist `needs_full_resync` gesetzt, läuft stattdessen ein
   Backfill. Jeder Aufruf sendet `Prefer: IdType="ImmutableId"`; die Delta-Abfrage holt nur
   Kopfdaten (`$select`), der Body kommt erst beim Import als MIME.
4. **Verarbeitung je Nachricht** (`sync.process_message`): Register-Zeile anlegen oder
   aktualisieren, Vorprüfung, Router, ggf. Import in den Chatter.

Als `@removed` gemeldete Nachrichten werden im Register auf `removed` gesetzt; ein bereits
erzeugter Chatter-Eintrag bleibt bestehen.

### 1.2 Register (`mail.sync.message`)

Eine Zeile je Nachricht und Postfach, eindeutig über `(mailbox_id, graph_id)`. Gespeichert
werden Kopfdaten (`subject`, `email_from`, `to_emails`, `cc_emails`, `received_at`,
`direction`, `has_attachments`, `internet_message_id`, `conversation_id`, `web_link`), eine
Vorschau `body_preview` (max. 255 Zeichen, aus Graph `bodyPreview`, bei **jeder** Zeile), der
Zustand `state`, ein lesbarer Grund `reason`, die gematchten Kontakte `partner_ids` und
Firmen `commercial_partner_ids`, das Ziel `model`/`res_id`, der Chatter-Eintrag
`mail_message_id`, die Sichtbarkeit `visibility` sowie `shared_by_id`/`shared_at`.

Zustände:

| `state` | Bedeutung |
|---|---|
| `skipped_internal` | Alle Adressen gehören zu den eigenen Domains |
| `skipped_blocklist` | Adresse oder Domain auf der Blockliste des Postfachs |
| `skipped_automatic` | Beim Import als automatische Nachricht erkannt (Auto-Reply, Bounce, Kalender, Bulk) |
| `private` | Nur als Selection vorhanden; privat wird über `visibility = private` abgebildet, der `state` bleibt das Routing-Ergebnis |
| `unmatched` | Kein Kontakt und keine Firma gefunden (K4) oder keine externe Adresse |
| `matched` | Kontakt vorhanden, Zieldatensatz fehlt (nach „Zuordnung lösen“ oder wenn das Ziel gelöscht wurde) |
| `linked` | Zieldatensatz gesetzt; bei `visibility = shared` auch im Chatter |
| `removed` | In Microsoft 365 gelöscht oder verschoben |
| `error` | Fehler bei der Verarbeitung dieser Nachricht (`error` enthält den Text) |

Entwürfe (`isDraft`) werden gar nicht erst registriert.

### 1.3 Vorprüfung und Router (K1–K5)

Reihenfolge in `services/router.py`, die erste zutreffende Regel beendet die Prüfung:

1. Alle Adressen (From, To, Cc) in `internal_domains` → `skipped_internal`.
2. Eine Adresse oder Domain auf der Blockliste des Postfachs → `skipped_blocklist`.
3. Betreff beginnt mit einem der Präfixe aus `exclude_subject_prefixes` (Standard `[PRIVAT]`)
   oder der Ordner heißt „Privat“, „Private“ oder „Persönlich“ → `visibility = private`;
   sonst gilt `visibility_default` des Postfachs. Die Zuordnung läuft trotzdem weiter.
4. Keine externe Adresse übrig → `unmatched` („Keine externe Adresse“).

Dann die Zuordnung:

| # | Regel wie implementiert |
|---|---|
| K1 | Jede externe Adresse wird exakt (klein geschrieben) gegen `res.partner.email_normalized` gesucht; mehrere Treffer je Mail sind möglich. |
| K2 | Firmen = `commercial_partner_id` der Treffer, sofern `is_company`. |
| K3 | Für Adressen ohne Treffer: Domain (nicht Freemail, nicht intern) gegen `res.partner.email_normalized =like %@domain` oder `website ilike domain`; die Firma wird nur übernommen, wenn genau **eine** Firma dabei herauskommt. Je nach `auto_create_partner` wird ein Kontakt angelegt: `no` nie; `company` nur unter einer per K3 gefundenen Firma und nie für Freemail-Adressen; `always` immer (Freemail eingeschlossen, Firma als `parent_id` wenn gefunden). Name = lokaler Teil der Adresse. |
| K4 | Weder Kontakt noch Firma → `unmatched`, Grund „Kein Kontakt für …“; kein Chatter-Eintrag. |
| K5 | Richtung `outbound`, wenn die Absenderdomain intern ist oder der Ordner `sentitems` ist; sonst `inbound`. Existiert zur `internetMessageId` bereits eine `mail.message` in Odoo (z. B. von Odoo selbst versandt oder aus einem anderen Postfach importiert), wird die Zeile ohne Import auf deren Datensatz gesetzt (Grund „Bereits in Odoo vorhanden (Message-ID)“). |

Zieldatensatz ohne CRM-Erweiterung (`_pick_partner_target`): genau ein Kontakt → Kontakt;
sonst genau eine Firma → Firma; sonst der erste Kontakt; sonst der erste Treffer. Die
CRM-Erweiterung hängt sich über `_extra_target` davor.

Wichtig: Der Hook `_extra_target` (und damit alle D-Regeln) wird nur erreicht, wenn K1–K3
mindestens einen Kontakt oder eine Firma geliefert haben.

### 1.4 Import in den Chatter (`services/ingest.py`)

Nur Zeilen mit `state = linked` und `visibility = shared` werden importiert, direkt im
Delta-/Backfill-Job. Ablauf:

1. MIME über `/messages/{id}/$value` laden (harte Grenze 50 MB, darüber Fehler).
2. Parsen mit Odoos `mail.thread.message_parse`.
3. Automatik-Erkennung auf den MIME-Headern: `Auto-Submitted` (auto-replied/-generated/
   -notified), `X-Auto-Response-Suppress`, `X-Autoreply`, `X-Autorespond`, `Precedence`
   bulk/junk/list/auto_reply, Content-Type `multipart/report` oder Kalender, `.ics`-Anhang →
   `skipped_automatic`, kein Chatter-Eintrag. Diese Prüfung findet erst hier statt, weil die
   Delta-Abfrage diese Header nicht liefert.
4. Threading: Verweist `References`/`In-Reply-To` auf eine `mail.message`, die an einem
   Datensatz hängt, wird dieser Datensatz zum Ziel (Grund „D1: Antwort in bestehender
   Konversation auf …“).
5. `message_post` auf dem Ziel als Notiz (`mail.mt_note`, `message_type = email`), ohne
   Benachrichtigung, ohne Follower, ohne Tracking. Anhänge bis `attachment_max_mb` werden
   eingebettet; größere werden weggelassen und im Body mit Namen, Größe und Link „In Outlook
   öffnen“ vermerkt.

### 1.5 Sichtbarkeit

* `shared`: Zeile für alle Mail-Sync-Benutzer sichtbar; Chatter-Eintrag folgt den Rechten
  des Zieldatensatzes.
* `private`: Zeile nur für `owner_user_id` des Postfachs (und Verwalter) sichtbar; kein
  Chatter-Eintrag, kein Body in Odoo (nur `body_preview`). „Freigeben“ (nur Eigentümer oder
  Verwalter) setzt `visibility = shared`, `shared_by_id`, `shared_at` und reiht den Import
  als Job ein.

### 1.6 Housekeeping

Täglicher Cron `_cron_housekeeping`:

* Register-Zeilen in `unmatched`, `skipped_internal`, `skipped_blocklist`,
  `skipped_automatic`, die älter als `retention_days` des Kontos sind (gemessen am
  `create_date` der Zeile), werden gelöscht. `retention_days <= 0` schaltet die Löschung ab.
  Zeilen in `matched`, `linked`, `removed`, `error` und private Zeilen bleiben.
* Für jedes aktive, verbundene Postfach wird die Ordner-Discovery neu eingereiht.

## 2. Konfiguration in Odoo

Menü: **Einstellungen → E-Mail-Sync** (Gruppe „Mail Sync: Verwalter“).

### 2.1 Verbindung (`mail.sync.account`)

| Feld | Bedeutung |
|---|---|
| `tenant_id`, `client_id` | Entra-Mandant und App-ID der App-Registrierung |
| `auth_mode` | `app` (zentral, Anwendungsberechtigung + Exchange-Freigabe) oder `delegated` (je Benutzer) |
| `credential_source` | `env`: Client-Secret aus der Umgebungsvariable `MAIL_SYNC_CLIENT_SECRET` (Fallback `MAIL_SYNC_CLIENT_SECRET_<account-id>`); `param`: Systemparameter `mail_sync.client_secret.<account-id>`, Button „Secret hinterlegen“ öffnet den Parameter zum Eintragen. Das Secret wird in beiden Modi gebraucht, auch im delegierten (Token-Austausch). |
| `secret_configured` | Anzeige, ob ein Secret gefunden wird |
| `internal_domains` | Eigene Domains, eine je Zeile; beim Verbindungstest aus `organization.verifiedDomains` vorbelegt, wenn leer |
| `freemail_domains` | Domains, die nie als Firmendomain gelten (Vorbelegung gmail, outlook, web.de, gmx, t-online, …) |
| `excluded_folder_patterns` | Ein Muster je Zeile, Teilzeichenkette ohne Groß-/Kleinschreibung gegen den Ordnerpfad; Vorbelegung `privat`, `private`, `persönlich`, `bewerbung`, `personal`, `zeiterfassung`. Wirkt nur auf die Vorbelegung neu gefundener Ordner |
| `retention_days` | Aufbewahrung nicht zugeordneter Zeilen, Standard 30 |
| `attachment_max_mb` | Anhangsgrenze je Datei, Standard 10 |
| `state` | `draft` / `connected` / `error`, gesetzt durch „Verbindung testen“ |

Ein Zertifikat statt Secret wird vom Graph-Client unterstützt, ist aber nicht über die
Oberfläche konfigurierbar.

### 2.2 Postfach (`mail.sync.mailbox`)

| Feld | Bedeutung |
|---|---|
| `upn` | Postfachadresse (UPN); wird klein geschrieben; passender Odoo-Benutzer wird als Eigentümer vorgeschlagen |
| `kind` | `personal` oder `shared` (Funktionspostfach) |
| `owner_user_id` | Sieht private Zeilen, verbindet im delegierten Modus, erhält Aktivitäten bei `auth_error`; im delegierten Modus Pflicht |
| `visibility_default` | `shared` oder `private` für alle neuen Zeilen |
| `start_date` | Backfill ab diesem Datum, Standard heute − 90 Tage |
| `sync_sent` | Ordner „Gesendete Elemente“ einschließen (wirkt nur bei der Vorbelegung neuer Ordner) |
| `auto_create_partner` | `no` / `company` / `always`, siehe K3 |
| `exclude_subject_prefixes` | Kommagetrennt, Standard `[PRIVAT]` |
| `blocklist_addresses`, `blocklist_domains` | Eine je Zeile |
| `state` | `draft`, `connected`, `auth_error`, `error` |

Buttons: „Postfach verbinden“ (nur delegiert), „Ordner einlesen“, „Backfill starten“
(reiht je eingeschlossenem Ordner einen Job ein), „Jetzt abgleichen“ (Delta sofort, ohne
Identitätsschlüssel).

### 2.3 Ordner (`mail.sync.folder`)

`include` wird bei der Discovery vorbelegt und kann danach je Ordner geändert werden:

* Nie: `deleteditems`, `drafts`, `junkemail`, `outbox`, `clutter`, `conversationhistory`,
  `recoverableitemsdeletions`, `syncissues`, `conflicts`, `localfailures`, `serverfailures`,
  `scheduled`, `searchfolders`.
* `sentitems` nur bei `sync_sent`.
* Unterordner eines ausgeschlossenen Ordners nicht (Vererbung bei der Discovery).
* Ordner, deren Pfad eines der `excluded_folder_patterns` des Kontos enthält, nicht
  (Vorbelegung u. a. `privat`, `bewerbung`, `zeiterfassung`).
* Alle anderen (Posteingang, Archiv, Benutzerordner) ja.

Ein manuell eingeschlossener Ordner mit dem Namen „Privat“, „Private“ oder „Persönlich“
wird synchronisiert, seine Nachrichten gelten aber als privat (siehe 1.3, Schritt 3).

Weitere Felder: `delta_link`, `backfill_done`, `needs_full_resync`, `last_sync`.

### 2.4 Crons und Warteschlange

| Cron | Intervall | Aufgabe |
|---|---|---|
| „Mail Sync: Postfächer abgleichen“ | alle 5 Minuten | je aktivem Postfach in `connected`/`draft` und je eingeschlossenem Ordner einen Delta-Job einreihen (Identitätsschlüssel `mail_sync_delta_<folder>` verhindert Doppelungen) |
| „Mail Sync: Aufräumen und Ordner prüfen“ | täglich | siehe 1.6 |

Alle Jobs laufen im `queue_job`-Kanal `root.mail_sync` (Kapazität 4, angelegt durch
`data/queue_job_data.xml`). Job-Funktionen: `job_discover_folders`, `job_backfill`,
`job_delta`, `job_ingest_message`, Retry-Muster `{1: 60, 3: 300, 6: 1800}` Sekunden;
Delta und Backfill mit `max_retries = 50`. Der Jobrunner von `queue_job` muss aktiv sein
(`server_wide_modules` enthält `queue_job`, Kanal `root` konfiguriert).

## 3. Anbindungsarten

**Zentral (`app`)**: Client-Credentials-Flow mit `https://graph.microsoft.com/.default`.
Welche Postfächer die App lesen darf, wird in Exchange Online über „RBAC for Applications“
auf eine Sicherheitsgruppe eingeschränkt (siehe Konfigurationsanleitung). Postfächer in
`draft` werden vom Cron mitgenommen und wechseln nach dem ersten erfolgreichen Lauf auf
`connected`.

**Je Benutzer (`delegated`)**: Authorization-Code-Flow mit den Scopes `offline_access` und
`https://graph.microsoft.com/Mail.Read`. Der Eigentümer (oder ein Verwalter) klickt
„Postfach verbinden“, wird zu `/mail_sync/oauth/start` und von dort zu Microsoft geleitet;
der Rückweg ist `<web.base.url>/mail_sync/oauth/callback`. Der `state`-Parameter ist mit
`database.secret` HMAC-signiert und an Postfach und Benutzer gebunden. Das Refresh-Token wird
Fernet-verschlüsselt (Schlüssel aus `database.secret` abgeleitet) im Feld
`refresh_token_enc` gespeichert, das nur `base.group_system` lesen kann. Nach dem Verbinden
wird die Ordner-Discovery eingereiht. Die Aktion `res.users.action_connect_mailbox` legt bei
Bedarf ein persönliches Postfach für den aktuellen Benutzer an und startet den Flow.

## 4. Rechte

Gruppen (`security/mail_sync_security.xml`):

* **Mail Sync: Benutzer** (`group_mail_sync_user`, impliziert `base.group_user`)
* **Mail Sync: Verwalter** (`group_mail_sync_manager`, impliziert Benutzer; `admin` ist Mitglied)

Zugriffsrechte (`ir.model.access.csv`):

| Modell | Benutzer | Verwalter |
|---|---|---|
| `mail.sync.account` | lesen | alles |
| `mail.sync.mailbox` | lesen, schreiben, anlegen | alles |
| `mail.sync.folder` | lesen, schreiben | alles |
| `mail.sync.message` | lesen, schreiben | alles |
| `mail.sync.run` | lesen | alles |

Record Rules auf `mail.sync.message`:

* Benutzer: `visibility = shared` **oder** `owner_user_id = user` (lesen, schreiben).
  Geteilte Zeilen aller Postfächer sind damit für jeden Mail-Sync-Benutzer sichtbar.
* Verwalter: alle Zeilen.
* Global: nur Zeilen der eigenen Firmen (`company_id in company_ids`); dieselbe Regel gilt für
  `mail.sync.mailbox`.

Chatter-Einträge unterliegen den Rechten des Datensatzes, an dem sie hängen (Kontakt, Lead).

## 5. Menüs

* **Einstellungen → E-Mail-Sync** (Verwalter): Verbindungen, Postfächer, Register (gruppiert
  nach Zustand), Läufe.
* **Dialog → Meine E-Mails** (Benutzer): Register der Postfächer, deren Eigentümer der
  Benutzer ist, Standardfilter „Zuordnung offen“; Buttons „Freigeben“ und „Öffnen“.
* Kontaktformular: Smart Button „E-Mails“ (Zeilen mit dem Partner in `partner_ids` oder
  `commercial_partner_ids`).

## 6. Grenzen

* Kein Rückschreiben nach Exchange: keine Markierungen, keine Ordnerbewegungen, kein Versand.
* Anhänge über `attachment_max_mb` werden nicht übernommen; Nachrichten mit mehr als 50 MB
  MIME werden nicht importiert.
* `body_preview` (255 Zeichen) wird für jede registrierte Zeile gespeichert, auch für private
  und nicht zugeordnete.
* Die Automatik-Erkennung greift erst beim Import, also nur für geteilte, zugeordnete
  Nachrichten; nicht zugeordnete Auto-Replies bleiben als `unmatched` im Register bis zur
  Löschfrist.
* Nicht zugeordnete Zeilen werden nach `retention_days` gelöscht; die Mail bleibt in
  Microsoft 365.
* Der Backfill nutzt eine gefilterte Delta-Abfrage ohne Zeitfenster-Aufteilung.
* Nur Microsoft Graph; kein IMAP, kein Gmail.
* Keine Überwachung der Entra-Berechtigungen und kein Watchdog für ausbleibende Läufe.

## 7. Fehlersuche

| Symptom | Ursache / Maßnahme |
|---|---|
| Verbindung `error` | „Verbindung testen“ zeigt die Graph-Fehlermeldung; `last_error` am Konto. Meist Secret, Tenant/Client-ID oder fehlende Berechtigung. Darf die App das Verzeichnis nicht lesen (`/organization` liefert 401/403, typisch bei reiner Exchange-RBAC-Freigabe), prüft der Test stattdessen den Ordnerzugriff auf das erste angelegte Postfach; ohne Postfach bricht er mit einem Hinweis ab. Die eigenen Domains sind dann manuell einzutragen. |
| Postfach `auth_error` | 401/403 von Graph nach einmaligem Token-Neuversuch. App-Modus: Postfach nicht in der Exchange-Freigabegruppe oder RBAC noch nicht propagiert. Delegiert: Refresh-Token abgelaufen oder widerrufen → „Postfach verbinden“. Der Eigentümer (sonst `admin`) bekommt eine Aktivität. Der Cron überspringt Postfächer in `auth_error`; nach dem Beheben „Jetzt abgleichen“ oder Zustand zurücksetzen. |
| Postfach `error` | Sonstiger Graph-Fehler; Job wird nach 600 s wiederholt, `last_error` am Postfach. |
| Lauf mit „Throttled“ | 429/503/504 oder Netzfehler; Wiederholung nach `Retry-After` (min. 5 s). |
| Lauf mit „Delta-Status ungültig“ | 410 bzw. `syncStateNotFound`; Ordner bekommt `needs_full_resync`, der nächste Job macht einen Backfill. Duplikate fängt die Eindeutigkeit über `graph_id` ab. |
| Zeile `error` | Fehler bei genau dieser Nachricht, Text im Feld `error`; der Job läuft weiter. |
| Zeile `unmatched` | Kein Kontakt zu den externen Adressen; Kontakt anlegen und manuell zuordnen oder manuell zuordnen; eine bereits registrierte Zeile wird nur dann neu geroutet, wenn Graph sie im Delta erneut liefert (Änderung in Microsoft 365) und sie noch nicht `linked` ist. |
| Zeile `matched` | Ziel fehlt (z. B. nach „Zuordnung lösen“); über die Register-Aktionen neu zuordnen. |
| Nichts passiert | Jobrunner prüfen (Einstellungen → Technisch → Warteschlange), Kanal `root.mail_sync`, Cron aktiv, Postfach aktiv und in `connected`/`draft`, Ordner `include`. |

Läufe (`mail.sync.run`) zeigen je Job Art (`discover`, `backfill`, `delta`, `ingest`), Zähler
(`fetched`, `created`, `linked`, `skipped`, `errors`), Dauer und Fehlertext. Der Grund jeder
Routing-Entscheidung steht in `reason` der Register-Zeile.

## 8. Entwicklung

Tests liegen in `tests/` und ersetzen den Graph-Client durch `tests/fake_graph.py`. Aufruf
siehe [README im Repository](../README.md).
