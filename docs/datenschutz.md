# Datenschutz: technische Zusagen des E-Mail-Sync (Vorlage)

> **Vorlage, keine Rechtsberatung.** Dieses Dokument beschreibt, was die Module
> `mail_sync_microsoft` und `crm_mail_sync` (Stand 18.0.1.0.0) technisch tun und
> garantieren. Es dient als Faktengrundlage für Betriebsvereinbarung, Verzeichnis der
> Verarbeitungstätigkeiten und Datenschutz-Folgenabschätzung. Die rechtliche Bewertung, die
> Beteiligung des Betriebsrats und die Entscheidung über die offenen Punkte in Abschnitt 8
> liegen beim Unternehmen. Platzhalter in spitzen Klammern sind zu ersetzen.

**Unternehmen:** <Firma>
**Verantwortlich für den Betrieb:** <Rolle / Name>
**Betroffene Postfächer:** <Liste oder Verweis auf Freigabegruppe>
**Stand:** <Datum>

## 1. Zweck und Systembeschreibung

Das System liest E-Mails ausgewählter Microsoft-365-Postfächer über die Microsoft Graph API
und legt geschäftliche Konversationen in Odoo am zugehörigen Kontakt, an dessen Firma und
ggf. an der passenden Verkaufschance ab. Zweck: <z. B. Nachvollziehbarkeit der
Kundenkommunikation im CRM, Ablösung von Pipedrive Email Sync>.

Das System ist **lesend**. Es schreibt nichts in Microsoft 365 zurück, versendet keine
E-Mails, verschiebt oder löscht nichts und ändert keine Kennzeichnungen.

Zugriffsweg: entweder zentral über eine App-Registrierung, deren Leserecht in Exchange Online
per „RBAC for Applications“ auf eine Sicherheitsgruppe beschränkt ist, oder je Benutzer
über dessen eigene Anmeldung (delegiert). Welcher Weg genutzt wird: <App-Modus / delegiert>.

## 2. Datenumfang

### 2.1 Für jede registrierte Nachricht (Register `mail.sync.message`)

| Datum | Anmerkung |
|---|---|
| Betreff, Absender, Empfänger (An, Cc), Empfangszeitpunkt, Richtung | Kopfdaten |
| Graph-ID, Message-ID, Konversations-ID | technische Kennungen |
| Vorschau des Textes | erste 255 Zeichen (`bodyPreview` aus Graph), **auch bei privaten und nicht zugeordneten Nachrichten** |
| Anhänge vorhanden (ja/nein), Link „In Outlook öffnen“ | kein Anhangsinhalt |
| Zustand, Grund der Einordnung, gematchte Kontakte und Firmen, Zieldatensatz | Ergebnis der Zuordnung |
| Sichtbarkeit, freigegeben von / am | Audit |

Nicht registriert werden Entwürfe und Nachrichten in ausgeschlossenen Ordnern (Abschnitt 3).

### 2.2 Zusätzlich für geteilte, zugeordnete Nachrichten (Chatter-Eintrag)

Nur wenn die Nachricht `geteilt` ist **und** ein Zieldatensatz (Kontakt, Firma,
Verkaufschance) feststeht, wird die vollständige Nachricht (MIME) geladen und als
Chatter-Eintrag am Zieldatensatz abgelegt:

* vollständiger Text (HTML, durch Odoo bereinigt), Betreff, Absender, Datum,
* Anhänge bis <`attachment_max_mb`, Standard 10> MB je Datei; größere Anhänge werden nicht
  übernommen, sondern nur mit Name und Größe im Eintrag vermerkt,
* keine Benachrichtigung an Empfänger oder Follower, keine automatische Abonnierung.

Nachrichten mit mehr als 50 MB Gesamtgröße werden nicht importiert.

### 2.3 Konfigurationsdaten

Mandanten-ID, App-ID, Postfachadressen, Eigentümer-Zuordnung, Domainlisten, Blocklisten. Im
delegierten Modus je Postfach ein verschlüsseltes Refresh-Token (lesbar nur für
Systemadministratoren). Das Client-Secret liegt in einer Umgebungsvariable oder einem
administratorbeschränkten Systemparameter.

## 3. Was nie in Odoo gespiegelt wird

