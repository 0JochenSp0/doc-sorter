# Dokumenten-Sorter (Raspberry Pi, Docker, Web-UI)

Ein leichtgewichtiges, Docker-basiertes System für Raspberry Pi, das **nur PDF-Dateien** aus einem Inbox-Ordner verarbeitet:
- stabilitätsgeprüft (2-Minuten-Regel),
- Versanddatum + Absender heuristisch extrahiert,
- bei fehlender Textschicht automatisch **OCR** (Deutsch) ausführt,
- Dateien normiert umbenennt,
- in eine strukturierte Ausgabe verschiebt,
- Duplikate per `_dup_{n}` nummeriert,
- unsichere/fehlerhafte Fälle in einen Review-Ordner verschiebt,
- revisionssicher in **SQLite** protokolliert,
- Steuerung/Status über Web-UI im LAN (Port **5434**).

---

## Architekturübersicht

Ein Container enthält:
- **FastAPI**: Web-UI + API
- **APScheduler**: Ausführung **jede volle Stunde**
- **Worker**: sequentielle Verarbeitung (>= 100 PDFs/h)
- **SQLite**: Audit-Log + Settings (persistiert in Docker-Volume)

Host-Mount:
- Nur bereits hostseitig gemountete Verzeichnisse unter `/media` werden genutzt.
- UI-Folder-Selector kann ausschließlich innerhalb `/media` browsen.

---

## Voraussetzungen (Raspberry Pi)

- Raspberry Pi OS (64-bit empfohlen)
- Docker + Docker Compose Plugin

### Docker Installation (Kurz)

Siehe offizielle Docker-Dokumentation für Debian/Raspberry Pi OS.

---

## Docker Compose Beispiel

Dieses Repo enthält eine `docker-compose.yml`:
- Port: **5434**
- Volume: `/media:/media`
- Persistenz: `doc-sorter-data` (SQLite DB)

Start:
```bash
docker compose up -d --build
```

Öffnen im LAN:
- `http://<raspberry-ip>:5434`

---

## NFS Mount Anleitung (Beispiel /etc/fstab)

Beispiel: NAS-Share nach `/media/NAS` mounten.

1) Mountpunkt:
```bash
sudo mkdir -p /media/NAS
```

2) `/etc/fstab` Beispiel:
```fstab
# <server>:/export  <mountpoint>   <type>  <options>                               <dump> <pass>
192.168.1.10:/volume1/docs  /media/NAS      nfs    rw,hard,intr,noatime,_netdev     0      0
```

3) Mounten:
```bash
sudo mount -a
```

---

## Rechteprobleme & Troubleshooting

### 1) Container kann nicht verschieben/erstellen
- Prüfe Schreibrechte auf `/media/...`
- Test:
```bash
docker exec -it doc-sorter sh -lc 'touch /media/test_write && rm /media/test_write'
```

### 2) OCR sehr langsam / fehlgeschlagen
- OCR ist CPU-intensiv (Pi 4 ok, Pi 3 langsamer)
- Stelle sicher: PDFs sind nicht beschädigt.
- Review-Ordner prüfen.

### 3) Nichts passiert zur vollen Stunde
- UI öffnen → Status prüfen → „Jetzt ausführen“
- Logs liegen im konfigurierten Log-Ordner (Settings).

---

## Erklärung der UI

- Ordner setzen:
  - Inbox
  - Output
  - Review
  - Log-Ordner
- Status:
  - Letzter Lauf: Zeitpunkt + Dauer
  - Counts: success / review / ignored / error
  - Letzte Fehler
- Button: **Jetzt ausführen**
- Folder-Selector: serverseitiges Browsing **nur unter `/media`**

---

## Beispiel-Workflows

### Standard
1) PDFs in Inbox ablegen (z.B. Scanner-Upload)
2) Jede volle Stunde läuft die Verarbeitung
3) Ergebnis:
   - Output: `<output>/<jahr>/<absender>/YYYY-MM-DD_Absender.pdf`
   - oder bei unsicherem Absender: `<output>/<jahr>/<monat>/...`
4) Problemfälle landen in Review

### Duplikate
- Existiert Zielname bereits:
  - `_dup_1`, `_dup_2`, ...

---

## Audit-Log (SQLite)

Pro Datei werden gespeichert:
- ursprünglicher Pfad + Name
- extrahiertes Datum
- erkannter Absender + Confidence
- Zielpfad + neuer Name
- Duplikatnummer
- Zeitstempel
- Status (success/review/ignored/error)
- Fehlermeldung (falls vorhanden)

DB liegt im Container unter `/data/app.db` (persistiert via Volume).

---
