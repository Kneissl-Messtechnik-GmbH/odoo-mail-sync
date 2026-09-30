# CRM Mail Sync (`crm_mail_sync`)

Erweitert [`mail_sync_microsoft`](../mail_sync_microsoft/README.md) um die Zuordnung
synchronisierter Konversationen zu Verkaufschancen (`crm.lead`, `type = opportunity`).
Das Modul hängt sich als `LeadRouter` in den Router des Kernmoduls (`_extra_target`) und wird
automatisch installiert, sobald `mail_sync_microsoft` und `crm` installiert sind.

* Odoo 18.0 Community, Lizenz LGPL-3
* Abhängigkeiten: `mail_sync_microsoft`, `crm`

## 1. Regeln D1–D7 (wie in `services/lead_rules.py` implementiert)

Voraussetzung für D1–D5: Der Kern-Router hat über K1–K3 mindestens einen Kontakt oder eine
Firma gefunden. Nachrichten ohne bekannten Kontakt bleiben `unmatched`, auch wenn sie eine
Deal-Referenz enthalten. Die Regeln werden in dieser Reihenfolge geprüft; der erste Treffer
bestimmt das Ziel.

### D1 – Fortsetzung einer Konversation

Gibt es im Register (über alle Postfächer) bereits eine Zeile mit derselben Graph
`conversation_id` und gesetztem `lead_id`, wird dieser Lead das Ziel. Es wird nur geprüft, ob
der Lead noch existiert; Zustand, Phase oder Archivierung spielen keine Rolle. Laufende
Threads bleiben damit auch nach Gewonnen/Verloren am Lead. Grund: „D1: Fortsetzung der
Konversation auf …“.

Zusätzlich prüft der Import im Kernmodul die Header `References`/`In-Reply-To` gegen
`mail.message`; hängt die referenzierte Nachricht an einem Datensatz, wird dieser zum Ziel
(Grund „D1: Antwort in bestehender Konversation auf …“).

### D2 – Deal-Referenz im Betreff

`deal_reference_regex` am Konto (Standard `\bID[\s:#-]*(\d{3,})\b`, eine Gruppe) wird
case-insensitiv auf den **Betreff** angewendet (nicht auf den Body). Bei Treffer:

1. `crm.lead` (inkl. archivierter) mit `name ilike <gesamter Treffertext>` suchen; genau ein
   Treffer → dieser Lead.
2. Sonst, wenn der Gruppeninhalt eine Zahl ist: Lead mit dieser Datenbank-ID, falls vorhanden.

Ein ungültiges Muster wird protokolliert und ignoriert. Grund: „D2: Deal-Referenz im Betreff → …“.

### Kandidaten für D3–D5

Alle `crm.lead` mit `type = opportunity`, `active = True`, Phase nicht `is_won`,
`probability < 100`, deren `partner_id` einer der gematchten Kontakte, eine der gematchten
Firmen oder ein Kindkontakt einer gematchten Firma ist.

### D3 – Genau ein Kandidat

Genau eine offene Verkaufschance → Ziel. Grund: „D3: einzige offene Verkaufschance von …“.

### D4 – Mehrere Kandidaten: Scoring

Punkte je Kandidat, Gewichte am Konto einstellbar:

| Kriterium | Feld | Standard |
|---|---|---|
| Lead-Verantwortlicher ist der Eigentümer des Postfachs | `score_owner` | 3 |
| Betreff teilt Wörter mit dem Lead-Namen (Reply-Präfixe wie AW/RE/WG/FW entfernt, Wörter ab 4 Zeichen, Stoppwortliste) | `score_subject` | 2 |
| `write_date` oder `date_last_stage_update` des Leads innerhalb der letzten 14 Tage | `score_activity` | 2 |
| Jüngster Kandidat nach `create_date` | `score_recent` | 1 |

Liegt der beste Kandidat mindestens `score_threshold` Punkte (Standard 3; negative Werte
zählen als 0) vor dem zweitbesten, wird er zugeordnet (Grund „D4: n offene Verkaufschancen,
… mit x Punkten (y für Platz 2)“). Sonst wird **kein** Lead gesetzt: Die Nachricht wird wie
ohne CRM an den Kontakt bzw. die Firma gehängt (`state = linked`), die Kandidaten stehen in
`candidate_lead_ids`, Grund „D4: … kein klarer Favorit → Vorschlag“. Solche Zeilen erscheinen
unter „E-Mail-Vorschläge“ und am Lead als „Vorschläge“.

### D5 – Kein Kandidat: Lead-Vorschlag im Vertriebspostfach

Ohne Kandidaten bleibt die Zuordnung an Kontakt/Firma. Ist am Postfach `is_sales_mailbox`
gesetzt, die Nachricht `inbound` und für dieselbe `conversation_id` noch keine Zeile mit
`lead_proposal = True` vorhanden:

* `lead_proposal = suggest`: die Zeile wird mit `lead_proposal = True` markiert (Grund
  „D5: neue Anfrage, Lead vorgeschlagen“). Der Button „Lead anlegen“ erzeugt später eine
  Verkaufschance (Name = Betreff oder „Anfrage von <Absender>“, Partner = erster gematchter
  Kontakt, sonst `email_from`, Team = `lead_team_id`, Verantwortlicher = Postfach-Eigentümer,
  Beschreibung = `body_preview`) und verknüpft die ganze Konversation.
