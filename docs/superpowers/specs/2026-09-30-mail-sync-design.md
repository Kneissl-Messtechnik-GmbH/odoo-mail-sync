# Odoo Mail Sync für Microsoft 365 – Design

Stand: 2026-09-30. Lizenz LGPL-3, eigenes Repository `Kneissl-Messtechnik-GmbH/odoo-mail-sync`,
Branches `18.0` und `19.0`. Erstkunde: Kneissl Messtechnik GmbH; Zielgruppe: jede Firma mit
Microsoft 365 und Odoo Community.

Grundlage: Recherchebericht „E-Mails automatisch Deals zuordnen Odoo“ (2026-09-30) mit Pipedrives
dokumentierten Regeln, Odoos Router-Logik und den Microsoft-Graph-Randbedingungen.

## 1. Ziel und Nicht-Ziele

**Ziel.** Alle geschäftlichen E-Mails ausgewählter Microsoft-365-Postfächer landen automatisch in
Odoo, jede Konversation hängt am richtigen Kontakt, an dessen Firma und, wenn eindeutig, an der
passenden offenen Verkaufschance. Nutzer sehen ihre Mails im Chatter des Datensatzes und in einer
eigenen Liste, ohne selbst etwas klicken zu müssen. Verhalten und Wortschatz orientieren sich an
Pipedrive Email Sync, damit ein Wechsel von Pipedrive nichts Gelerntes entwertet.

**Nicht-Ziele.** Kein E-Mail-Client in Odoo (kein Versand aus der Liste, kein Ordner-Management),
keine Zwei-Wege-Synchronisation zurück nach Exchange (Odoo schreibt nie ins Postfach), keine
Unterstützung für Gmail oder IMAP in Version 1 (die Abstraktion lässt es zu), keine Enterprise-Abhängigkeit.

## 2. Module

| Modul | Abhängigkeiten | Inhalt |
|---|---|---|
| `mail_sync_microsoft` | `mail`, `contacts`, `queue_job` (OCA) | Verbindung zu Microsoft Graph (App-only zentral oder je Benutzer delegiert), Postfächer, Ordner, Delta-Sync, Nachrichtenregister, Teilnehmer→Kontakt-Zuordnung, Firmen-Zuordnung, Sichtbarkeit, Ingest in den Chatter, Routing-Protokoll, Liste „Meine E-Mails“ |
| `crm_mail_sync` | `mail_sync_microsoft`, `crm` | Zuordnung zu `crm.lead`: Regelwerk, Deal-Referenzen, Scoring, Vorschläge, Rückwirkung, Lead-Vorschlag aus neuer Anfrage, Smart Buttons und Assistenten auf Lead |
| `mail_sync_microsoft_ai` *(Roadmap, separates Modul)* | `crm_mail_sync` | Optionale KI-Unterstützung: Klassifikation, Kandidatenwahl bei Mehrdeutigkeit, Zusammenfassung; opt-in, eigener API-Schlüssel |

Alles Kundenspezifische (welche Postfächer, Domains, Regeln, Migration alter Pipedrive-Verlinkungen)
ist Konfiguration oder liegt im Repo des Kunden, nie in diesen Modulen.

## 3. Architektur

```
Microsoft 365 ──Graph API──▶ mail_sync_microsoft ──message_process──▶ Chatter (mail.message)
   Postfächer      Delta        Register + Regeln                        auf Partner / Lead
                              ▲                 │
                  queue_job (Jobs je Postfach/Ordner)   crm_mail_sync (Deal-Regeln)
```

* **Quelle** ist ausschließlich Microsoft Graph. Zwei Anbindungsarten, beide im Kern:
  * **Zentral (App-only)**: Client-Credentials mit Zertifikat oder Secret; Freigabe der Postfächer
    über Exchange „RBAC for Applications“ (Sicherheitsgruppe). Empfohlen für Firmen mit Exchange-Admin.
  * **Je Benutzer (delegiert)**: Jeder Odoo-Benutzer verbindet sein Postfach selbst per OAuth
    (Scope `Mail.Read offline_access`); Refresh-Token verschlüsselt am Postfach. Für Firmen ohne
    zentrale Freigabe oder für Einzelpersonen.
