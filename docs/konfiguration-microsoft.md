# Einrichtung: Microsoft Entra, Exchange Online und Odoo

Schritt-für-Schritt-Anleitung für `mail_sync_microsoft`. Es gibt zwei Anbindungsarten;
Abschnitt 1 und 4 gelten für beide, Abschnitt 2 nur für den zentralen App-Modus,
Abschnitt 3 nur für den delegierten Modus.

Benötigte Rollen: Entra „Anwendungsadministrator“ (App-Registrierung, Admin-Zustimmung),
Exchange Online „Organisationsverwaltung“ (RBAC for Applications), Odoo-Administrator.

## 1. App-Registrierung in Microsoft Entra

1. Entra Admin Center → **Anwendungen → App-Registrierungen → Neue Registrierung**.
   * Name: z. B. `Odoo Mail Sync`.
   * Kontotypen: „Nur Konten in diesem Organisationsverzeichnis“ (Single Tenant).
   * Umleitungs-URI (nur für den delegierten Modus nötig, schadet im App-Modus nicht):
     Plattform **Web**, Wert `<base_url>/mail_sync/oauth/callback`, wobei `<base_url>` dem
     Odoo-Systemparameter `web.base.url` entspricht (HTTPS, ohne abschließenden Schrägstrich).
2. Notieren: **Anwendungs-ID (Client-ID)** und **Verzeichnis-ID (Mandanten-ID)** von der
   Übersichtsseite.
3. **Zertifikate & Geheimnisse → Neuer geheimer Clientschlüssel**: Beschreibung und Laufzeit
   wählen, den **Wert** sofort kopieren (er ist später nicht mehr sichtbar). Das Ablaufdatum
   in den Betriebskalender eintragen; nach Ablauf gehen alle Postfächer auf `auth_error`.
4. **API-Berechtigungen**:
   * App-Modus: `Microsoft Graph → Anwendungsberechtigungen → Mail.Read` hinzufügen und
     **Administratorzustimmung erteilen**. Diese Zustimmung gilt zunächst für **alle**
     Postfächer des Mandanten; Abschnitt 2 schränkt sie ein.
   * Delegierter Modus: `Microsoft Graph → Delegierte Berechtigungen → Mail.Read` und
     `offline_access` hinzufügen. Administratorzustimmung ist optional; ohne sie muss jeder
     Benutzer beim ersten Verbinden selbst zustimmen (sofern die Mandantenrichtlinie das
     erlaubt).

„Verbindung testen“ ruft zuerst `GET /organization` auf, um die eigenen Domains
(`verifiedDomains`) vorzubelegen. Mit reiner Exchange-RBAC-Freigabe (ohne
Verzeichnisberechtigung) antwortet Graph darauf mit 401/403; der Test weicht dann auf einen
Ordnerabruf des ersten angelegten Postfachs aus. In diesem Fall also zuerst ein Postfach
anlegen, die eigenen Domains manuell im Feld „Eigene Domains“ eintragen und den Test
danach ausführen.

## 2. App-Modus: Zugriff mit Exchange „RBAC for Applications“ einschränken

Ziel: Die App darf nur die Postfächer lesen, die Mitglied einer bestimmten Gruppe sind.

### 2.1 Gruppe anlegen

Im Exchange Admin Center oder in Entra eine **E-Mail-aktivierte Sicherheitsgruppe** anlegen,
z. B. `CRM-Mail-Sync`, und die zu synchronisierenden Postfächer (persönliche und
Funktionspostfächer) als Mitglieder aufnehmen. Eine reine Entra-Sicherheitsgruppe ohne
E-Mail-Aktivierung oder eine Microsoft-365-Gruppe funktioniert für den Filter nicht.

### 2.2 Service Principal, Scope und Rollenzuweisung

In PowerShell mit dem Modul `ExchangeOnlineManagement` (Version 3.x):

```powershell
Connect-ExchangeOnline

# Objekt-ID des Enterprise-Application-Objekts (nicht die Objekt-ID der App-Registrierung):
# Entra → Unternehmensanwendungen → <App> → Objekt-ID
New-ServicePrincipal -AppId <app-id> -ObjectId <sp-object-id> -DisplayName "Odoo Mail Sync"

# Distinguished Name der Gruppe ermitteln
(Get-DistributionGroup "CRM-Mail-Sync").DistinguishedName

# Scope: nur Empfänger, die Mitglied der Gruppe sind
New-ManagementScope -Name "MailSyncScope" `
    -RecipientRestrictionFilter "MemberOfGroup -eq '<group DN>'"

