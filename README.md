# Dokumenten-Sorter (Docker) – Anleitung für Einsteiger

Dieses Projekt überwacht einen **Inbox-Ordner** (nur **PDF**), erkennt **Datum** und **Absender**, benennt die Dateien um und verschiebt sie in eine **Jahr/Monat-Struktur**. Dokumente, die unsicher sind, landen im **Review-Ordner** und können in der Web‑Oberfläche bequem nachbearbeitet werden.

Die Anleitung ist bewusst so geschrieben, dass sie auch ohne Raspberry‑Erfahrung und ohne Programmierkenntnisse funktioniert.

---

## Was kann das Tool?

### ✅ Automatik (Inbox → Output)
- Nimmt **nur PDF-Dateien** aus dem Inbox-Ordner.
- Erkennt **Datum** (z. B. Versanddatum / Rechnungsdatum / Belegdatum …).
- Schätzt den **Absender** (Briefkopf / Header).
- Erstellt automatisch Ordner:
  - **Output/<Jahr>/<Monat>/**
- Benennt Dateien nach Schema:
  - `YYYY-MM-DD_<Absender>.pdf` (taggenau)  
  - `YYYY-MM_<Absender>.pdf` (monatsgenau, wenn kein Tag gefunden)
- Erkennt Duplikate und hängt automatisch an:
  - `..._dup_1.pdf`, `..._dup_2.pdf`, ...

### 🟡 Review-Workflow (für Sonderfälle)
Wenn Datum oder Jahr nicht sicher ist, wird die Datei in den **Review-Ordner** verschoben.
In der Web‑UI kannst du dann:
- PDF öffnen
- Datum eingeben (YYYY-MM oder YYYY-MM-DD)
- optional Sender setzen
- **Apply** klicken → Datei wird in Output verschoben und sauber benannt

### 🏦 Bank-Routing (optional)
Wenn du eine Liste von Bank-/Depot-Absendern pflegst, kann das Tool Bank-Dokumente in:
- **Output/<Jahr>/<Bank-Ordnername>/**
legen.

---

## Voraussetzungen

Du brauchst:
- Einen Computer oder Server (Windows / macOS / Linux / Raspberry Pi)
- **Docker** und **Docker Compose**
- Zugriff auf Ordner, die als **/media** in den Container gemountet werden

> Wichtig: Dieses Setup funktioniert auch ohne Raspberry – der Raspberry ist nur ein möglicher Host.

---

## 1) Docker installieren

### Windows / macOS
- Installiere **Docker Desktop**
- Stelle sicher, dass Docker läuft (Docker-Symbol sichtbar)

### Linux (Ubuntu/Debian)
- Installiere Docker Engine + Compose Plugin (typisch via Paketmanager)
- Prüfe:
  - `docker --version`
  - `docker compose version`

Wenn beide Befehle funktionieren, passt es.

---

## 2) Ordner anlegen (Inbox/Output/Review)

Lege dir auf deinem System drei Ordner an (Beispiel):

- `/media/inbox`
- `/media/output`
- `/media/review`

> Du kannst andere Pfade nehmen – wichtig ist nur: sie müssen innerhalb von **/media** liegen, weil der Container `/media` als Root sieht.

---

## 3) Projekt starten (Docker)

Im Projektordner (da wo `docker-compose.yml` liegt) ausführen:

```bash
docker compose up -d --build
```

Danach prüfen:

```bash
docker compose ps
```

Du solltest sehen, dass der Container läuft.

---

## 4) Web‑UI öffnen

Öffne im Browser:

- `http://<DEIN-HOST>:5434`

**Beispiele**
- Auf demselben Rechner: `http://localhost:5434`
- Im Heimnetz: `http://192.168.x.x:5434`

> Port ist hier bewusst: **5434**

---

## 5) Grundkonfiguration in der UI

In der Web‑UI unter **Konfiguration** setzt du:

1. **Inbox-Ordner**  
   Der Ordner, in den du neue PDFs legst.

2. **Output-Ordner**  
   Hier wird die Zielstruktur erzeugt (Jahr/Monat).