* **Transport und Regeln sind getrennt**: `services/graph.py` kennt nur Graph, `services/router.py`
  kennt nur Odoo-Datensätze. Ein `FakeGraph` in den Tests ersetzt den Transport vollständig.
* **Hintergrundverarbeitung** mit OCA `queue_job`: ein Cron (Standard alle 5 Minuten) reiht je
  aktivem Postfach und Ordner einen Delta-Job ein, Kanal `root.mail_sync` mit Kapazität 4 und
  höchstens 4 gleichzeitigen Aufrufen je Postfach (Graph-Limit). Backfill als eigene Job-Gruppe.
* **Ingest** über Odoos eigenen Weg: die Rohmail (MIME über `/messages/{id}/$value`) geht durch
  `mail.thread.message_process(model, mime, thread_id=…)`. Damit greifen Odoos Duplikatprüfung,
  Reply-Threading, HTML-Bereinigung und Anhangsbehandlung unverändert. Eine Konversation liegt
  als `mail.message` genau einmal im Chatter des verlinkten Datensatzes; weitere Bezüge (mehrere
  Kontakte, Firma) verwaltet das Register über `partner_ids`, nicht über Kopien.

## 4. Datenmodell (`mail_sync_microsoft`)

`mail.sync.account` – Verbindung: `name`, `company_id`, `tenant_id`, `client_id`, `auth_mode`
(app|delegated), Secret/Zertifikat nur über Umgebungsvariablen oder `ir.config_parameter` mit
Gruppenschutz, `internal_domains` (aus `organization.verifiedDomains` befüllt, editierbar),
`freemail_domains` (Vorbelegung gmail, outlook, web.de, gmx, …), `state`, „Verbindung testen“.

`mail.sync.mailbox` – Postfach: `account_id`, `upn`, `display_name`, `kind` (personal|shared),
`owner_user_id` (Odoo-Benutzer, bei shared optional), `active`, `visibility_default`
(shared|private), `start_date` (Backfill ab), `sync_sent` (bool), `auto_create_partner`
(nein|unter bekannter Firma|immer), `blocklist_addresses`, `blocklist_domains`,
`exclude_subject_prefixes` (Standard `[PRIVAT]`), `state`, `last_sync`, Zähler.
Delegiert: verschlüsseltes Refresh-Token, „Postfach verbinden“-Button (nur Eigentümer).

`mail.sync.folder` – Ordner je Postfach: `graph_id`, `well_known_name`, `display_name`,
`include` (Inbox, Sent Items, Archive und Benutzerordner ja; Drafts, Junk, Deleted, Outbox,
Clutter, Conversation History nein), `delta_link`, `needs_full_resync`, `last_sync`.

`mail.sync.message` – Register, eine Zeile je Nachricht und Postfach:
`mailbox_id`, `folder_id`, `graph_id` (Immutable ID), `internet_message_id`, `conversation_id`,
`subject`, `email_from`, `to_emails`, `cc_emails` (Text), `received_at`, `direction`
(inbound|outbound), `has_attachments`, `size`, `state` (skipped_internal, skipped_blocklist,
skipped_automatic, unmatched, matched, linked, private, error), `reason` (lesbarer Routing-Grund),
`partner_ids` (M2M, alle gematchten Kontakte), `commercial_partner_ids` (Firmen), `model`/`res_id`
(verlinkter Datensatz), `mail_message_id` (Chatter-Eintrag), `visibility` (shared|private),
`shared_by`/`shared_at`, `error`.
Constraints: `unique(mailbox_id, internet_message_id)`.

