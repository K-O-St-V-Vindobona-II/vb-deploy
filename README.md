# vb-deploy

Zentrales Betriebshandbuch für das Vindobona-II-System (`vindobona2.at`): welches
Repo wofür da ist, wie die Produktion aufgebaut ist, und wie man sie aufsetzt,
deployt und im Ernstfall wiederherstellt. Dieses Repo selbst enthält alles
Betriebliche: Ansible-Playbooks, Caddy-Konfiguration, Podman-Quadlets und
(vault-verschlüsselte) Secrets.

## Die 4 Repos im Überblick

| Repo | Was | Tech-Stack | Wird deployt als |
|---|---|---|---|
| [`vb-api`](../vb-api) | Backend: internes Vereinsverwaltungssystem (Mitglieder/Beiträge, Standesdb, Archiv, P4x-Finanzbuchhaltung, Scheduler-Jobs, ...) | Python 3.12, FastAPI, SQLAlchemy, Alembic, PostgreSQL 17, S3-kompatibler Storage | `vb-api-prod` + `vb-api-pg-prod` (ein Pod) |
| [`vb-intern`](../vb-intern) | Frontend zu `vb-api`: die eigentliche Verwaltungsoberfläche für Vereinsmitglieder/Funktionäre (Login erforderlich) | Vue 3 (`<script setup>`, TypeScript), Vite, nginx zur Auslieferung | `vb-intern-prod` |
| [`vb-www`](../vb-www) | Öffentliche, unauthentifizierte Website `www.vindobona2.at` (Marketing/Info, Galerie, Kontaktformular) | Vue 3, TypeScript, Vite, nginx | `vb-www-prod` |
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

- **systemd Quadlets** statt `docker-compose`: Jeder Container/Pod wird als
  `.container`/`.pod`/`.volume`-Datei beschrieben (INI-artige Syntax), die
  `systemd --user` (dank `loginctl enable-linger service` auch ohne aktive
  Login-Session dauerhaft) automatisch in einen echten systemd-Service
  übersetzt. Vorteil ggü. `docker-compose`: native systemd-Integration
  (`systemctl --user status/restart/logs`, automatischer Neustart, Healthchecks,
  Boot-Persistenz) ohne zusätzlichen Compose-Daemon.
- **Ein Pod pro App**: `vb-api-prod-pod` enthält `vb-api-prod` (Backend) +
  `vb-api-pg-prod` (PostgreSQL) — beide teilen sich ein Netzwerk-Namespace,
  das Backend erreicht die DB einfach über `localhost`. `vb-intern-prod-pod`
  und `vb-www-prod-pod` enthalten je einen einzelnen nginx-Container.