# Rolle "Application Mail.Read" nur innerhalb des Scopes zuweisen
New-ManagementRoleAssignment -App <app-id> -Role "Application Mail.Read" `
    -CustomResourceScope "MailSyncScope"

# Prüfen: Ergebnis "InScope = True" für ein Gruppenmitglied, False für ein anderes Postfach
Test-ServicePrincipalAuthorization -Identity <app-id> -Resource <mailbox>
```

### 2.3 Mandantenweite Zustimmung entfernen

Die in Entra erteilte Anwendungsberechtigung `Mail.Read` und die Exchange-Rollenzuweisung
wirken **als Vereinigung**: Solange die mandantenweite Zustimmung besteht, darf die App
weiterhin jedes Postfach lesen, unabhängig vom Scope. Deshalb nach erfolgreichem Test:

1. Entra → **Unternehmensanwendungen → `Odoo Mail Sync` → Berechtigungen**: die
   Zustimmung für `Mail.Read` (Anwendung) widerrufen, oder
2. in der App-Registrierung die Anwendungsberechtigung `Mail.Read` entfernen.

Die App erhält ihr Token weiterhin über `.default`; Exchange prüft dann nur noch die
RBAC-Rollenzuweisung. Danach erneut mit `Test-ServicePrincipalAuthorization` und mit
„Verbindung testen“/einem Delta-Lauf in Odoo prüfen.

### 2.4 Propagation

Neue Rollenzuweisungen, Scope-Änderungen und Gruppenmitgliedschaften brauchen bis zu
**zwei Stunden**, bis Exchange sie überall anwendet. Bis dahin liefern Graph-Aufrufe 403 und
das Postfach in Odoo geht auf `auth_error`. Nach Ablauf der Frist am Postfach „Jetzt
abgleichen“ auslösen bzw. den Zustand zurücksetzen.

Hinweis: Das ältere Verfahren `New-ApplicationAccessPolicy` erreicht dasselbe Ziel, ist aber
von Microsoft zugunsten von RBAC for Applications abgekündigt.

## 3. Alternative: Delegierter Modus

Ohne Exchange-Administrator oder für Einzelpersonen verbindet jeder Benutzer sein Postfach
selbst:

1. App-Registrierung wie in Abschnitt 1 mit Umleitungs-URI und den delegierten
   Berechtigungen `Mail.Read`, `offline_access`.
2. Verbindung in Odoo mit `auth_mode = Je Benutzer (delegiert)` anlegen; auch hier wird das
   Client-Secret benötigt (Token-Austausch).
3. Je Postfach ein Datensatz mit `owner_user_id` (Pflicht). Der Eigentümer öffnet das
   Postfach und klickt **„Postfach verbinden“**; er wird zu Microsoft geleitet, meldet sich
   mit dem Postfach an (`login_hint` ist vorbelegt) und kehrt nach Odoo zurück. Das
   Refresh-Token wird verschlüsselt am Postfach gespeichert; die Ordner-Discovery startet
   automatisch.
4. Widerruft der Benutzer die Zustimmung (Microsoft „Meine Apps“) oder läuft das Token ab,
   geht das Postfach auf `auth_error` und muss neu verbunden werden.

Der Zugriff ist auf das Postfach des angemeldeten Benutzers beschränkt; Funktionspostfächer
sind nur erreichbar, wenn der Benutzer sich mit deren Konto anmelden kann.

## 4. Odoo-Seite

### 4.1 Voraussetzungen

* Module `queue_job` (OCA), `mail_sync_microsoft` und ggf. `crm_mail_sync` installiert.
* Python-Pakete `msal` und `requests` im Odoo-Umfeld.
* Jobrunner von `queue_job` aktiv: in `odoo.conf` `server_wide_modules = base,web,queue_job`
  und ein `[queue_job]`-Abschnitt mit `channels = root:4` (oder entsprechend). Der Kanal
  `root.mail_sync` mit Kapazität 4 wird vom Modul angelegt.
* `web.base.url` zeigt auf die öffentliche HTTPS-Adresse (nur delegierter Modus).

### 4.2 Client-Secret hinterlegen

Zwei Wege, gesteuert über `credential_source` der Verbindung:

* **Umgebungsvariable** (Standard): `MAIL_SYNC_CLIENT_SECRET=<wert>` im Prozessumfeld des
  Odoo-Servers, z. B. über `.env` von Docker Compose (siehe `.env.example`). Alternativ je
  Verbindung `MAIL_SYNC_CLIENT_SECRET_<id>` mit der Datenbank-ID der Verbindung.
* **Systemparameter**: Verbindung zuerst speichern, dann „Secret hinterlegen“ klicken; es
  öffnet sich der Parameter `mail_sync.client_secret.<id>`, in dessen Feld „Wert“ das Secret
  eingetragen wird. Der Parameter ist nur für Administratoren lesbar.