`mail.sync.run` – Protokoll je Job (Postfach, Ordner, Art initial|delta|backfill, Zähler je
Zustand, Dauer, Fehler), plus Log-Zeilen bei Ausnahmen.

## 5. Synchronisationsablauf

1. **Ordner-Discovery** je Postfach beim Aktivieren und täglich: `mailFolders` rekursiv,
   `include` nach Well-known-Regel setzen, neue Benutzerordner standardmäßig einschließen.
2. **Backfill** ab `start_date`: `messages?$filter=receivedDateTime ge …&$select=…` seitenweise
   (Graph-Kappe 5.000 je Filterabfrage; darüber in Monatsfenster teilen), anschließend
   Delta-Link initialisieren.
3. **Delta** je Ordner: `messages/delta` mit gespeichertem `deltaLink`, `Prefer: IdType="ImmutableId"`
   auf jedem Aufruf, `$select` nur Header. Entfernte oder verschobene Nachrichten (`@removed`)
   werden im Register markiert, im Chatter nie gelöscht (Odoo ist Archiv). 410/`syncStateNotFound`
   → `needs_full_resync`. 429 → `Retry-After` als Job-Wiedervorlage.
4. **Vorprüfung** je Nachricht in dieser Reihenfolge (die erste zutreffende Regel gewinnt):
   Duplikat (Message-Id schon im Register oder in `mail.message`) → skipped; rein intern (alle
   Adressen in `internal_domains`) → skipped_internal; Blockliste, Auto-Submitted/NDR/Kalender
   (`Auto-Submitted`, `X-Auto-Response-Suppress`, `.ics`, Report-MIME) → skipped_automatic;
   Betreffpräfix oder Ordner „Privat“ → private ohne Body.
5. **Teilnehmer-Zuordnung** (Abschnitt 6), Ergebnis: Kontakte, Firmen, ggf. Lead.
6. **Sichtbarkeit**: `visibility_default` des Postfachs; privat bedeutet Register mit Header
   und `bodyPreview`, kein Chatter-Eintrag, sichtbar nur für den Eigentümer, Freigabe holt Body nach.
7. **Ingest** bei shared und vorhandenem Ziel: MIME laden, `message_process` auf das Ziel
   (Lead vor Partner), Anhänge bis konfigurierbarer Größe (Standard 10 MB) einbetten, darüber
   nur `webLink`. Register verlinkt den erzeugten `mail.message`.
8. **Folgeantworten** in einer bekannten Konversation folgen dem Datensatz der Konversation
   (Reply-Threading von Odoo, zusätzlich `conversation_id` als Sekundärschlüssel).

## 6. Zuordnungsregeln

### 6.1 Kontakte und Firmen (Kern)

| # | Regel | Pipedrive | Hier |
|---|---|---|---|
| K1 | Jede externe Adresse in From, To, Cc wird exakt und case-insensitiv gegen `res.partner.email_normalized` gesucht | gleich | gleich, mehrere Treffer je Mail möglich |
| K2 | Firma = `commercial_partner_id` des Kontakts zum Sync-Zeitpunkt | gleich | gleich |
| K3 | **Klüger:** unbekannte Adresse mit bekannter Firmen-Domain (nicht Freemail) → Firma wird zugeordnet; optional Kontakt unter der Firma anlegen (`auto_create_partner`) | nur über Person | Domain-Index über `res.partner.website`/E-Mail-Domains der Firma, Freemail-Liste ausgeschlossen |
| K4 | Ohne jeden Treffer bleibt die Mail `unmatched` im Register (Header, 30 Tage), kein Chatter | keine Zuordnung | gleich, mit manueller Nachzuordnung |
| K5 | Gesendete Mails (Sent Items) werden wie eingehende behandelt, Richtung `outbound`; Mails, die Odoo selbst gesendet hat, sind über ihre Message-Id Duplikate | gleich | gleich |

### 6.2 Verkaufschancen (`crm_mail_sync`)