- **Caddy** ist der einzige Dienst, der öffentlich auf Port 80/443 lauscht.
  Er terminiert TLS (automatisches Let's-Encrypt-Zertifikat) und reverse-proxied
  anhand des Hostnamens auf die jeweilige App, die selbst nur auf
  `127.0.0.1:<port>` lauscht:
  ```
  Internet
     │  :80 / :443
     ▼
  Caddy (Host-Netzwerk)
     ├─ api.vindobona2.at    → 127.0.0.1:21000 → vb-api-prod-pod
     ├─ intern.vindobona2.at → 127.0.0.1:21001 → vb-intern-prod-pod
     │    └─ /logging/dozzle* (Basic-Auth) → 127.0.0.1:8081 → Dozzle
     └─ www.vindobona2.at    → 127.0.0.1:21002 → vb-www-prod-pod
  ```
- **Image-Bezug**: Alle App-Container haben `Image=ghcr.io/.../<name>:latest` +
  `AutoUpdate=registry`. Der von Podman selbst mitgelieferte
  `podman-auto-update.timer` (läuft täglich) prüft, ob sich der `:latest`-Digest
  in der Registry geändert hat, pullt bei Bedarf und startet den Container neu —
  **ganz ohne Ansible**. `vb-deploy` wird nur gebraucht, um Config/Secrets/Quadlets
  initial bzw. bei Änderungen zu verteilen, und optional für einen sofortigen
  Deploy (siehe unten), wenn man nicht auf den nächtlichen Lauf warten will.
- **Dozzle** ist ein simpler, schreibgeschützter Log-Viewer für alle laufenden
  Container (liest den Podman-Socket read-only), erreichbar über
  `intern.vindobona2.at/logging/dozzle` hinter Basic-Auth.
- **podman-prune.timer** räumt wöchentlich ungenutzte Images/Container auf,
  damit der begrenzte VPS-Plattenplatz nicht volläuft.

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
- Passwortloser SSH-Zugriff auf den Host (Key-Auth) — für `setup_vps.yml`
  beim Erstlauf als `root`, danach als `admin`.
- Ein Vault-Passwort für `secrets/*.env` (wird interaktiv abgefragt, kein
  `vault_password_file` im Repo hinterlegt).

### Phase 1 — VPS-Grundkonfiguration (nur bei Neuaufsetzung nötig)

```bash
ansible-playbook playbooks/setup_vps.yml -u root
# Re-Run / Verifikation:
ansible-playbook playbooks/setup_vps.yml -u admin --ask-become-pass --check --diff
```

Erzeugt die User `admin` (sudo) und `service` (rootless Podman), härtet SSH,
öffnet nur 22/80/443 in der Firewall, macht das journald-Log persistent.
**Das generierte `admin`-Passwort wird nur einmal angezeigt** — sofort im
Passwort-Safe sichern. Endet mit einem Reboot.

### Phase 2 — Deploy (laufender Betrieb)

```bash
ansible-playbook playbooks/deploy.yml --ask-vault-pass --check --diff   # erst pruefen
ansible-playbook playbooks/deploy.yml --ask-vault-pass                  # dann anwenden
```

Synct Secrets, Caddyfile und Quadlets auf den Host und (neu-)startet die
betroffenen Dienste. Baut keine Images, klont keine Repos.

### Sofort-Deploy (statt auf den nächtlichen Auto-Update-Lauf zu warten)

```bash
ansible-playbook playbooks/deploy.yml --ask-vault-pass --tags deploy-api
ansible-playbook playbooks/deploy.yml --ask-vault-pass --tags deploy-intern
ansible-playbook playbooks/deploy.yml --ask-vault-pass --tags deploy-www
```

Pullt das aktuelle `:latest`-Image des jeweiligen Services aus ghcr.io und
startet den Service neu. Läuft nur, wenn der Tag explizit angegeben wird.

### Secrets pflegen

```bash
ansible-vault edit secrets/vb-api-prod.env
ansible-vault view secrets/vb-api-prod.env
```

## Operative Skripte in `vb-api`

Neben den Ansible-Playbooks hier gibt es in `vb-api/scripts/` eine Reihe
Betriebs-Skripte, die **im laufenden `vb-api`-Container** ausgeführt werden
(`podman exec vb-api python scripts/<name>.py`). Volle Doku inkl. aller
Parameter: [`vb-api/scripts/README.md`](../vb-api/scripts/README.md). Die für
den Alltag relevanten:

| Skript | Wofür |
|---|---|
| `backup_db.py` | Manuelles PostgreSQL-Backup nach S3 anstoßen (dieselbe Operation wie der tägliche `db_backup`-Scheduler-Job) — z. B. vor riskanten Änderungen. |
| `restore_db.py` | PostgreSQL aus einem S3-Backup wiederherstellen. Verweigert sich bei `APP_ENVIRONMENT=production` ohne `--force` — das ist der Weg für eine echte Disaster-Recovery auf Prod. |
| `check_s3_integrity.py` | Read-only-Konsistenzcheck DB ↔ S3 (fehlende Objekte + Waisen), löscht nie etwas selbst. |
| `downsync_prod.py` | Nur auf der **Dev/Non-Prod-Stage** relevant: zieht Prod-S3 + darauf aufbauend die lokale DB auf den aktuellen Prod-Stand. Verweigert sich hart auf `APP_ENVIRONMENT=production`. |

Die übrigen Skripte (`sqlite2pg.py`, `migrate_to_s3.py`,
`migrate_public_gallery.py`) waren einmalige, historische Migrationen und sind
für den Alltagsbetrieb nicht mehr relevant.

## Disaster Recovery

Läuft **nicht** über dieses Repo. Postgres-Restore erfolgt über `restore_db.py`
(siehe oben), Datei-Restore über die Versionierung des S3-Buckets
(`vindobona2-at`). Für den Fall, dass der VPS selbst komplett neu aufgesetzt
werden muss: `playbooks/setup_vps.yml` (Host-Grundkonfiguration) gefolgt von
`playbooks/deploy.yml` (Secrets/Config/Quadlets + Dienste starten) — die
App-Images kommen danach automatisch aus ghcr.io.
