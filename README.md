# vb-deploy

Central operations handbook for the Vindobona II system (`vindobona2.at`):
which repo is for what, how production is set up, and how to set it up,
deploy it, and restore it in an emergency. This repo itself holds
everything operational: Ansible playbooks, Caddy configuration, Podman
Quadlets, and (vault-encrypted) secrets.

## The 4 Repos at a Glance

| Repo | What | Tech Stack | Deployed as |
|---|---|---|---|
| [`vb-api`](../vb-api) | Backend: internal club management system (members/fees, Standesdb, archive, P4x bookkeeping, scheduler jobs, ...) | Python 3.12, FastAPI, SQLAlchemy, Alembic, PostgreSQL 18, S3-compatible storage | `vb-api` + `vb-api-pg` (one pod) |
| [`vb-intern`](../vb-intern) | Frontend to `vb-api`: the actual management UI for club members/officials (login required) | Vue 3 (`<script setup>`, TypeScript), Vite, nginx for serving | `vb-intern` |
| [`vb-www`](../vb-www) | Public, unauthenticated website `www.vindobona2.at` (marketing/info, gallery, contact form) | Vue 3, TypeScript, Vite, nginx | `vb-www` |
| `vb-deploy` (this repo) | Operations: Ansible, Caddy, Quadlets, secrets | Ansible, systemd Quadlets | doesn't run as a service itself — configures the others |

All three app repos build their own container image via CI/CD (GitHub
Actions) and push it to `ghcr.io/k-o-st-v-vindobona-ii/<repo>:latest`
automatically on every merge to `main`. `vb-deploy` builds **no** images and
clones **no** app repos onto the production host — it only distributes
config and secrets and makes sure the right images are running.

## Architecture: rootless Podman on a VPS

A single VPS (the "P-System") carries the entire production setup. Instead
of classic root Docker containers, everything runs **rootless** under a
dedicated, unprivileged Linux user named `service` — containers thus run
without root privileges on the host, so a compromised container can't
directly escalate to host root. A second user, `admin`, exists solely for
administrative root tasks (`sudo`) and never runs any containers itself.

> **Aside: why two users (`admin` + `service`) instead of one?**
> The separation is a deliberate security boundary, not an accident.
> `service` is the user that actually runs containers — exactly the user
> that would be affected first in the event of a container-escape
> vulnerability (an attacker breaking out of a compromised container onto
> the host). If `service` had `sudo` rights, a container escape would
> simultaneously be a path to full root on the host. Since `service`
> belongs to **no** privileged group and has **no** `sudo`, a breakout from
> a container is, at worst, limited to the privileges of an ordinary,
> unprivileged user — no root, no way to manipulate other
> containers/data on the host, no access to system configuration. `admin`
> exists exclusively for humans who need to do administrative tasks
> (package installation, firewall, SSH config, ...) — this user never
> touches a container, so it has no container attack surface either, even
> if compromised.

- **systemd Quadlets** instead of `docker-compose`: every container/pod is
  described as a `.container`/`.pod`/`.volume` file (INI-like syntax),
  which `systemd --user` (persistent even without an active login session,
  thanks to `loginctl enable-linger service`) automatically translates into
  a real systemd service. Advantage over `docker-compose`: native systemd
  integration (`systemctl --user status/restart/logs`, automatic restarts,
  healthchecks, boot persistence) without an extra Compose daemon.
- **One pod per app**: `vb-api-pod` contains `vb-api` (backend) +
  `vb-api-pg` (PostgreSQL) — both share a network namespace, so the
  backend reaches the DB simply via `localhost`. `vb-intern-pod` and
  `vb-www-pod` each contain a single nginx container. Pod/container names
  are identical regardless of stage (even on the dev stage) — which stage
  a container is is decided exclusively by
  `APP_ENVIRONMENT`/`VITE_APP_ENVIRONMENT` in its `EnvironmentFile`, never
  by the name itself.