Kandidaten: alle `crm.lead` mit `type = opportunity`, `active = True`, Stage nicht `is_won`,
deren `partner_id` einer der gematchten Kontakte oder deren Firma ist.

| # | Regel | Pipedrive | Hier |
|---|---|---|---|
| D1 | Konversation gehört zu einem Lead, wenn eine frühere Nachricht derselben Konversation dort liegt (Reply-Threading, `conversation_id`) – auch nach Won/Lost | Thread-Verlinkung; nach Won/Lost keine neuen | **Klüger:** laufende Threads bleiben beim Lead |
| D2 | **Klüger:** Deal-Referenz im Betreff oder Body (konfigurierbares Muster, Standard: Odoo-Lead-Anzeige-ID und `ID 1234`) → eindeutige Zuordnung, auch bei mehreren offenen Deals | nicht vorhanden | Muster je Konto einstellbar, Treffer wird protokolliert |
| D3 | Genau ein Kandidat → zuordnen | gleich | gleich |
| D4 | **Klüger:** mehrere Kandidaten → Scoring statt Verzicht: +3 Lead-Eigentümer ist Postfach-Eigentümer, +2 Betreff teilt Wörter mit Lead-Titel oder Produktzeilen, +2 letzte Aktivität am Lead innerhalb 14 Tage, +1 jüngster Lead. Zuordnen nur bei Vorsprung ≥ 3 Punkte, sonst Zustand `matched` mit Kandidatenliste als Vorschlag | keine Zuordnung | Schwelle konfigurierbar; Vorschläge erscheinen als Buttons in „Meine E-Mails“ und am Lead |
| D5 | Kein Kandidat → nur Kontakt/Firma; wenn Postfach als Vertriebspostfach markiert und die Konversation neu ist: **Lead-Vorschlag** (optional automatisch anlegen, mit Duplikatschutz über Konversation und Absender) | Smart BCC legt Personen an | je Postfach einstellbar |
| D6 | **Klüger:** Rückwirkung als Vorschlag: beim Anlegen eines Leads für einen Kontakt werden dessen `unmatched`/`matched` Konversationen der letzten N Tage (Standard 30) angeboten und mit einem Klick verknüpft | keine Rückwirkung | Assistent auf dem Lead |
| D7 | Manuell: „Konversation verknüpfen“ und „lösen“ am Lead und am Kontakt; Verlinken zieht die ganze Konversation um | gleich | Rechte: Lead-Eigentümer, Vertriebsleiter |

Alle Entscheidungen schreiben einen lesbaren `reason` („D3: einziger offener Deal von Musterwerk GmbH“),
damit Support-Fragen ohne Log-Lesen beantwortbar sind.

## 7. Sichtbarkeit und Datenschutz

* Zugriff nur auf freigegebene Postfächer (Exchange-Gruppe bzw. persönliche Verbindung); ein
  monatlicher Kontrolljob meldet, wenn die Entra-App über die Gruppe hinaus Mail-Rechte trägt.
* Record Rules: private Registereinträge sieht nur der Eigentümer; geteilte Chatter-Einträge
  folgen den Rechten des Datensatzes (Lead, Kontakt).
* Ausschluss privater Mails: Betreffpräfix, Ordner „Privat“, Blocklisten; rein interne Mails werden
  nie gespiegelt.
* Ungematchte Mails: nur Header, Löschung nach 30 Tagen (konfigurierbar), Body bleibt in Exchange.
* Audit: wer hat wann welche Mail freigegeben, verknüpft, gelöst (Chatter am Register).
* Löschkonzept: Postfach deaktivieren → Sync stoppt; „Register bereinigen“ löscht Registerzeilen
  und optional die Chatter-Einträge des Postfachs; Off-boarding dokumentiert.
* Betriebsvereinbarung und DSFA sind Kundensache; das Modul liefert die technischen Zusagen
  (Umfang, Sichtbarkeitsmatrix, Löschfristen) als Doku-Vorlage in `docs/datenschutz.md`.