Das Feld „Secret konfiguriert“ zeigt an, ob ein Wert gefunden wird.

### 4.3 Verbindung anlegen und testen

**Einstellungen → E-Mail-Sync → Verbindungen → Neu**:

1. Name, Tenant-ID, Client-ID, Anbindungsart, Secret-Quelle eintragen; Firma prüfen.
2. „Regeln“: Aufbewahrung nicht zugeordneter Mails (Standard 30 Tage), Anhangsgrenze
   (Standard 10 MB).
3. Speichern, dann **„Verbindung testen“**. Bei Erfolg wird der Zustand `Verbunden` und die
   eigenen Domains werden aus Microsoft 365 übernommen, falls das Feld leer war und die App
   das Verzeichnis lesen darf. Bei reiner Exchange-RBAC-Freigabe: erst ein Postfach anlegen
   (Abschnitt 4.4), Domains manuell eintragen, dann testen; der Test prüft dann den
   Ordnerzugriff auf dieses Postfach. Domains prüfen und ggf. ergänzen (Subdomains, alte
   Domains).
4. Freemail-Domains und „Ausgeschlossene Ordner“ (Muster gegen den Ordnerpfad, Vorbelegung
   u. a. `privat`, `bewerbung`, `zeiterfassung`) bei Bedarf anpassen. Die Muster wirken nur
   auf die Vorbelegung neu gefundener Ordner.

### 4.4 Postfächer anlegen

**Einstellungen → E-Mail-Sync → Postfächer → Neu** (oder über den Smart Button der
Verbindung):

1. UPN eintragen; ein Odoo-Benutzer mit gleicher E-Mail wird als Eigentümer vorgeschlagen.
2. Art (persönlich / Funktionspostfach), Eigentümer, Sichtbarkeit (geteilt / privat),
   Startdatum (Standard 90 Tage zurück; ein früheres Datum verlängert den Backfill deutlich),
   „Gesendete Elemente einbeziehen“, „Kontakte automatisch anlegen“.
3. Reiter „Ausschlüsse“: private Betreffpräfixe, Blocklisten.
4. Mit `crm_mail_sync`: „Vertriebspostfach“, Verhalten bei neuen Anfragen, Verkaufsteam.
5. Speichern. Im App-Modus muss das Postfach Mitglied der Freigabegruppe sein; im
   delegierten Modus jetzt „Postfach verbinden“.

### 4.5 Ordner einlesen

„Ordner einlesen“ reiht einen Job ein. Nach dem Lauf im Reiter „Ordner“ die Spalte
„Synchronisieren“ prüfen: Posteingang, Archiv und Benutzerordner sind eingeschlossen,
Entwürfe/Junk/Gelöscht/Postausgang, Ordner mit einem Muster aus „Ausgeschlossene Ordner“
im Pfad und deren Unterordner nicht. Anpassen und speichern.

### 4.6 Backfill

„Backfill starten“ erzeugt je eingeschlossenem Ordner einen Job, der alle Nachrichten ab dem
Startdatum liest und den Delta-Link setzt. Große Postfächer brauchen mehrere Stunden; die
Jobs laufen mit maximal 4 parallelen Aufrufen und wiederholen sich bei Drosselung (429)
automatisch. Ohne manuellen Backfill macht der erste Delta-Lauf des Crons dasselbe.

### 4.7 Läufe beobachten

* **Einstellungen → E-Mail-Sync → Läufe**: je Job Art, Zähler, Dauer, Fehlertext.
* **Einstellungen → Technisch → Warteschlange → Jobs**: Zustand der `queue_job`-Jobs
  (wartend, laufend, fehlgeschlagen), Kanal `root.mail_sync`.
* **Postfächer**: Zustand, letzter Abgleich, Zähler; Reiter „Fehler“ mit letztem Fehler.
* **Register**: Zeilen nach Zustand gruppiert; Spalte „Grund“ erklärt jede Entscheidung.
* Der Cron „Mail Sync: Postfächer abgleichen“ läuft alle 5 Minuten; „Jetzt abgleichen“ am
  Postfach erzwingt einen Lauf.

### 4.8 Benutzer berechtigen

* Verwalter: Gruppe „Mail Sync: Verwalter“ (Menü Einstellungen → E-Mail-Sync).
* Benutzer: Gruppe „Mail Sync: Benutzer“ (Menü Dialog → Meine E-Mails, Smart Buttons).
  Mit `crm_mail_sync` erhalten alle Vertriebsbenutzer diese Gruppe automatisch.