* `lead_proposal = create`: die Verkaufschance wird sofort angelegt (gleiche Felder, ohne
  Beschreibung) und ist das Ziel (`state = linked`, Grund „D5: neue Anfrage im
  Vertriebspostfach → Verkaufschance … angelegt“). Die Erzeugung läuft mit Kontext
  `mail_sync_import`, sodass D6 nicht ausgelöst wird.

### D6 – Rückwirkende Vorschläge beim Anlegen eines Leads

Beim Anlegen einer Verkaufschance (`type = opportunity`) mit `partner_id` – außer im Kontext
`mail_sync_import` – sucht das Modul Register-Zeilen (mit `sudo`, also über alle Postfächer)
mit:

* `partner_ids` in {Partner, dessen Firma, deren Kindkontakte},
* `received_at` innerhalb der letzten `retro_days` Tage (Wert vom ersten gefundenen Konto,
  Standard 30),
* `visibility = shared`, `lead_id` leer, `state` in `linked` oder `matched`.

Gefundene Zeilen erhalten den neuen Lead in `candidate_lead_ids`; im Chatter des Leads
erscheint eine Notiz mit der Anzahl gefundener Konversationen und dem Hinweis auf den
Button „Vorschläge“. Zeilen im Zustand `unmatched` können nicht vorgeschlagen werden, da sie
keinen Kontakt haben.

### D7 – Manuelle Zuordnung

Assistent `mail.sync.link.wizard` („Konversation verknüpfen“):

* Aufruf aus Register-Liste und -Formular (Button „Verknüpfen“, bei privaten Zeilen
  ausgeblendet), aus der Aktionsliste der Register-Ansicht (Mehrfachauswahl) und über den
  Smart Button „Vorschläge“ am Lead (Lead ist dann vorbelegt).
* Felder: Kandidaten (Anzeige), Verkaufschance (nur `type = opportunity`), „Ganze
  Konversation“ (Standard an: alle Zeilen mit derselben `conversation_id` werden mitgezogen).
* „Verknüpfen“: Kandidaten und `lead_proposal` werden geleert, `action_link_to` setzt Ziel
  und `state = linked` (Grund „Manuell zugeordnet“), ein vorhandener Chatter-Eintrag wird auf
  den Lead umgehängt, sonst wird der Import als Job eingereiht.
* „Zuordnung lösen“: `action_unlink_target` hängt Zeile und Chatter-Eintrag zurück an die
  Firma des ersten Kontakts (`state = matched`) bzw. setzt `unmatched`, wenn kein Kontakt
  vorhanden ist.

Berechtigt ist jeder Benutzer der Gruppe „Mail Sync: Benutzer“, für die Zeilen, die er laut
Record Rules sieht. Eine Beschränkung auf Lead-Verantwortliche oder Vertriebsleiter gibt es
nicht.

## 2. Hinzugefügte Felder

**`mail.sync.account`** (Reiter „Verkaufschancen“): `deal_reference_regex`, `retro_days`,
`score_owner`, `score_subject`, `score_activity`, `score_recent`, `score_threshold`.

**`mail.sync.mailbox`**: `is_sales_mailbox`, `lead_proposal` (`suggest` | `create`),
`lead_team_id`.

**`mail.sync.message`**: `lead_id` (berechnet und gespeichert aus `model`/`res_id`, wenn
`model = crm.lead`), `candidate_lead_ids`, `has_candidates` (gespeichert), `lead_proposal`.

**`crm.lead`**: `mail_sync_message_ids`, `mail_sync_count`, `mail_sync_suggestion_count`.

## 3. Oberfläche

* **CRM → E-Mail-Vorschläge** (Gruppe „Mail Sync: Benutzer“): Register-Zeilen mit
  `has_candidates` oder `lead_proposal`.
* **Lead-Formular**: Smart Button „E-Mails“ (Zeilen mit `lead_id` = Lead) und Smart Button
  „Vorschläge“ (nur sichtbar, wenn der Lead in `candidate_lead_ids` einer Zeile steht; öffnet
  die Liste mit vorbelegtem Lead für den Assistenten).
* **Register** (Liste, Formular, „Meine E-Mails“): Spalten „Kandidaten“ und „Lead
  vorgeschlagen“, Buttons „Verknüpfen“ und „Lead anlegen“, Suchfilter „Vorschläge“.

## 4. Zugriff für Vertriebsbenutzer

`security/crm_mail_sync_security.xml` fügt der Gruppe `sales_team.group_sale_salesman` die
Gruppe `mail_sync_microsoft.group_mail_sync_user` als implizierte Gruppe hinzu. Jeder
Vertriebsbenutzer sieht damit geteilte Register-Zeilen aller Postfächer und die eigenen
privaten Zeilen, kann den Assistenten benutzen (`ir.model.access` für
`mail.sync.link.wizard`) und Zeilen verknüpfen. Die Record Rules des Kernmoduls bleiben
unverändert.
