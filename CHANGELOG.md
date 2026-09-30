# Changelog

Alle nennenswerten Änderungen an den Modulen dieses Repositories. Versionen folgen dem
Odoo-Schema `18.0.X.Y.Z`; jedes Modul wird getrennt geführt.

## mail_sync_microsoft

### 18.0.1.0.1 – 2026-09-30

* Ordner-Jobs schreiben die Postfachzeile nicht mehr bei jedem Lauf (`last_sync` wird aus den
  Ordnern berechnet); lange Backfill-Seiten scheiterten sonst dauerhaft mit
  Serialisierungsfehlern gegen die kurzen Delta-Jobs der anderen Ordner.
* Backfill-/Delta-Jobs desselben Ordners werden über eine Zeilensperre serialisiert; ein
  parallel gestarteter Job wird nach 60 s erneut eingeplant statt am Unique-Key des
  Registers zu scheitern.
* Automatisch angelegte Kontakte werden ohne USt-IdNr.-Prüfung erzeugt (die Nummer wird von
  der Firma übernommen; ungeprüft importierte Nummern blockierten die Zuordnung).
* `message/rfc822`-Anhänge (weitergeleitete Mails) werden beim Einspielen korrekt serialisiert.

### 18.0.1.0.0 – 2026-09-30

Erste Version.

* Microsoft-Graph-Client mit Token-Providern für Client-Credentials (App-Modus) und
  Refresh-Token (delegierter Modus); Behandlung von 429/503/504 (Retry-After), 401 (einmaliger
  Token-Neuversuch), 403, 404, 410/`syncStateNotFound`.
* Modelle `mail.sync.account`, `mail.sync.mailbox`, `mail.sync.folder`, `mail.sync.message`,
  `mail.sync.run`; Erweiterungen an `res.partner` (Smart Button) und `res.users`
  (Postfach verbinden).
* Ordner-Discovery mit Auflösung der Well-known-Ordner über Alias-URLs, Vorbelegung über
  konfigurierbare Ausschlussmuster am Konto (Unterordner erben den Ausschluss), Backfill ab
  Startdatum, Delta-Sync je Ordner mit Immutable IDs; `@removed`-Behandlung.
* Verbindungstest weicht ohne Verzeichnisberechtigung (reine Exchange-RBAC-Freigabe) auf
  einen Postfachzugriff aus.
* Vorprüfung (intern, Blockliste, privat), Router K1–K5 mit Domain-Zuordnung und optionaler
  Kontaktanlage, Duplikaterkennung über Message-ID.
* Import über `message_parse`/`message_post` als Notiz ohne Benachrichtigung; Automatik-
  Erkennung (Auto-Submitted, Bounce, Kalender, Bulk); Anhangsgrenze mit Hinweis und
  Outlook-Link; Threading über References/In-Reply-To.
* Sichtbarkeit geteilt/privat mit Freigabe durch den Eigentümer, Record Rules, Gruppen
  Benutzer und Verwalter, Firmenregel.
* Delegierter OAuth-Flow (`/mail_sync/oauth/start`, `/mail_sync/oauth/callback`) mit
  signiertem State und Fernet-verschlüsseltem Refresh-Token.
* Crons: Delta alle 5 Minuten, Housekeeping täglich (Löschung nicht zugeordneter Zeilen nach
  `retention_days`, Ordnerprüfung); `queue_job`-Kanal `root.mail_sync` (Kapazität 4).
* Menüs „Einstellungen → E-Mail-Sync“ und „Dialog → Meine E-Mails“.
* Tests mit `FakeGraph` (Sync, Sichtbarkeit, Prefilter, Graph-Client, OAuth).

## crm_mail_sync

### 18.0.1.0.1 – 2026-09-30

* Backfill-/Delta-Jobs desselben Ordners werden über eine Zeilensperre serialisiert; ein
  parallel gestarteter Job wird nach 60 s erneut eingeplant statt am Unique-Key des
  Registers zu scheitern.
* Automatisch angelegte Kontakte werden ohne USt-IdNr.-Prüfung erzeugt (die Nummer wird von
  der Firma übernommen; ungeprüft importierte Nummern blockierten die Zuordnung).
* `message/rfc822`-Anhänge (weitergeleitete Mails) werden beim Einspielen korrekt serialisiert.

### 18.0.1.0.0 – 2026-09-30

Erste Version.

* `LeadRouter` mit den Regeln D1 (Konversationsfortsetzung, auch nach Gewonnen), D2
  (Deal-Referenz im Betreff, Muster je Konto), D3 (einzige offene Verkaufschance), D4
  (Scoring mit vier Gewichten und Schwelle, sonst Vorschlag), D5 (Lead-Vorschlag oder
  -Anlage für Vertriebspostfächer).
* D6: rückwirkende Vorschläge beim Anlegen einer Verkaufschance (`retro_days`).
* D7: Assistent „Konversation verknüpfen“ / „Zuordnung lösen“ mit Übernahme der ganzen
  Konversation.
* Felder an Konto, Postfach und Register; Smart Buttons „E-Mails“ und „Vorschläge“ am Lead;
  Menü „CRM → E-Mail-Vorschläge“.
* Vertriebsbenutzer erhalten die Gruppe „Mail Sync: Benutzer“ automatisch.
* Tests für D1–D7.