| Ausschluss | Wirkung |
|---|---|
| Rein interne Nachrichten (alle Adressen in den eigenen Domains) | Registerzeile `Übersprungen: intern` nur mit Kopfdaten, kein Text, Löschung nach Frist |
| Adressen/Domains auf der Blockliste des Postfachs | Registerzeile `Übersprungen: Blockliste`, kein Text, Löschung nach Frist |
| Betreff beginnt mit `[PRIVAT]` (Präfixe je Postfach einstellbar) | Registerzeile privat: nur Eigentümer sieht sie, kein Chatter-Eintrag, kein Volltext |
| Ordner, deren Pfad ein Muster aus „Ausgeschlossene Ordner“ enthält (Vorbelegung: `privat`, `private`, `persönlich`, `bewerbung`, `personal`, `zeiterfassung`), samt Unterordnern | Ordner wird bei der Discovery nicht eingeschlossen; Nachrichten darin werden nicht registriert. Wird ein Ordner namens „Privat“, „Private“ oder „Persönlich“ manuell eingeschlossen, gelten seine Nachrichten als privat |
| Entwürfe, Junk, Gelöschte Elemente, Postausgang, Clutter, Unterhaltungsverlauf, Wiederherstellbare Elemente, Suchordner | nie synchronisiert |
| Manuell ausgeschlossene Ordner | nicht synchronisiert |
| Automatische Nachrichten (Auto-Reply, Unzustellbarkeit, Kalendereinladungen, Massenmail-Kennzeichnung) | beim Import erkannt, kein Chatter-Eintrag; die Erkennung greift nur bei Nachrichten, die sonst importiert würden |
| Nachrichten ohne bekannten Kontakt | kein Chatter-Eintrag; Registerzeile mit Kopfdaten und Vorschau bis zur Löschfrist |

Hinweis für Beschäftigte: Der Schutz privater Nachrichten hängt davon ab, dass sie mit dem
Präfix versehen oder in den Ordner „Privat“ verschoben werden **bevor** der nächste Abgleich
läuft (alle 5 Minuten). Eine nachträgliche Verschiebung setzt die Registerzeile auf „In
Microsoft 365 gelöscht“, ein bereits erzeugter Chatter-Eintrag bleibt bestehen.

## 4. Sichtbarkeitsmatrix

Rollen: **Eigentümer** = Odoo-Benutzer, der am Postfach hinterlegt ist; **Mail-Sync-Benutzer**
= Gruppe „Mail Sync: Benutzer“ (mit `crm_mail_sync` automatisch alle Vertriebsbenutzer);
**Verwalter** = Gruppe „Mail Sync: Verwalter“; **Datensatzberechtigte** = wer den Kontakt
bzw. die Verkaufschance in Odoo sehen darf.

| Objekt | Eigentümer | Mail-Sync-Benutzer (andere) | Verwalter | Datensatzberechtigte ohne Mail-Sync-Gruppe |
|---|---|---|---|---|
| Registerzeile privat (Kopfdaten, Vorschau) | ja | nein | ja | nein |
| Registerzeile geteilt, nicht zugeordnet | ja | ja | ja | nein |
| Registerzeile geteilt, zugeordnet | ja | ja | ja | nein |
| Chatter-Eintrag (Volltext, Anhänge) | nach Rechten am Datensatz | nach Rechten am Datensatz | nach Rechten am Datensatz | ja |
| Postfach-Konfiguration, Läufe | lesen | lesen | alles | nein |
| Verschlüsseltes Refresh-Token | nein | nein | nur Systemadministratoren | nein |

Zusätzlich gilt eine Firmenregel: Zeilen und Postfächer sind nur innerhalb der eigenen
Odoo-Firma(en) sichtbar.

Freigabe einer privaten Nachricht: nur der Eigentümer oder ein Verwalter; danach wird sie
wie eine geteilte Nachricht behandelt (Chatter-Eintrag wird erzeugt).

## 5. Aufbewahrung und Löschung

| Daten | Frist |
|---|---|
| Registerzeilen `Kein Kontakt`, `Übersprungen: intern/Blockliste/automatisch` | Löschung durch täglichen Lauf nach <`retention_days`, Standard 30> Tagen ab Registrierung; die E-Mail bleibt in Microsoft 365 |
| Registerzeilen zugeordnet, Zuordnung offen, privat, gelöscht in M365, Fehler | keine automatische Löschung |
| Chatter-Einträge (Volltext, Anhänge) | keine automatische Löschung; sie sind Teil der Geschäftsdokumentation am Kontakt bzw. an der Verkaufschance und unterliegen den dortigen Aufbewahrungsregeln |
| Refresh-Token (delegiert) | bis zur Neuverbindung, zum Löschen des Postfachdatensatzes oder zum Widerruf durch den Benutzer |
| Lauf-Protokolle (`mail.sync.run`) | keine automatische Löschung |

Löschung oder Verschiebung in Microsoft 365 wirkt **nicht** auf Odoo: Die Registerzeile wird
als „In Microsoft 365 gelöscht“ markiert, der Chatter-Eintrag bleibt.