## 8. Oberfläche

* Einstellungen → E-Mail-Sync: Konten, Postfächer (Liste mit Status, letztem Lauf, Zählern),
  Ordner je Postfach, Läufe, Routing-Protokoll mit Filter nach Zustand und Grund.
* „Meine E-Mails“ (jeder Benutzer): Register der eigenen Postfächer, Filter nach Zustand,
  Aktionen „Freigeben“, „Verknüpfen mit …“, Kandidaten-Buttons bei Vorschlägen.
* Kontakt: Smart Button „E-Mails“ (Register über `partner_ids`), Reiter mit Konversationen.
* Lead: Smart Button „E-Mails“, Assistenten „Konversation verknüpfen“ und „Vorschläge prüfen“.
* Systemparameter: Cron-Intervall, Anhangslimit, Aufbewahrung ungematchter Mails, Scoring-Schwelle.

## 9. Fehlerbehandlung

* Graph 429/5xx und Netzfehler → `RetryableJobError` mit `Retry-After`; 401 → Token-Refresh
  einmal, dann Postfach `state = auth_error` und Benachrichtigung an Eigentümer/Admin.
* 410 → vollständige Neusynchronisation des Ordners, Duplikate über Register abgefangen.
* Fehler je Nachricht werden am Registereintrag gespeichert (`state = error`), der Job läuft weiter.
* Job-Laufzeiten begrenzt (max. Nachrichten je Job, Fortsetzung durch Folgejob).
* Watchdog-Cron: Postfach ohne erfolgreichen Lauf seit 6 Stunden → Aktivität an Admin.

## 10. Tests

* `FakeGraph` mit Postfächern, Ordnern, Delta-Seiten, MIME-Rohmails, 429/410-Szenarien.
* Router-Tests je Regel K1–K5 und D1–D7, mit Tabellen-Fixtures; Scoring-Grenzfälle.
* Sichtbarkeitstests (Record Rules), Ingest-Tests (Chatter-Eintrag, Anhangslimit, Duplikat).
* Delegierter Auth-Flow mit gemocktem Token-Endpunkt.
* CI: GitHub Actions mit Odoo 18/19 im Docker, pre-commit (ruff, pylint-odoo), OCA-Style-Checks.

## 11. Versionen, Repository, Veröffentlichung

* Branch `18.0` zuerst, `19.0` per Port (keine `fetchmail`-Abhängigkeit, daher geringer Unterschied).
* Struktur wie OCA: ein Ordner je Modul, `README.rst` aus `readme/`, `setup/` für pip, `i18n` de/en.
* Veröffentlichung: GitHub (LGPL-3), Odoo Apps Store kostenlos, später Angebot an OCA `mail`.
* Semantische Modulversionen `18.0.x.y.z`, CHANGELOG je Modul.

## 12. Abgrenzung zum Kneissl-Repo

* `KMT-Odoo` bindet dieses Repo als Submodul ein und installiert beide Module.
* Migration der historischen Pipedrive-Verlinkungen (Thread → Deal, Person, `shared_flag`) ist
  eine Kneissl-Aufgabe in `kneissl_pipedrive_sync` und schreibt in `mail.sync.message`.
* Kneissl-Konfiguration: Konto mit Tenant, Freigabegruppe `CRM-Mail-Sync`, Start mit den
  Funktionspostfächern `info@`, `kundenservice@`, `anfragen@`, danach persönliche Postfächer
  nach Betriebsvereinbarung.

## 13. Offene Prüfpunkte vor der Umsetzung

1. `message_process` mit `thread_id` auf `res.partner` per Aufruf (im Bericht ungetestet).
2. Exchange-RBAC-Kette live mit einem Testpostfach (Propagation 30 min bis 2 h).
3. Graph-Delta auf Sent Items bei Mails, die Odoo selbst über M365 verschickt hat (Duplikatpfad).
