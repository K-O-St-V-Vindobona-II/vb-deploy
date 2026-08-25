# vb-deploy

Zentrales Betriebshandbuch für das Vindobona-II-System (`vindobona2.at`): welches
Repo wofür da ist, wie die Produktion aufgebaut ist, und wie man sie aufsetzt,
deployt und im Ernstfall wiederherstellt. Dieses Repo selbst enthält alles
Betriebliche: Ansible-Playbooks, Caddy-Konfiguration, Podman-Quadlets und
(vault-verschlüsselte) Secrets.

Alle relevanten Repos sind in der GitHub-Organisation
[K-O-St-V-Vindobona-II](https://github.com/K-O-St-V-Vindobona-II) zusammengefasst.

## Die 4 Repos im Überblick

| Repo | Was | Tech-Stack | Wird deployt als |
|---|---|---|---|
| [`vb-api`](../vb-api) | Backend: internes Vereinsverwaltungssystem (Mitglieder/Beiträge, Standesdb, Archiv, P4x-Finanzbuchhaltung, Scheduler-Jobs, ...) | Python 3.12, FastAPI, SQLAlchemy, Alembic, PostgreSQL 18, S3-kompatibler Storage | `vb-api` + `vb-api-pg` (ein Pod) |
| [`vb-intern`](../vb-intern) | Frontend zu `vb-api`: die eigentliche Verwaltungsoberfläche für Vereinsmitglieder/Funktionäre (Login erforderlich) | Vue 3 (`<script setup>`, TypeScript), Vite, nginx zur Auslieferung | `vb-intern` |
| [`vb-www`](../vb-www) | Öffentliche, unauthentifizierte Website `www.vindobona2.at` (Marketing/Info, Galerie, Kontaktformular) | Vue 3, TypeScript, Vite, nginx | `vb-www` |
| `vb-deploy` (dieses Repo) | Betrieb: Ansible, Caddy, Quadlets, Secrets | Ansible, systemd Quadlets | läuft nicht selbst als Service — konfiguriert die anderen |

Alle drei App-Repos bauen ihr Container-Image selbst per CI/CD (GitHub Actions)
und pushen es bei jedem Merge nach `main` automatisch nach
`ghcr.io/k-o-st-v-vindobona-ii/<repo>:latest`. `vb-deploy` baut **keine** Images
und klont **keine** App-Repos auf den Produktionshost — es verteilt nur Config
und Secrets und sorgt dafür, dass die richtigen Images laufen.

## Architektur: rootless Podman auf einem VPS

Ein einzelner VPS (P-System) trägt die gesamte Produktion. Statt klassischer
Root-Docker-Container läuft alles **rootless** unter einem eigenen, unprivilegierten
Linux-User namens `service` — Container laufen dadurch ohne Root-Rechte auf dem
Host, ein kompromittierter Container kann also nicht direkt auf Host-Root
eskalieren. Ein zweiter User `admin` existiert nur für administrative
Root-Aufgaben (`sudo`), er betreibt selbst keine Container.

> **Exkurs: Warum zwei User (`admin` + `service`) statt einem?**
> Die Trennung ist eine bewusste Sicherheitsgrenze, kein Zufall. `service`
> ist der User, der tatsächlich Container ausführt — genau der User also,
> der im Fall einer Container-Escape-Schwachstelle (ein Angreifer bricht aus
> einem kompromittierten Container auf den Host aus) als Erstes betroffen
> wäre. Hätte `service` `sudo`-Rechte, wäre ein Container-Escape gleichzeitig
> ein Weg zu vollem Root auf dem Host. Da `service` **keiner** privilegierten
> Gruppe angehört und **kein** `sudo` hat, bleibt ein Ausbruch aus einem
> Container im schlimmsten Fall auf die Rechte eines gewöhnlichen,
> unprivilegierten Users beschränkt — kein Root, keine Möglichkeit, andere
> Container/Daten auf dem Host zu manipulieren, keinen Zugriff auf
> System-Konfiguration. `admin` existiert ausschließlich für Menschen, die
> administrative Aufgaben (Paketinstallation, Firewall, SSH-Konfig, ...)
> erledigen müssen — dieser User rührt nie einen Container an, hat also auch
> im Kompromittierungsfall keine Container-Angriffsfläche.

- **systemd Quadlets** statt `docker-compose`: Jeder Container/Pod wird als
  `.container`/`.pod`/`.volume`-Datei beschrieben (INI-artige Syntax), die
  `systemd --user` (dank `loginctl enable-linger service` auch ohne aktive
  Login-Session dauerhaft) automatisch in einen echten systemd-Service
  übersetzt. Vorteil ggü. `docker-compose`: native systemd-Integration
  (`systemctl --user status/restart/logs`, automatischer Neustart, Healthchecks,
  Boot-Persistenz) ohne zusätzlichen Compose-Daemon.
- **Ein Pod pro App**: `vb-api-pod` enthält `vb-api` (Backend) +
  `vb-api-pg` (PostgreSQL) — beide teilen sich ein Netzwerk-Namespace,
  das Backend erreicht die DB einfach über `localhost`. `vb-intern-pod`
  und `vb-www-pod` enthalten je einen einzelnen nginx-Container. Pod-/
  Container-Namen sind stage-unabhängig identisch (auch auf der
  Dev-Stage) — welche Stage ein Container ist, entscheidet ausschließlich
  `APP_ENVIRONMENT`/`VITE_APP_ENVIRONMENT` in seiner `EnvironmentFile`,
  nie der Name selbst.
- **Caddy** ist der einzige Dienst, der öffentlich auf Port 80/443 lauscht.
  Er terminiert TLS (automatisches Let's-Encrypt-Zertifikat) und reverse-proxied
  anhand des Hostnamens auf die jeweilige App, die selbst nur auf
  `127.0.0.1:<port>` lauscht:
  ```
  Internet
     │  :80 / :443
     ▼
  Caddy (Host-Netzwerk)
     ├─ api.vindobona2.at    → 127.0.0.1:21000 → vb-api-pod
     ├─ intern.vindobona2.at → 127.0.0.1:21001 → vb-intern-pod
     │    └─ /logging/dozzle* (Basic-Auth) → 127.0.0.1:8081 → Dozzle
     └─ www.vindobona2.at    → 127.0.0.1:21002 → vb-www-pod
  ```
- **Image-Bezug**: Alle App-Container haben `Image=ghcr.io/.../<name>:latest` +
  `AutoUpdate=registry`. Der von Podman selbst mitgelieferte
  `podman-auto-update.timer` (läuft täglich) prüft, ob sich der `:latest`-Digest
  in der Registry geändert hat, pullt bei Bedarf und startet den Container neu —
  **ganz ohne Ansible**. `vb-deploy` wird nur gebraucht, um Config/Secrets/Quadlets
  initial bzw. bei Änderungen zu verteilen, und optional für einen sofortigen
  Deploy (siehe unten), wenn man nicht auf den nächtlichen Lauf warten will.
- **Persistente Daten liegen bewusst außerhalb des VPS:** Dateien (Archiv-/
  Standesdb-Bilder, DB-Backups) liegen komplett in AWS S3 (Bucket
  `vindobona2-at`), nicht auf der VPS-Platte. **Einzige Ausnahme:** die
  Postgres-Datenbank selbst liegt lokal unter `~/data/vb/api/postgres` — das
  ist der einzige Datenbestand, der bei einem VPS-Verlust/Neuaufsetzen wirklich
  weg ist und aus einem S3-Backup zurückgeholt werden muss (siehe
  [Disaster Recovery](#disaster-recovery--datenbank-restore) unten).
- **Dozzle** ist ein simpler, schreibgeschützter Log-Viewer für alle laufenden
  Container (liest den Podman-Socket read-only), erreichbar über
  `intern.vindobona2.at/logging/dozzle` hinter Basic-Auth.
- **podman-prune.timer** räumt wöchentlich ungenutzte Images/Container auf,
  damit der begrenzte VPS-Plattenplatz nicht volläuft.

## Kompletter Cutover / VPS-Neuaufsetzen (Schritt-für-Schritt-Runbook)

Dieser Ablauf gilt sowohl für den allerersten Aufbau eines neuen VPS als auch
für jeden späteren Fall, in dem der Produktions-VPS komplett neu installiert
werden muss (Hardware-Wechsel, OS-Upgrade per Neuinstallation, Notfall). Er
wurde 1:1 so gegen einen Wegwerf-Test-VPS **und** gegen die echte Produktion
durchgespielt — jeder Schritt hier hat sich in der Praxis bewährt.

**0. Vorbereitung, bevor der alte Host abgeschaltet wird (falls möglich):**
- Wartungsfenster ankündigen.
- **Manuelles Backup auslösen**, um die Datenverlust-Lücke zum letzten
  automatischen Backup zu minimieren: `podman exec vb-api python
  scripts/backup_db.py` auf dem noch laufenden alten Host (oder der Button
  "Backup jetzt erstellen" in `vb-intern` unter System → Scheduler). Der
  tägliche `db_backup`-Job läuft sonst nur einmal pro Nacht — ohne diesen
  Schritt gehen alle Änderungen seit dem letzten nächtlichen Lauf verloren.
- Falls der Hosting-Provider einen VM-Snapshot/Image-Backup vor dem
  Reinstall anbietet: nutzen, als zusätzliches Sicherheitsnetz (in diesem
  Repo/Runbook nicht automatisiert).

**1. Neues Betriebssystem installieren.** Debian (aktuelle stabile Version),
UEFI statt BIOS/Legacy (moderner Standard, keine Nachteile bei gängigen
Cloud-/VPS-Anbietern).

**2. Root-Zugriff herstellen.**
- Falls beim Reinstall schon ein SSH-Public-Key hinterlegt wurde: direkter
  Key-Login, kein Passwort-Umweg nötig.
- Falls nur ein initiales Root-Passwort vergeben wurde: `ansible-playbook
  ... --ask-pass` verwenden (siehe Phase 1 unten) — fragt interaktiv nach dem
  SSH-Passwort. Verlangt der erste Login zusätzlich einen erzwungenen
  Passwortwechsel (kein TTY über eine einfache SSH-Verbindung verfügbar):
  einmal ganz normal interaktiv per `ssh root@<host>` einloggen und das neue
  Passwort setzen, danach erst mit Ansible weitermachen.
- **Nach einem Reinstall meldet SSH "REMOTE HOST IDENTIFICATION HAS
  CHANGED"** (neuer Host-Key) — das ist erwartet, kein Sicherheitsvorfall.
  Alten Eintrag entfernen: `ssh-keygen -f ~/.ssh/known_hosts -R <hostname>`
  (für jeden verwendeten Hostnamen/jede IP, unter der der Host bekannt war).

**3. `playbooks/setup_vps.yml` ausführen** (siehe Phase 1 unten für die
genauen Befehle). Härtet den Host, legt `admin`/`service` an, konfiguriert
Firewall + rootless Podman, endet mit einem Reboot.

**4. `playbooks/deploy.yml` ausführen** (siehe Phase 2 unten). Verteilt
Secrets/Caddyfile/Quadlets und startet den kompletten Stack. **Postgres
startet dabei mit einer leeren, frischen Datenbank** — das ist normal und
erwartet, siehe nächster Schritt.

**5. Datenbank-Restore — zwingend bei einem komplett neuen Host!** Siehe
[Disaster Recovery](#disaster-recovery--datenbank-restore) unten für die
genauen Befehle. Ohne diesen Schritt läuft der Stack zwar technisch, aber mit
einer leeren Datenbank (keine Mitglieder, keine Daten).

**6. Verifikation:**
- `curl -I https://api.vindobona2.at/`, `https://intern.vindobona2.at/`,
  `https://www.vindobona2.at/` — alle `200`, gültiges (echtes)
  Let's-Encrypt-Zertifikat.
- `systemctl --user list-units 'caddy*' 'logging*' 'vb-*'` und `podman ps`
  auf dem Zielsystem — alle Services `active`/`healthy`, keine `failed`.
- Datenbank-Stichprobe, um zu bestätigen, dass der Restore echte (nicht
  leere) Daten gebracht hat, z. B.:
  `podman exec vb-api-pg psql -U vb -d vb -c 'SELECT count(*) FROM members;'`
- `ufw status verbose` (als `admin`, mit `sudo`) — nur 22/80/443 offen.
- `systemctl --user list-timers --all` — `podman-auto-update.timer` und
  `podman-prune.timer` aktiv.

**7. Wartungsfenster schließen**, sobald alle Punkte aus Schritt 6 grün sind.

## `vb-deploy` im Detail

```
playbooks/setup_vps.yml   Bus-Faktor-Bootstrap: nacktes VPS -> gehärtetes System
playbooks/deploy.yml      laufender Betrieb: Secrets/Caddy/Quadlets syncen + starten
quadlets/                 Podman-Quadlets (.container/.pod/.volume) je Komponente
systemd-timers/           reine systemd-Timer (keine Quadlets), z.B. podman-prune.timer
config/caddy/Caddyfile    Reverse-Proxy-Konfiguration
secrets/*.env             ansible-vault-verschlüsselte Env-Dateien
```

### Voraussetzungen

- Ansible lokal installiert.
- SSH-Zugriff auf den Host — **welcher User gebraucht wird, hängt vom
  Playbook/Zeitpunkt ab** (siehe [Exkurs oben](#architektur-rootless-podman-auf-einem-vps)
  zur Rollentrennung):
  - **`root`** — nur für den allerersten Lauf von `setup_vps.yml` auf einem
    frischen Host (`-u root`, ggf. mit `--ask-pass`, siehe Runbook). Root-Login
    wird von `setup_vps.yml` selbst am Ende deaktiviert — danach geht es mit
    `root` nicht mehr.
  - **`admin`** — für jeden weiteren Lauf von `setup_vps.yml` (Re-Run/
    Verifikation), da dieser `sudo`/`become` braucht (`-u admin
    --ask-become-pass`). Wird **nicht** für `deploy.yml` gebraucht.
  - **`service`** — für `deploy.yml` (im Playbook fest als `remote_user:
    service` hinterlegt, kein `sudo` nötig, da alle Schritte rein im
    User-Scope laufen: Dateien im eigenen Home, `systemctl --user`).
  Idealerweise für `admin`/`service` passwortloser Key-Login (wird von
  `setup_vps.yml` beim Erstlauf automatisch eingerichtet), sonst
  `--ask-pass` (SSH-Login-Passwort) bzw. `--ask-become-pass`
  (`sudo`-Passwort für `admin`) verwenden.
- Ein Vault-Passwort für `secrets/*.env` (wird interaktiv abgefragt, kein
  `vault_password_file` im Repo hinterlegt).

### Phase 1 — VPS-Grundkonfiguration (nur bei Neuaufsetzung nötig)

```bash
ansible-playbook -i inventory/hosts.ini playbooks/setup_vps.yml -u root
# Falls nur Passwort-Login moeglich ist:
ansible-playbook -i inventory/hosts.ini playbooks/setup_vps.yml -u root --ask-pass

# Re-Run / Verifikation (spaeter, root-Login ist danach deaktiviert):
ansible-playbook -i inventory/hosts.ini playbooks/setup_vps.yml -u admin --ask-become-pass --check --diff
```

Erzeugt die User `admin` (sudo) und `service` (rootless Podman), härtet SSH,
öffnet nur 22/80/443 in der Firewall, macht das journald-Log persistent.
**Das generierte `admin`-Passwort wird nur einmal angezeigt** — sofort im
Passwort-Safe sichern. Endet mit einem Reboot.

### Phase 2 — Deploy (laufender Betrieb)

```bash
ansible-playbook -i inventory/hosts.ini playbooks/deploy.yml --ask-vault-pass --check --diff   # erst pruefen
ansible-playbook -i inventory/hosts.ini playbooks/deploy.yml --ask-vault-pass                  # dann anwenden
```

Synct Secrets, Caddyfile und Quadlets auf den Host und (neu-)startet die
betroffenen Dienste. Baut keine Images, klont keine Repos, restored keine
Datenbank (siehe [Disaster Recovery](#disaster-recovery--datenbank-restore)).

### Sofort-Deploy (statt auf den nächtlichen Auto-Update-Lauf zu warten)

```bash
ansible-playbook -i inventory/hosts.ini playbooks/deploy.yml --ask-vault-pass --tags deploy-api
ansible-playbook -i inventory/hosts.ini playbooks/deploy.yml --ask-vault-pass --tags deploy-intern
ansible-playbook -i inventory/hosts.ini playbooks/deploy.yml --ask-vault-pass --tags deploy-www
```

Pullt das aktuelle `:latest`-Image des jeweiligen Services aus ghcr.io und
startet den zugehörigen **Pod** neu (nicht nur den einzelnen Container — der
Container ist per generiertem `BindsTo=` an seinen Pod gebunden, ein reiner
Container-Neustart würde den Pod mitreißen, ohne dass er sich von selbst
wieder hochzieht). Läuft nur, wenn der Tag explizit angegeben wird.

### Secrets pflegen

```bash
ansible-vault edit secrets/vb-api.env
ansible-vault view secrets/vb-api.env
```

## Operative Skripte in `vb-api`

Neben den Ansible-Playbooks hier gibt es in `vb-api/scripts/` eine Reihe
Betriebs-Skripte, die **auf dem P-System, im laufenden `vb-api`-Container**
ausgeführt werden (`podman exec vb-api python scripts/<name>.py` — nicht
lokal auf dem eigenen Rechner!). Volle Doku inkl. aller Parameter:
[`vb-api/scripts/README.md`](../vb-api/scripts/README.md). Die für den
Alltag relevanten:

| Skript | Wofür |
|---|---|
| `backup_db.py` | Manuelles PostgreSQL-Backup nach S3 anstoßen (dieselbe Operation wie der tägliche `db_backup`-Scheduler-Job) — z. B. vor riskanten Änderungen oder vor einem geplanten Cutover (siehe Runbook oben). |
| `restore_db.py` | PostgreSQL aus einem S3-Backup wiederherstellen. Verweigert sich bei `APP_ENVIRONMENT=production` ohne `--force` — das ist der Weg für eine echte Disaster-Recovery auf Prod, siehe unten. |
| `check_s3_integrity.py` | Read-only-Konsistenzcheck DB ↔ S3 (fehlende Objekte + Waisen), löscht nie etwas selbst. |
| `downsync_prod.py` | Nur auf der **Dev/Non-Prod-Stage** relevant: zieht Prod-S3 + darauf aufbauend die lokale DB auf den aktuellen Prod-Stand. Verweigert sich hart auf `APP_ENVIRONMENT=production`. |

## Disaster Recovery / Datenbank-Restore

Läuft **nicht** über dieses Repo — `deploy.yml` startet Postgres nur, füllt es
aber nie mit Daten. Datei-Storage (Archiv/Standesdb-Bilder) braucht keinen
Restore, da es ohnehin durchgehend in S3 liegt (versioniert, bucket
`vindobona2-at`). Der Restore der Postgres-Datenbank ist der **einzige** noch
nötige Wiederherstellungsschritt und läuft komplett über ein Skript aus
`vb-api` — **auf dem Zielsystem selbst ausführen, per SSH auf das P-System**,
nicht lokal:

```bash
# Auf dem P-System einloggen (z.B. per SSH):
ssh service@<hostname-oder-ip-des-p-systems>

# 1. Verfuegbare Backups ansehen (optional, nur zur Kontrolle):
podman exec vb-api python scripts/restore_db.py --list

# 2. Restore ausfuehren (nimmt ohne --backup-name automatisch das neueste):
podman exec -it vb-api python scripts/restore_db.py --force
```

`--force` ist zwingend erforderlich, weil `restore_db.py` einen Restore bei
`APP_ENVIRONMENT=production` standardmäßig verweigert (Schutz vor
versehentlichem Überschreiben der Live-Datenbank) — auf Prod ist das also
kein Einmal-Skip, sondern jedes Mal so gewollt. Ohne `--backup-name <name>`
wird automatisch der zeitlich neueste Dump im S3-Bucket verwendet.

**Wann nötig:**
- Nach einem kompletten VPS-Neuaufsetzen (siehe Runbook oben, Schritt 5) —
  hier **zwingend**, da die lokale Postgres-Platte beim Reinstall verloren
  geht und `deploy.yml` nur eine leere DB hochfährt.
- Bei jedem anderen Vorfall, bei dem die Produktionsdatenbank beschädigt ist
  oder auf einen früheren Stand zurückgesetzt werden muss.

**Danach immer verifizieren**, dass wirklich echte Daten da sind (nicht nur,
dass der Befehl fehlerfrei durchlief) — z. B. per Stichprobe wie in Schritt 6
des Runbooks oben.