## 6. Nachvollziehbarkeit (Audit)

* Jede Registerzeile enthält den Grund ihrer Einordnung (`reason`, z. B. „D3: einzige
  offene Verkaufschance von …“, „Manuell zugeordnet“, „Zuordnung manuell gelöst“).
* Freigabe privater Nachrichten: `shared_by_id`, `shared_at` an der Zeile.
* Änderungen an Postfach und Verbindung (Eigentümer, Sichtbarkeitsvorgabe, Aktivierung,
  Zustand, Anbindungsart) werden im Chatter des jeweiligen Datensatzes protokolliert.
* Jeder Synchronisationslauf ist mit Zeitpunkt, Zählern und Fehlern protokolliert.
* Manuelle Zuordnungen und Freigaben sind Odoo-Aktionen des angemeldeten Benutzers und über
  `write_uid`/`write_date` der Zeile nachvollziehbar.
* Im Chatter einer neu angelegten Verkaufschance wird vermerkt, wenn rückwirkend
  Konversationen vorgeschlagen wurden.

Nicht protokolliert werden Lesezugriffe auf Registerzeilen oder Chatter-Einträge.

## 7. Off-boarding (Austritt, Wechsel, Widerruf)

1. Postfach in Odoo **deaktivieren** (`active = False`): kein weiterer Abgleich, keine Jobs.
2. App-Modus: Postfach aus der Exchange-Freigabegruppe entfernen. Delegiert: Zustimmung in
   Microsoft „Meine Apps“ widerrufen oder Postfachdatensatz löschen (löscht das Token).
3. Entscheiden, was mit den Registerzeilen geschieht: Verwalter können sie löschen
   (Löschen des Postfachdatensatzes entfernt alle Zeilen und Ordner mit).
4. Entscheiden, was mit den Chatter-Einträgen geschieht: Sie hängen an Kontakten und
   Verkaufschancen und werden **nicht** automatisch entfernt; eine Löschung ist eine
   manuelle Odoo-Aktion (Administrator) je Eintrag oder per Skript.
5. Eigentümer-Zuordnung am Postfach anpassen, wenn ein Funktionspostfach an eine andere
   Person übergeht; private Zeilen werden dann für den neuen Eigentümer sichtbar.

## 8. Offene Punkte, die das Unternehmen entscheiden muss

* **Betriebsvereinbarung**: Einbeziehung persönlicher Postfächer, Informationspflicht
  gegenüber Beschäftigten, Regel für die Kennzeichnung privater Nachrichten (Präfix, Ordner),
  Umgang mit Nachrichten vor Inkrafttreten (Startdatum des Backfills).
* **Private Nutzung** des dienstlichen Postfachs: erlaubt oder untersagt; davon hängt ab,
  ob die Vorschau (255 Zeichen) auch privater Nachrichten im Register vertretbar ist und ob
  Postfächer mit `Sichtbarkeit = privat` betrieben werden müssen.
* **Datenschutz-Folgenabschätzung**: Ob eine DSFA nötig ist (systematische Verarbeitung von
  Kommunikationsdaten Beschäftigter und Dritter).
* **Information externer Kommunikationspartner** (Art. 13/14 DSGVO): Datenschutzhinweise um
  die CRM-Ablage von E-Mail-Korrespondenz ergänzen.
* **Aufbewahrungsfristen** für Chatter-Einträge und Registerzeilen jenseits der
  automatischen Löschung nicht zugeordneter Zeilen; Umgang mit Löschanfragen Betroffener.
* **Berechtigungskonzept**: Wer erhält „Mail Sync: Benutzer“ (mit `crm_mail_sync` alle
  Vertriebsbenutzer, damit Einsicht in geteilte Registerzeilen aller Postfächer) und wer
  „Verwalter“ (Einsicht in alle, auch private Zeilen).
* **Sichtbarkeitsvorgabe je Postfach**: geteilt (Standard) oder privat mit Freigabe durch
  den Eigentümer.
* **Freigabegruppe** (App-Modus): Wer pflegt die Mitgliedschaft, wie wird die Entfernung
  der mandantenweiten Zustimmung geprüft (siehe Konfigurationsanleitung, Abschnitt 2.3).
* **Auftragsverarbeitung**: Microsoft 365 und ggf. Odoo-Hosting im Verzeichnis der
  Verarbeitungstätigkeiten und in AV-Verträgen berücksichtigen.
* **Technische und organisatorische Maßnahmen**: Verwahrung des Client-Secrets, Rotation
  vor Ablauf, Zugriff auf `database.secret` (Schlüsselmaterial für Token-Verschlüsselung
  und OAuth-State).