- **Caddy** is the only service that listens publicly on port 80/443. It
  terminates TLS (automatic Let's Encrypt certificate) and reverse-proxies
  based on hostname to the respective app, which itself only listens on
  `127.0.0.1:<port>`:
  ```
  Internet
     │  :80 / :443
     ▼
  Caddy (host network)
     ├─ api.vindobona2.at    → 127.0.0.1:21000 → vb-api-pod
     ├─ intern.vindobona2.at → 127.0.0.1:21001 → vb-intern-pod
     │    └─ /logging/dozzle* (basic auth) → 127.0.0.1:8081 → Dozzle
     └─ www.vindobona2.at    → 127.0.0.1:21002 → vb-www-pod
  ```
  On a non-prod stage (see [Stages](#stages)), the same diagram applies
  with the respective stage domains instead of `vindobona2.at`.
- **Image source**: all app containers have `Image=ghcr.io/.../<name>:latest`
  + `AutoUpdate=registry`. Podman's own built-in `podman-auto-update.timer`
  (runs daily) checks whether the `:latest` digest in the registry has
  changed, pulls it if needed, and restarts the container — **entirely
  without Ansible**. `vb-deploy` is only needed to distribute
  config/secrets/Quadlets initially or on changes, and optionally for an
  immediate deploy (see below) if you don't want to wait for the nightly
  run.
- **Persistent data deliberately lives outside the VPS:** files (archive/
  Standesdb images, DB backups) live entirely in AWS S3 (bucket
  `vindobona2-at`), not on the VPS disk. **Sole exception:** the Postgres
  database itself lives locally under `~/data/vb/<stage>/postgres`
  (production: `~/data/vb/production/postgres`) — this is the only data
  store that is truly lost on a VPS loss/reinstall and has to be restored
  from an S3 backup (see [Disaster Recovery](#disaster-recovery--database-restore)
  below).
- **Dozzle** is a simple, read-only log viewer for all running containers
  (reads the Podman socket read-only), reachable at
  `intern.vindobona2.at/logging/dozzle` behind basic auth.
- **podman-prune.timer** cleans up unused images/containers weekly, so the
  VPS's limited disk space doesn't fill up.

## Complete Cutover / VPS Reinstall (Step-by-Step Runbook)

This procedure applies both to the very first setup of a new VPS and to
any later case where the production VPS has to be completely reinstalled
(hardware change, OS upgrade via reinstall, emergency). It has been played
through 1:1 like this against a throwaway test VPS **and** against the
real production system — every step here has proven itself in practice.
The runbook is implicitly production; for a new test/QA stage, see
[Stages](#stages).

**0. Preparation, before the old host is shut down (if possible):**
- Announce a maintenance window.
- **Trigger a manual backup** to minimize the data-loss gap to the last
  automatic backup: `podman exec vb-api python scripts/backup_db.py` on
  the still-running old host (or the "Create backup now" button in
  `vb-intern` under System → Scheduler). The daily `db_backup` job
  otherwise only runs once per night — without this step, all changes
  since the last nightly run are lost.
- If the hosting provider offers a VM snapshot/image backup before the
  reinstall: use it as an additional safety net (not automated in this
  repo/runbook).

**1. Install a new operating system.** Debian (current stable version),
UEFI instead of BIOS/legacy (modern standard, no downsides on common
cloud/VPS providers).

**2. Establish root access.**
- If an SSH public key was already deposited during the reinstall: direct
  key login, no password detour needed.
- If only an initial root password was assigned: use `ansible-playbook
  ... --ask-pass` (see Phase 1 below) — asks interactively for the SSH
  password. If the first login additionally requires a forced password
  change (no TTY available over a plain SSH connection): log in once
  normally and interactively via `ssh root@<host>` and set the new
  password, only then continue with Ansible.
- **After a reinstall, SSH reports "REMOTE HOST IDENTIFICATION HAS
  CHANGED"** (new host key) — this is expected, not a security incident.
  Remove the old entry: `ssh-keygen -f ~/.ssh/known_hosts -R <hostname>`
  (for every hostname/IP the host was known under).

**3. Run `playbooks/setup_vps.yml`** (see [Phase 1](#phase-1--vps-base-configuration-only-needed-for-a-fresh-setup)
below for the exact commands). Hardens the host, creates `admin`/`service`,
configures the firewall + rootless Podman, ends with a reboot.

**4. Run `playbooks/deploy.yml`** (see [Phase 2](#phase-2--day-2-operations)
below). Distributes secrets/Caddyfile/Quadlets and starts the full stack.
**Postgres starts with an empty, fresh database** — that's normal and
expected, see the next step.

**5. Database restore — mandatory on a completely new host!** See
[Disaster Recovery](#disaster-recovery--database-restore) below for the
exact commands. Without this step, the stack technically runs, but with an
empty database (no members, no data).

**6. Verification:**
- `curl -I https://api.vindobona2.at/`, `https://intern.vindobona2.at/`,
  `https://www.vindobona2.at/` — all `200`, a valid (real) Let's Encrypt
  certificate.
- `systemctl --user list-units 'caddy*' 'logging*' 'vb-*'` and `podman ps`
  on the target system — all services `active`/`healthy`, none `failed`.
- A database spot-check to confirm the restore actually brought real (not
  empty) data, e.g.:
  `podman exec vb-api-pg psql -U vb -d vb -c 'SELECT count(*) FROM members;'`
- `ufw status verbose` (as `admin`, with `sudo`) — only 22/80/443 open.
- `systemctl --user list-timers --all` — `podman-auto-update.timer` and
  `podman-prune.timer` active.

**7. Close the maintenance window** once all items from step 6 are green.

## Prerequisites

- Ansible installed locally.
- SSH access to the host — **which user is needed depends on the
  playbook/point in time** (see the [aside above](#architecture-rootless-podman-on-a-vps)
  on the role separation):
  - **`root`** — only for the very first run of `setup_vps.yml` on a fresh
    host (`-u root`, optionally with `--ask-pass`, see runbook). Root login
    is disabled by `setup_vps.yml` itself at the end — after that, `root`
    no longer works.
  - **`admin`** — for every further run of `setup_vps.yml` (re-run/
    verification), since it needs `sudo`/`become` (`-u admin
    --ask-become-pass`). **Not** needed for `deploy.yml`.
  - **`service`** — for `deploy.yml` (hardcoded in the playbook as
    `remote_user: service`, no `sudo` needed since every step runs purely
    within the user's own scope: files in its own home, `systemctl --user`).
  Ideally passwordless key login for `admin`/`service` (set up
  automatically by `setup_vps.yml` on the first run), otherwise use
  `--ask-pass` (SSH login password) or `--ask-become-pass` (`sudo`
  password for `admin`).
- A vault password for `secrets/<stage>/*` (asked interactively, no
  `vault_password_file` deposited in the repo).
- **DNS**: all three domains of the respective stage (e.g.
  `api.vindobona2.at`, `intern.vindobona2.at`, `www.vindobona2.at` for
  production) must already point to the target VPS via A/AAAA record
  **before** the first `deploy.yml` run. Caddy obtains its TLS certificate
  automatically via the Let's Encrypt ACME HTTP-01 challenge over port 80
  (no manually deposited certificate, no `tls` block in the Caddyfile) —
  if a domain doesn't point correctly at that point, the certificate
  request fails. That means a complete outage for all three apps (Caddy
  needs the certificate to terminate HTTPS at all) and the risk of hitting
  Let's Encrypt's rate limits on repeated failed attempts.

## Stages

| Stage | Inventory file | Status |
|---|---|---|
| Production | `inventory/production.ini` | Live, `www.vindobona2.at` |
| Test | `inventory/test.ini` | Skeleton, no dedicated VPS yet |
| QA | `inventory/qa.ini` | Skeleton, no dedicated VPS yet |

`development` never runs through this repo — see
[Local Development Environment](#local-development-environment) below for
that. `playbooks/deploy.yml` refuses any other value via an `assert` task.

### Setting Up a New Stage (test/qa)

1. Fill in `inventory/<stage>.ini`: enter the real hostname and the three
   real domains instead of the `CHANGEME.example.invalid` placeholders.
2. `secrets/<stage>/` already exists as a skeleton with independently,
   freshly generated required values (`SECRET_KEY`, Postgres password,
   Caddy basic-auth hash) — SMTP, S3, and `GOOGLE_CLIENT_ID` values are
   deliberately empty, since no real mail server/S3 storage exists yet for
   this stage. Fill them in before production use (see
   [Maintaining Secrets](#maintaining-secrets)).
3. Set DNS for the three domains of this stage (see
   [Prerequisites](#prerequisites) above).
4. `playbooks/setup_vps.yml`, then `playbooks/deploy.yml`, each with
   `-i inventory/<stage>.ini` (see [Phase 1](#phase-1--vps-base-configuration-only-needed-for-a-fresh-setup)/[Phase 2](#phase-2--day-2-operations)).

## Phase 1 — VPS Base Configuration (only needed for a fresh setup)

```bash
ansible-playbook -i inventory/production.ini playbooks/setup_vps.yml -u root
# If only password login is possible:
ansible-playbook -i inventory/production.ini playbooks/setup_vps.yml -u root --ask-pass

# Re-run / verification (later, root login is disabled by then):
ansible-playbook -i inventory/production.ini playbooks/setup_vps.yml -u admin --ask-become-pass --check --diff
```

(For a new test/QA stage, use `-i inventory/test.ini` or `-i inventory/qa.ini`
instead of `production.ini`.) Runs through in this order:

- Creates the users `admin` (sudo) and `service` (rootless Podman),
  distributes SSH keys.
- Enables linger for `service` (containers survive logout) as well as the
  kernel tunings needed for rootless Podman.
- Makes the journald log persistent.
- **Reboot**, to finalize kernel/Podman/systemd changes.
- Opens the firewall exclusively for SSH/HTTP/HTTPS (22/80/443).
- Hardens SSH at the very end: password login disabled for `admin`, `root`
  login disabled entirely.

**The generated `admin` password is shown only once** — save it to the
password safe immediately.

## Maintaining Secrets

```bash
ansible-vault edit secrets/production/vb-api.env.j2
ansible-vault view secrets/production/vb-api.env.j2
```

Applies analogously to every file under `secrets/<stage>/`, whether `.env`
or `.env.j2`. For `.env.j2` files (domain-/stage-dependent content, see
[Stages](#stages)), the deploy first runs transparent vault decryption,
then Jinja2 templating of the domain/stage variables —
`ansible.builtin.template` automatically decrypts a vault file as it reads
it, so there's no ordering collision between vault and templating.

## Phase 2 — Day-2 Operations

```bash
ansible-playbook -i inventory/<stage>.ini playbooks/deploy.yml --ask-vault-pass --check --diff   # dry-run first
ansible-playbook -i inventory/<stage>.ini playbooks/deploy.yml --ask-vault-pass                  # then apply
```

Syncs secrets, the Caddyfile, and Quadlets to the host and (re)starts the
affected services. Builds no images, clones no repos, restores no database
(see [Disaster Recovery](#disaster-recovery--database-restore)).

### Immediate Deploy (instead of waiting for the nightly auto-update run)

```bash
ansible-playbook -i inventory/<stage>.ini playbooks/deploy.yml --ask-vault-pass --tags deploy-api
ansible-playbook -i inventory/<stage>.ini playbooks/deploy.yml --ask-vault-pass --tags deploy-intern
ansible-playbook -i inventory/<stage>.ini playbooks/deploy.yml --ask-vault-pass --tags deploy-www
```

Pulls the current `:latest` image of the respective service from ghcr.io
and restarts the associated **pod** (not just the individual container —
the container is bound to its pod via a generated `BindsTo=`, so a plain
container restart would take the pod down with it without it pulling
itself back up on its own). Only runs when the tag is explicitly given.

## Operational Scripts in `vb-api`

Besides the Ansible playbooks here, there's a set of operational scripts
under `vb-api/scripts/` that are run **on the P-System, inside the running
`vb-api` container** (`podman exec vb-api python scripts/<name>.py` — not
locally on your own machine!). Full docs including all parameters:
[`vb-api/scripts/README.md`](../vb-api/scripts/README.md). The ones
relevant for everyday use:

| Script | What for |
|---|---|
| `backup_db.py` | Manually trigger a PostgreSQL backup to S3 (the same operation as the daily `db_backup` scheduler job) — e.g. before risky changes or before a planned cutover (see runbook above). |
| `restore_db.py` | Restore PostgreSQL from an S3 backup. Refuses on `APP_ENVIRONMENT=production` without `--force` — that's the way to a real disaster recovery on prod, see below. |
| `check_s3_integrity.py` | Read-only DB ↔ S3 consistency check (missing objects + orphans), never deletes anything itself. |
| `downsync_prod.py` | Only relevant on a **non-prod stage**: pulls prod S3 and, based on that, the local DB to the current prod state. Refuses hard on `APP_ENVIRONMENT=production`. |

## Disaster Recovery / Database Restore

Does **not** run through this repo — `deploy.yml` only starts Postgres, it
never fills it with data. File storage (archive/Standesdb images) needs no
restore, since it lives entirely in S3 anyway (versioned, bucket
`vindobona2-at`). Restoring the Postgres database is the **only**
remaining recovery step needed and runs entirely through a script from
`vb-api` — **run this on the target system itself, over SSH to the
P-System**, not locally:

```bash
# Log in on the P-System (e.g. via SSH):
ssh service@<hostname-or-ip-of-the-p-system>

# 1. View available backups (optional, for verification only):
podman exec vb-api python scripts/restore_db.py --list

# 2. Run the restore (auto-selects the newest one without --backup-name):
podman exec -it vb-api python scripts/restore_db.py --force
```

`--force` is mandatory because `restore_db.py` refuses a restore on
`APP_ENVIRONMENT=production` by default (protection against accidentally
overwriting the live database) — on prod, this isn't a one-time skip but
intentional every time. Without `--backup-name <name>`, the chronologically
newest dump in the S3 bucket is used automatically.

**When needed:**
- After a complete VPS reinstall (see runbook above, step 5) — here
  **mandatory**, since the local Postgres disk is lost on reinstall and
  `deploy.yml` only brings up an empty DB.
- On any other incident where the production database is damaged or has
  to be reset to an earlier state.

**Always verify afterward** that real data actually made it (not just that
the command ran without error) — e.g. via a spot-check like in step 6 of
the runbook above.

### Fresh Postgres Data Directory

Two pitfalls that actually occurred during the PostgreSQL 18 upgrade —
relevant for any new stage with a fresh VPS:

1. **Mount convention:** the volume deliberately mounts one level above the
   actual PG18 data directory
   (`Volume=%h/data/vb/<stage>/postgres:/var/lib/postgresql:Z`, not
   directly onto a versioned subfolder) — Postgres creates its own
   versioned subdirectory inside it itself.
2. **Rootless ownership before the first start:** a fresh directory
   created by Ansible via `ansible.builtin.file` initially belongs to the
   `service` user in the "normal" UID namespace. The rootless Podman
   container, however, sees its own `postgres` user (UID 999) in a mapped
   user namespace — without an ownership change, `initdb` fails with a
   permission error on the very first container start:

```bash
podman unshare chown -R 999:999 ~/data/vb/<stage>/postgres
chmod 700 ~/data/vb/<stage>/postgres
```

Needed once before the very first start on a new/empty data directory —
not on every regular deploy.

**Applies generally, not just to `chown`:** once the data directory has
been initialized by Postgres, it belongs to the mapped `postgres` UID (not
`service`) — every further host-side filesystem operation on it (`mv`,
`rm`, `cp`, ...) also needs `podman unshare`, otherwise it fails with
"permission denied", even if the parent directory belongs to `service` and
is writable (moving/renaming a directory requires updating its own `..`
entry, which requires write permission on the directory itself — not just
on the parent directories).

## Local Development Environment

Runs completely independently of Ansible/Vault, directly on the dev host,
via its own Podman Quadlets. The generalized templates live under `dev/`:

```
dev/quadlets/api/      vb-api + vb-api-pg + pod
dev/quadlets/intern/   vb-intern
dev/quadlets/www/      vb-www
dev/quadlets/minio/    vb-minio (S3 replacement for AWS S3 in dev)
dev/env/               *.env.example for all five containers
```

### Setting Up

1. Copy all `*.example` files from `dev/quadlets/` 1:1 to
   `~/.config/containers/systemd/vb/<component>/` (dropping the `.example`
   extension), and all `*.example` files from `dev/env/` to `~/.env/`.
2. Replace the placeholders in the copied Quadlets:
   `<path-to-vb-fastapi-vue>` (path to this 4-repo checkout),
   `<your-mail-dev-domain>`/`<your-minio-dev-domain>` (see Caddy routing
   below), `<path-to-local-minio-data-dir>`.
3. Replace every `change-me` placeholder in the copied env files with real
   local values.

### Local Caddy Routing

No separate Caddy dev container needed — the three frontend/backend ports
(`20000`–`20002`) as well as MinIO (`9000`/`9001`) bind directly to
`127.0.0.1`. For a real domain name instead of `localhost:<port>` (e.g. to
test cookies/CORS like in production), set up your own local DNS
resolution + your own local reverse proxy:

```
<your-dev-domain>
   │
   ▼
local reverse proxy (your choice, e.g. Caddy)
   ├─ api.<your-dev-domain>    → 127.0.0.1:20000 → vb-api-pod
   ├─ intern.<your-dev-domain> → 127.0.0.1:20001 → vb-intern-pod
   └─ minio.<your-dev-domain>  → 127.0.0.1:9000  → vb-minio-pod
```

Not part of this repo — the `AddHost=` lines in
`vb-api.pod.example`/`vb-minio.pod.example` merely expect
`<your-mail-dev-domain>`/`<your-minio-dev-domain>` to be resolvable
somehow (a simple local DNS/hosts entry is enough; a reverse proxy is only
needed for domain-based browser testing).

### First Start

```bash
# Build the vb-api:dev image - there's no dedicated .build Quadlet for
# this, that's currently a deliberately manual step:
podman build --target dev -t vb-api:dev <path-to-vb-fastapi-vue>/vb-api

systemctl --user daemon-reload
systemctl --user start vb-minio-pod vb-api-pod vb-intern-pod vb-www-pod

# Create the database schema:
podman exec vb-api alembic upgrade head
```

**Seed data:** no separate seed script needed — `podman exec vb-api python
scripts/downsync_prod.py` pulls real production data (unchanged, no
anonymization) from AWS S3 into the local MinIO instance and restores the
local DB from it. Needs `~/.env/vb-api-aws-prod.env` (see
[Scripts](../vb-api/README.md#scripts) in `vb-api`).

---

# Deutsch

Zentrales Betriebshandbuch für das Vindobona-II-System (`vindobona2.at`): welches
Repo wofür da ist, wie die Produktion aufgebaut ist, und wie man sie aufsetzt,
deployt und im Ernstfall wiederherstellt. Dieses Repo selbst enthält alles
Betriebliche: Ansible-Playbooks, Caddy-Konfiguration, Podman-Quadlets und
(vault-verschlüsselte) Secrets.

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
  Auf einer Non-Prod-Stage (siehe [Stages](#stages-1)) gilt dasselbe Diagramm
  mit den jeweiligen Stage-Domains statt `vindobona2.at`.
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
  Postgres-Datenbank selbst liegt lokal unter `~/data/vb/<stage>/postgres`
  (Production: `~/data/vb/production/postgres`) — das ist der einzige
  Datenbestand, der bei einem VPS-Verlust/Neuaufsetzen wirklich weg ist und
  aus einem S3-Backup zurückgeholt werden muss (siehe
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
durchgespielt — jeder Schritt hier hat sich in der Praxis bewährt. Das Runbook
ist implizit Production; für eine neue Test-/QA-Stage siehe [Stages](#stages-1).

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

**3. `playbooks/setup_vps.yml` ausführen** (siehe [Phase 1](#phase-1--vps-grundkonfiguration-nur-bei-neuaufsetzung-nötig)
unten für die genauen Befehle). Härtet den Host, legt `admin`/`service` an,
konfiguriert Firewall + rootless Podman, endet mit einem Reboot.

**4. `playbooks/deploy.yml` ausführen** (siehe [Phase 2](#phase-2--tag-2-betrieb)
unten). Verteilt Secrets/Caddyfile/Quadlets und startet den kompletten Stack.
**Postgres startet dabei mit einer leeren, frischen Datenbank** — das ist
normal und erwartet, siehe nächster Schritt.

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

## Voraussetzungen

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
- Ein Vault-Passwort für `secrets/<stage>/*` (wird interaktiv abgefragt, kein
  `vault_password_file` im Repo hinterlegt).
- **DNS**: Alle drei Domains der jeweiligen Stage (z. B. `api.vindobona2.at`,
  `intern.vindobona2.at`, `www.vindobona2.at` für Production) müssen bereits
  **vor** dem ersten `deploy.yml`-Lauf per A-/AAAA-Record auf den Ziel-VPS
  zeigen. Caddy bezieht sein TLS-Zertifikat automatisch per Let's-Encrypt
  ACME-HTTP-01-Challenge über Port 80 (kein manuell hinterlegtes Zertifikat,
  kein `tls`-Block in der Caddyfile) — zeigt eine Domain zu diesem Zeitpunkt
  noch nicht korrekt, schlägt der Zertifikatsbezug fehl. Das bedeutet einen
  Komplettausfall für alle drei Apps (Caddy braucht das Zertifikat, um
  überhaupt HTTPS zu terminieren) und das Risiko, bei wiederholten
  Fehlversuchen Let's-Encrypts Rate-Limits zu treffen.

## Stages

| Stage | Inventory-Datei | Status |
|---|---|---|
| Production | `inventory/production.ini` | Live, `www.vindobona2.at` |
| Test | `inventory/test.ini` | Skeleton, noch kein eigener VPS |
| QA | `inventory/qa.ini` | Skeleton, noch kein eigener VPS |

`development` läuft nie über dieses Repo — dafür siehe
[Lokale Entwicklungsumgebung](#lokale-entwicklungsumgebung) unten.
`playbooks/deploy.yml` verweigert per `assert`-Task jeden anderen Wert.

### Neue Stage einrichten (test/qa)

1. `inventory/<stage>.ini` befüllen: echten Hostnamen und die drei echten
   Domains eintragen statt der `CHANGEME.example.invalid`-Platzhalter.
2. `secrets/<stage>/` existiert bereits als Skeleton mit unabhängig frisch
   generierten Pflichtwerten (`SECRET_KEY`, Postgres-Passwort,
   Caddy-Basic-Auth-Hash) — SMTP-, S3- und `GOOGLE_CLIENT_ID`-Werte sind
   bewusst leer, da für diese Stage noch kein echter Mailserver/S3-Storage
   existiert. Vor dem produktiven Einsatz ergänzen (siehe
   [Secrets pflegen](#secrets-pflegen)).
3. DNS für die drei Domains dieser Stage setzen (siehe
   [Voraussetzungen](#voraussetzungen) oben).
4. `playbooks/setup_vps.yml`, dann `playbooks/deploy.yml`, jeweils mit
   `-i inventory/<stage>.ini` (siehe [Phase 1](#phase-1--vps-grundkonfiguration-nur-bei-neuaufsetzung-nötig)/[Phase 2](#phase-2--tag-2-betrieb)).

## Phase 1 — VPS-Grundkonfiguration (nur bei Neuaufsetzung nötig)

```bash
ansible-playbook -i inventory/production.ini playbooks/setup_vps.yml -u root
# Falls nur Passwort-Login moeglich ist:
ansible-playbook -i inventory/production.ini playbooks/setup_vps.yml -u root --ask-pass

# Re-Run / Verifikation (spaeter, root-Login ist danach deaktiviert):
ansible-playbook -i inventory/production.ini playbooks/setup_vps.yml -u admin --ask-become-pass --check --diff
```

(Für eine neue Test-/QA-Stage `-i inventory/test.ini` bzw. `-i inventory/qa.ini`
statt `production.ini`.) Läuft in dieser Reihenfolge durch:

- Legt die User `admin` (sudo) und `service` (rootless Podman) an, verteilt
  SSH-Keys.
- Aktiviert Linger für `service` (Container überleben Logout) sowie die für
  rootless Podman nötigen Kernel-Tunings.
- Macht das journald-Log persistent.
- **Reboot**, um Kernel-/Podman-/systemd-Änderungen zu finalisieren.
- Öffnet die Firewall ausschließlich für SSH/HTTP/HTTPS (22/80/443).
- Härtet SSH ganz am Ende: Passwort-Login für `admin` deaktiviert,
  `root`-Login komplett deaktiviert.

**Das generierte `admin`-Passwort wird nur einmal angezeigt** — sofort im
Passwort-Safe sichern.

## Secrets pflegen

```bash
ansible-vault edit secrets/production/vb-api.env.j2
ansible-vault view secrets/production/vb-api.env.j2
```

Gilt analog für jede Datei unter `secrets/<stage>/`, egal ob `.env` oder
`.env.j2`. Bei den `.env.j2`-Dateien (domain-/stage-abhängiger Inhalt, siehe
[Stages](#stages-1)) läuft beim Deploy zuerst die transparente
Vault-Entschlüsselung, danach das Jinja2-Templating der
Domain-/Stage-Variablen — `ansible.builtin.template` entschlüsselt eine
Vault-Datei beim Lesen automatisch mit, es gibt also keine
Reihenfolge-Kollision zwischen Vault und Templating.

## Phase 2 — Tag-2-Betrieb

```bash
ansible-playbook -i inventory/<stage>.ini playbooks/deploy.yml --ask-vault-pass --check --diff   # erst pruefen
ansible-playbook -i inventory/<stage>.ini playbooks/deploy.yml --ask-vault-pass                  # dann anwenden
```

Synct Secrets, Caddyfile und Quadlets auf den Host und (neu-)startet die
betroffenen Dienste. Baut keine Images, klont keine Repos, restored keine
Datenbank (siehe [Disaster Recovery](#disaster-recovery--datenbank-restore)).

### Sofort-Deploy (statt auf den nächtlichen Auto-Update-Lauf zu warten)

```bash
ansible-playbook -i inventory/<stage>.ini playbooks/deploy.yml --ask-vault-pass --tags deploy-api
ansible-playbook -i inventory/<stage>.ini playbooks/deploy.yml --ask-vault-pass --tags deploy-intern
ansible-playbook -i inventory/<stage>.ini playbooks/deploy.yml --ask-vault-pass --tags deploy-www
```

Pullt das aktuelle `:latest`-Image des jeweiligen Services aus ghcr.io und
startet den zugehörigen **Pod** neu (nicht nur den einzelnen Container — der
Container ist per generiertem `BindsTo=` an seinen Pod gebunden, ein reiner
Container-Neustart würde den Pod mitreißen, ohne dass er sich von selbst
wieder hochzieht). Läuft nur, wenn der Tag explizit angegeben wird.

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
| `downsync_prod.py` | Nur auf einer **Non-Prod-Stage** relevant: zieht Prod-S3 + darauf aufbauend die lokale DB auf den aktuellen Prod-Stand. Verweigert sich hart auf `APP_ENVIRONMENT=production`. |

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

### Frisches Postgres-Datenverzeichnis

Zwei Fallstricke, die beim PostgreSQL-18-Upgrade tatsächlich aufgetreten
sind — relevant für jede neue Stage mit einem frischen VPS:

1. **Mount-Konvention:** Das Volume mountet bewusst eine Ebene höher als das
   eigentliche PG18-Datenverzeichnis
   (`Volume=%h/data/vb/<stage>/postgres:/var/lib/postgresql:Z`, nicht direkt
   auf einen versionierten Unterordner) — Postgres legt sein eigenes,
   versioniertes Unterverzeichnis darin selbst an.
2. **Rootless-Ownership vor dem ersten Start:** Ein frisches, von Ansible per
   `ansible.builtin.file` angelegtes Verzeichnis gehört zunächst dem
   `service`-User im "normalen" UID-Namespace. Der rootless-Podman-Container
   sieht seinen eigenen `postgres`-User (UID 999) aber in einem gemappten
   User-Namespace — ohne einen Besitzerwechsel schlägt `initdb` beim
   allerersten Containerstart mit einem Permission-Error fehl:

```bash
podman unshare chown -R 999:999 ~/data/vb/<stage>/postgres
chmod 700 ~/data/vb/<stage>/postgres
```

Nötig einmalig vor dem allerersten Start auf einem neuen/leeren
Datenverzeichnis — nicht bei jedem regulären Deploy.

**Gilt generell, nicht nur für `chown`:** Sobald das Datenverzeichnis
einmal von Postgres initialisiert wurde, gehört es der gemappten
`postgres`-UID (nicht `service`) — jede weitere Host-seitige
Dateisystem-Operation darauf (`mv`, `rm`, `cp`, ...) braucht ebenfalls
`podman unshare`, sonst scheitert sie mit "Keine Berechtigung", selbst
wenn das Elternverzeichnis `service` gehört und beschreibbar ist (beim
Verschieben/Umbenennen eines Verzeichnisses muss dessen eigener
`..`-Eintrag aktualisiert werden, was Schreibrecht auf das Verzeichnis
selbst voraussetzt — nicht nur auf die Elternverzeichnisse).

## Lokale Entwicklungsumgebung

Läuft komplett unabhängig von Ansible/Vault, direkt auf dem Dev-Host, über
eigene Podman-Quadlets. Die generalisierten Vorlagen liegen unter `dev/`:

```
dev/quadlets/api/      vb-api + vb-api-pg + Pod
dev/quadlets/intern/   vb-intern
dev/quadlets/www/      vb-www
dev/quadlets/minio/    vb-minio (S3-Ersatz fuer AWS S3 in Dev)
dev/env/               *.env.example fuer alle fuenf Container
```

### Einrichten

1. Alle `*.example`-Dateien aus `dev/quadlets/` 1:1 nach
   `~/.config/containers/systemd/vb/<component>/` kopieren (Endung
   `.example` dabei weglassen), alle `*.example`-Dateien aus `dev/env/` nach
   `~/.env/`.
2. In den kopierten Quadlets die Platzhalter ersetzen:
   `<path-to-vb-fastapi-vue>` (Pfad zu diesem 4-Repo-Checkout),
   `<your-mail-dev-domain>`/`<your-minio-dev-domain>` (siehe
   Caddy-Routing unten), `<path-to-local-minio-data-dir>`.
3. In den kopierten Env-Dateien alle `change-me`-Platzhalter durch echte
   lokale Werte ersetzen.

### Caddy-Routing lokal

Kein separater Caddy-Dev-Container nötig — die drei Frontend-/Backend-Ports
(`20000`–`20002`) sowie MinIO (`9000`/`9001`) binden direkt an `127.0.0.1`.
Für einen echten Domainnamen statt `localhost:<port>` (z. B. um
Cookies/CORS wie in Production zu testen) eigene lokale DNS-Auflösung +
einen eigenen lokalen Reverse-Proxy einrichten:

```
<your-dev-domain>
   │
   ▼
lokaler Reverse-Proxy (eigene Wahl, z.B. Caddy)
   ├─ api.<your-dev-domain>    → 127.0.0.1:20000 → vb-api-pod
   ├─ intern.<your-dev-domain> → 127.0.0.1:20001 → vb-intern-pod
   └─ minio.<your-dev-domain>  → 127.0.0.1:9000  → vb-minio-pod
```

Nicht Teil dieses Repos — die `AddHost=`-Zeilen in
`vb-api.pod.example`/`vb-minio.pod.example` erwarten lediglich, dass
`<your-mail-dev-domain>`/`<your-minio-dev-domain>` irgendwie auflösbar sind
(ein einfacher lokaler DNS-/Hosts-Eintrag reicht; ein Reverse-Proxy ist nur
für domainbasiertes Browser-Testen nötig).

### Erststart

```bash
# vb-api:dev-Image bauen - kein eigenes .build-Quadlet vorhanden, das ist
# aktuell ein bewusst manueller Schritt:
podman build --target dev -t vb-api:dev <path-to-vb-fastapi-vue>/vb-api

systemctl --user daemon-reload
systemctl --user start vb-minio-pod vb-api-pod vb-intern-pod vb-www-pod

# Datenbankschema anlegen:
podman exec vb-api alembic upgrade head
```

**Seed-Daten:** kein separates Seed-Script nötig — `podman exec vb-api python
scripts/downsync_prod.py` zieht echte Produktionsdaten (unverändert, keine
Anonymisierung) von AWS S3 in die lokale MinIO-Instanz und restored die
lokale DB daraus. Braucht `~/.env/vb-api-aws-prod.env` (siehe
[Skripte](../vb-api/README.md#skripte) in `vb-api`).
