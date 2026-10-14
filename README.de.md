# vb-deploy

[English](README.md) | [Deutsch](README.de.md)

Das führende Handbuch für das Vindobona-II-System (`vindobona2.at`): wie es aufgebaut ist, was bei einem Ausfall zu tun ist, wie man es neu aufbaut oder umzieht, wie eine Nachfolgerin oder ein Nachfolger es übernimmt und welche Werkzeuge es gibt. Dieses Repository enthält alles, was zum *Betrieb* des Systems nötig ist: die Ansible-Playbooks, die Caddy-Konfiguration, die Podman-Quadlets, die Vorlagen für eine neue Installation und das Betriebswerkzeug `vb`. Es enthält **keine Secrets und keine Daten der Installation**: Inventory, Secrets, Betriebsschlüssel und Zugangs-Notiz liegen im privaten Repository `vb-access` ([Anhang C](#anhang-c-zugang-und-übergabe)). Die READMEs der anderen Repositories setzen voraus, dass du dieses Dokument kennst.

**Du übernimmst das System?** Beginne mit dem einseitigen [Übergabeblatt](HANDOVER.de.md).

## Wo fange ich an?

| Ich möchte... | Lesen |
|---|---|
| das System übernehmen (Nachfolge) | das [Übergabeblatt](HANDOVER.de.md), dann [Anhang C](#anhang-c-zugang-und-übergabe) |
| verstehen, wie das System aufgebaut ist | [1. Architektur-Übersicht](#1-architektur-übersicht) |
| etwas reparieren, das ausgefallen oder kaputt ist | [2. Wenn etwas ausfällt](#2-wenn-etwas-ausfällt), beginne mit der [Triage-Tabelle](#20-triage-tabelle) |
| auf einem neuen Server installieren oder umziehen | [3. Neuaufbau oder Umzug auf einen neuen Server](#3-neuaufbau-oder-umzug-auf-einen-neuen-server) |
| ein Skript, einen Befehl oder ein Werkzeug finden | [4. Skripte und CLI-Werkzeuge](#4-skripte-und-cli-werkzeuge) |
| eine test-/qa-Stage oder die lokale Entwicklungsumgebung aufbauen | [5. Vorgelagerte Stages und lokale Entwicklung](#5-vorgelagerte-stages-und-lokale-entwicklung) |
| eine typische Situation Schritt für Schritt durchgehen | [Anhang D: Fallbeispiele](#anhang-d-fallbeispiele) |
| eine schnelle Antwort | [Anhang E: Häufig gestellte Fragen](#anhang-e-häufig-gestellte-fragen) |

**Wo läuft ein Befehl?** Jeder Befehlsblock in diesem Dokument beginnt mit einer Markierung:

| Markierung | Bedeutung |
|---|---|
| `# [Arbeitsplatz]` | Dein eigener Computer, in einem Checkout dieses Repositories neben einem Checkout von `vb-access`. Das Werkzeug `./vb` und Ansible laufen hier. |
| `# [Host]` | Das Zielsystem, nach `./vb ssh production` (jede der drei Domains erreicht den Produktions-Host). |
| `# [Container]` | In einem Container. In der Praxis startest du es vom Host aus mit `podman exec vb-api ...`. |
| `# [alter Host]`, `# [neuer Host]` | Nur in Kapitel 3: der Host, der ersetzt wird, und sein Ersatz. |

Eine **Stage** ist eine vollständige Kopie des Systems (production, test, qa, development). Manche Begriffe sind Vereinssprache: Der **Produktions-Host** heißt im Verein "P-System" (Produktionssystem), die **Standesdatenbank** ist die Mitgliederdatenbank im Verwaltungssystem, **AH-Kassen** (Modul `p4x`) sind die Buchhaltungskonten der Altherrenschaft.

## Inhalt

- [1. Architektur-Übersicht](#1-architektur-übersicht)
- [2. Wenn etwas ausfällt](#2-wenn-etwas-ausfällt)
- [3. Neuaufbau oder Umzug auf einen neuen Server](#3-neuaufbau-oder-umzug-auf-einen-neuen-server)
- [4. Skripte und CLI-Werkzeuge](#4-skripte-und-cli-werkzeuge)
- [5. Vorgelagerte Stages und lokale Entwicklung](#5-vorgelagerte-stages-und-lokale-entwicklung)
- [Anhang A: Secrets und Pflichtvariablen](#anhang-a-secrets-und-pflichtvariablen)
- [Anhang B: Fallstricke](#anhang-b-fallstricke)
- [Anhang C: Zugang und Übergabe](#anhang-c-zugang-und-übergabe)
- [Anhang D: Fallbeispiele](#anhang-d-fallbeispiele)
- [Anhang E: Häufig gestellte Fragen](#anhang-e-häufig-gestellte-fragen)

---

## 1. Architektur-Übersicht

### 1.1 Das Gesamtbild

```
                    Internet (80/443)
                           |
                  +--------v--------+
                  |      Caddy      |   TLS (Let's Encrypt), Sicherheits-Header
                  +--+-----+-----+--+
        api.<domain> |     |     | www.<domain>
        127.0.0.1:21000    |     127.0.0.1:21002
                           | intern.<domain> 127.0.0.1:21001
                           +-- /logging/dozzle* (Basic Auth) -> 127.0.0.1:8081 (Dozzle)

  Pod vb-api-pod            Pod vb-intern-pod       Pod vb-www-pod
  +------------------+      +--------------+        +------------+
  | vb-api           |--+   | vb-intern    |        | vb-www     |
  | vb-api-worker    |--+   +--------------+        +------------+
  | vb-api-pg        |  |
  | vb-api-valkey    |  +--> AWS S3 (Dateien, Datenbank-Sicherungen)
  +------------------+
```

Alles läuft auf einem Host; die Container sind in **Pods** gruppiert (Gruppen von Containern, die ein Netzwerk teilen und sich deshalb über `localhost` erreichen). Der Browser spricht mit `vb-intern` und `vb-www` (statische Seiten, ausgeliefert von nginx) und mit `vb-api` (dem JSON-Backend); Caddy ist die einzige Komponente, die öffentlich lauscht. Alle Dateien (Archiv, Mitgliederbilder, öffentliche Galerie) und alle Datenbank-Sicherungen liegen in einem S3-Bucket, nicht auf dem Host.

### 1.2 Technologien und Begründung

| Technologie | Rolle | Warum |
|---|---|---|
| Ein Produktions-Host (VPS) | Betreibt das ganze System | Eine bewusste Entscheidung für ein System dieser Größe: das Schlankeste im Betrieb, keine Cluster- oder Multi-Node-Komplexität, geringe Kosten, überschaubare Wartung. Derzeit ein einzelner VPS mit Debian 13; das Setup ist für ein allgemeines Zielsystem geschrieben, nicht für einen Anbieter. Er muss ein **x86_64**-Server sein: Die Anwendungs-Images werden nur für `linux/amd64` gebaut. |
| Rootless Podman | Container-Laufzeit | Container laufen ohne Root-Rechte unter einem unprivilegierten Benutzer (`service`), sodass ein Container-Ausbruch kein Root auf dem Host bringt. Das macht den Ein-Host-Ansatz sicher genug. |
| systemd-Quadlets | Beschreiben Container und Pods als `.container`-/`.pod`-Dateien | systemd macht daraus echte Dienste (`systemctl --user ...`): automatische Neustarts, Health-Checks, Start beim Booten, kein Compose-Daemon. |
| Ansible und Ansible Vault | Host einrichten, Konfiguration und Secrets verteilen | Wiederholbares Setup; Secrets liegen verschlüsselt im privaten Repository `vb-access`. |
| Das Werkzeug `vb` | Entsperrt den Betriebsschlüssel und startet die Playbooks | Ein Befehl pro Aufgabe (`./vb setup`, `./vb deploy`, ...) statt einer langen Liste von Ansible-Optionen; der Schlüssel liegt nur im Arbeitsspeicher und nur, solange ein Befehl läuft, unverschlüsselt vor. |
| Caddy | Reverse Proxy und TLS | Besorgt und erneuert Let's-Encrypt-Zertifikate selbst. |
| GitHub Actions, GHCR, `podman auto-update` | Images bauen, speichern und ausrollen | Jeder Merge nach `main` baut ein Image; der Host holt geänderte Images jede Nacht selbst. |
| PostgreSQL 18 | Datenbank | Läuft im API-Pod. |
| Valkey 8 und ARQ | Job-Warteschlange und Scheduler | Der Container `vb-api-worker` führt geplante und Hintergrund-Jobs aus. |
| AWS S3 (Produktion), Garage (andere Stages) | Dateispeicher | Dateien hängen nicht vom Host ab. Garage ist ein S3-kompatibler Ersatz für die anderen Stages. |
| Dozzle | Log-Betrachter | Zeigt Container-Logs im Browser. |

**Betriebssysteme.** `setup_vps.yml` und `deploy.yml` liefen mit dem aktuellen Zugangskonzept (Oktober 2026) komplett durch auf **Debian 13**, **Ubuntu 26.04**, **Rocky Linux 10** (auch mit erzwungenem SELinux) und **openSUSE Leap 16.0**. Fedora 44 lief im August 2026 mit dem früheren Zugangsmechanismus durch; die Tasks, die sich seither geändert haben, sind auf jeder Distribution dieselben, aber betrachte es als "sollte funktionieren, vorher mit `./vb check` ansehen". Podman 5 oder neuer ist Voraussetzung; das Playbook bricht bei älteren Versionen ab (Debian 12, Ubuntu 24.04, RHEL 9, openSUSE Leap 15).

### 1.3 Die GitHub-Organisation und die Repositories

Vier Repositories sind öffentlich und eines ist privat; alle liegen in der GitHub-Organisation **[K-O-St-V-Vindobona-II](https://github.com/K-O-St-V-Vindobona-II)**.

| Repository | Rolle | Domain in Produktion | Tech-Stack | Läuft als |
|---|---|---|---|---|
| [`vb-api`](https://github.com/K-O-St-V-Vindobona-II/vb-api) | Backend: das Verwaltungssystem des Vereins (Mitglieder, Beiträge, Archiv, Buchhaltung, geplante Jobs) | `api.vindobona2.at` | Python 3.14, FastAPI, SQLAlchemy, Alembic | Image `vb-api`, Container `vb-api` und `vb-api-worker` |
| [`vb-intern`](https://github.com/K-O-St-V-Vindobona-II/vb-intern) | Frontend für Mitglieder und Funktionäre (Login nötig) | `intern.vindobona2.at` | Vue 3, TypeScript, Vite, nginx | Image und Container `vb-intern` |
| [`vb-www`](https://github.com/K-O-St-V-Vindobona-II/vb-www) | Öffentliche Website | `www.vindobona2.at` | Vue 3, TypeScript, Vite, nginx | Image und Container `vb-www` |
| [`vb-deploy`](https://github.com/K-O-St-V-Vindobona-II/vb-deploy) (dieses Repository, öffentlich) | **Das Wie:** Playbooks, Caddy, Quadlets, Vorlagen, das Werkzeug `vb`, dieses Handbuch | keine | Ansible, systemd-Quadlets, Python | führt selbst nichts aus, es konfiguriert die anderen |
| `vb-access` (**privat**) | **Das Was:** die Daten dieser Installation: Inventory (Server, Domains), Secrets, der Betriebsschlüssel, die Zugangs-Notiz | keine | nur Dateien | führt nichts aus und hat keine Domain |

**Warum zwei Repositories für den Betrieb?** `vb-deploy` ist Code und Wissen und kann öffentlich sein, damit alle, auch bei einer Übernahme, nachlesen können, wie das System gebaut ist. Alles, was für die Installation spezifisch und sensibel ist (welcher Server, welche Passwörter, welcher Schlüssel), liegt in `vb-access`, das nur Owner der Organisation lesen können. Das Werkzeug `vb` verbindet beide: Es liest die privaten Daten aus `vb-access` und wendet sie mit den öffentlichen Playbooks an.

**Namenskonvention:** Der Repository-Name ist die Subdomain: `vb-api` ist `api.vindobona2.at`, `vb-intern` ist `intern.vindobona2.at`, `vb-www` ist `www.vindobona2.at`. Die Domains jeder Stage stammen aus der Inventory-Datei dieser Stage (`sites/<stage>/inventory.ini` in `vb-access`). `vb-deploy` und `vb-access` haben keine Domain.

Die drei Anwendungs-Repositories bauen in der CI jeweils ihr eigenes Container-Image und schieben es bei jedem Merge nach `main` nach `ghcr.io/k-o-st-v-vindobona-ii/<repo>:latest` und `...:<voller commit sha>`. `vb-deploy` baut keine Images und klont kein Anwendungs-Repository auf den Host: Es verteilt nur Konfiguration und Secrets und stellt sicher, dass die richtigen Images laufen.

### 1.4 Container und Pods

Alle Container in Produktion (die Container-Namen sind auf jeder Stage gleich; zu welcher Stage ein Container gehört, entscheidet allein `APP_ENVIRONMENT` in seiner Environment-Datei):

| Container | Pod | Image | Auf dem Host veröffentlicht | Zweck | Code aus |
|---|---|---|---|---|---|
| `caddy` | keiner (Host-Netzwerk) | `caddy:2.11` | 80, 443 | TLS, Routing, Sicherheits-Header | keiner |
| `logging-dozzle` | keiner | `dozzle` | 127.0.0.1:8081 | Log-Betrachter, nur über Caddy unter `intern.<domain>/logging/dozzle` erreichbar | keiner |
| `vb-api` | `vb-api-pod` | `ghcr.io/.../vb-api` | 127.0.0.1:21000 (Container-Port 8000) | Das Backend (gunicorn mit uvicorn-Workern) | `vb-api` |
| `vb-api-worker` | `vb-api-pod` | gleiches Image wie `vb-api` | keiner | Geplante und Hintergrund-Jobs (`arq`) | `vb-api` |
| `vb-api-pg` | `vb-api-pod` | `postgres:18` | keiner (intern) | Die Datenbank | keiner |
| `vb-api-valkey` | `vb-api-pod` | `valkey/valkey:8` | keiner (intern) | Job-Warteschlange des Workers | keiner |
| `vb-intern` | `vb-intern-pod` | `ghcr.io/.../vb-intern` | 127.0.0.1:21001 (Container-Port 8080) | nginx, das das gebaute Frontend ausliefert | `vb-intern` |
| `vb-www` | `vb-www-pod` | `ghcr.io/.../vb-www` | 127.0.0.1:21002 (Container-Port 8080) | nginx, das die gebaute öffentliche Seite ausliefert | `vb-www` |
| `vb-garage` | `vb-api-pod` | `dxflrs/garage` | 9000 (S3-API, öffentlich über Caddy), 9001 (Admin-API, nur Loopback) | S3-Ersatz, **nur Nicht-Produktions-Stages** | keiner |

Warum der Pod? Container eines Pods teilen einen Netzwerk-Namespace und erreichen sich über `localhost`. So bleibt interner Verkehr (API zu Datenbank, API zu Warteschlange, API zu Garage) vom öffentlichen Internet fern, und für Datenbank und Warteschlange braucht es keine veröffentlichten Ports. `vb-api-worker` nutzt dasselbe Image wie `vb-api` und ersetzt nur den Startbefehl (`arq app.worker.WorkerSettings`).

Jeder Produktions-Container ist gehärtet: schreibgeschütztes Root-Dateisystem, keine zusätzlichen Rechte, alle Linux-Capabilities entfernt (Caddy bekommt nur die eine zurück, die für Port 80/443 nötig ist), Speicher- und Prozessgrenzen, ein Health-Check und `Notify=healthy`, sodass systemd einen Dienst erst als gestartet ansieht, wenn er gesund ist. Details stehen in den Quadlet-Vorlagen unter `templates/` und `quadlets/`.

Auf dem Host laufen zwei Timer: `podman-auto-update.timer` (nächtlich, siehe [1.7](#17-wie-eine-änderung-in-produktion-kommt)) und `podman-prune.timer` (sonntags 04:00, entfernt unbenutzte Images und Container).

### 1.5 Wo die Daten liegen

| Daten | Wo | Gesichert? | Wie man sie zurückbekommt |
|---|---|---|---|
| PostgreSQL-Datenbank | Auf dem Host: `~/data/vb/production/postgres` | **Ja:** jede Nacht um 03:00 (Wiener Zeit) ein vollständiger Dump in den S3-Präfix `db-backups/`, 90 Tage aufbewahrt (`BACKUP_RETENTION_DAYS`), dazu manuelle Dumps | [2.4 Datenbank wiederherstellen](#24-die-datenbank-ist-beschädigt-sicherung-und-wiederherstellung) |
| Dateien (Archiv, Mitgliederbilder, öffentliche Galerie) | AWS-S3-Bucket `vindobona2-at` | Der Bucket ist versioniert (in AWS konfiguriert, nicht Teil dieses Repositories) | Nach einem Host-Verlust gibt es nichts wiederherzustellen. Gelöschte oder überschriebene Dateien lassen sich aus früheren Versionen in AWS zurückholen. |
| Job-Warteschlange | `~/data/vb/production/valkey` | Nein, bewusst: Ein verlorener Job ist eine Benachrichtigungs-Mail oder ein manuell wiederholbarer Downsync (die Auffrischung einer Nicht-Produktions-Stage aus Produktionsdaten, siehe [5.5](#55-downsync)) | Nichts |
| Secrets (Environment-Dateien) | Vault-verschlüsselt im privaten Repository `vb-access` unter `sites/<stage>/secrets/`, auf dem Host nach `~/.env/` gerendert | Das private Repository ist die Sicherung (dazu eine Offline-Kopie, siehe [Anhang C](#anhang-c-zugang-und-übergabe)) | `./vb deploy` |
| Betriebsschlüssel | Vault-verschlüsselt in `vb-access` (`sites/<stage>/operator_key.vault`); seine öffentliche Hälfte steht in `authorized_keys` auf dem Host | Das private Repository | `./vb setup` |
| Konfiguration (Caddyfile, Quadlets) | Dieses Repository | Das Git-Repository | `./vb deploy` |
| TLS-Zertifikate | `~/.local/share/caddy/caddy_data` | Nein, werden automatisch neu ausgestellt, sobald DNS auf den Host zeigt | Automatisch |
| Container-Images | `ghcr.io` | Aus den Repositories neu bauen | `podman pull` / Auto-Update |
| Logs | systemd-Journal (persistent) und Container-Ausgabe (Dozzle) | Nein | keine |

**In keinem Repository:** das **Vault-Passwort** (es öffnet alles in `vb-access`), die DNS-Zone, der AWS-Account, die GitHub-Organisation und der Hosting-Account. Die Accounts stehen mit Logins und zweiten Faktoren in der Zugangs-Notiz `credentials.md` in `vb-access` ([Anhang C](#anhang-c-zugang-und-übergabe)); das Vault-Passwort bewahren die Menschen auf, die das System betreiben.

**Empfehlung (nicht der aktuelle Stand):** Die Datenbank-Sicherungen liegen im selben AWS-Account wie die Dateien. Ginge dieser Account verloren, wäre beides weg. Eine zweite Kopie des Dumps an anderer Stelle (anderer Anbieter oder eine Offline-Platte, zum Beispiel ein wöchentlicher Download des neuesten Dumps) schließt diese Lücke.

### 1.6 Benutzer und Sicherheitsmodell

```
  Arbeitsplatz                                      Produktions-Host
  +------------------------------+                  +---------------------------+
  | ./vb deploy production       |                  |  Benutzer service (kein   |
  |   fragt das Vault-Passwort   |   SSH, nur Key   |    sudo): alle Container  |
  |   entpackt den Betriebs-     |----------------->|  Benutzer admin (sudo,    |
  |   schlüssel (nur im RAM)     |                  |    ohne Passwort): Setup  |
  +------------------------------+                  +---------------------------+
       liest sites/production/ aus dem privaten Repository vb-access
```

- **Zwei Linux-Benutzer auf dem Host.** **`service`** betreibt alle Container und hat weder `sudo` noch eine administrative Gruppe (nur `systemd-journal`, um Logs zu lesen). **`admin`** hat `sudo` ohne Passwort und wird nur von `setup_vps.yml` benutzt; er startet nie einen Container. Ein Container-Ausbruch endet damit bei den Rechten eines gewöhnlichen Benutzers.
- **Ein Betriebsschlüssel, keine Passwörter.** SSH geht für alle nur mit Schlüssel: kein `root`-Login, nur `admin` und `service` sind erlaubt, und beide Accounts haben ein gesperrtes Passwort (`/etc/ssh/sshd_config.d/00-vb-hardening.conf`, gesetzt von `setup_vps.yml`). Die `authorized_keys` beider Benutzer enthalten genau die Schlüssel, die unter `operator_public_keys` im Inventory der Stage stehen; jeder andere Schlüssel wird bei jedem `./vb setup` entfernt. Der private Schlüssel liegt vault-verschlüsselt in `vb-access` und existiert nur im Arbeitsspeicher unverschlüsselt, solange `./vb` läuft.
- **Was das bedeutet.** Wer den Betriebsschlüssel hat, kann auf dem Host alles tun, auch `sudo`. Das ist eine bewusste Vereinfachung für einen kleinen Verein: Der Schlüssel ist durch das Vault-Passwort und das private Repository geschützt, und [Anhang C](#anhang-c-zugang-und-übergabe) beschreibt, was zu tun ist, wenn eine Person ausscheidet. `./vb setup` weigert sich zu laufen, wenn der Schlüssel, mit dem es sich verbindet, nicht in der Liste steht (es würde den eigenen Zugang löschen), und bevor es `root` und Passwort-Logins schließt, beweist es, dass der Schlüssel wirklich als `admin` und als `service` einloggt.
- Firewall: Nur die Ports 22, 80 und 443 sind offen (UFW unter Debian, firewalld in der Red-Hat- und SUSE-Familie). Sicherheits-Updates werden automatisch eingespielt; ein anstehender Neustart bleibt eine manuelle Entscheidung.
- Dozzle bekommt den Podman-API-Socket von `service`, das ist die volle Kontrolle über jeden Rootless-Container. Seine Basic-Auth-Zugangsdaten sind deshalb so sensibel wie die Secrets des Systems. Sie müssen lang und zufällig sein; der Zugriff lässt sich zusätzlich mit der Inventory-Variable `logging_allowed_cidrs` auf IP-Bereiche begrenzen.
- Caddy setzt eine strikte Content-Security-Policy für `intern` und `www`, Sicherheits-Header für alle Seiten und entfernt den `Server`-Header.

### 1.7 Wie eine Änderung in Produktion kommt

```
Branch development ──PR──> main (geschützt) ──CI──> Image :latest und :<commit sha> auf ghcr.io
                                                          |
                                      nächtlich: podman-auto-update.timer holt ein geändertes :latest
                                      und startet den Container neu    (kein Ansible nötig)
                                      sofort: ./vb deploy production --tags deploy-api|intern|www
```

1. Gearbeitet wird auf `development`; `main` ist geschützt und nimmt nur Pull Requests an.
2. Die CI-Pipeline des Repositories führt Lint, Tests, CodeQL, einen nur lesenden Smoke-Test des Images aus und baut und pusht auf `main` das Image.
3. Jede Nacht (`OnCalendar=daily` mit zufälliger Verzögerung bis 15 Minuten) prüft der Host für jeden Container mit dem Label `AutoUpdate=registry`, ob sich `:latest` geändert hat, holt es und startet den Container neu. `./vb deploy production --tags deploy-<dienst>` macht dasselbe auf Abruf.
4. Ein neues `vb-api`-Image wendet ausstehende Datenbank-Migrationen beim Start selbst an (`alembic upgrade head` in seinem Entrypoint). Der Worker-Container überspringt Migrationen (`SKIP_MIGRATIONS=true`), sodass genau ein Prozess migriert.
5. Änderungen an Konfiguration, Secrets oder Quadlets werden mit `./vb deploy` verteilt, nie von Hand.

### 1.8 Stages

| Stage | Inventory (in `vb-access`) | Speicher | Daten | Status |
|---|---|---|---|---|
| `production` | `sites/production/inventory.ini` | AWS S3 | echt | live |
| `test` | `sites/test/inventory.ini` | eigenes Garage | jede Nacht aus Produktion aufgefrischt | nur Vorlage (`site-template/test`), noch kein eigener Host |
| `qa` | `sites/qa/inventory.ini` | eigenes Garage | jede Nacht aus Produktion aufgefrischt | nur Vorlage (`site-template/qa`), noch kein eigener Host |
| `development` | keines (läuft außerhalb dieses Repositories, siehe [5.7](#57-lokale-entwicklungsumgebung)) | lokales Garage | jede Nacht aus Produktion aufgefrischt | der eigene Host der Entwickler |

`deploy.yml` akzeptiert nur `production`, `test` und `qa` und lehnt alles andere ab. Der schrittweise Aufbau einer weiteren Stage steht in [5.2](#52-eine-test--oder-qa-stage-aufbauen).

---

## 2. Wenn etwas ausfällt

### 2.0 Triage-Tabelle

| Symptom | Wahrscheinliche Ursache | Was zu tun ist | Abschnitt |
|---|---|---|---|
| Keine der drei Seiten öffnet sich, oder der Browser zeigt einen Zertifikatsfehler | `caddy` gestoppt, DNS falsch, Let's-Encrypt-Limit | Caddy und DNS prüfen, Caddy neu starten | [2.1](#21-erste-prüfungen-nur-lesend), [2.2](#22-dienst-oder-pod-neu-starten), [Anhang B](#anhang-b-fallstricke) |
| Login schlägt fehl oder die Seiten zeigen Serverfehler, aber `www` geht | `vb-api-pod` ungesund | Logs ansehen, Pod neu starten | [2.1](#21-erste-prüfungen-nur-lesend), [2.2](#22-dienst-oder-pod-neu-starten) |
| Direkt nach einem Update ging alles kaputt | Fehlerhaftes Release oder Migration | Erst die Prüfungen aus 2.1, dann auf das vorherige Image zurückrollen (ein Frontend-Fehler braucht keinen Datenbank-Schritt) | [2.3](#23-fehlerhaftes-release-zurückrollen) |
| Daten sind falsch, fehlen oder sind beschädigt | Fehlerhafte Änderung oder Datenschaden | Datenbank aus einer Sicherung wiederherstellen | [2.4](#24-die-datenbank-ist-beschädigt-sicherung-und-wiederherstellung) |
| Bilder oder Dateien erscheinen defekt, Downloads schlagen fehl | Fehlende Objekte in S3, Zugangsdaten oder CORS | Integritätsprüfung ausführen | [2.5](#25-dateien-und-speicher) |
| Geplante Mails oder Jobs laufen nicht | `vb-api-worker` oder `vb-api-valkey` ausgefallen | Scheduler-Seite und Worker prüfen | [2.1](#21-erste-prüfungen-nur-lesend), [2.2](#22-dienst-oder-pod-neu-starten) |
| `./vb ssh` verbindet nicht, der Host ist weg | Host aus oder verloren, falsche Adresse | Konsole des Anbieters; bei Verlust neu aufbauen | [2.6](#26-totalverlust-des-hosts), [3](#3-neuaufbau-oder-umzug-auf-einen-neuen-server) |
| `./vb` meldet, der Schlüssel werde abgelehnt | Der Betriebsschlüssel ist nicht (mehr) auf dem Host | Konsole des Anbieters und Rescue-Modus | [Anhang C](#anhang-c-zugang-und-übergabe) |
| Die Platte ist voll | Alte Images, Logs | `podman image prune`, `df -h` prüfen | [2.1](#21-erste-prüfungen-nur-lesend) |
| Du kommst nicht in den Hosting-, AWS-, Google- oder GitHub-Account | Zugangsdaten, zweiter Faktor | Die Zugangs-Notiz in `vb-access` | [Anhang C](#anhang-c-zugang-und-übergabe) |

### 2.1 Erste Prüfungen (nur lesend)

Keiner dieser Befehle ändert etwas. Die Schleifen verwenden die Domains `api.`, `intern.` und `www.vindobona2.at`; nimm für eine andere Stage oder nach einem Domainwechsel die Werte von `api_domain`, `intern_domain` und `www_domain` aus ihrer `inventory.ini`.

```bash
# [Arbeitsplatz] ist jede Seite erreichbar und ihr Zertifikat gültig? (erwartet: dreimal 200)
for d in api intern www; do curl -s -o /dev/null -w "$d %{http_code}\n" https://$d.vindobona2.at/; done

# [Arbeitsplatz] am Host anmelden (fragt das Vault-Passwort; öffnet eine Shell als Benutzer service)
./vb ssh production

# [Host] Container und ihr Zustand (erwartet: jeder Dienst-Container "healthy"; die Helfer mit der Endung -pod-infra zeigen nur "Up")
podman ps

# [Host] fehlgeschlagene Units (erwartet: keine Ausgabe)
systemctl --user --failed

# [Host] Zustand jeder Unit des Systems
systemctl --user list-units 'vb-*' 'caddy*' 'logging*' 'podman-*'

# [Host] letzte Log-Zeilen eines Dienstes (Unit-Namen nach Bedarf ersetzen)
journalctl --user -u vb-api.service -n 100 --no-pager
podman logs --tail 100 vb-api-worker

# [Host] Caddy: Zertifikats- oder Routing-Probleme zeigen sich hier
journalctl --user -u caddy.service -n 100 --no-pager

# [Arbeitsplatz] wohin zeigt jede Domain? (mit der Adresse des Hosts vergleichen)
for d in api intern www; do dig +short $d.vindobona2.at; done

# [Host] Speicherplatz
df -h /

# [Host] Datenbank-Revision (erwartet: eine Revisions-ID gefolgt von "(head)")
podman exec vb-api alembic current
```

**Alles in Ordnung, wenn:** die Schleife dreimal `200` ausgibt; `podman ps` die Dienst-Container `healthy` zeigt; `systemctl --user --failed` nichts ausgibt; `alembic current` mit `(head)` endet; und der neueste Dump von `restore_db.py --list` (siehe [2.4](#24-die-datenbank-ist-beschädigt-sicherung-und-wiederherstellung)) von letzter Nacht ist, also `production-<heute oder gestern>_03-00-00.dump`.

Eine Vorschau, was ein Lauf der Playbooks auf dem Host ändern würde, ohne etwas zu ändern, liefert `./vb check production`. Der Browser-basierte Log-Betrachter ist `https://intern.vindobona2.at/logging/dozzle` (Basic Auth). Die Seite **System → Scheduler** in `vb-intern` (Berechtigung `systemAdmin`) zeigt jeden geplanten Job mit nächstem und letztem Lauf.

### 2.2 Dienst oder Pod neu starten

**Starte einen Pod nur über seine Pod-Unit neu, nie über einen seiner Container.** Ein Container ist an seine Pod-Unit gebunden; startest du den Container neu, wird der ganze Pod entfernt, und nichts startet den Pod von selbst wieder.

```bash
# [Host] der ganze API-Pod (api, worker, postgres, valkey); dauert ein paar Sekunden
systemctl --user restart vb-api-pod.service

# [Host] die Frontends
systemctl --user restart vb-intern-pod.service
systemctl --user restart vb-www-pod.service

# [Host] Caddy und Dozzle haben keinen Pod
systemctl --user restart caddy.service
systemctl --user restart logging-dozzle.service

# [Host] danach: wieder alles gesund?
podman ps
```

### 2.3 Fehlerhaftes Release zurückrollen

**Welcher Weg?** Hat das fehlerhafte Release das Datenbankschema nicht geändert, oder ist seine Migration mit dem vorherigen Release verträglich, rolle nur das Image zurück. Ist seine Migration nicht verträglich (der alte Code arbeitet mit dem neuen Schema nicht), nimm zuerst die Migration zurück und rolle dann das Image zurück. Eine Sicherung einzuspielen ([2.4](#24-die-datenbank-ist-beschädigt-sicherung-und-wiederherstellung)) ist der letzte Ausweg, denn dabei geht alles verloren, was seit dem Dump geschrieben wurde.

Reihenfolge: 1. Sicherung anlegen, 2. die Migration **mit dem noch laufenden Release** zurücknehmen (ein älteres Image kennt die neuere Revision nicht), 3. das alte Image pinnen, 4. prüfen. **Pinne sofort nach dem Downgrade:** Jeder Neustart des neuen Images dazwischen (das nächtliche Auto-Update, ein Health-Neustart) führt `alembic upgrade head` erneut aus und macht den Downgrade rückgängig. Ein Downgrade kann Spalten löschen und damit die seit dem Release dort geschriebenen Daten; `alembic history` listet die Revisionen. Kann der Container gar nicht starten (`podman exec` schlägt also fehl), ist die Wiederherstellung ([2.4](#24-die-datenbank-ist-beschädigt-sicherung-und-wiederherstellung)) der Weg.

**Images** (Schritt 3; die Befehle für Schritt 2 stehen unten unter **Datenbank**). Jedes Image ist zusätzlich mit dem vollen Commit-SHA des Merges getaggt. Finde den SHA des letzten guten Releases und pinne ihn. **Welches Release läuft gerade?** Jedes Image trägt den Commit, aus dem es gebaut wurde:

```bash
# [Host] der Commit des laufenden Images (für die Frontends vb-intern oder vb-www einsetzen)
podman inspect vb-api --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'
```

Öffne die Commit-Liste von `main` (`https://github.com/K-O-St-V-Vindobona-II/vb-api/commits/main`, ebenso für `vb-intern` und `vb-www`; öffentlich, ohne Anmeldung): Der Merge-Commit vor dem eben gefundenen ist das letzte gute Release. Die Paketseite des Repositories auf GitHub listet dieselben Tags. GitHub zeigt den kurzen SHA; der Kopieren-Knopf daneben liefert den vollen SHA, den der Pin braucht.

Ein älteres Image, aus der Zeit vor dem Label, gibt eine leere Zeile aus. Nimm dann die Bauzeit, die knapp nach dem Merge liegt: `podman image inspect ghcr.io/k-o-st-v-vindobona-ii/vb-api:latest --format '{{.Created}}'` (UTC) und `podman ps --format '{{.Names}}\t{{.Image}}\t{{.CreatedAt}}'` (wann das nächtliche Update jeden Container zuletzt neu angelegt hat). Suche den Merge-Commit, der knapp vor dieser Zeit gebaut wurde. Dann pinne das Release:

```bash
# [Arbeitsplatz] pro Repository die passende Variable; das Tag ist der volle Commit-SHA:
#   vb_api_image_tag, vb_intern_image_tag, vb_www_image_tag
# (ein Image von vb-intern oder vb-www ohne Commit-Label, siehe unten, ist älter und trägt das Präfix "sha-": sha-<voller-commit-sha>)
./vb deploy production -e vb_api_image_tag=<voller-commit-sha> --check --diff   # erst die Vorschau
./vb deploy production -e vb_api_image_tag=<voller-commit-sha>                   # dann anwenden
```

Die geänderte Quadlet veranlasst das Playbook, den Pod neu zu starten. Das nächtliche Auto-Update überschreibt einen Pin nicht, weil das Tag eines Commit-SHA sich nie ändert. Der Pin gilt nur für diesen Lauf: Ein späteres `./vb deploy production` ohne `-e` geht wieder auf `latest`. Für einen dauerhaften Pin trage die Variable in `sites/production/inventory.ini` unter `[vps:vars]` ein (zum Beispiel `vb_api_image_tag=<voller-commit-sha>`); committe und pushe die Änderung in `vb-access` und entferne die Zeile wieder, sobald der Fix released ist. `grep image_tag ../vb-access/sites/production/inventory.ini` zeigt, ob noch ein Pin aktiv ist; das nächtliche Update überschreibt ihn nicht, der Dienst bliebe also unbemerkt auf der alten Version. Prüfe mit der Schleife und den Prüfungen aus [2.1](#21-erste-prüfungen-nur-lesend) (alle drei Seiten `200`, `podman ps` gesund, `alembic current`).

**Datenbank.** Ein älteres Image gegen eine neuere Datenbank zu starten ist sicher, solange jede Migration mit dem vorherigen Release verträglich bleibt (siehe *expand first, contract later* im [`vb-api`-README](https://github.com/K-O-St-V-Vindobona-II/vb-api/blob/main/README.de.md#datenbank-migrationen)): Das Image loggt eine Warnung und startet ohne zu migrieren. Ist eine Migration selbst das Problem:

```bash
# [Host] 1. erst eine Sicherung anlegen
podman exec vb-api python scripts/backup_db.py
# [Host] 2. die neueste Migration mit dem noch laufenden Release zurücknehmen (oder statt -1 eine Revision nennen)
podman exec vb-api alembic downgrade -1
# danach das alte Image pinnen wie oben
```

**Brachte das fehlerhafte Release eine Migration, und auf welche Revision gehst du zurück?** Vergleiche die beiden Releases auf GitHub: `https://github.com/K-O-St-V-Vindobona-II/vb-api/compare/<letzter-guter-sha>...<fehlerhafter-sha>`. Jede neue Datei unter `alembic/versions/` ist eine Migration. Gibt es keine, ist kein Datenbank-Schritt nötig. Bei einer ist `-1` richtig; bei mehreren nimm `-N` oder nenne die Revision: Die `down_revision` in der ältesten neuen Datei ist die Revision, auf die du zurückgehst.

### 2.4 Die Datenbank ist beschädigt: Sicherung und Wiederherstellung

**Wie Sicherungen funktionieren.** Der Job `db_backup` schreibt jede Nacht um 03:00 Wiener Zeit einen vollständigen Dump (`pg_dump`, Custom-Format) in den S3-Präfix `db-backups/` und löscht danach Dumps, die älter als `BACKUP_RETENTION_DAYS` sind (90 Tage in der Produktionsvorlage; ohne die Variable behält die Anwendung 29). Dateinamen sind `<stage>-YYYY-MM-DD_HH-MM-SS.dump` in Wiener Zeit; manuelle Dumps enden auf `-manual.dump`. Bei einer Wiederherstellung geht alles seit dem letzten Dump verloren, also lege vor jeder geplanten riskanten Änderung eine manuelle Sicherung an. **Wähle den Dump bewusst:** Der neueste Dump kann den Schaden schon enthalten (eine manuelle Sicherung, die du jetzt anlegst, tut es und wird der neueste). Wähle mit `--backup-name` einen von vor dem Schaden.

**An der Konsole** (nur so ist eine Wiederherstellung in Produktion möglich; anmelden mit `./vb ssh production`):

```bash
# [Host] verfügbare Sicherungen auflisten (nur lesend)
podman exec vb-api python scripts/restore_db.py --list
#   erwartet: eine Zeile je Dump, ältester zuerst, manuelle Dumps eingeschlossen, zum Beispiel
#   db-backups/production-2026-10-13_03-00-00.dump

# [Host] jetzt eine manuelle Sicherung anlegen (mit --cleanup werden zusätzlich abgelaufene Dumps gelöscht)
podman exec vb-api python scripts/backup_db.py
#   erwartet: "Backup complete: production-<zeitstempel>-manual.dump"

# [Host] den neuesten Dump einspielen (--force ist in Produktion Pflicht)
podman exec vb-api python scripts/restore_db.py --force
#   erwartet: "Restore complete. Backup: <name>"

# [Host] oder einen bestimmten Dump einspielen
podman exec vb-api python scripts/restore_db.py --force \
  --backup-name production-2026-10-11_03-00-00.dump

# [Host] ein Dump, der älter ist als der Code, braucht die neueren Migrationen
podman exec vb-api alembic upgrade head
# [Host] nach einem Restore den Pod immer neu starten (das bereinigt auch die vom Restore getrennten Verbindungen)
systemctl --user restart vb-api-pod.service
```

Was die Wiederherstellung tut: Sie löscht das Schema `public` der Live-Datenbank und legt es neu an (andere Datenbankverbindungen werden beendet), lädt den Dump, behält die Job-Historie dieser Stage und verweigert den Abschluss, wenn die wiederhergestellte Datenbank keine Zeilen enthält. `--force` ist bei jeder Nutzung Absicht: Es schützt die Live-Datenbank vor einer versehentlichen Wiederherstellung.

**Prüfe danach, dass echte Daten angekommen sind**, nicht nur, dass der Befehl ohne Fehler endete:

```bash
# [Host] erwartet: eine Zahl größer als 0
podman exec vb-api-pg psql -U vb -d vb -c 'SELECT count(*) FROM members;'
```

Prüfe außerdem `podman exec vb-api alembic current` (erwartet `(head)`), melde dich dann auf `https://intern.vindobona2.at` an und öffne ein Mitglied. Bewahre den Dump des beschädigten Zustands auf (führe in der Zwischenzeit kein `backup_db.py --cleanup` aus), bis du sicher bist, dass die Wiederherstellung gut ist.

**Im Browser** (Berechtigung `systemAdmin`, Seite **System → Scheduler** in `vb-intern`; die Berechtigungen deines Logins stehen auf deiner Profilseite, und ein bestehender Systemadministrator vergibt neue; die Oberfläche ist deutsch, die Schaltflächennamen unten sind wörtlich). Der Browser braucht eine funktionierende API und Anmeldung, bei einem Ausfall nimm also die Konsole:

| Stage | Schaltfläche | Was sie tut |
|---|---|---|
| production | `Backup jetzt erstellen` | Schreibt sofort eine manuelle Sicherung, unabhängig von der nächtlichen. |
| jede andere Stage | `Downsync jetzt durchführen` | Spiegelt den Produktions-Bucket und stellt die neueste Produktions-Sicherung auf dieser Stage wieder her. |

**In Produktion gibt es absichtlich keine Wiederherstellungs-Schaltfläche:** Eine Wiederherstellung ersetzt die gesamte Live-Datenbank und ist deshalb nur an der Konsole mit `--force` möglich.

### 2.5 Dateien und Speicher

```bash
# [Host] nur lesende Prüfung: jede der Datenbank bekannte Datei existiert in S3 und umgekehrt
podman exec vb-api python scripts/check_s3_integrity.py
#   erwartet: "0 missing file(s)." und "No orphaned files found." für Archive und Standesdb
```

Dieselbe Prüfung läuft jeden Dienstag (`archive_health_check` um 01:00, `standesdb_health_check` um 03:00) und schickt den Administratoren einen Bericht. Fehlende Bilder sind fast immer ein Speicher-Problem (Zugangsdaten, Bucket, CORS), kein Datenbank-Problem. Die Dateien gehören nicht zu den Datenbank-Sicherungen; der S3-Bucket ist versioniert, gelöschte oder überschriebene Objekte lassen sich also in der AWS-Konsole aus früheren Versionen zurückholen. Soft-gelöschte Archiv-Dateien lassen sich mit `maintain_deleted_archive_files.py restore` wiederherstellen (siehe die [Skript-Referenz](https://github.com/K-O-St-V-Vindobona-II/vb-api/blob/main/scripts/README.de.md)).

### 2.6 Totalverlust des Hosts

Ist der Host weg (Hardware, Anbieter, durch ein Update zerstört): Alles, was in Git liegt, lässt sich neu aufbauen, die Datenbank kommt aus der neuesten S3-Sicherung zurück, und die Dateien lagen nie auf dem Host. Folge [3. Neuaufbau oder Umzug auf einen neuen Server](#3-neuaufbau-oder-umzug-auf-einen-neuen-server); Fallbeispiel 3 in [Anhang D](#anhang-d-fallbeispiele) geht es durch. Ist ein Hosting- oder AWS-Account verloren, beginne mit [Anhang C](#anhang-c-zugang-und-übergabe).

### 2.7 Die benötigten vb-Befehle

Alle Befehle laufen am **Arbeitsplatz**, im Checkout dieses Repositories. `vb` findet die privaten Daten unter `../vb-access/sites`; `--sites-dir <ordner>` vor dem Befehl nennt einen anderen Ort. Alles nach dem Stage-Namen wird an `ansible-playbook` weitergereicht.

| Ziel | Befehl |
|---|---|
| Am Host anmelden | `./vb ssh production` (als `service`); `./vb ssh production admin` für administrative Arbeiten |
| Zeigen, was sich ändern würde, nichts ändern | `./vb check production` |
| Konfiguration, Secrets und Quadlets anwenden | `./vb deploy production` |
| Das neueste Image jetzt holen und neu starten | ergänze `--tags deploy-api`, `--tags deploy-intern` oder `--tags deploy-www` |
| Ein Image zurückrollen | ergänze `-e vb_api_image_tag=<voller-commit-sha>` (siehe [2.3](#23-fehlerhaftes-release-zurückrollen)) |
| Ersteinrichtung eines Hosts oder die Basis-Konfiguration prüfen | `./vb setup production` |
| Den Betriebsschlüssel ersetzen | `./vb rotate-key production` |
| Die Daten einer neuen Stage anlegen | `./vb init test` (siehe [5.2](#52-eine-test--oder-qa-stage-aufbauen)) |
| Ein Secret lesen oder ändern | `PAGER=cat ansible-vault view ../vb-access/sites/production/secrets/vb-api.env.j2`, `ansible-vault edit ...` (danach `./vb deploy production`) |

### 2.8 Was du brauchst und nicht in den Repositories steht

| Gebraucht | Wofür | Wo beschrieben |
|---|---|---|
| Das Vault-Passwort | Öffnet alles in `vb-access` (Schlüssel, Secrets, Zugangs-Notiz) | [Anhang C](#anhang-c-zugang-und-übergabe) |
| Zugang zur GitHub-Organisation | Liest `vb-access`, Code, CI, Images | [1.3](#13-die-github-organisation-und-die-repositories), [Anhang C](#anhang-c-zugang-und-übergabe) |
| DNS-Zone von `vindobona2.at` (Registrar oder DNS-Anbieter) | Die Domains auf einen (neuen) Host zeigen lassen | die Zugangs-Notiz |
| AWS-Account (Root-User und IAM-User) | Bucket mit Dateien und Sicherungen | die Zugangs-Notiz |
| Hosting-Account | Server, Konsole, Rescue-Modus, Abrechnung | die Zugangs-Notiz |
| Google-Account und die Google-OAuth-App | Mail-Login und "Mit Google anmelden" | die Zugangs-Notiz |
| Let's Encrypt | Zertifikate, von Caddy selbst ausgestellt | [Anhang B](#anhang-b-fallstricke) (Rate Limits) |

---

## 3. Neuaufbau oder Umzug auf einen neuen Server

Dieses Verfahren dient sowohl einer Ersteinrichtung als auch jedem späteren Fall, in dem der Produktions-Host ersetzt werden muss (Hardware-Wechsel, Neuinstallation des Betriebssystems, Anbieterwechsel, Notfall). Es wurde komplett gegen Wegwerf-Server (Debian 13 und Rocky Linux 10, ausgehend vom rohen Image des Anbieters) und gegen das Produktionssystem durchgespielt. Für eine neue test- oder qa-Stage siehe [5.2](#52-eine-test--oder-qa-stage-aufbauen); dieses Kapitel beschreibt Produktion.

**Reihenfolge im Überblick:** vorbereiten; den neuen Host einrichten und deployen (er startet mit leerer Datenbank); dann im Wartungsfenster den alten Host einfrieren und die letzte Sicherung anlegen, auf dem neuen Host wiederherstellen, DNS umschalten; prüfen; aufräumen. Ist der alte Host tot, entfällt das Einfrieren.

**Arbeite mit zwei Sites, bis DNS umgezogen ist.** Die Site `production` nennt den alten Host. Solange DNS auf den alten Host zeigt, würde alles, was die Domain benutzt, den **alten** Host erreichen. Gib dem neuen Host deshalb eine eigene Site, eine Kopie mit anderer Adresse, und nutze sie für den gesamten Aufbau:

```bash
# [Arbeitsplatz] im Ordner, der die Site-Verzeichnisse enthält
cp -r ../vb-access/sites/production ../vb-access/sites/production-new
# danach in production-new/inventory.ini die IP-Adresse des neuen Hosts unter [vps] eintragen:
#   [vps]
#   <neue-ip>
```

Die Domains der Stage sind eigene Variablen in derselben Datei und bleiben, wie sie sind. Ab hier arbeitet `./vb ... production-new` auf dem neuen Host und `./vb ... production` weiter auf dem alten. Prüfe vor jedem Befehl, wohin er gehen würde: `ansible-inventory -i ../vb-access/sites/production-new/inventory.ini --list` muss `<neue-ip>` zeigen.

### 3.1 Vorbereitung (bevor der alte Host angefasst wird)

- Kündige ein Wartungsfenster an.
- **Notiere die Adresse des alten Hosts** (`dig +short intern.vindobona2.at`, oder die Konsole des Anbieters). Nach dem DNS-Umschalten führen die Domains nicht mehr dorthin, und du brauchst sie für den Rückweg und fürs Aufräumen.
- **Senke die DNS-TTL** der drei Domains einen Tag vorher, damit das Umschalten schnell wirkt (nicht möglich, wenn der alte Host schon tot ist).
- Halte das Vault-Passwort und den Zugang zur Organisation bereit ([Anhang C](#anhang-c-zugang-und-übergabe)).
- **Prüfe die AWS-Seite:** Ist der Produktions-IAM-User auf eine IP-Adresse beschränkt (`aws:SourceIp`), ergänze die Adresse des neuen Hosts **jetzt**, sonst erreicht der neue Host den Bucket nicht und die Wiederherstellung scheitert (in AWS, außerhalb dieses Repositories).
- Die letzte Sicherung wird im Fenster selbst angelegt, siehe [3.6](#36-wartungsfenster-den-alten-host-einfrieren-und-die-letzte-sicherung-anlegen). Ohne sie geht alles seit dem letzten nächtlichen Dump verloren.
- Bietet der Anbieter Snapshots an, mache vor einer Neuinstallation einen. Dieses Repository automatisiert das nicht.

### 3.2 Betriebssystem installieren

Bestelle einen neuen Server (oder installiere den bestehenden neu) mit **Debian 13** als Referenz; andere Distributionen funktionieren wie in [1.2](#12-technologien-und-begründung) aufgeführt. Er muss **x86_64** sein: Die Anwendungs-Images gibt es nur für `amd64`, und `./vb deploy` bricht auf einem ARM-Server mit einer klaren Meldung ab (zum Beispiel bei den günstigeren ARM-Tarifen mancher Anbieter). Auf SELinux-Hosts tragen die Bind-Mounts `:Z`/`:z`-Labels, und Dozzle läuft ohne Label-Einschränkung (siehe die Kommentare in `quadlets/`).

Notiere die **IP-Adresse und das anfängliche `root`-Passwort**, das der Anbieter dir gibt. Mehr muss nicht vorbereitet werden: kein Benutzer, kein Schlüssel, kein Paket. Hat der Anbieter das Passwort als abgelaufen markiert, verlangt der erste SSH-Login ein neues, und Ansible kann diese Frage nicht beantworten. Melde dich einmal an (`ssh root@<neue-ip>`), setze ein neues Passwort und nimm dieses.

Vergleiche den Fingerabdruck des Host-Schlüssels mit dem, den die Konsole des Anbieters zeigt, bevor du ihm vertraust:

```bash
# [Arbeitsplatz]
ssh-keyscan -t ed25519 <neue-ip> | ssh-keygen -lf -
```

Ohne Zugang zur Konsole gibt es nichts zu vergleichen, und die erste Verbindung lässt sich nicht verifizieren. Ein frisch installierter Server, dessen erster Login direkt nach der Installation passiert, ist der risikoärmste Zeitpunkt; schreibe den Fingerabdruck in die Zugangsnotiz, damit eine spätere Änderung auffällt.

Nach einer Neuinstallation derselben Adresse meldet SSH `REMOTE HOST IDENTIFICATION HAS CHANGED`. Das ist zu erwarten, aber entferne den alten Eintrag erst, nachdem du den Fingerabdruck verglichen hast (ein geänderter Schlüssel, den keine Neuinstallation erklärt, ist ein möglicher Abhörversuch): `ssh-keygen -R <adresse>`.

### 3.3 Voraussetzungen am Arbeitsplatz

- Linux oder macOS (unter Windows WSL). Python 3 kommt mit Ansible; `./vb` nutzt nichts außer der Python-Standardbibliothek.
- Ansible (getestet mit ansible-core 2.19; die Playbooks nutzen die Collections `community.general` und `ansible.posix`, die das vollständige `ansible`-Paket enthält; mit nur `ansible-core` führe `ansible-galaxy collection install community.general ansible.posix` aus), `git`, `curl`, `dig` (für die DNS-Prüfungen; Paket `dnsutils` unter Debian und Ubuntu) und `sshpass` (nur für den allerersten Lauf auf einem neuen Host, der sich mit dem Passwort des Anbieters anmeldet). Unter Debian und Ubuntu: `sudo apt install git openssh-client ansible curl dnsutils sshpass` (auch `./vb init` braucht `openssh-client`, es erzeugt den Betriebsschlüssel).
- Ein Checkout dieses Repositories und, **daneben**, ein Checkout des privaten Repositories `vb-access` ([das Übergabeblatt](HANDOVER.de.md) zeigt, wie man es bekommt).
- Das Vault-Passwort. Du brauchst **keinen** eigenen SSH-Schlüssel: Der Betriebsschlüssel liegt in `vb-access`.
- **DNS:** Die drei Domains müssen auf den neuen Host zeigen, damit Caddy seine Zertifikate bekommt. Caddy startet beim Deploy, bevor DNS umgezogen ist, und versucht es weiter, bis es klappt (siehe 3.8).

Das Werkzeug wählt den Login selbst:

| Befehl | Login | Warum |
|---|---|---|
| `./vb setup` auf einem neuen Host | `root` mit dem Passwort des Anbieters (`./vb` fragt danach, nach dem Vault-Passwort; `sshpass` reicht es an SSH weiter) | Der Host hat noch keine anderen Benutzer. Akzeptiert `root` den Betriebsschlüssel schon (der Anbieter hat ihn eingetragen), ist kein Passwort nötig. |
| `./vb setup` auf einem eingerichteten Host | `admin` mit dem Betriebsschlüssel | Braucht `sudo`, das ohne Passwort geht. Der `root`-Login ist geschlossen. |
| `./vb deploy` | `service` mit dem Betriebsschlüssel | Alles läuft im eigenen Bereich des Benutzers, kein `sudo`. |

### 3.4 Host einrichten: `./vb setup`

```bash
# [Arbeitsplatz]
./vb setup production-new
```

Es fragt nach dem Vault-Passwort (`Vault password:`) und auf einem rohen Host gleich danach nach dem Root-Passwort des Anbieters (`SSH password:`); ein Lauf ohne Terminal wartet ohne Fehlermeldung an der zweiten Frage. Es gibt aus, welche Situation es vorgefunden hat. In dieser Reihenfolge: Das Playbook spielt Updates und die nötigen Pakete ein, prüft, dass Podman Version 5 oder neuer ist, legt die Benutzer `admin` und `service` ohne Passwörter an, erlaubt `admin` `sudo` ohne Passwort, macht das Journal persistent, trägt genau die Betriebsschlüssel des Inventory für beide Benutzer ein, aktiviert Linger für `service` (Container überleben das Abmelden), stimmt den Kernel auf Rootless Podman ab, **startet neu** (einmalig), öffnet die Firewall für 22, 80 und 443, **beweist, dass der Betriebsschlüssel als `admin` und als `service` einloggt und dass `admin` `sudo` hat**, und härtet erst dann SSH (nur Schlüssel, kein `root`) und sperrt die Passwörter beider Benutzer. Schlägt der Beweis fehl, bricht der Lauf ab, solange der alte Zugang noch offen ist.

Ein vollständiges System-Upgrade dauert einige Minuten (unter Rocky Linux etwa zehn). Das `root`-Passwort des Anbieters bleibt an der Konsole des Anbieters gültig und ist nur dort nützlich. Ein späteres erneutes `./vb setup` prüft die Konfiguration; auf einem aktuellen Host meldet es keine Änderung.

### 3.5 Deployen: `./vb deploy`

```bash
# [Arbeitsplatz] erst die Vorschau
./vb check production-new
# dann anwenden
./vb deploy production-new
```

Die Vorschau braucht einen eingerichteten Host (auf einem neuen Host bricht `./vb check` mit "does not accept the operator key ... run './vb setup' first" ab). Auf einem Host, auf dem noch nie deployt wurde, endet die Vorschau des zweiten Teils mit einem Fehler zu `caddy.service` ("Could not find the requested service"); das ist erwartet, weil die Dienste noch nicht existieren. Die erste saubere Vorschau ist die nach dem ersten Deploy.

Beachte zwei Dinge. Ein Deploy auf dem neuen Host startet sofort seine geplanten Jobs; mit der leeren Datenbank wird nichts verschickt, weshalb die Wiederherstellung erst nach dem Stoppen des alten Hosts stattfindet ([3.6](#36-wartungsfenster-den-alten-host-einfrieren-und-die-letzte-sicherung-anlegen)). Und bis zur Wiederherstellung schreibt der neue Host seine eigenen, leeren nächtlichen Dumps in denselben S3-Präfix wie die echten, spiele also nie "den neuesten" Dump ein: Nenne ihn immer ([3.7](#37-datenbank-auf-dem-neuen-host-wiederherstellen)). Auch die echten Dumps altern: Nach `BACKUP_RETENTION_DAYS` (90 Tagen) löscht der nächtliche Job sie, lass einen neuen Host also nicht so lange mit leerer Datenbank laufen. Der Deploy legt die Verzeichnisse an, korrigiert den Besitzer des Postgres-Verzeichnisses für Rootless Podman, lehnt Platzhalter oder inkonsistente Secrets ab, verteilt Secrets, Caddyfile und Quadlets, legt die Anwendungs-Datenbankrolle an, startet alle Dienste und aktiviert die beiden Timer. **Postgres startet mit leerer Datenbank.** Das ist zu erwarten; der nächste Schritt füllt sie. Der erste Start von Postgres legt sein Datenverzeichnis an und kann etwa eine Minute dauern; der Deploy wartet bis zu drei Minuten (seine Ausgabe zeigt dabei `FAILED - RETRYING`-Zeilen, die normal sind), und scheitert er trotzdem, starte ihn einfach erneut, er macht dort weiter, wo er aufgehört hat. Er baut keine Images und stellt keine Daten wieder her.

### 3.6 Wartungsfenster: den alten Host einfrieren und die letzte Sicherung anlegen

Tu das zu Beginn des Fensters auf dem **alten Host**, in dieser Reihenfolge (ist der alte Host tot, überspringe diesen Abschnitt: Der neueste nächtliche Dump von vor dem Ausfall ist der Endstand, und alles danach Geschriebene ist verloren):

```bash
# [Arbeitsplatz] die Site "production" nennt noch den alten Host
./vb ssh production
# [alter Host]
# 1. die öffentliche Tür schließen: ab jetzt kann niemand mehr Daten ändern
systemctl --user stop caddy.service
# 2. die letzte Sicherung anlegen und den ausgegebenen Dateinamen notieren
podman exec vb-api python scripts/backup_db.py
# 3. den Stack stoppen, damit kein geplanter Job (Mail) auf beiden Hosts läuft
systemctl --user stop vb-api-pod.service vb-intern-pod.service vb-www-pod.service
```

Ab hier starte den alten Host nicht neu und lösche ihn nicht; er ist der Rückweg ([3.10](#310-wenn-der-umzug-scheitert)).

### 3.7 Datenbank auf dem neuen Host wiederherstellen

Auf dem neuen Host, mit dem in 3.6 notierten Dump (Befehle und Hintergrund in [2.4](#24-die-datenbank-ist-beschädigt-sicherung-und-wiederherstellung)):

```bash
# [Arbeitsplatz]
./vb ssh production-new
# [neuer Host]
podman exec vb-api python scripts/restore_db.py --list
podman exec vb-api python scripts/restore_db.py --force --backup-name <dump-aus-3.6>
podman exec vb-api alembic upgrade head
systemctl --user restart vb-api-pod.service
podman exec vb-api-pg psql -U vb -d vb -c 'SELECT count(*) FROM members;'   # erwartet eine Zahl > 0
podman exec vb-api alembic current                                         # erwartet "(head)"
```

**Wenn `--list` fehlschlägt oder nichts zeigt.** `InvalidAccessKeyId` oder `AccessDenied` weist auf die Zugangsdaten der Ablage in `secrets/vb-api.env.j2` (`S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_REGION`) oder auf eine `aws:SourceIp`-Beschränkung des IAM-Users, die den neuen Host noch nicht erlaubt ([3.1](#31-vorbereitung-bevor-der-alte-host-angefasst-wird)). Eine leere Liste bei funktionierenden Zugangsdaten heißt, dass unter `db-backups/` kein Dump liegt; sieh dir den Bucket in der AWS-Konsole an. Behebe die Ursache, führe `./vb deploy production-new` erneut aus, wenn du ein Secret geändert hast, und wiederhole den Befehl. Ohne irgendeinen Dump startet das System leer, und niemand kann sich anmelden; dieses Handbuch beschreibt keinen Weg, ein erstes Konto anzulegen, behandle einen fehlenden Dump also als Datenverlust.

**Nenne den Dump immer.** Der neue Host schreibt seit seinem ersten Deploy eigene, leere nächtliche Dumps in denselben Präfix; "der neueste" kann einer davon sein. Ist der alte Host tot, nimm den neuesten Dump, der **vor** seinem Ausfall entstand (der Dateiname trägt die Zeit), und ignoriere jeden Dump, den der neue Host erzeugt hat.

Die Wiederherstellung ist auf einem neuen Host **Pflicht**: Ohne sie läuft der Stack, aber mit leerer Datenbank. Eine Browser-Schaltfläche dafür gibt es in Produktion nicht. Auf einer Nicht-Produktions-Stage spiegelt die Schaltfläche `Downsync jetzt durchführen` (**System → Scheduler**) den Produktions-Bucket und stellt die neueste Produktions-Sicherung wieder her, siehe [5.5](#55-downsync).

### 3.8 DNS umschalten und Zertifikate besorgen

Caddy bekommt seine Zertifikate per HTTP-01 auf Port 80, jede Domain muss also zuerst auf den neuen Host zeigen. Caddy versucht es seit dem Deploy mit wachsenden Abständen; ein paar Fehlversuche sind normal, viele können die Let's-Encrypt-Rate-Limits erreichen ([Anhang B](#anhang-b-fallstricke)). Kurze `connection reset by peer`-Zeilen für den API-Port im Caddy-Journal sind normal, solange `vb-api` noch nicht gesund ist.

1. Setze die A/AAAA-Einträge von `api`, `intern` und `www` auf den neuen Host.
2. Warte, bis Caddy seine Zertifikate hat (meist innerhalb von Minuten), und prüfe:
   ```bash
   # [Arbeitsplatz] muss die neue Adresse zeigen (eine alte Antwort kann bis zum Ablauf der TTL im Cache bleiben; nimm deine eigenen Domains, wenn sie nicht `vindobona2.at` heißen)
   for d in api intern www; do dig +short $d.vindobona2.at; done
   # [neuer Host] such nach "certificate obtained successfully"
   journalctl --user -u caddy.service -n 50 --no-pager
   ```
   Passiert nach etwa zehn Minuten nichts, prüfe die DNS-Antwort und ob Port 80 den neuen Host erreicht. Starte Caddy nicht in einer Schleife neu.

Vor dem DNS-Umschalten kannst du den neuen Host ohne gültiges Zertifikat testen, zum Beispiel `curl -k --resolve api.vindobona2.at:443:<neue-ip> https://api.vindobona2.at/`.

### 3.9 Prüfung

- `api`, `intern` und `www` antworten mit `200` und einem gültigen Zertifikat (die Schleife aus [2.1](#21-erste-prüfungen-nur-lesend); ein einfaches `curl -I` liefert auf `api` `405`, weil die API `HEAD` nicht annimmt). Die `dig`-Prüfung in 3.8 zeigt die neue Adresse.
- `podman ps` auf dem neuen Host zeigt jeden Dienst-Container `healthy` (die `-pod-infra`-Helfer nur `Up`); `systemctl --user --failed` gibt nichts aus.
- Die Mitgliederzahl (3.7) ist eine echte Zahl; melde dich auf `intern` an und öffne ein Mitglied.
- `systemctl --user list-timers --all` zeigt `podman-auto-update.timer` und `podman-prune.timer`.
- `./vb ssh production-new admin`, dann zeigt `sudo ufw status verbose` (Debian) oder `sudo firewall-cmd --list-all` (Red-Hat- und SUSE-Familie) nur 22, 80 und 443.
- Die Objektablage funktioniert von diesem Host aus (der Deploy testet sie nicht; ein falscher Schlüssel fiele sonst erst bei der nächsten Sicherung um 03:00 auf): `podman exec vb-api python scripts/backup_db.py` endet mit `Backup complete` (es legt einen weiteren manuellen Dump im Bucket ab), und `podman exec vb-api python scripts/check_s3_integrity.py` meldet `0 missing file(s)` und `No orphaned files found.`
- **System → Scheduler** zeigt die Jobs mit sinnvollen nächsten Laufzeiten.

Schließe das Wartungsfenster, wenn alles grün ist.

### 3.10 Wenn der Umzug scheitert

Solange der alte Host noch die Daten hält und nicht gelöscht ist, ist der Rückweg:

1. Zeigt DNS schon auf den neuen Host: Stoppe zuerst dessen API-Pod, damit keine Mail von beiden Hosts abgeht: `systemctl --user stop vb-api-pod.service` auf dem neuen Host.
2. Haben Leute nach dem DNS-Umschalten schon auf dem neuen Host gearbeitet, lege dort eine Sicherung an (`podman exec vb-api python scripts/backup_db.py`) und spiele sie wie in 3.7 auf dem alten Host ein, sonst gehen diese Änderungen verloren.
3. Auf dem alten Host (`./vb ssh production`): `systemctl --user start caddy.service vb-api-pod.service vb-intern-pod.service vb-www-pod.service`.
4. Setze DNS zurück auf die Adresse des alten Hosts.

Lösche den alten Host nicht, bevor der neue mindestens eine nächtliche Sicherung und ein Auto-Update durchlaufen hat.

### 3.11 Den alten Host aufräumen

- Schalte den alten Host aus, sobald der neue geprüft ist. Lass ihn nicht einfach laufen: Ein Neustart würde seinen Stack und seine geplanten Mails zurückbringen. Lösche ihn beim Anbieter erst später. Er enthält personenbezogene Daten von Mitgliedern: Lass den Anbieter die Platte löschen, oder zerstöre den Server, statt ihn nur auszuschalten.
- Entferne die Adresse des alten Hosts aus der `aws:SourceIp`-Beschränkung des Produktions-IAM-Users, falls es eine gibt (siehe 3.1). Die nur lesenden IAM-User der anderen Stages hängen an den Hosts dieser Stages und sind nicht betroffen.
- Mache den neuen Host zur Site `production`: lösche in `vb-access/sites` das alte `production`, benenne `production-new` in `production` um und setze in dessen `inventory.ini` unter `[vps]` wieder die Domain `www.vindobona2.at` ein. Committe die Änderung in `vb-access`. Setze die DNS-TTL auf den normalen Wert zurück.
- Entferne den alten Host aus `known_hosts`.
- Rotiere die Secrets, die der alte Host hielt, wenn über sein Schicksal Zweifel bestehen ([Anhang D](#anhang-d-fallbeispiele), Fallbeispiel 6).

---

## 4. Skripte und CLI-Werkzeuge

### 4.1 Das Werkzeug `vb` und Ansible (Arbeitsplatz)

| Was | Wo | Zweck |
|---|---|---|
| `./vb setup <stage>` | dieses Repository | Basis-Konfiguration eines Hosts: Benutzer, SSH-Schlüssel, Podman, Firewall, SSH-Härtung. Erster Lauf auf einem neuen Host, erneut zum Prüfen. |
| `./vb deploy <stage>` | dieses Repository | Secrets, Caddyfile, Quadlets, Dienste, Timer. Tags: `deploy-api`, `deploy-intern`, `deploy-www` (laufen nur, wenn sie genannt werden). |
| `./vb check <stage>` | dieses Repository | Vorschau beider Playbooks (`--check --diff`), ändert nichts. |
| `./vb ssh <stage> [admin\|service]` | dieses Repository | Interaktive Shell auf dem Host mit dem Betriebsschlüssel (sie nimmt keinen Befehl: führe Befehle in der Shell aus). |
| `./vb init <stage>` | dieses Repository | Legt das Datenverzeichnis einer Stage aus `site-template/` mit einem neuen Betriebsschlüssel an. |
| `./vb rotate-key <stage>` | dieses Repository | Ersetzt den Betriebsschlüssel in zwei Schritten, sodass immer einer funktioniert. |
| `playbooks/setup_vps.yml`, `playbooks/deploy.yml` | dieses Repository | Die Playbooks hinter `setup` und `deploy`. `deploy.yml` braucht `-e site_dir=<Stage-Verzeichnis>`. |
| `ansible-vault` | Secrets | `edit`, `view`, `rekey` für die Dateien unter `vb-access`. |

Jeder `./vb`-Befehl fragt einmal nach dem Vault-Passwort, entpackt den Betriebsschlüssel in einen privaten Ordner im Arbeitsspeicher (`/dev/shm`, oder das temporäre Verzeichnis, wo es das nicht gibt), läuft und löscht den Schlüssel wieder, auch wenn du Strg+C drückst. Die eigenen Prüfungen des Werkzeugs (Ruff, Pyright, pytest mit den Doku-Tests, die Syntaxprüfung beider Playbooks) laufen mit `pre-commit` und bei jedem Pull Request in GitHub Actions (`.github/workflows/ci.yml`); einmal pro Clone `pre-commit install`. Die Versionen aller Prüfwerkzeuge sind mit Hashes in `requirements-dev.lock` festgelegt.

### 4.2 Betriebs-Skripte in `vb-api`

Sie liegen in `vb-api/scripts/`, laufen **im Container `vb-api` auf dem Host** (`podman exec vb-api python scripts/<name>.py`), nicht auf deinem eigenen Computer. Vollständige Parameter und Beispiele: [`vb-api/scripts/README.de.md`](https://github.com/K-O-St-V-Vindobona-II/vb-api/blob/main/scripts/README.de.md).

| Skript | Zweck | Ändert Daten? |
|---|---|---|
| `backup_db.py` | Schreibt einen PostgreSQL-Dump nach S3 (`--list`, `--cleanup`). | Fügt einen Dump hinzu. |
| `restore_db.py` | Stellt die Datenbank aus einem S3-Dump wieder her (`--list`, `--backup-name`, `--force`). Braucht in Produktion `--force`. | **Ersetzt die Datenbank.** |
| `check_s3_integrity.py` | Vergleicht Datenbank und S3 in beide Richtungen. | Nein. |
| `downsync_prod.py` | Nur Nicht-Produktion: spiegelt den Produktions-Bucket und stellt den neuesten Produktions-Dump wieder her (`--dry-run`, `--yes`, `--skip-db`, `--skip-s3`, `--no-delete`, `--light`). Verweigert sich in Produktion. | **Ersetzt Daten der Stage.** |
| `maintain_deleted_archive_files.py` | Listet, stellt wieder her und löscht endgültig soft-gelöschte Archiv-Dateien (`list`, `restore`, `purge`, `purge-duplicates`, `list-duplicates`, `urlpath`). Das einzige Werkzeug, das Archivdaten endgültig löscht. | `purge*` löscht endgültig. |
| `trigger_chronicles.py` | Probelauf oder manueller Versand der wöchentlichen Chronik-Mail für ein Stichdatum. Verschickt ohne `--send` nichts. | Verschickt Mail mit `--send`. |

Entwickler-Helfer im selben Ordner: `run_pytest.sh` (Testlauf plus Coverage-Gate), `check_coverage_gate.py`, `check_router_soc.py` (Router dürfen keine Geschäftslogik enthalten).

### 4.3 Geplante Jobs (Container `vb-api-worker`)

Alle Zeiten sind Wiener Zeit. Die Seite **System → Scheduler** zeigt dieselben Informationen live.

| Job | Zeitplan | Stage | Zweck |
|---|---|---|---|
| `cleanup` | stündlich | alle | Löscht abgelaufene Sitzungen, Reset-Tokens, alte Aktivitätsprotokolle, gesendete Mails, verwaiste User-Agents. |
| `refresh_category_filter_hits` | täglich 07:00 | production | Berechnet die Filter-Treffer der AH-Kassen-Kategorien neu. |
| `birthday_mails` | täglich 15:53 | production | Geburtstagsgrüße an Mitglieder, die morgen Geburtstag haben. |
| `debtor_reminder` | am 25. um 18:32 | production | Erinnerungen bei Beitragsrückständen (mehr als 300 EUR). |
| `standesdb_chronicles` | Dienstag 17:00 | production | Die wöchentliche Jubiläums-Mail. |
| `archive_health_check` | Dienstag 01:00 | production | Archiv-Dateien gegen S3, Bericht an die Archiv-Administratoren. |
| `standesdb_health_check` | Dienstag 03:00 | production | Mitgliederbilder gegen S3, Bericht an die Administratoren. |
| `db_backup` | täglich 03:00 (`BACKUP_HOUR`) | production | Dump nach S3, Dumps älter als `BACKUP_RETENTION_DAYS` löschen (90 Tage in der Vorlage). |
| `downsync` | täglich 04:00 (`BACKUP_HOUR` + 1) | alle außer production | Produktion spiegeln, neuesten Dump wiederherstellen. |

Auf dem Host laufen außerdem zwei systemd-Timer, die nicht zum Scheduler gehören: `podman-auto-update.timer` (nächtlich, kurz nach Mitternacht mit zufälliger Verzögerung bis 15 Minuten) und `podman-prune.timer` (sonntags 04:00). Beide folgen der Host-Uhr, die derzeit Wiener Zeit ist.

### 4.4 Alltagsbefehle auf dem Host

| Ziel | Befehl |
|---|---|
| Container und Zustand | `podman ps` |
| Fehlgeschlagene Units | `systemctl --user --failed` |
| Einen Pod neu starten | `systemctl --user restart vb-api-pod.service` (siehe [2.2](#22-dienst-oder-pod-neu-starten)) |
| Logs eines Dienstes | `journalctl --user -u vb-api.service -n 100 --no-pager`, `podman logs --tail 100 <container>` |
| Timer | `systemctl --user list-timers --all` |
| Was würde das Auto-Update ändern? | `podman auto-update --dry-run` |
| Datenbank-Revision | `podman exec vb-api alembic current` |
| Eine Datenbank-Shell öffnen | `podman exec -it vb-api-pg psql -U vb -d vb` |
| Unbenutzte Images aufräumen | `podman image prune` |
| Dateien eines Rootless-Datenbankverzeichnisses (verschieben, löschen) | mit `podman unshare` davor ([Anhang B](#anhang-b-fallstricke)) |

### 4.5 Frontend-Befehle (`vb-intern`, `vb-www`)

`npm run dev` (Hot Reload), `npm run build` (Typprüfung und Produktions-Build), `npm run test:unit`, `npm run test:coverage`, `npm run lint`, `npm run format:check`, `npm run type-check`. Führe sie im Dev-Container aus, siehe die READMEs der Repositories.

### 4.6 Web-Funktionen für Betreiber

Seite **System → Scheduler** in `vb-intern` (Berechtigung `systemAdmin`): Job-Liste mit nächstem und letztem Lauf und Job-Historie, `Backup jetzt erstellen` (Produktion), `Downsync jetzt durchführen` (andere Stages). Die Seite **System → SQL-Einsicht** zeigt Datenbanktabellen nur lesend. Logs: `https://intern.vindobona2.at/logging/dozzle` (Basic Auth).

---

## 5. Vorgelagerte Stages und lokale Entwicklung

### 5.1 Voraussetzungen

Wie in [3.3](#33-voraussetzungen-am-arbeitsplatz), und zusätzlich:

- **Speicherplatz:** Eine Nicht-Produktions-Stage spiegelt den gesamten Produktions-Bucket in ihr eigenes Garage (`~/data/vb/<stage>/garage-data`). Die Spiegelung maß am 13. Oktober 2026 43 GB und wächst nur. Plane mindestens die Größe des Produktions-Buckets plus Reserve ein, zusätzlich zu Betriebssystem und Postgres. **So prüfst du es:** Die Größe einer bestehenden Spiegelung zeigt `podman unshare du -sh ~/data/vb/<stage>/garage-data` auf einer beliebigen Nicht-Produktions-Stage (`podman unshare`, weil das Verzeichnis dem abgebildeten Container-Benutzer gehört); den freien Platz eines neuen Servers zeigt `df -h /` nach `./vb ssh <stage>`. Das erste `./vb deploy` einer Nicht-Produktions-Stage bricht mit einer Meldung ab, wenn weniger als `min_free_disk_gb` GiB frei sind (Standard 60). Mit dem Light-Downsync (`DOWNSYNC_LIGHT=true`, siehe [5.5](#55-downsync)) genügen rund 15 GB: Trage `min_free_disk_gb=15` unter `[vps:vars]` im Inventory ein. Für einen Wegwerf-Server, der nie einen Downsync fährt, trage dort `min_free_disk_gb=0` ein. Spätere Deployments prüfen nicht erneut, weil dann die Spiegelung selbst den Platz belegt.
- **DNS:** Vier Domains pro Stage: `api`, `intern`, `www` und `storage_domain` (die S3-API von Garage).

### 5.2 Eine test- oder qa-Stage aufbauen

1. **Die Daten der Stage anlegen:** `./vb init test` kopiert `site-template/test` nach `../vb-access/sites/test` und erzeugt einen **neuen Betriebsschlüssel** für diese Stage. Es fragt zweimal nach einem Vault-Passwort: **Nimm dasselbe Vault-Passwort wie für die anderen Dateien in `vb-access`**, sonst bräuchte diese Stage ein eigenes. Ein Passwort unter 20 Zeichen lehnt es ab.
2. **Inventory:** Fülle `sites/test/inventory.ini`: den echten Hostnamen, alle vier Domains statt der Platzhalter `CHANGEME.example.invalid` und `acme_email` (der Kontakt des Zertifikats-Accounts). Solange eine Stage erprobt wird, wählt `caddy_acme_ca` (in der Vorlage auskommentiert) die Staging-CA von Let's Encrypt, die keine Rate Limits hat. Läuft alles, kommentiere die Zeile wieder aus und führe `./vb deploy` aus: Caddy holt dann innerhalb etwa einer Minute Zertifikate der echten CA.
3. **Secrets:** Lege die echten Dateien in `sites/test/secrets/` aus den `*.example`-Dateien von `site-template/test/secrets/` an und verschlüssele jede mit `ansible-vault create` ([Anhang A](#anhang-a-secrets-und-pflichtvariablen)). Erzeuge jeden Wert für diese Stage frisch (`SECRET_KEY`, Postgres- und Valkey-Passwörter, den Caddy-Basic-Auth-Hash, die eigenen Garage-Zugangsdaten der Stage); kopiere nie einen Wert aus einer anderen Stage. SMTP und `GOOGLE_CLIENT_ID` bleiben leer, bis es für die Stage einen Mailserver und eine OAuth-App gibt. Leer heißt: den Wert auf nichts setzen (`NAME=`) oder die Zeile löschen, denn `deploy.yml` lehnt jede Datei ab, die noch `change-me` enthält; dasselbe gilt für die AWS-Werte, solange kein Downsync läuft. Der Downsync braucht zusätzlich `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_BUCKET` und `AWS_REGION` eines **nur lesenden** IAM-Users (`s3:ListBucket` und `s3:GetObject` auf den Produktions-Bucket), der nur für diese Stage existiert und per `aws:SourceIp`-Bedingung auf den Host der Stage beschränkt ist („Der nur lesende AWS-Schlüssel“ unter der Liste zeigt, wie du ihn anlegst). Der Produktions-Bucket enthält auch die Datenbank-Dumps, dieser Schlüssel kann also eine vollständige Kopie aller Mitgliederdaten lesen.
4. **DNS** für alle vier Domains.
5. **Host:** `./vb setup test`, dann `./vb deploy test` (`./vb check` geht erst nach `setup`; [3.4](#34-host-einrichten-vb-setup), [3.5](#35-deployen-vb-deploy)). Das fährt auch das Garage der Stage hoch und korrigiert den Besitzer des Postgres-Verzeichnisses.
6. **Daten:** Löse einen Downsync aus ([5.5](#55-downsync)).

**Der nur lesende AWS-Schlüssel.** Jede Stage mit Downsync braucht einen eigenen IAM-User im AWS-Account des Produktions-Buckets. Der Schlüssel gehört nirgends hin außer in die Vault-Datei `sites/<Stage>/secrets/vb-api.env.j2` dieser Stage (`AWS_*`) und in die AWS-Konsole. Verwende nie die `S3_*`-Werte aus `sites/production/secrets/vb-api.env.j2`: Das ist der Schlüssel der Anwendung, und der darf schreiben und löschen.

1. Öffne in der AWS-Konsole IAM, Users, Create user. Benenne ihn nach der Stage (zum Beispiel `vb-downsync-test`) und gib ihm keinen Konsolenzugang.
2. Hänge diese Inline-Policy an. Ersetze `203.0.113.10/32` durch die öffentliche IPv4-Adresse des Hosts der Stage; die Bedingung macht einen abgeflossenen Schlüssel überall sonst wertlos. `vindobona2-at` ist der Name des Produktions-Buckets:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": "s3:ListBucket",
         "Resource": "arn:aws:s3:::vindobona2-at",
         "Condition": { "IpAddress": { "aws:SourceIp": "203.0.113.10/32" } }
       },
       {
         "Effect": "Allow",
         "Action": "s3:GetObject",
         "Resource": "arn:aws:s3:::vindobona2-at/*",
         "Condition": { "IpAddress": { "aws:SourceIp": "203.0.113.10/32" } }
       }
     ]
   }
   ```

3. Öffne den User, Security credentials, Create access key, Verwendungszweck „Application running outside AWS“. Kopiere beide Werte direkt in die Vault-Datei der Stage: `ansible-vault edit ../vb-access/sites/<Stage>/secrets/vb-api.env.j2`, setze `AWS_ACCESS_KEY_ID` und `AWS_SECRET_ACCESS_KEY` (`AWS_BUCKET=vindobona2-at` und `AWS_REGION=eu-central-1` bleiben wie in der Vorlage). Das Secret wird nur einmal angezeigt.
4. Führe `./vb deploy <Stage>` aus und teste dann, ohne etwas zu ändern: `podman exec vb-api python scripts/downsync_prod.py --dry-run` listet auf, was ein Downsync kopieren würde. `AccessDenied` heißt: Die Policy oder die IP-Adresse stimmt nicht. Wechselt der Host seine Adresse, ändere sie auch in der Policy.

**Die Stage prüfen.** Auf dem Host: `podman ps` zeigt jeden Dienst-Container `healthy`, auch `vb-garage`; `podman exec vb-api alembic current` endet mit `(head)`; `podman exec vb-garage /garage bucket list` listet den Bucket. Vom Arbeitsplatz: `curl -s -o /dev/null -w '%{http_code}\n' https://<Domain>/` antwortet für `api`, `intern` und `www` mit `200` und für `storage` mit `403` (anonymer Zugriff auf die S3-Wurzel wird verweigert, das ist richtig); `https://<Intern-Domain>/config.js` zeigt die Laufzeit-Konfiguration; die Dozzle-Adresse antwortet ohne Login mit `401`. Vor dem ersten Downsync ist die Datenbank leer, also kann sich niemand anmelden.

**Wegwerf-Server.** Um die Pipeline auf einem Server zu erproben, den es morgen nicht mehr gibt, nutze die Stage `test` mit den `.invalid`-Domains und der Staging-CA und, ist ihre Platte kleiner als 60 GiB, `min_free_disk_gb=0` ([5.1](#51-voraussetzungen)): Es wird nichts Echtes berührt, und Caddy kann eben keine Zertifikate besorgen. Nimm dafür nie die Stage `production`: Sie würde die echten Secrets auf den Wegwerf-Server legen.

### 5.3 Secrets

```bash
# [Arbeitsplatz]
ansible-vault edit ../vb-access/sites/<stage>/secrets/vb-api.env.j2
PAGER=cat ansible-vault view ../vb-access/sites/<stage>/secrets/vb-api.env.j2
./vb deploy <stage>      # die Änderung verteilen
```

Dateien auf `.env.j2` enthalten stage- und domain-abhängigen Inhalt; der Deploy entschlüsselt sie transparent und füllt danach die Template-Variablen aus. In `vb-access` sind alle echten Dateien vault-verschlüsselt; die unverschlüsselten `.example`-Dateien gibt es nur hier in `site-template/`, sie dokumentieren die Zielstruktur jedes Stage-Typs. `EDITOR=nano ansible-vault edit ...` wählt für einen Aufruf einen anderen Editor. Ändere Secrets nur mit `ansible-vault edit`, nie durch Entschlüsseln einer Datei an Ort und Stelle. Ein Pre-commit-Hook in `vb-access` lehnt jede unverschlüsselte Secret-Datei und jeden privaten Schlüssel im Klartext ab, und ein Hook in diesem Repository lehnt jede Vault-Datei hier ab: **Dieses öffentliche Repository enthält nie eine Vault-Datei.** Die benötigten Variablen pro Stage-Typ stehen in [Anhang A](#anhang-a-secrets-und-pflichtvariablen).

### 5.4 Garage auf Nicht-Produktions-Stages

`test` und `qa` betreiben [Garage](https://garagehq.deuxfleurs.fr/) als S3-kompatiblen Ersatz für AWS S3; Produktion betreibt nie Garage. Garage tritt `vb-api-pod` bei, wie Postgres und Valkey, sodass `vb-api` es über `localhost` erreicht: Dieser Verkehr muss im Pod bleiben. Ein eigener Pod wäre nur über die öffentliche Domain erreichbar, was mit Rootless-Netzwerk nicht funktioniert (jeder Pod hat sein eigenes Loopback, und es gibt kein Hairpin-NAT).

- Die Flags `--single-node --default-bucket` legen beim ersten Start den Bucket und einen Zugriffsschlüssel an. `deploy.yml` setzt zusätzlich bei jedem Deploy die CORS-Richtlinie des Buckets (damit Browser presigned URLs lesen dürfen), über die echte S3-API mit dem eigenen `boto3` von `vb-api`, und entzieht dem Anwendungsschlüssel das Recht, Buckets anzulegen.
- **Port 9000 (S3-API)** ist hinter Caddy und TLS auf `storage_domain` öffentlich, weil presigned Links vom Browser des Besuchers abgerufen werden. `vb-api` selbst spricht mit Garage über `http://127.0.0.1:9000` im Pod.
- **Port 9001 (Admin-API)** ist nur Loopback; erreiche ihn über einen SSH-Tunnel mit dem Betriebsschlüssel.

### 5.5 Downsync

Jede Nicht-Produktions-Stage frischt sich einmal täglich aus Produktion auf (Job `downsync`, eine Stunde nach der Produktions-Sicherung): Sie spiegelt den gesamten Produktions-Bucket in das Garage der Stage, stellt den neuesten Produktions-Dump wieder her und führt `alembic upgrade head` aus. Mit `DOWNSYNC_LIGHT=true` in `vb-api.env.j2` (oder `--light` auf der Kommandozeile) spiegelt sie nur die Mitgliederbilder, die öffentliche Galerie und den neuesten Dump: rund 50 MB statt 43 GB, aber Archivdateien fehlen dann auf der Stage. Auf Abruf:

```bash
# [Host] auf der Stage; --yes, weil podman exec ohne -it kein Terminal für die Rückfrage hat
podman exec vb-api python scripts/downsync_prod.py --yes
```

oder die Schaltfläche `Downsync jetzt durchführen` auf **System → Scheduler**. Produktion verweigert beides. Die Daten sind echt, nicht anonymisiert.

### 5.6 Den Alltag betreiben

Alles aus [2.1](#21-erste-prüfungen-nur-lesend) bis [2.7](#27-die-benötigten-vb-befehle) gilt für jede Stage; nur der Stage-Name ändert sich.

### 5.7 Lokale Entwicklungsumgebung

Die Entwicklungs-Stage läuft unabhängig von Ansible und Vault direkt auf dem Entwickler-Host, mit eigenen Quadlets (live eingebundener Quellcode, Vite-Dev-Server). Die Vorlagen liegen unter `dev/`:

```
dev/quadlets/api/      vb-api, vb-api-pg, vb-api-valkey, vb-api-worker und der Pod
dev/quadlets/intern/   vb-intern (Vite-Dev-Server)
dev/quadlets/www/      vb-www (Vite-Dev-Server)
dev/quadlets/garage/   vb-garage (S3-Ersatz) und sein eigener Pod
dev/postgres/          pg_hba.conf für vb-api-pg
dev/env/               *.env.example für jeden Container
```

**Einrichtung**

1. Kopiere jedes `*.example` aus `dev/quadlets/` (ohne `.example`) nach `~/.config/containers/systemd/vb/<komponente>/`, jedes `*.example` aus `dev/env/` nach `~/.env/`, `config/garage/garage.toml` dorthin, wohin das Config-Volume der Garage-Quadlet zeigt, und `dev/postgres/pg_hba.conf` nach `~/data/vb-api/postgres-config/pg_hba.conf`.
2. Ersetze die Platzhalter in den Quadlets: `<path-to-vb-fastapi-vue>` (der Checkout, der die Repositories enthält), `<your-mail-dev-domain>`, `<your-garage-dev-domain>` und die Garage-Daten- und Meta-Verzeichnisse.
3. Ersetze jedes `change-me` in den Env-Dateien.

**Garage in der Entwicklung.** Hier läuft Garage in einem **eigenen Pod** (`vb-garage-pod`) und wird über eine lokale Domain erreicht, die auf das Host-Gateway aufgelöst wird (die `AddHost`-Zeilen der Pod-Dateien). Die Stages, die dieses Repository deployt, halten Garage in `vb-api-pod` ([5.4](#54-garage-auf-nicht-produktions-stages)).

**Ports und Routing.** Die Entwicklung lauscht auf `127.0.0.1`: API 20000, intern 20001, www 20002, Garage 9000 und 9001. Für echte Domain-Namen (um Cookies und CORS wie in Produktion zu testen) richte dein eigenes DNS und einen Reverse Proxy ein, zum Beispiel Caddy; dieser Teil ist nicht in diesem Repository.

**Erster Start**

```bash
# [Host] das Entwicklungs-Image bauen (dafür gibt es absichtlich kein Quadlet)
podman build --target dev -t vb-api:dev <path-to-vb-fastapi-vue>/vb-api

systemctl --user daemon-reload
systemctl --user start vb-garage-pod vb-api-pod vb-intern vb-www

# das Schema anlegen
podman exec vb-api alembic upgrade head

# echte Daten laden (braucht ~/.env/vb-api-aws-prod.env, siehe das vb-api-README)
podman exec vb-api python scripts/downsync_prod.py --yes
```

Führe nie `pip install` oder `npm install` auf dem Host aus; nimm `podman exec`.

---

## Anhang A: Secrets und Pflichtvariablen

Welche Variablen einen echten Wert brauchen, unterscheidet sich zwischen Produktion (echtes AWS S3, SMTP und Google) und `test`/`qa` (eigenes Garage, meist kein Mailserver oder OAuth-App). "Optional" heißt, die Einstellung hat in `app/core/config.py` einen funktionierenden Standardwert. Die Dateien liegen in `sites/<stage>/secrets/` von `vb-access`; die Vorlagen stehen in `site-template/<stage>/secrets/` hier.

| Datei | Variable(n) | Produktion | `test`/`qa` |
|---|---|---|---|
| `caddy.env` | `LOGGING_USER`, `LOGGING_PASSWORD_HASH` | erforderlich | erforderlich |
| `vb-api-pg.env` | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | erforderlich | erforderlich |
| `vb-api.env.j2` | `SECRET_KEY` | erforderlich | erforderlich |
| `vb-api.env.j2` | `DATABASE_URL` | erforderlich (Rolle `vb_app`, vom Bootstrap-Superuser durch `config/postgres/split-app-role.sql` abgespalten) | erforderlich |
| `vb-api.env.j2` | `VALKEY_URL` | erforderlich | erforderlich |
| `vb-api-valkey.env` | `VALKEY_PASSWORD` | erforderlich, muss zu `VALKEY_URL` passen | erforderlich |
| `vb-api.env.j2` | `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` | erforderlich (echtes AWS) | erforderlich (der Garage-Schlüssel dieser Stage, passend zu `vb-garage.env.j2`) |
| `vb-api.env.j2` | `S3_ENDPOINT_URL`, `S3_PUBLIC_ENDPOINT_URL` | nicht gesetzt (AWS-Standard) | erforderlich (`http://127.0.0.1:9000` und `https://{{ storage_domain }}`) |
| `vb-api.env.j2` | `S3_REGION` | erforderlich (`eu-central-1`) | erforderlich (`us-east-1`, muss zu `s3_region` in `config/garage/garage.toml` passen) |
| `vb-api.env.j2` | `S3_PATH_*` | optional | optional |
| `vb-api.env.j2` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_BUCKET`, `AWS_REGION` | nicht anwendbar (Downsync verweigert sich in Produktion) | erforderlich für den Downsync (nur lesende Produktions-Zugangsdaten) |
| `vb-api.env.j2` | `DOWNSYNC_LIGHT` | nicht anwendbar | optional (`true`: Der Downsync spiegelt nur die Mitgliederbilder, die öffentliche Galerie und den neuesten Dump, nicht das Archiv) |
| `vb-api.env.j2` | `BACKUP_ENABLED`, `BACKUP_HOUR`, `BACKUP_RETENTION_DAYS` | `true`; täglich zu `BACKUP_HOUR` | optional (`false`: die eigenen Sicherungen einer Wegwerf-Stage haben wenig Wert) |
| `vb-api.env.j2` | `SMTP_*` | erforderlich | nicht gesetzt |
| `vb-api.env.j2` | `GOOGLE_CLIENT_ID` | erforderlich (eine öffentliche Kennung, kein Geheimnis) | nicht gesetzt |
| `vb-api.env.j2` | `AKTIVITAS_ACCOUNT_HOLDER`, `AKTIVITAS_IBAN`, `AKTIVITAS_BIC`, `AKTIVITAS_FEE_TEXT` | erforderlich (Zahlungsseite; `deploy.yml` lehnt eine Datei ohne sie ab) | erforderlich (ein wohlgeformtes Muster genügt: eine IBAN mit gültiger Prüfsumme, zum Beispiel `AT61 1904 3002 3457 3201`, und eine BIC mit 8 oder 11 Zeichen, zum Beispiel `BKAUATWW`) |
| `vb-intern.env.j2` | `API_BASE_URL`, `PASSWORD_MIN_LENGTH`, `APP_ENVIRONMENT`, `GOOGLE_CLIENT_ID` (optional) | erforderlich | erforderlich |
| `vb-www.env.j2` | `API_BASE_URL` | erforderlich | erforderlich |
| `vb-garage.env.j2` | `GARAGE_RPC_SECRET`, `GARAGE_ADMIN_TOKEN`, `GARAGE_DEFAULT_ACCESS_KEY`, `GARAGE_DEFAULT_SECRET_KEY`, `GARAGE_DEFAULT_BUCKET` | Datei existiert nicht | erforderlich |

`deploy.yml` lehnt eine Datei ab, die noch einen Platzhalterwert enthält, ein Valkey-Passwort, das in seinen beiden Dateien abweicht, oder ein `PASSWORD_MIN_LENGTH`, das zwischen API und Frontend abweicht.

**Ein Secret ändern.** Bearbeite die Datei mit `ansible-vault edit`, dann führe `./vb deploy <stage>` aus. Der Deploy erkennt, welche Environment-Dateien sich geändert haben, und startet genau die Pods neu, die sie lesen.

| Secret | Hinweise |
|---|---|
| `SECRET_KEY` | Signiert die kurzlebigen Zugriffs-Tokens (15 Minuten). Ein neuer Schlüssel macht jedes ausgegebene Zugriffs-Token ungültig; Browser holen sich mit ihrem Refresh-Cookie ein neues, **die Nutzer bleiben also angemeldet**. Nimm einen zufälligen Wert mit mindestens 32 Zeichen ohne Anführungszeichen (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`). |
| `DATABASE_URL` (Passwort von `vb_app`) | Der Deploy setzt das neue Passwort auf der Datenbankrolle. Nimm 16 oder mehr Zeichen aus `A-Za-z0-9._~-`. |
| `VALKEY_PASSWORD` und `VALKEY_URL` | Ändere beide Dateien im selben Schritt; der Deploy prüft, dass sie übereinstimmen. |
| Dozzle-Login | Erzeuge einen Hash mit `podman run --rm docker.io/library/caddy:2.11 caddy hash-password` und trage ihn in `caddy.env` als `LOGGING_PASSWORD_HASH` ein, ohne Anführungszeichen. Ohne Podman auf dem Arbeitsplatz geht jeder bcrypt-Hash, zum Beispiel `python3 -c "import bcrypt, getpass; print(bcrypt.hashpw(getpass.getpass().encode(), bcrypt.gensalt()).decode())"` (das Modul kommt meist mit Ansible). |
| AWS-Schlüssel, SMTP-Passwort | Ändere sie zuerst in der AWS-Konsole bzw. beim Mail-Anbieter, dann hier, dann `./vb deploy`. |

## Anhang B: Fallstricke

Aus echten Vorfällen gesammelt.

- **Pod-Optionen wirken nur beim Anlegen des Pods.** Startet `./vb deploy` einen bereits laufenden Pod, wird er nicht mit neuen Optionen neu angelegt. Starte nach einer Änderung einer Pod-Option die Pod-Unit selbst neu (`systemctl --user restart vb-api-pod.service`).
- **Pods über die Pod-Unit neu starten, nicht über einen Container** ([2.2](#22-dienst-oder-pod-neu-starten)).
- **Exit-Policy der Pods.** Die Pods setzen `--exit-policy=continue` über `PodmanArgs`. Mit dem Quadlet-Standard (`stop`) würde ein nächtliches Auto-Update, das einen Container stoppt und neu anlegt, den ganzen Pod dauerhaft herunterreißen.
- **Rootless-Besitz des Postgres-Verzeichnisses.** Nach `initdb` gehört das Verzeichnis dem gemappten Benutzer `postgres` (UID 999), nicht `service`. Jedes `mv`, `rm` oder `cp` auf dem Host braucht `podman unshare`; ein frisches Verzeichnis braucht vor dem allerersten Start `podman unshare chown -R 999:999 ~/data/vb/<stage>/postgres` und `chmod 700` (`deploy.yml` erledigt das bei der Ersteinrichtung).
- **Postgres-Mount.** Das Volume wird eine Ebene über dem versionierten Datenverzeichnis eingebunden (`~/data/vb/<stage>/postgres:/var/lib/postgresql`); Postgres legt sein eigenes Unterverzeichnis an.
- **Der erste Start von Postgres ist langsam.** Er legt sein Datenverzeichnis an und dauerte auf einem kleinen Server etwa 40 Sekunden; der Deploy wartet bis zu drei Minuten auf die Datenbankrolle. Bleibt ein Deploy trotzdem dort stehen, starte ihn erneut.
- **Let's-Encrypt-Rate-Limits.** Fünf doppelte Zertifikate pro identischer Domain-Menge und Woche. Viele Neuinstallationen oder Neustarts mit falschem DNS können es ausschöpfen; Caddy bekommt dann für diese Menge kein Zertifikat, bis das Fenster verstrichen ist. Teste mit `curl -k --resolve ...`, statt es erneut zu versuchen, oder nimm auf Wegwerf-Stages die Staging-CA.
- **Die Anwendungs-Images gibt es nur für x86_64.** Ein ARM-Server scheitert zu Beginn von `./vb deploy` mit einer klaren Meldung; bestelle einen x86_64-Tarif.
- **Das Root-Passwort des Anbieters kann abgelaufen sein.** Der erste SSH-Login verlangt dann ein neues Passwort, und Ansible kann nicht antworten. Melde dich einmal von Hand an, setze ein neues und nimm dieses ([3.2](#32-betriebssystem-installieren)).
- **Die Vorschau von `setup` listet eine Änderung, die nicht echt ist.** Auf einem aktuellen Host zeigt `./vb check` unter Debian eine `ufw`-Änderung: Die Vorschau meldet das Entfernen der alten Rate-Limit-Regel auch dann, wenn sie schon weg ist. Der echte Lauf meldet keine Änderung.
- **Zwei Läufe innerhalb einer Minute.** Ansible hält SSH-Verbindungen 60 Sekunden offen. Die Beweisschritte von `setup_vps.yml` öffnen deshalb eine eigene Verbindung, damit ein früherer Login nicht für den Schlüssel einspringt, der bewiesen werden soll; entferne das nicht.
- **Bearbeite `operator_public_keys` nie und starte dann Ansible von Hand.** Auf dem Host bleiben genau die gelisteten Schlüssel. `./vb setup` lehnt eine Liste ab, die den Schlüssel nicht enthält, mit dem es sich verbindet; ein manueller `ansible-playbook`-Lauf tut das nicht.
- **SELinux.** Auf Hosts der Red-Hat- und SUSE-Familie tragen die Bind-Mounts der Quadlets `:Z`/`:z`; Dozzle läuft ohne Label-Einschränkung. Manche Anbieter-Images liefern SELinux im Permissive-Modus aus (protokolliert, erzwingt nicht); der Stack wurde auch mit erzwungenem SELinux geprüft.
- **Migrationen laufen einmal pro Rollout.** Nur `vb-api` migriert; `vb-api-worker` hat `SKIP_MIGRATIONS=true`. Alembic hat keine verteilte Sperre, zwei migrierende Container wären also ein echtes Risiko.
- **Passwort-Login ist aus.** Nach `./vb setup` bringt dich nur der Betriebsschlüssel auf den Host. Geht der Schlüssel verloren, bleibt der Rescue-Modus des Anbieters ([Anhang C](#anhang-c-zugang-und-übergabe)).
- **Ein neu aufgesetzter Server mit alter Adresse.** SSH meldet einen geänderten Host-Schlüssel. Vergleiche zuerst den Fingerabdruck mit der Konsole des Anbieters, dann `ssh-keygen -R <adresse>`.

## Anhang C: Zugang und Übergabe

Drei Arten von Information halten dieses System am Laufen. Sie sind absichtlich getrennt:

| Art | Wo | Wer kann sie lesen |
|---|---|---|
| Wie das System gebaut und betrieben wird | dieses Repository und die drei Anwendungs-Repositories (öffentlich) | alle |
| Die Daten dieser Installation: Server und Domains, die Secrets, der Betriebsschlüssel | `vb-access`, Ordner `sites/` (vault-verschlüsselt, privat) | Owner der Organisation mit dem Vault-Passwort |
| Die Accounts, die nicht maschinenlesbar sind: Hosting, Domain, AWS-Konsole, Google, GitHub-Owner, mit Logins, zweiten Faktoren und Wiederherstellungscodes | `vb-access`, die Notiz `credentials.md` (Freitext, vault-verschlüsselt, privat) | dieselben Personen |

**Eine Nachfolgerin oder ein Nachfolger braucht drei Dinge, und sonst nichts:**

1. Die Adresse der GitHub-Organisation: [K-O-St-V-Vindobona-II](https://github.com/K-O-St-V-Vindobona-II).
2. Das Login des gemeinsamen Owner-Kontos der Organisation (Pool-Konto), einschließlich der Wiederherstellungscodes seines zweiten Faktors. Es gibt Zugang zum privaten Repository `vb-access`.
3. Das Vault-Passwort, auf einem anderen Weg übergeben als das Login.

Mit diesen führt [das Übergabeblatt](HANDOVER.de.md) in wenigen Minuten zu einer funktionierenden Shell auf dem Produktions-Host, und Kapitel 3 baut das ganze System auf einem neuen Server neu auf. Alles andere, auch die Logins der Hosting-, AWS- und Domain-Accounts, steht in der Zugangs-Notiz. Lies sie mit `PAGER=cat ansible-vault view credentials.md` in `vb-access`.

**Die Zugangskette.**

```
Owner-Login ──> privates Repository vb-access ──(Vault-Passwort)──> operator_key.vault ──> SSH zum Host
                                  |                                      secrets/ ──> ./vb deploy
                                  └──(Vault-Passwort)──> credentials.md (Accounts, 2FA, Wiederherstellungscodes)
```

**Das Vault-Passwort** ist das einzige Geheimnis, das sich ein Mensch merken oder sicher aufbewahren muss. Nimm ein langes, zufälliges (mindestens 20 Zeichen; das Werkzeug lehnt bei der Neuanlage weniger ab), bewahre es in einem Passwortmanager und auf Papier an einem sicheren Ort auf, nie in einem Repository, einer Mail oder einem Chat. Teile es mit mindestens einer weiteren vertrauenswürdigen Person, und halte **mindestens zwei Owner** der GitHub-Organisation.

**Der Betriebsschlüssel.** Ein Schlüsselpaar dient für alles auf dem Host. Seine private Hälfte ist `sites/<stage>/operator_key.vault` in `vb-access`; seine öffentliche Hälfte ist der Eintrag unter `operator_public_keys` in `sites/<stage>/inventory.ini`, und genau diese Liste installiert `./vb setup` auf dem Host. Um den Schlüssel zu ersetzen, zum Beispiel nachdem eine Person ausgeschieden ist, führe `./vb rotate-key production` aus: Es fügt den neuen Schlüssel hinzu, prüft, dass er einloggt, speichert ihn und entfernt erst dann den alten vom Host. Committe danach die geänderten Dateien in `vb-access`.

**Einen zweiten Betreiber hinzufügen.** Zwei Wege: Gib der Person Zugang zu `vb-access` und das Vault-Passwort (sie nutzt dann denselben Schlüssel), oder gib ihr einen eigenen Schlüssel. Beim zweiten Weg erzeugt die Person auf ihrem Arbeitsplatz ein Schlüsselpaar (`ssh-keygen -t ed25519`) und schickt nur die `.pub`-Datei; trage deren einzelne Zeile in `operator_public_keys` in `sites/production/inventory.ini` ein (die Liste ist eine JSON-Liste in einer Zeile, Schlüssel durch Kommas getrennt) und führe `./vb setup production` aus. Entferne den Eintrag und führe `./vb setup` erneut aus, um den Zugang zu entziehen.

**Wenn kein Schlüssel mehr funktioniert** (der Betriebsschlüssel ist nicht auf dem Host, oder `vb-access` oder das Vault-Passwort ist verloren): Nutze die Konsole oder das Rescue-System des Anbieters. Boote das Rescue-System, binde das Root-Dateisystem ein, trage einen neuen öffentlichen Schlüssel in `/home/service/.ssh/authorized_keys` und `/home/admin/.ssh/authorized_keys` ein, setze Besitzer und Gruppe auf die numerischen IDs dieses Benutzers (`ls -ln /home/service`) und den Modus (`700` für das Verzeichnis, `600` für die Datei), führe auf SELinux-Hosts `restorecon -R` auf beiden `.ssh`-Verzeichnissen aus, hänge aus und starte ins normale System. Das Root-Passwort des Anbieters funktioniert an dessen Konsole weiterhin. Die genauen Schritte unterscheiden sich je Anbieter und bei verschlüsselten oder LVM-Root-Dateisystemen. Trage danach den neuen Schlüssel in `operator_public_keys` ein und führe `./vb setup` aus, damit die Listen übereinstimmen.

**Wenn GitHub nicht erreichbar ist** oder die Organisation verloren ist, hilft die Offline-Kopie: Halte einen Klon von `vb-deploy` und `vb-access` (das private enthält nur vault-verschlüsselte Dateien) auf einem verschlüsselten Stick an einem sicheren Ort und erneuere ihn nach jeder Änderung in `vb-access`.

**Checkliste bei einer Übergabe**

1. Die neue Person ist Owner der GitHub-Organisation und kennt das Vault-Passwort.
2. Die neue Person hat [das Übergabeblatt](HANDOVER.de.md) durchgearbeitet und den Host mit `./vb ssh production` erreicht.
3. Jeder zweite Faktor funktioniert für die neue Person; die Wiederherstellungscodes stehen in der Zugangs-Notiz.
4. Das Rollen-Postfach jedes Accounts erreicht die neue Person.

**Checkliste, wenn eine Person ausscheidet**

1. Entferne sie aus der GitHub-Organisation, falls sie dort ein persönliches Konto hat. Kannte sie das Login des gemeinsamen Owner-Kontos, ändere dessen Passwort, erneuere den zweiten Faktor samt Wiederherstellungscodes und trage die neuen Codes in die Zugangs-Notiz ein.
2. Ersetze den Betriebsschlüssel: `./vb rotate-key production`, und committe das Ergebnis in `vb-access`. Hatte sie einen eigenen Schlüssel, lösche auch dessen Zeile aus `operator_public_keys` in `sites/production/inventory.ini` und führe `./vb setup production` aus.
3. Ändere das Vault-Passwort: `ansible-vault rekey` für jede Vault-Datei in `vb-access` (`credentials.md`, `sites/*/operator_key.vault`, `sites/*/secrets/*`; der Befehl steht im `vb-access`-README), committe und pushe die geänderten Dateien in `vb-access` und sage es den anderen persönlich oder über einen Kanal, dem du vertraust. Alte Fassungen der Dateien bleiben im Git-Verlauf mit dem alten Passwort lesbar, deshalb ist Schritt 4 nicht optional.
4. Rotiere die Secrets, die sie kannte ([Anhang A](#anhang-a-secrets-und-pflichtvariablen), "Ein Secret ändern"), beginnend mit den AWS-Schlüsseln und dem SMTP-Passwort, und dem Dozzle-Login, wenn sie ihn kannte.
5. Nimm sie aus den Accounts der Zugangs-Notiz heraus (Hosting, Domain, AWS, Google) und aktualisiere die Notiz. Erneuere die Offline-Kopie auf dem Stick, wenn sie eine hatte.

## Anhang D: Fallbeispiele

Typische Situationen, in der Reihenfolge der Schritte.

### 1. Du übernimmst das System (erster Tag)

1. Hole die drei Dinge von deiner Vorgängerin oder deinem Vorgänger: Adresse der Organisation, Owner-Login mit Wiederherstellungscodes, Vault-Passwort.
2. Folge [dem Übergabeblatt](HANDOVER.de.md): Werkzeuge installieren, `vb-deploy` und `vb-access` nebeneinander klonen.
3. Beweise den Zugang: `./vb ssh production`, dann `podman ps` (alle Dienst-Container `healthy`).
4. Lies die Zugangs-Notiz (`PAGER=cat ansible-vault view credentials.md`) und stelle sicher, dass du jeden Account öffnen kannst; prüfe die zweiten Faktoren.
5. Führe `./vb check production` aus und lies die Ausgabe einmal: So sieht ein normaler Lauf aus.
6. Überfliege Kapitel 2 (wenn etwas ausfällt) und [2.4](#24-die-datenbank-ist-beschädigt-sicherung-und-wiederherstellung); beide zählen im Notfall am meisten.

### 2. Jemand scheidet aus

Folge der Checkliste "wenn eine Person ausscheidet" in [Anhang C](#anhang-c-zugang-und-übergabe). Die Reihenfolge zählt: erst den Zugang entziehen (Organisation, Schlüssel), dann das Vault-Passwort ändern, dann rotieren, was sie kannte.

### 3. Der Server ist weg

Der Host ist verloren (Hardware, Anbieter, ein Update). Die Daten sind sicher: Die Datenbank liegt in den nächtlichen Dumps in S3, die Dateien lagen nie auf dem Host, die Konfiguration ist in Git.

1. Bestelle einen neuen x86_64-Server mit Debian 13 ([3.2](#32-betriebssystem-installieren)).
2. Ist der Produktions-IAM-User in AWS auf eine IP-Adresse beschränkt, erlaube zuerst die Adresse des neuen Hosts, sonst scheitert die Wiederherstellung ([3.1](#31-vorbereitung-bevor-der-alte-host-angefasst-wird)).
3. Kopiere die Site: `cp -r ../vb-access/sites/production ../vb-access/sites/production-new`, trage die neue IP in die neue `inventory.ini` ein.
4. `./vb setup production-new`, dann `./vb deploy production-new`.
5. Stelle den neuesten Dump wieder her, der vor dem Ausfall entstand ([3.7](#37-datenbank-auf-dem-neuen-host-wiederherstellen), den Dump nennen).
6. Lass DNS auf den neuen Host zeigen und warte auf die Zertifikate ([3.8](#38-dns-umschalten-und-zertifikate-besorgen)); prüfe ([3.9](#39-prüfung)).
7. Mache `production-new` zur Site `production` ([3.11](#311-den-alten-host-aufräumen)).

### 4. Umzug zu einem anderen Anbieter

Wie Fall 3, aber der alte Host läuft noch: Nimm das Wartungsfenster aus Kapitel 3 ([3.6](#36-wartungsfenster-den-alten-host-einfrieren-und-die-letzte-sicherung-anlegen)), damit die letzte Sicherung aktuell ist und keine Daten verloren gehen. Senke die DNS-TTL einen Tag vorher.

### 5. Das Vault-Passwort ist verloren

Ohne es lässt sich nichts in `vb-access` öffnen: nicht der Betriebsschlüssel, nicht die Secrets, nicht die Zugangs-Notiz. Das laufende System ist nicht betroffen. Wiederherstellung: Ein anderer Owner kennt das Passwort vielleicht noch (deshalb sollten es zwei Personen kennen); sonst fange neu an: Erzeuge die Secrets aus den Anbieter-Accounts neu, `./vb init production` legt (nachdem du das alte Verzeichnis `sites/production` beiseitegelegt hast) ein neues Datenverzeichnis mit neuem Schlüssel und neuem Passwort an, und die Konsole des Anbieters bringt den neuen öffentlichen Schlüssel auf den Host ([Anhang C](#anhang-c-zugang-und-übergabe), "Wenn kein Schlüssel mehr funktioniert"). Plane ein Wartungsfenster; rotiere jedes Secret.

### 6. Ein Secret ist abgeflossen

Ändere es zuerst an der Quelle (AWS-Konsole, Mail-Anbieter), dann in `vb-access` mit `ansible-vault edit`, dann `./vb deploy production` ([Anhang A](#anhang-a-secrets-und-pflichtvariablen)). Ein abgeflossener `SECRET_KEY` wird auf dieselbe Weise ersetzt; die Nutzer bleiben angemeldet. Ein abgeflossener Betriebsschlüssel oder ein abgeflossenes Vault-Passwort bedeutet: `./vb rotate-key production`, ein neues Vault-Passwort (`ansible-vault rekey` für jede Vault-Datei) und ein Blick in die Logs für die Zeit seit dem Abfluss.

### 7. Du brauchst eine Testkopie des Systems

Nimm die Stage `test` mit einem eigenen Server: [5.2](#52-eine-test--oder-qa-stage-aufbauen). Für ein kurzes Experiment mit Wegwerf-Servern nimm die `.invalid`-Domains und die Staging-CA; kopiere nie die Produktions-Secrets auf so einen Server.

## Anhang E: Häufig gestellte Fragen

**Warum gibt es zwei Repositories für den Betrieb?** `vb-deploy` ist Code und kann öffentlich sein; `vb-access` enthält, was spezifisch und sensibel ist, und ist privat. Jede und jeder kann lesen, wie das System gebaut ist, und nur Owner der Organisation können die Daten lesen.

**Warum liegt `vb` in `vb-deploy` und nicht in `vb-access`?** Es enthält keine Secrets, nur die Logik, und ist für jeden nützlich, der das System mit eigenem privaten Repository betreibt. `vb-access` speichert nur Daten.

**Kann ich meinen eigenen SSH-Schlüssel benutzen?** Für die tägliche Arbeit nimm `./vb ssh`, das den Betriebsschlüssel aus dem Vault nutzt. Ein persönlicher Schlüssel lässt sich in `operator_public_keys` eintragen ([Anhang C](#anhang-c-zugang-und-übergabe)), ist aber nicht nötig.

**Warum hat `admin` `sudo` ohne Passwort?** Niemand kann sich ohne den Betriebsschlüssel als `admin` anmelden, und Passwörter sind gesperrt; ein `sudo`-Passwort würde also nichts schützen, was der Schlüssel nicht ohnehin schützt, und wäre ein weiteres Geheimnis, das zu übergeben ist.

**Warum muss der Server x86_64 sein?** Die Anwendungs-Images werden nur für `linux/amd64` gebaut. `./vb deploy` bricht auf ARM mit einer Meldung ab.

**Brauche ich Python oder besondere Software?** Ansible (das Python 3 enthält), `git`, einen SSH-Client, `curl` und `dig` für die Prüfungen und `sshpass` für den allerersten Lauf auf einem neuen Host. `./vb` braucht nichts weiter.

**Was, wenn GitHub ausfällt?** Das laufende System hängt nicht von GitHub ab. Für Änderungen brauchst du deine Offline-Kopie der beiden Repositories ([Anhang C](#anhang-c-zugang-und-übergabe)).

**Ist es sicher, `./vb deploy` jederzeit auszuführen?** Ja. Es ändert nur, was abweicht, und startet nur die Pods neu, deren Dateien sich geändert haben. `./vb check production` zeigt vorher, was das wäre.

**Was ändert `./vb check`?** Nichts. Es führt beide Playbooks im Vorschau-Modus aus.

**Wie finde ich heraus, welche Version läuft?** `podman inspect vb-api --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'` auf dem Host liefert den Commit (mehr in [2.3](#23-fehlerhaftes-release-zurückrollen)), und `podman exec vb-api alembic current` die Datenbank-Revision.

**Wo sind die Logs?** `journalctl --user -u <dienst>`, `podman logs <container>` oder Dozzle im Browser ([2.1](#21-erste-prüfungen-nur-lesend)).

**Was passiert mit dem Zugang, wenn ich `./vb setup` mit einer anderen Schlüsselliste ausführe?** Der Host hat danach genau die Schlüssel der Liste. Das Werkzeug lehnt den Lauf ab, wenn dein eigener Schlüssel nicht darin steht.

**Wo melde ich einen Fehler in diesem Handbuch?** Behebe ihn in einem Pull Request auf `development` von `vb-deploy`; die englische und die deutsche Datei müssen dieselbe Struktur behalten (ein Test prüft das).