3. **Review-Ordner**  
   Unsichere Dokumente landen hier. In der UI kannst du sie korrigieren.

4. **Log-Ordner** (optional)  
   Falls leer, verwendet das Tool automatisch einen Log-Ordner unter `/media`.

5. **Year-Check Policy** (wichtig)  
   Steuert, wie streng das Tool ist, wenn das erkannte Jahr nicht zum Scan-/Metadatenjahr passt:

   - **strict**: nur Scan‑Jahr oder Scan‑Jahr‑1 wird akzeptiert  
   - **relaxed**: erlaubt +/- Toleranz (siehe Feld darunter)  
   - **off**: nie Review nur wegen Jahr

6. **Relaxed-Toleranz (Jahre)**  
   Nur relevant, wenn du `relaxed` nutzt.

7. **Absenderliste** (optional, aber empfehlenswert)  
   Eine Zeile pro Absendername. Hilft bei sauberer Zuordnung.

8. **Bank-Absenderliste** + **Bank-Ordnername** (optional)  
   Wenn du Bank‑Routing möchtest.

Dann **Speichern** klicken.

---

## 6) Erster Testlauf (empfohlen)

1. Lege 1–3 PDFs in den Inbox-Ordner.
2. Klicke in der UI auf **Jetzt ausführen**.
3. Schaue:
   - Output: neue Ordner und umbenannte PDFs
   - Review: ggf. Dateien, die manuell bestätigt werden müssen

---

## Review-Queue benutzen (das Wichtigste)

In der UI findest du den Bereich **Review‑Queue**:

- Jede Datei hat:
  - **Öffnen** (PDF ansehen)
  - Feld für Datum (`YYYY-MM` oder `YYYY-MM-DD`)
  - optional Sender
  - **Apply**

Wenn du Apply klickst:
- Die Datei wird nach Output verschoben
- korrekt umbenannt
- als „manual_apply“ im Audit protokolliert

---

## Häufige Fragen / Probleme

### „Es passiert nichts“
- Prüfe, ob du wirklich PDFs in der Inbox hast.
- Prüfe, ob Inbox/Output/Review korrekt gesetzt und erreichbar sind.
- In der UI → **Status** ansehen (Running? Letzter Lauf? Fehler?)

### „Pfad ist ungültig“
Das Tool akzeptiert nur Pfade, die **innerhalb von /media** liegen.  
Stelle sicher, dass dein Docker Compose wirklich `/media:/media` gemountet hat.

### „Review‑Ordner zeigt nichts“
- Prüfe, ob `review_dir` gesetzt ist.
- Prüfe, ob wirklich PDFs im Review-Ordner liegen.

### „Duplikate“
Wenn eine Datei mit gleichem Namen schon existiert, wird automatisch:
- `_dup_1`, `_dup_2`, … angehängt  
So gehen keine Dateien verloren.

---

## Was wird **nicht** gemacht?

- **Keine** Bearbeitung anderer Dateitypen als PDF
- **Kein** Löschen von Originalen außerhalb der definierten Verschiebung
- **Kein** Upload in Cloud oder externe Dienste

---

## Betrieb im Alltag (Empfehlung)

1. Scanner / Handy‑Upload so einstellen, dass PDFs in **Inbox** landen
2. Regelmäßig:
   - UI öffnen
   - **Review‑Queue** abarbeiten
3. Fertig.

---

## Update / Neustart

Wenn du Code aktualisiert hast:

```bash
docker compose down
docker compose up -d --build
```

---

## Daten & Persistenz

- Einstellungen und Audit werden in einer SQLite‑DB im Docker Volume gespeichert (siehe Compose).
- PDFs liegen **in deinen gemounteten Ordnern** (Inbox/Output/Review), also außerhalb des Containers.

---

## Sicherheitshinweise

- Die UI ist für **LAN** gedacht.
- Wenn du Port‑Forwarding ins Internet machst: bitte mit Auth/Reverse‑Proxy absichern.

---

Viel Erfolg beim Sortieren!
