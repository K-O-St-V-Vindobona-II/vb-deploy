# vb-deploy

[English](README.md) | [Deutsch](README.de.md)

The leading handbook for the Vindobona II system (`vindobona2.at`): how it is built, what to do when it breaks, how to rebuild or move it, how a successor takes it over, and which tools exist. This repository holds everything needed to *operate* the system: the Ansible playbooks, the Caddy configuration, the Podman Quadlets, the templates for a new installation and the operator tool `vb`. It holds **no secrets and no data of the installation**: the inventory, the secrets, the operator key and the access note live in the private repository `vb-access` ([Appendix C](#appendix-c-access-and-handover)). The READMEs of the other repositories assume that you know this document.

**Taking over the system?** Start with the one-page [handover sheet](HANDOVER.md).

## Where do I start?

| I want to... | Read |
|---|---|
| take over the system as a successor | the [handover sheet](HANDOVER.md), then [Appendix C](#appendix-c-access-and-handover) |
| understand how the system is built | [1. Architecture overview](#1-architecture-overview) |
| fix something that is down or broken | [2. When something is down](#2-when-something-is-down), start with the [triage table](#20-triage-table) |
| install on a new server or move to another one | [3. New build or migration to a new server](#3-new-build-or-migration-to-a-new-server) |
| find a script, command or tool | [4. Scripts and CLI tools](#4-scripts-and-cli-tools) |
| set up a test/qa stage or the local development environment | [5. Upstream stages and local development](#5-upstream-stages-and-local-development) |
| see a typical situation worked through step by step | [Appendix D: Case studies](#appendix-d-case-studies) |
| get a quick answer | [Appendix E: Frequently asked questions](#appendix-e-frequently-asked-questions) |

**Where does a command run?** Every command block in this document starts with a marker:

| Marker | Meaning |
|---|---|
| `# [workstation]` | Your own computer, in a checkout of this repository next to a checkout of `vb-access`. The tool `./vb` and Ansible run here. |
| `# [host]` | The target system, after `./vb ssh production` (any of the three domains reaches the production host). |
| `# [container]` | Inside a container. In practice you run it from the host with `podman exec vb-api ...`. |
| `# [old host]`, `# [new host]` | Only in chapter 3: the host that is being replaced and its replacement. |

A **stage** is one complete copy of the system (production, test, qa, development). Some terms are club vocabulary: the **production host** is called "P-System" (Produktionssystem) by the club, **Standesdatenbank** is the member database inside the management system, **AH-Kassen** (module `p4x`) are the bookkeeping accounts of the alumni association.

## Contents

- [1. Architecture overview](#1-architecture-overview)
- [2. When something is down](#2-when-something-is-down)
- [3. New build or migration to a new server](#3-new-build-or-migration-to-a-new-server)
- [4. Scripts and CLI tools](#4-scripts-and-cli-tools)
- [5. Upstream stages and local development](#5-upstream-stages-and-local-development)
- [Appendix A: Secrets and required variables](#appendix-a-secrets-and-required-variables)
- [Appendix B: Pitfalls](#appendix-b-pitfalls)
- [Appendix C: Access and handover](#appendix-c-access-and-handover)
- [Appendix D: Case studies](#appendix-d-case-studies)
- [Appendix E: Frequently asked questions](#appendix-e-frequently-asked-questions)

---

## 1. Architecture overview

### 1.1 The big picture

```
                    Internet (80/443)
                           |
                  +--------v--------+
                  |      Caddy      |   TLS (Let's Encrypt), security headers
                  +--+-----+-----+--+
        api.<domain> |     |     | www.<domain>
        127.0.0.1:21000    |     127.0.0.1:21002
                           | intern.<domain> 127.0.0.1:21001
                           +-- /logging/dozzle* (basic auth) -> 127.0.0.1:8081 (Dozzle)

  pod vb-api-pod            pod vb-intern-pod       pod vb-www-pod
  +------------------+      +--------------+        +------------+
  | vb-api           |--+   | vb-intern    |        | vb-www     |
  | vb-api-worker    |--+   +--------------+        +------------+
  | vb-api-pg        |  |
  | vb-api-valkey    |  +--> AWS S3 (files, database backups)
  +------------------+
```

Everything runs on one host; the containers are grouped in **pods** (groups of containers that share one network, so they reach each other over `localhost`). The browser talks to `vb-intern` and `vb-www` (static pages served by nginx) and to `vb-api` (the JSON backend); Caddy is the only component that listens publicly. All files (archive, member images, public gallery) and all database backups live in an S3 bucket, not on the host.

### 1.2 Technologies and why

| Technology | Role | Why |
|---|---|---|
| One production host (VPS) | Runs the whole system | A deliberate choice for a system of this size: the leanest thing to operate, no cluster or multi-node complexity, low cost, manageable maintenance. Currently a single VPS with Debian 13; the setup is written for a generic target system, not for one provider. It must be an **x86_64** server: the application images are built for `linux/amd64` only. |
| Rootless Podman | Container runtime | Containers run without root privileges under an unprivileged user (`service`), so a container escape does not give root on the host. This is what makes the single-host approach safe enough. |
| systemd Quadlets | Describe containers and pods as `.container`/`.pod` files | systemd turns them into real services (`systemctl --user ...`): automatic restarts, health checks, start at boot, no Compose daemon. |
| Ansible and Ansible Vault | Provision the host, distribute configuration and secrets | Repeatable setup; secrets live encrypted in the private repository `vb-access`. |
| The tool `vb` | Unlocks the operator key and runs the playbooks | One command per task (`./vb setup`, `./vb deploy`, ...) instead of a long list of Ansible options; the key exists unencrypted only in memory, only while a command runs. |
| Caddy | Reverse proxy and TLS | Obtains and renews Let's Encrypt certificates on its own. |
| GitHub Actions, GHCR, `podman auto-update` | Build, store and roll out images | Every merge to `main` builds an image; the host pulls changed images by itself every night. |
| PostgreSQL 18 | Database | Runs inside the API pod. |
| Valkey 8 and ARQ | Job queue and scheduler | The `vb-api-worker` container runs scheduled and background jobs. |
| AWS S3 (production), Garage (other stages) | File storage | Files do not depend on the host. Garage is an S3 compatible stand-in for the other stages. |
| Dozzle | Log viewer | Reads container logs in the browser. |

**Operating systems.** `setup_vps.yml` and `deploy.yml` were run end to end with the current access concept (October 2026) on **Debian 13**, **Ubuntu 26.04**, **Rocky Linux 10** (also with SELinux enforcing) and **openSUSE Leap 16.0**. Fedora 44 ran through in August 2026 with the previous access mechanism; the tasks that changed since are the same on every distribution, but treat it as "expected to work, preview with `./vb check` first". Podman 5 or newer is required; the playbook stops on older releases (Debian 12, Ubuntu 24.04, RHEL 9, openSUSE Leap 15).

### 1.3 The GitHub organization and the repositories

Four repositories are public and one is private; all live in the GitHub organization **[K-O-St-V-Vindobona-II](https://github.com/K-O-St-V-Vindobona-II)**.

| Repository | Role | Production domain | Tech stack | Runs as |
|---|---|---|---|---|
| [`vb-api`](https://github.com/K-O-St-V-Vindobona-II/vb-api) | Backend: the club's management system (members, fees, archive, bookkeeping, scheduled jobs) | `api.vindobona2.at` | Python 3.14, FastAPI, SQLAlchemy, Alembic | image `vb-api`, containers `vb-api` and `vb-api-worker` |
| [`vb-intern`](https://github.com/K-O-St-V-Vindobona-II/vb-intern) | Frontend for members and officials (login required) | `intern.vindobona2.at` | Vue 3, TypeScript, Vite, nginx | image and container `vb-intern` |
| [`vb-www`](https://github.com/K-O-St-V-Vindobona-II/vb-www) | Public website | `www.vindobona2.at` | Vue 3, TypeScript, Vite, nginx | image and container `vb-www` |
| [`vb-deploy`](https://github.com/K-O-St-V-Vindobona-II/vb-deploy) (this repository, public) | **The how:** playbooks, Caddy, Quadlets, templates, the tool `vb`, this handbook | none | Ansible, systemd Quadlets, Python | runs nothing itself, it configures the others |
| `vb-access` (**private**) | **The what:** the data of this installation: inventory (server, domains), secrets, the operator key, the access note | none | files only | runs nothing and has no domain |

**Why two repositories for operations?** `vb-deploy` is code and knowledge and can be public, so anyone (and any successor) can read how the system is built. Everything that is specific to the installation and sensitive (which server, which passwords, which key) is in `vb-access`, which only owners of the organization can read. The tool `vb` joins the two: it reads the private data from `vb-access` and applies it with the public playbooks.

**Naming convention:** the repository name is the subdomain: `vb-api` is `api.vindobona2.at`, `vb-intern` is `intern.vindobona2.at`, `vb-www` is `www.vindobona2.at`. The domains of every stage come from the inventory file of that stage (`sites/<stage>/inventory.ini` in `vb-access`). `vb-deploy` and `vb-access` have no domain.

The three application repositories each build their own container image in CI and push it to `ghcr.io/k-o-st-v-vindobona-ii/<repo>:latest` and `...:<full commit sha>` on every merge to `main`. `vb-deploy` builds no images and clones no application repository onto the host: it only distributes configuration and secrets and makes sure the right images run.

### 1.4 Containers and pods

All containers on production (the container names are the same on every stage; which stage a container belongs to is decided only by `APP_ENVIRONMENT` in its environment file):

| Container | Pod | Image | Published on host | Purpose | Code from |
|---|---|---|---|---|---|
| `caddy` | none (host network) | `caddy:2.11` | 80, 443 | TLS, routing, security headers | none |
| `logging-dozzle` | none | `dozzle` | 127.0.0.1:8081 | Log viewer, reachable only through Caddy at `intern.<domain>/logging/dozzle` | none |
| `vb-api` | `vb-api-pod` | `ghcr.io/.../vb-api` | 127.0.0.1:21000 (container port 8000) | The backend (gunicorn with uvicorn workers) | `vb-api` |
| `vb-api-worker` | `vb-api-pod` | same image as `vb-api` | none | Scheduled and background jobs (`arq`) | `vb-api` |
| `vb-api-pg` | `vb-api-pod` | `postgres:18` | none (internal) | The database | none |
| `vb-api-valkey` | `vb-api-pod` | `valkey/valkey:8` | none (internal) | Job queue of the worker | none |
| `vb-intern` | `vb-intern-pod` | `ghcr.io/.../vb-intern` | 127.0.0.1:21001 (container port 8080) | nginx serving the built frontend | `vb-intern` |
| `vb-www` | `vb-www-pod` | `ghcr.io/.../vb-www` | 127.0.0.1:21002 (container port 8080) | nginx serving the built public site | `vb-www` |
| `vb-garage` | `vb-api-pod` | `dxflrs/garage` | 9000 (S3 API, public through Caddy), 9001 (admin API, loopback only) | S3 replacement, **non-production stages only** | none |

Why the pod? Containers of a pod share one network namespace and reach each other over `localhost`. That keeps internal traffic (API to database, API to queue, API to Garage) away from the public internet and needs no published ports for the database and the queue. `vb-api-worker` uses the same image as `vb-api` and only replaces the start command (`arq app.worker.WorkerSettings`).

Every production container is hardened: read-only root file system, no extra privileges, all Linux capabilities dropped (Caddy adds back only the one needed for ports 80/443), memory and process limits, a health check, and `Notify=healthy` so systemd only calls a service started once it is healthy. Details are in the Quadlet templates under `templates/` and `quadlets/`.

Two timers run on the host: `podman-auto-update.timer` (nightly, see [1.7](#17-how-a-change-reaches-production)) and `podman-prune.timer` (Sunday 04:00, removes unused images and containers).

### 1.5 Where the data lives

| Data | Where | Backed up? | How to get it back |
|---|---|---|---|
| PostgreSQL database | On the host: `~/data/vb/production/postgres` | **Yes:** a full dump every night at 03:00 (Vienna time) to the S3 prefix `db-backups/`, kept for 90 days (`BACKUP_RETENTION_DAYS`), plus manual dumps | [2.4 Restore the database](#24-the-database-is-damaged-backup-and-restore) |
| Files (archive, member images, public gallery) | AWS S3 bucket `vindobona2-at` | The bucket is versioned (configured in AWS, not part of this repository) | Nothing to restore after a host loss. Deleted or overwritten files can be recovered from earlier versions in AWS. |
| Job queue | `~/data/vb/production/valkey` | No, deliberately: a lost job is a notification mail or a manually repeatable downsync (the refresh of a non-production stage from production data, see [5.5](#55-downsync)) | Nothing |
| Secrets (environment files) | Vault-encrypted in the private repository `vb-access` under `sites/<stage>/secrets/`, rendered to `~/.env/` on the host | The private repository is the backup (plus an offline copy, see [Appendix C](#appendix-c-access-and-handover)) | `./vb deploy` |
| Operator key | Vault-encrypted in `vb-access` (`sites/<stage>/operator_key.vault`); its public half is in `authorized_keys` on the host | The private repository | `./vb setup` |
| Configuration (Caddyfile, Quadlets) | This repository | The Git repository | `./vb deploy` |
| TLS certificates | `~/.local/share/caddy/caddy_data` | No, re-issued automatically as soon as DNS points to the host | Automatic |
| Container images | `ghcr.io` | Rebuildable from the repositories | `podman pull` / auto-update |
| Logs | systemd journal (persistent) and container output (Dozzle) | No | none |

**Not in any repository:** the **Vault password** (it opens everything in `vb-access`), the DNS zone, the AWS account, the GitHub organization and the hosting account. The accounts are listed, with their logins and second factors, in the access note `credentials.md` in `vb-access` ([Appendix C](#appendix-c-access-and-handover)); the Vault password is kept by the people who run the system.

**Recommendation (not the current state):** the database backups sit in the same AWS account as the files. If that account were lost, both would be gone. A second copy of the dump somewhere else (another provider or an offline disk, for example a weekly download of the newest dump) closes that gap.

### 1.6 Users and security model

```
  workstation                                       production host
  +------------------------------+                  +---------------------------+
  | ./vb deploy production       |                  |  user service (no sudo)   |
  |   asks the Vault password    |   SSH, key only  |    runs all containers    |
  |   unpacks the operator key   |----------------->|  user admin (sudo, no     |
  |   (in memory only)           |                  |    password): setup only  |
  +------------------------------+                  +---------------------------+
       reads sites/production/ from the private repository vb-access
```

- **Two Linux users on the host.** **`service`** runs all containers and has no `sudo` and no administrative group (only `systemd-journal`, to read logs). **`admin`** has `sudo` without a password and is used only by `setup_vps.yml`; it never runs a container. A container escape therefore ends at the rights of an ordinary user.
- **One operator key, no passwords.** SSH is key-only for everyone: no `root` login, only `admin` and `service` are allowed, and both accounts have a locked password (`/etc/ssh/sshd_config.d/00-vb-hardening.conf`, set by `setup_vps.yml`). The `authorized_keys` of both users contain exactly the keys listed under `operator_public_keys` in the inventory of the stage; every other key is removed on each `./vb setup`. The private key is stored vault-encrypted in `vb-access` and exists unencrypted only in memory while `./vb` runs.
- **What that means.** Whoever has the operator key can do everything on the host, including `sudo`. That is a deliberate simplification for a small club: the key is protected by the Vault password and by the private repository, and [Appendix C](#appendix-c-access-and-handover) describes what to do when a person leaves. `./vb setup` refuses to run if the key it connects with is not in the list (it would delete its own access), and before it closes `root` and password logins it proves that the key really logs in as `admin` and as `service`.
- Firewall: only ports 22, 80 and 443 are open (UFW on Debian, firewalld on the Red Hat and SUSE families). Security updates install automatically; a pending reboot stays a manual decision.
- Dozzle gets the Podman API socket of `service`, which is full control over every rootless container. Its basic-auth credential is therefore as sensitive as the secrets of the system. It must be long and random; access can additionally be limited to IP ranges with the inventory variable `logging_allowed_cidrs`.
- Caddy sets a strict Content-Security-Policy for `intern` and `www`, security headers for all sites, and removes the `Server` header.

### 1.7 How a change reaches production

```
branch development ──PR──> main (protected) ──CI──> image :latest and :<commit sha> on ghcr.io
                                                          |
                                      nightly: podman-auto-update.timer pulls a changed :latest
                                      and restarts the container      (no Ansible needed)
                                      immediately: ./vb deploy production --tags deploy-api|intern|www
```

1. Work happens on `development`; `main` is protected and takes pull requests only.
2. The CI pipeline of the repository runs lint, tests, CodeQL, a read-only smoke test of the image and, on `main`, builds and pushes the image.
3. Every night (`OnCalendar=daily` with a random delay up to 15 minutes) the host checks whether `:latest` changed for each container labelled `AutoUpdate=registry`, pulls it and restarts the container. `./vb deploy production --tags deploy-<service>` does the same on demand.
4. A new `vb-api` image applies pending database migrations itself on start (`alembic upgrade head` in its entrypoint). The worker container skips migrations (`SKIP_MIGRATIONS=true`), so exactly one process migrates.
5. Changes to configuration, secrets or Quadlets are distributed with `./vb deploy`, never by hand.

### 1.8 Stages

| Stage | Inventory (in `vb-access`) | Storage | Data | Status |
|---|---|---|---|---|
| `production` | `sites/production/inventory.ini` | AWS S3 | real | live |
| `test` | `sites/test/inventory.ini` | own Garage | refreshed from production every night | template only (`site-template/test`), no dedicated host yet |
| `qa` | `sites/qa/inventory.ini` | own Garage | refreshed from production every night | template only (`site-template/qa`), no dedicated host yet |
| `development` | none (runs outside this repository, see [5.7](#57-local-development-environment)) | local Garage | refreshed from production every night | the developers' own host |

`deploy.yml` accepts only `production`, `test` and `qa` and refuses everything else. The step-by-step setup of another stage is in [5.2](#52-set-up-a-test-or-qa-stage).

---

## 2. When something is down

### 2.0 Triage table

| Symptom | Likely cause | What to do | Section |
|---|---|---|---|
| None of the three sites opens, or the browser shows a certificate error | `caddy` stopped, DNS wrong, Let's Encrypt limit | Check Caddy and DNS, restart Caddy | [2.1](#21-first-checks-read-only), [2.2](#22-restart-a-service-or-pod), [Appendix B](#appendix-b-pitfalls) |
| Login fails or the pages show server errors, but `www` works | `vb-api-pod` unhealthy | Look at logs, restart the pod | [2.1](#21-first-checks-read-only), [2.2](#22-restart-a-service-or-pod) |
| Everything broke right after an update | Faulty release or migration | First the checks of 2.1, then roll back to the previous image (a frontend fault needs no database step) | [2.3](#23-roll-back-a-faulty-release) |
| Data is wrong, missing or corrupt | Faulty change or data damage | Restore the database from a backup | [2.4](#24-the-database-is-damaged-backup-and-restore) |
| Images or files show as broken, downloads fail | Missing objects in S3, credentials or CORS | Run the integrity check | [2.5](#25-files-and-storage) |
| Scheduled mails or jobs do not run | `vb-api-worker` or `vb-api-valkey` down | Check the scheduler page and the worker | [2.1](#21-first-checks-read-only), [2.2](#22-restart-a-service-or-pod) |
| `./vb ssh` does not connect, the host is gone | Host down or lost, wrong address | Provider console; if the host is lost, rebuild | [2.6](#26-total-loss-of-the-host), [3](#3-new-build-or-migration-to-a-new-server) |
| `./vb` says the key is refused | The operator key is not on the host (any more) | Provider console and rescue mode | [Appendix C](#appendix-c-access-and-handover) |
| The disk is full | Old images, logs | `podman image prune`, check `df -h` | [2.1](#21-first-checks-read-only) |
| You cannot get into the hosting, AWS, Google or GitHub account | Credentials, second factor | The access note in `vb-access` | [Appendix C](#appendix-c-access-and-handover) |

### 2.1 First checks (read-only)

None of these commands changes anything. The loops use the domains `api.`, `intern.` and `www.vindobona2.at`; for another stage or after a domain change use the values of `api_domain`, `intern_domain` and `www_domain` from its `inventory.ini`.

```bash
# [workstation] is each site reachable and is its certificate valid? (expect: 200 three times)
for d in api intern www; do curl -s -o /dev/null -w "$d %{http_code}\n" https://$d.vindobona2.at/; done

# [workstation] log in to the host (asks the Vault password; opens a shell as the user service)
./vb ssh production

# [host] containers and their health (expect: every service container "healthy"; the helpers ending in -pod-infra only show "Up")
podman ps

# [host] failed units (expect: no output)
systemctl --user --failed

# [host] state of every unit of the system
systemctl --user list-units 'vb-*' 'caddy*' 'logging*' 'podman-*'

# [host] recent log lines of one service (replace the unit name as needed)
journalctl --user -u vb-api.service -n 100 --no-pager
podman logs --tail 100 vb-api-worker

# [host] Caddy: certificate or routing problems show up here
journalctl --user -u caddy.service -n 100 --no-pager

# [workstation] where does each domain point? (compare with the host's address)
for d in api intern www; do dig +short $d.vindobona2.at; done

# [host] disk space
df -h /

# [host] database revision (expect: a revision id followed by "(head)")
podman exec vb-api alembic current
```

**Everything is fine when:** the loop prints `200` three times; `podman ps` shows the service containers `healthy`; `systemctl --user --failed` prints nothing; `alembic current` ends in `(head)`; and the newest dump of `restore_db.py --list` (see [2.4](#24-the-database-is-damaged-backup-and-restore)) is from last night, that is `production-<today or yesterday>_03-00-00.dump`.

To preview what a run of the playbooks would change on the host, without changing anything, use `./vb check production`. On a healthy system both parts end with `failed=0` and `changed=0` in the `PLAY RECAP` line (on Debian the first part may show `changed=1`, a known false report, see [Appendix B](#appendix-b-pitfalls)). The browser based log viewer is `https://intern.vindobona2.at/logging/dozzle` (basic auth). The page **System → Scheduler** in `vb-intern` (permission `systemAdmin`) shows every scheduled job with its next and last run.

### 2.2 Restart a service or pod

**Restart a pod only through its pod unit, never through one of its containers.** A container is bound to its pod unit; restarting the container removes the whole pod, and nothing starts the pod again by itself.

```bash
# [host] the whole API pod (api, worker, postgres, valkey); takes a few seconds
systemctl --user restart vb-api-pod.service

# [host] the frontends
systemctl --user restart vb-intern-pod.service
systemctl --user restart vb-www-pod.service

# [host] Caddy and Dozzle have no pod
systemctl --user restart caddy.service
systemctl --user restart logging-dozzle.service

# [host] afterwards: all healthy again?
podman ps
```

### 2.3 Roll back a faulty release

**Which way?** If the faulty release did not change the database schema, or its migration is compatible with the previous release, roll back only the image. If its migration is not compatible (the old code cannot work with the new schema), undo the migration first and then roll back the image. Restoring a backup ([2.4](#24-the-database-is-damaged-backup-and-restore)) is the last resort, because it loses everything written since the dump.

Order: 1. take a backup, 2. undo the migration **with the release that is still running** (an older image does not know the newer revision), 3. pin the old image, 4. verify. **Pin immediately after the downgrade:** any restart of the new image in between (the nightly auto-update, a health restart) runs `alembic upgrade head` again and undoes the downgrade. A downgrade can drop columns, and with them the data written to them since the release; `alembic history` lists the revisions. If the container cannot start at all (so `podman exec` fails), a restore ([2.4](#24-the-database-is-damaged-backup-and-restore)) is the way.

**Images** (step 3; the commands for step 2 are under **Database** below). Every image is also tagged with the full commit SHA of the merge. Find the SHA of the last good release and pin it. **Which release runs now?** Every image carries the commit it was built from:

```bash
# [host] the commit of the running image (use vb-intern or vb-www for the frontends)
podman inspect vb-api --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'
```

Open the commit list of `main` (`https://github.com/K-O-St-V-Vindobona-II/vb-api/commits/main`, likewise for `vb-intern` and `vb-www`; public, no login needed): the merge commit before the one you just found is the last good release. The package page of the repository on GitHub lists the same tags. GitHub shows the short SHA; the copy button next to it gives the full SHA that the pin needs.

An older image, from before the label existed, prints an empty line. Then use the build time, which lies close to the merge: `podman image inspect ghcr.io/k-o-st-v-vindobona-ii/vb-api:latest --format '{{.Created}}'` (UTC) and `podman ps --format '{{.Names}}\t{{.Image}}\t{{.CreatedAt}}'` (when the nightly update last re-created each container). Look for the merge commit built just before that time. Then pin the release:

```bash
# [workstation] use the matching variable per repository; the tag is the full commit SHA:
#   vb_api_image_tag, vb_intern_image_tag, vb_www_image_tag
# (a vb-intern or vb-www image without the commit label, see below, is older and carries the prefix "sha-": sha-<full-commit-sha>)
./vb deploy production -e vb_api_image_tag=<full-commit-sha> --check --diff   # preview first
./vb deploy production -e vb_api_image_tag=<full-commit-sha>                   # then apply
```

The changed Quadlet makes the playbook restart the pod. The nightly auto-update does not override a pin, because the tag of a commit SHA never changes. The pin holds only for this run: a later `./vb deploy production` without `-e` goes back to `latest`. For a lasting pin, add the variable under `[vps:vars]` in `sites/production/inventory.ini` (for example `vb_api_image_tag=<full-commit-sha>`); commit and push the change in `vb-access`, and remove the line again once the fix is released. `grep image_tag ../vb-access/sites/production/inventory.ini` shows whether a pin is still active; the nightly update does not override it, so the service would stay on the old version unnoticed. Verify with the loop and the checks of [2.1](#21-first-checks-read-only) (all three sites `200`, `podman ps` healthy, `alembic current`).

**Database.** Starting an older image against a newer database is safe as long as each migration stays compatible with the previous release (see *expand first, contract later* in the [`vb-api` README](https://github.com/K-O-St-V-Vindobona-II/vb-api/blob/main/README.md#database-migrations)): the image logs a warning and starts without migrating. If a migration itself is the problem:

```bash
# [host] 1. take a backup first
podman exec vb-api python scripts/backup_db.py
# [host] 2. undo the newest migration with the still running release (or name a revision instead of -1)
podman exec vb-api alembic downgrade -1
# then pin the old image as above
```

**Did the faulty release bring a migration, and to which revision do you go back?** Compare the two releases on GitHub: `https://github.com/K-O-St-V-Vindobona-II/vb-api/compare/<last-good-sha>...<faulty-sha>`. Every new file under `alembic/versions/` is one migration. If there is none, no database step is needed. With one, `-1` is right; with several, use `-N` or name the revision: the `down_revision` inside the oldest new file is the revision to go back to.

### 2.4 The database is damaged: backup and restore

**How backups work.** The job `db_backup` writes a full dump (`pg_dump`, custom format) every night at 03:00 Vienna time to the S3 prefix `db-backups/` and then deletes dumps older than `BACKUP_RETENTION_DAYS` (90 days in the production template; without the variable the application keeps 29). File names are `<stage>-YYYY-MM-DD_HH-MM-SS.dump` in Vienna time; manual dumps end in `-manual.dump`. Everything since the last dump is lost in a restore, so take a manual backup before any planned risky change. **Choose the dump deliberately:** the newest dump may already contain the damage (a manual backup you take now does, and becomes the newest). Pick one from before the damage with `--backup-name`.

**On the console** (this is the only way to restore on production; log in with `./vb ssh production`):

```bash
# [host] list the available backups (read-only)
podman exec vb-api python scripts/restore_db.py --list
#   expect: one line per dump, oldest first, manual dumps included, for example
#   db-backups/production-2026-10-13_03-00-00.dump

# [host] create a manual backup now (add --cleanup to also delete expired dumps)
podman exec vb-api python scripts/backup_db.py
#   expect: "Backup complete: production-<timestamp>-manual.dump"

# [host] restore the newest dump (--force is mandatory on production)
podman exec vb-api python scripts/restore_db.py --force
#   expect: "Restore complete. Backup: <name>"

# [host] or restore one specific dump
podman exec vb-api python scripts/restore_db.py --force \
  --backup-name production-2026-10-11_03-00-00.dump

# [host] a dump older than the code needs the newer migrations
podman exec vb-api alembic upgrade head
# [host] always restart the pod after a restore (it also clears the connections the restore cut)
systemctl --user restart vb-api-pod.service
```

What the restore does: it drops and recreates the `public` schema of the live database (other database connections are terminated), loads the dump, keeps the job history of this stage, and refuses to finish when the restored database holds no rows. `--force` is deliberate on every use: it protects the live database against an accidental restore.

**Verify afterwards that real data arrived**, not only that the command ended without an error:

```bash
# [host] expect a number greater than 0
podman exec vb-api-pg psql -U vb -d vb -c 'SELECT count(*) FROM members;'
```

Also check `podman exec vb-api alembic current` (expect `(head)`), then log in on `https://intern.vindobona2.at` and open a member. Keep the dump of the damaged state (do not run `backup_db.py --cleanup` meanwhile) until you are sure the restore is good.

**In the browser** (permission `systemAdmin`, page **System → Scheduler** in `vb-intern`; the permissions of your own login are listed on your profile page, and an existing system administrator grants new ones; the interface is German, the button names below are verbatim). The browser needs a working API and login, so during an outage use the console:

| Stage | Button | What it does |
|---|---|---|
| production | `Backup jetzt erstellen` | Writes a manual backup immediately, independent of the nightly one. |
| every other stage | `Downsync jetzt durchführen` | Mirrors the production bucket and restores the newest production backup onto this stage. |

**There is no restore button on production, by design:** a restore replaces the whole live database and is therefore only possible on the console with `--force`.

### 2.5 Files and storage

```bash
# [host] read-only check: every file known to the database exists in S3 and vice versa
podman exec vb-api python scripts/check_s3_integrity.py
#   expect: "0 missing file(s)." and "No orphaned files found." for Archive and Standesdb
```

The same check runs every Tuesday (`archive_health_check` at 01:00, `standesdb_health_check` at 03:00) and mails a report to the administrators. Missing images are almost always a storage problem (credentials, bucket, CORS), not a database problem. The files are not part of the database backups; the S3 bucket is versioned, so deleted or overwritten objects can be recovered from earlier versions in the AWS console. Soft-deleted archive files can be restored with `maintain_deleted_archive_files.py restore` (see the [scripts reference](https://github.com/K-O-St-V-Vindobona-II/vb-api/blob/main/scripts/README.md)).

### 2.6 Total loss of the host

If the host is gone (hardware, provider, destroyed by an update): everything that is in Git can be rebuilt, the database comes back from the newest S3 backup, and the files were never on the host. Follow [3. New build or migration to a new server](#3-new-build-or-migration-to-a-new-server); case study 3 in [Appendix D](#appendix-d-case-studies) walks through it. If a hosting or AWS account is lost, start with [Appendix C](#appendix-c-access-and-handover).

### 2.7 The vb commands you will need

All commands run on the **workstation**, in the checkout of this repository. `vb` finds the private data in `../vb-access/sites`; `--sites-dir <folder>` before the command names another place. Everything after the stage name is passed on to `ansible-playbook`.

| Goal | Command |
|---|---|
| Log in to the host | `./vb ssh production` (as `service`); `./vb ssh production admin` for administrative work |
| Show what would change, change nothing | `./vb check production` |
| Apply configuration, secrets and Quadlets | `./vb deploy production` |
| Pull the newest image now and restart | add `--tags deploy-api`, `--tags deploy-intern` or `--tags deploy-www` |
| Roll an image back | add `-e vb_api_image_tag=<full-commit-sha>` (see [2.3](#23-roll-back-a-faulty-release)) |
| First setup of a host, or verify the base configuration | `./vb setup production` |
| Replace the operator key | `./vb rotate-key production` |
| Create the data of a new stage | `./vb init test` (see [5.2](#52-set-up-a-test-or-qa-stage)) |
| Read or edit a secret | `PAGER=cat ansible-vault view ../vb-access/sites/production/secrets/vb-api.env.j2`, `ansible-vault edit ...` (then `./vb deploy production`) |

### 2.8 What you need that is not in the repositories

| Needed | For | Where it is described |
|---|---|---|
| The Vault password | Opens everything in `vb-access` (key, secrets, access note) | [Appendix C](#appendix-c-access-and-handover) |
| Access to the GitHub organization | Reads `vb-access`, code, CI, images | [1.3](#13-the-github-organization-and-the-repositories), [Appendix C](#appendix-c-access-and-handover) |
| DNS zone of `vindobona2.at` (registrar or DNS provider) | Pointing the domains at a (new) host | the access note |
| AWS account (root user and IAM users) | Bucket with files and backups | the access note |
| Hosting account | Server, console, rescue mode, billing | the access note |
| Google account and the Google OAuth app | Mail login and "Sign in with Google" | the access note |
| Let's Encrypt | Certificates, issued by Caddy on its own | [Appendix B](#appendix-b-pitfalls) (rate limits) |

---

## 3. New build or migration to a new server

This procedure serves both a first setup and any later case where the production host has to be replaced (hardware change, operating system reinstall, provider change, emergency). It has been exercised end to end against throwaway servers (Debian 13 and Rocky Linux 10, starting from the raw image of the provider) and against the production system. For a new test or qa stage see [5.2](#52-set-up-a-test-or-qa-stage); this chapter describes production.

**Order at a glance:** prepare; set up the new host and deploy it (it starts with an empty database); then in the maintenance window freeze the old host and take the final backup, restore on the new host, switch DNS; verify; clean up. If the old host is dead, skip the freeze.

**Work with two sites until DNS has moved.** The site `production` names the old host. As long as DNS points to the old host, anything that uses the domain would reach the **old** host. So give the new host a site of its own, a copy with a different address, and use it for the whole build:

```bash
# [workstation] in the folder that contains the site directories
cp -r ../vb-access/sites/production ../vb-access/sites/production-new
# then, in production-new/inventory.ini, put the IP address of the new host under [vps]:
#   [vps]
#   <new-ip>
```

The domains of the stage are separate variables in the same file and stay as they are. From here `./vb ... production-new` works on the new host and `./vb ... production` still on the old one. Check where a command would go before you run it: `ansible-inventory -i ../vb-access/sites/production-new/inventory.ini --list` must show `<new-ip>`.

### 3.1 Preparation (before the old host is touched)

- Announce a maintenance window.
- **Note the address of the old host** (`dig +short intern.vindobona2.at`, or the provider console). After the DNS switch the domains no longer lead to it, and you need it for the way back and for the cleanup.
- **Lower the DNS TTL** of the three domains a day ahead so the switch takes effect quickly (not possible if the old host is already dead).
- Have the Vault password and access to the organization at hand ([Appendix C](#appendix-c-access-and-handover)).
- **Check the AWS side:** if the production IAM user is restricted to an IP address (`aws:SourceIp`), add the new host's address **now**, otherwise the new host cannot reach the bucket and the restore fails (in AWS, outside this repository).
- The final backup is taken in the window itself, see [3.6](#36-maintenance-window-freeze-the-old-host-and-take-the-final-backup). Without it, everything since the last nightly dump is lost.
- If the provider offers snapshots, take one before a reinstall. This repository does not automate it.

### 3.2 Install the operating system

Order a new server (or reinstall the existing one) with **Debian 13** as the reference; other distributions work as listed in [1.2](#12-technologies-and-why). It must be **x86_64**: the application images exist for `amd64` only, and `./vb deploy` stops with a clear message on an ARM server (for example the cheaper ARM plans of some providers). On SELinux hosts the bind mounts carry `:Z`/`:z` labels and Dozzle runs without label confinement (see the comments in `quadlets/`).

Note the **IP address and the initial `root` password** that the provider gives you. Nothing else needs to be prepared: no user, no key, no package. If the provider marked the password as expired, the first SSH login demands a new one, and Ansible cannot answer that question. Log in once (`ssh root@<new-ip>`), set a new password and use that one.

Compare the host key fingerprint with the one the provider console shows before you trust it:

```bash
# [workstation]
ssh-keyscan -t ed25519 <new-ip> | ssh-keygen -lf -
```

Without access to the console there is nothing to compare with, and the first connection cannot be verified. A freshly installed server whose first login happens right after the installation is the least risky moment; write the fingerprint into the access note so that a later change is noticed.

After a reinstall of the same address SSH reports `REMOTE HOST IDENTIFICATION HAS CHANGED`. That is expected, but remove the old entry only after you compared the fingerprint (a changed key that no reinstall explains is a possible interception): `ssh-keygen -R <address>`.

### 3.3 Prerequisites on the workstation

- Linux or macOS (on Windows use WSL). Python 3 comes with Ansible; `./vb` uses nothing but the Python standard library.
- Ansible (tested with ansible-core 2.19; the playbooks use the collections `community.general` and `ansible.posix`, which the full `ansible` package includes; with only `ansible-core` run `ansible-galaxy collection install community.general ansible.posix`), `git`, `curl`, `dig` (for the DNS checks; package `dnsutils` on Debian and Ubuntu) and `sshpass` (only for the very first run on a new host, which logs in with the password of the provider). On Debian and Ubuntu: `sudo apt install git openssh-client ansible curl dnsutils sshpass` (`./vb init` also needs `openssh-client`, it creates the operator key).
- A checkout of this repository and, **next to it**, a checkout of the private repository `vb-access` ([the handover sheet](HANDOVER.md) shows how to get it).
- The Vault password. You do **not** need an SSH key of your own: the operator key is in `vb-access`.
- **DNS:** the three domains must point to the new host for Caddy to get its certificates. Caddy starts during the deploy, before DNS has moved, and retries until it does (see 3.8).

The tool picks the login by itself:

| Command | Login | Why |
|---|---|---|
| `./vb setup` on a new host | `root` with the provider's password (`./vb` asks for it after the Vault password; `sshpass` hands it to SSH) | The host has no other users yet. If `root` already accepts the operator key (the provider installed it), no password is needed. |
| `./vb setup` on a host that is set up | `admin` with the operator key | Needs `sudo`, which is passwordless. `root` login is closed. |
| `./vb deploy` | `service` with the operator key | Everything runs in the user's own scope, no `sudo`. |

### 3.4 Set up the host: `./vb setup`

```bash
# [workstation]
./vb setup production-new
```

It asks for the Vault password (`Vault password:`) and, on a raw host, right after it for the root password of the provider (`SSH password:`); a run started without a terminal waits at the second question without an error. It prints which situation it found. In this order the playbook: installs updates and the required packages, checks that Podman is version 5 or newer, creates the users `admin` and `service` without passwords, allows `admin` passwordless `sudo`, makes the journal persistent, installs exactly the operator keys of the inventory for both users, enables linger for `service` (containers survive logout), tunes the kernel for rootless Podman, **reboots** (once), opens the firewall for 22, 80 and 443, **proves that the operator key logs in as `admin` and as `service` and that `admin` has `sudo`**, and only then hardens SSH (key-only, no `root`) and locks the passwords of both users. If the proof fails, the run stops while the old way in is still open.

A full system upgrade takes several minutes (on Rocky Linux about ten). The provider's `root` password stays valid at the provider's console and is only useful there. Running `./vb setup` again later verifies the configuration; it reports no change on a host that is up to date.

### 3.5 Deploy: `./vb deploy`

```bash
# [workstation] preview first
./vb check production-new
# then apply
./vb deploy production-new
```

The preview needs a host that is set up (on a new host `./vb check` stops with "does not accept the operator key ... run './vb setup' first"). On a host that was never deployed, the preview of the second part ends with an error about `caddy.service` ("Could not find the requested service"); that is expected because the services do not exist yet. The first clean preview is the one after the first deploy.

Note two things. A deploy on the new host starts its scheduled jobs at once; with the empty database nothing is sent, which is why the restore happens only after the old host has been stopped ([3.6](#36-maintenance-window-freeze-the-old-host-and-take-the-final-backup)). And until the restore, the new host writes its own, empty nightly dumps into the same S3 prefix as the real ones, so never restore "the newest" dump: always name it ([3.7](#37-restore-the-database-on-the-new-host)). The real dumps age too: after `BACKUP_RETENTION_DAYS` (90 days) the nightly job deletes them, so do not leave a new host with an empty database for that long. The deploy creates the directories, fixes the ownership of the Postgres directory for rootless Podman, refuses placeholder or inconsistent secrets, distributes secrets, Caddyfile and Quadlets, creates the application database role, starts all services and enables the two timers. **Postgres starts with an empty database.** That is expected; the next step fills it. The first start of Postgres creates its data directory and can take about a minute; the deploy waits for up to three minutes (its output shows `FAILED - RETRYING` lines meanwhile, which are normal), and if it still fails, simply run it again, it continues where it stopped. It builds no images and restores no data.

### 3.6 Maintenance window: freeze the old host and take the final backup

Do this at the start of the window, on the **old host**, in this order (if the old host is dead, skip this section: the newest nightly dump from before the failure is the final state, and everything written since is lost):

```bash
# [workstation] the site "production" still names the old host
./vb ssh production
# [old host]
# 1. close the public door: from now on nobody can change data
systemctl --user stop caddy.service
# 2. take the final backup and note the file name it prints
podman exec vb-api python scripts/backup_db.py
# 3. stop the stack, so no scheduled job (mail) runs on both hosts
systemctl --user stop vb-api-pod.service vb-intern-pod.service vb-www-pod.service
```

From here on do not reboot the old host and do not delete it; it is the way back ([3.10](#310-if-the-migration-fails)).

### 3.7 Restore the database on the new host

On the new host, with the dump you noted in 3.6 (commands and background in [2.4](#24-the-database-is-damaged-backup-and-restore)):

```bash
# [workstation]
./vb ssh production-new
# [new host]
podman exec vb-api python scripts/restore_db.py --list
podman exec vb-api python scripts/restore_db.py --force --backup-name <dump-from-3.6>
podman exec vb-api alembic upgrade head
systemctl --user restart vb-api-pod.service
podman exec vb-api-pg psql -U vb -d vb -c 'SELECT count(*) FROM members;'   # expect a number > 0
podman exec vb-api alembic current                                         # expect "(head)"
```

**If `--list` fails or shows nothing.** `InvalidAccessKeyId` or `AccessDenied` points to the storage credentials in `secrets/vb-api.env.j2` (`S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_REGION`) or to an `aws:SourceIp` restriction of the IAM user that does not yet allow the new host ([3.1](#31-preparation-before-the-old-host-is-touched)). An empty list with working credentials means that no dump exists under `db-backups/`; look at the bucket in the AWS console. Fix the cause, run `./vb deploy production-new` again if you changed a secret, and repeat the command. Without any dump the system starts empty and nobody can log in; this manual describes no way to create a first account, so treat a missing dump as a loss of data.

**Always name the dump.** The new host has been writing its own empty nightly dumps into the same prefix since its first deploy; "the newest" can be one of those. If the old host is dead, take the newest dump that was created **before** it went down (the file name carries the time) and ignore every dump created by the new host.

The restore is **mandatory on a new host**: without it the stack runs, but with an empty database. A browser button for this does not exist on production. On a non-production stage the button `Downsync jetzt durchführen` (**System → Scheduler**) mirrors the production bucket and restores the newest production backup, see [5.5](#55-downsync).

### 3.8 Switch DNS and obtain certificates

Caddy gets its certificates over HTTP-01 on port 80, so each domain must resolve to the new host first. Caddy has been retrying with growing delays since the deploy; a few failed attempts are normal, many can reach Let's Encrypt rate limits ([Appendix B](#appendix-b-pitfalls)). Short `connection reset by peer` lines for the API port in the Caddy journal are normal as long as `vb-api` is not yet healthy.

1. Point the A/AAAA records of `api`, `intern` and `www` at the new host.
2. Wait until Caddy has its certificates (usually within minutes) and check:
   ```bash
   # [workstation] must show the new address (an old answer can stay cached until the TTL has run out; use your own domains if they differ from `vindobona2.at`)
   for d in api intern www; do dig +short $d.vindobona2.at; done
   # [new host] look for "certificate obtained successfully"
   journalctl --user -u caddy.service -n 50 --no-pager
   ```
   If nothing happens after about ten minutes, check the DNS answer and that port 80 reaches the new host. Do not restart Caddy in a loop.

Before the DNS switch you can test the new host without a valid certificate, for example `curl -k --resolve api.vindobona2.at:443:<new-ip> https://api.vindobona2.at/`.

### 3.9 Verification

- `api`, `intern` and `www` answer with `200` and a valid certificate (the loop in [2.1](#21-first-checks-read-only); a plain `curl -I` gives `405` on `api`, because the API does not accept `HEAD`). The `dig` check in 3.8 shows the new address.
- `podman ps` on the new host shows every service container `healthy` (the `-pod-infra` helpers only `Up`); `systemctl --user --failed` prints nothing.
- The member count (3.7) is a real number; log in on `intern` and open a member.
- `systemctl --user list-timers --all` shows `podman-auto-update.timer` and `podman-prune.timer`.
- `./vb ssh production-new admin`, then `sudo ufw status verbose` (Debian) or `sudo firewall-cmd --list-all` (Red Hat and SUSE families) shows only 22, 80 and 443.
- The object storage works from this host (the deploy does not test it; a wrong key would otherwise show up only at the next 03:00 backup): `podman exec vb-api python scripts/backup_db.py` ends with `Backup complete` (it leaves one more manual dump in the bucket), and `podman exec vb-api python scripts/check_s3_integrity.py` reports `0 missing file(s)` and `No orphaned files found.`
- **System → Scheduler** shows the jobs with sensible next-run times.

Close the maintenance window when everything is green.

### 3.10 If the migration fails

As long as the old host still holds the data and is not wiped, the way back is:

1. If DNS already points to the new host: stop its API pod first, so no mail leaves from both hosts: `systemctl --user stop vb-api-pod.service` on the new host.
2. If people already worked on the new host after the DNS switch, take a backup there (`podman exec vb-api python scripts/backup_db.py`) and restore it on the old host as in 3.7, otherwise those changes are lost.
3. On the old host (`./vb ssh production`): `systemctl --user start caddy.service vb-api-pod.service vb-intern-pod.service vb-www-pod.service`.
4. Point DNS back to the old host's address.

Do not delete the old host before the new one has run through at least one nightly backup and one auto-update.

### 3.11 Clean up the old host

- Power the old host off once the new one is verified. Do not just leave it running: a reboot would bring its stack and its scheduled mails back. Delete it at the provider only later. It holds personal data of members: let the provider wipe the disk, or destroy the server instead of only powering it off.
- Remove the old host's address from the `aws:SourceIp` restriction of the production IAM user, if there is one (see 3.1). The read-only IAM users of the other stages are tied to those stages' own hosts and are unaffected.
- Make the new host the site `production`: in `vb-access/sites`, delete the old `production`, rename `production-new` to `production`, and in its `inventory.ini` put the domain `www.vindobona2.at` back under `[vps]`. Commit the change in `vb-access`. Set the DNS TTL back to its normal value.
- Remove the old host from `known_hosts`.
- Rotate the secrets the old host held if there is any doubt about its fate ([Appendix D](#appendix-d-case-studies), case study 6).

---

## 4. Scripts and CLI tools

### 4.1 The `vb` tool and Ansible (workstation)

| What | Where | Purpose |
|---|---|---|
| `./vb setup <stage>` | this repository | Base configuration of a host: users, SSH keys, Podman, firewall, SSH hardening. First run on a new host, re-run to verify. |
| `./vb deploy <stage>` | this repository | Secrets, Caddyfile, Quadlets, services, timers. Tags: `deploy-api`, `deploy-intern`, `deploy-www` (run only when named). |
| `./vb check <stage>` | this repository | Preview of both playbooks (`--check --diff`), changes nothing. |
| `./vb ssh <stage> [admin\|service]` | this repository | Interactive shell on the host with the operator key (it takes no command: run commands inside the shell). |
| `./vb init <stage>` | this repository | Creates the data directory of a stage from `site-template/` with a new operator key. |
| `./vb rotate-key <stage>` | this repository | Replaces the operator key in two steps, so there is always one that works. |
| `playbooks/setup_vps.yml`, `playbooks/deploy.yml` | this repository | The playbooks behind `setup` and `deploy`. `deploy.yml` needs `-e site_dir=<stage directory>`. |
| `ansible-vault` | secrets | `edit`, `view`, `rekey` on the files under `vb-access`. |

Every `./vb` command asks for the Vault password once, unpacks the operator key into a private folder in memory (`/dev/shm`, or the temporary directory where that does not exist), runs, and deletes the key again, also when you press Ctrl+C. The tool's own checks (Ruff, Pyright, pytest with the documentation tests, the syntax check of both playbooks) run with `pre-commit` and, on every pull request, in GitHub Actions (`.github/workflows/ci.yml`); `pre-commit install` once per clone. The versions of all check tools are pinned with hashes in `requirements-dev.lock`.

### 4.2 Operational scripts in `vb-api`

They live in `vb-api/scripts/`, run **inside the `vb-api` container on the host** (`podman exec vb-api python scripts/<name>.py`), not on your own computer. Full parameters and examples: [`vb-api/scripts/README.md`](https://github.com/K-O-St-V-Vindobona-II/vb-api/blob/main/scripts/README.md).

| Script | Purpose | Changes data? |
|---|---|---|
| `backup_db.py` | Writes a PostgreSQL dump to S3 (`--list`, `--cleanup`). | Adds a dump. |
| `restore_db.py` | Restores the database from an S3 dump (`--list`, `--backup-name`, `--force`). Needs `--force` on production. | **Replaces the database.** |
| `check_s3_integrity.py` | Compares database and S3 in both directions. | No. |
| `downsync_prod.py` | Non-production only: mirrors the production bucket and restores the newest production dump (`--dry-run`, `--yes`, `--skip-db`, `--skip-s3`, `--no-delete`, `--light`). Refuses on production. | **Replaces data of the stage.** |
| `maintain_deleted_archive_files.py` | Lists, restores and permanently deletes soft-deleted archive files (`list`, `restore`, `purge`, `purge-duplicates`, `list-duplicates`, `urlpath`). The only tool that deletes archive data for good. | `purge*` deletes for good. |
| `trigger_chronicles.py` | Dry run or manual send of the weekly chronicle mail for a reference date. Sends nothing without `--send`. | Sends mail with `--send`. |

Developer helpers in the same folder: `run_pytest.sh` (test run plus coverage gate), `check_coverage_gate.py`, `check_router_soc.py` (routers must not contain business logic).

### 4.3 Scheduled jobs (container `vb-api-worker`)

All times are Vienna time. The page **System → Scheduler** shows the same information live.

| Job | Schedule | Stage | Purpose |
|---|---|---|---|
| `cleanup` | hourly | all | Deletes expired sessions, reset tokens, old activity logs, sent mails, orphaned user agents. |
| `refresh_category_filter_hits` | daily 07:00 | production | Recomputes the filter hits of the AH-Kassen categories. |
| `birthday_mails` | daily 15:53 | production | Birthday greetings to members whose birthday is tomorrow. |
| `debtor_reminder` | on the 25th at 18:32 | production | Fee arrears reminders (more than 300 EUR). |
| `standesdb_chronicles` | Tuesday 17:00 | production | The weekly anniversaries mail. |
| `archive_health_check` | Tuesday 01:00 | production | Archive files against S3, report to archive administrators. |
| `standesdb_health_check` | Tuesday 03:00 | production | Member images against S3, report to administrators. |
| `db_backup` | daily 03:00 (`BACKUP_HOUR`) | production | Dump to S3, delete dumps older than `BACKUP_RETENTION_DAYS` (90 days in the template). |
| `downsync` | daily 04:00 (`BACKUP_HOUR` + 1) | all except production | Mirror production, restore newest dump. |

The host also runs two systemd timers that are not part of the scheduler: `podman-auto-update.timer` (nightly, shortly after midnight with a random delay up to 15 minutes) and `podman-prune.timer` (Sunday 04:00). Both follow the host clock, which is currently Vienna time.

### 4.4 Everyday commands on the host

| Goal | Command |
|---|---|
| Containers and health | `podman ps` |
| Failed units | `systemctl --user --failed` |
| Restart a pod | `systemctl --user restart vb-api-pod.service` (see [2.2](#22-restart-a-service-or-pod)) |
| Logs of a service | `journalctl --user -u vb-api.service -n 100 --no-pager`, `podman logs --tail 100 <container>` |
| Timers | `systemctl --user list-timers --all` |
| What would auto-update change? | `podman auto-update --dry-run` |
| Database revision | `podman exec vb-api alembic current` |
| Open a database shell | `podman exec -it vb-api-pg psql -U vb -d vb` |
| Clean up unused images | `podman image prune` |
| Files of a rootless database directory (move, delete) | prefix with `podman unshare` ([Appendix B](#appendix-b-pitfalls)) |

### 4.5 Frontend commands (`vb-intern`, `vb-www`)

`npm run dev` (hot reload), `npm run build` (type check and production build), `npm run test:unit`, `npm run test:coverage`, `npm run lint`, `npm run format:check`, `npm run type-check`. Run them inside the dev container, see the READMEs of the repositories.

### 4.6 Web functions for operators

Page **System → Scheduler** in `vb-intern` (permission `systemAdmin`): job list with next and last run and job history, `Backup jetzt erstellen` (production), `Downsync jetzt durchführen` (other stages). The page **System → SQL-Einsicht** shows database tables read-only. Logs: `https://intern.vindobona2.at/logging/dozzle` (basic auth).

---

## 5. Upstream stages and local development

### 5.1 Prerequisites

As in [3.3](#33-prerequisites-on-the-workstation), and in addition:

- **Disk space:** a non-production stage mirrors the complete production bucket into its own Garage (`~/data/vb/<stage>/garage-data`). The mirror measured 43 GB on 13 October 2026 and only grows. Provision at least the size of the production bucket plus headroom, on top of the operating system and Postgres. **How to check:** the size of a mirror that already exists is `podman unshare du -sh ~/data/vb/<stage>/garage-data` on any non-production stage (`podman unshare` because the directory belongs to the mapped container user); the free space of a new server is `df -h /` after `./vb ssh <stage>`. The first `./vb deploy` of a non-production stage stops with a message when less than `min_free_disk_gb` GiB are free (default 60). With the light downsync (`DOWNSYNC_LIGHT=true`, see [5.5](#55-downsync)) about 15 GB are enough: set `min_free_disk_gb=15` under `[vps:vars]` in the inventory. For a throwaway server that never runs a downsync put `min_free_disk_gb=0` there. Later deploys do not check again, because the mirror itself then occupies the space.
- **DNS:** four domains per stage: `api`, `intern`, `www` and `storage_domain` (Garage's S3 API).

### 5.2 Set up a test or qa stage

1. **Create the data of the stage:** `./vb init test` copies `site-template/test` to `../vb-access/sites/test` and generates a **new operator key** for this stage. It asks for a Vault password twice: **use the same Vault password as for the other files in `vb-access`**, otherwise this stage would need a password of its own. It refuses a password shorter than 20 characters.
2. **Inventory:** fill in `sites/test/inventory.ini`: the real hostname, all four domains instead of the `CHANGEME.example.invalid` placeholders, and `acme_email` (the contact of the certificate account). While a stage is being iterated on, `caddy_acme_ca` (commented in the template) selects the staging CA of Let's Encrypt, which has no rate limits. When everything runs, comment that line out again and run `./vb deploy`: Caddy then obtains certificates from the real CA within about a minute.
3. **Secrets:** create the real files in `sites/test/secrets/` from the `*.example` files of `site-template/test/secrets/` and encrypt each one with `ansible-vault create` ([Appendix A](#appendix-a-secrets-and-required-variables)). Generate every value fresh for this stage (`SECRET_KEY`, Postgres and Valkey passwords, the Caddy basic-auth hash, the stage's own Garage credentials); never copy a value from another stage. SMTP and `GOOGLE_CLIENT_ID` stay empty until a mail server and an OAuth app exist for the stage. Empty means: set the value to nothing (`NAME=`) or delete the line, because `deploy.yml` refuses any file that still contains `change-me`; the same goes for the AWS values while no downsync runs. The downsync additionally needs `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_BUCKET` and `AWS_REGION` of a **read-only** IAM user (`s3:ListBucket` and `s3:GetObject` on the production bucket) that exists for this stage alone and is restricted to the stage's host with an `aws:SourceIp` condition ("The read-only AWS key" below the list shows how to create it). The production bucket also holds the database dumps, so this key can read a complete copy of all member data.
4. **DNS** for all four domains.
5. **Host:** `./vb setup test`, then `./vb deploy test` (`./vb check` works only after `setup`; [3.4](#34-set-up-the-host-vb-setup), [3.5](#35-deploy-vb-deploy)). This also brings up the stage's Garage and fixes the Postgres directory ownership.
6. **Data:** trigger a downsync ([5.5](#55-downsync)).

**The read-only AWS key.** Every stage that runs a downsync needs an IAM user of its own in the AWS account of the production bucket. The key belongs nowhere else than in the Vault file `sites/<stage>/secrets/vb-api.env.j2` of that stage (`AWS_*`) and in the AWS console. Never reuse the `S3_*` values of `sites/production/secrets/vb-api.env.j2`: that is the application's key, and it can write and delete.

1. In the AWS console open IAM, Users, Create user. Name it after the stage (for example `vb-downsync-test`) and do not grant console access.
2. Attach this inline policy. Replace `203.0.113.10/32` with the public IPv4 address of the stage's host; the condition makes a leaked key useless anywhere else. `vindobona2-at` is the name of the production bucket:

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

3. Open the user, Security credentials, Create access key, use case "Application running outside AWS". Copy both values straight into the stage's Vault file: `ansible-vault edit ../vb-access/sites/<stage>/secrets/vb-api.env.j2`, set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (`AWS_BUCKET=vindobona2-at` and `AWS_REGION=eu-central-1` stay as in the template). The secret is shown only once.
4. Run `./vb deploy <stage>`, then test without changing anything: `podman exec vb-api python scripts/downsync_prod.py --dry-run` lists what a downsync would copy. `AccessDenied` means the policy or the IP address is wrong. If the host changes its address, change it in the policy as well.

**Check the stage.** On the host: `podman ps` shows every service container `healthy`, including `vb-garage`; `podman exec vb-api alembic current` ends in `(head)`; `podman exec vb-garage /garage bucket list` lists the bucket. From the workstation: `curl -s -o /dev/null -w '%{http_code}\n' https://<domain>/` answers `200` for `api`, `intern` and `www`, and `403` for `storage` (anonymous access to the S3 root is refused, which is correct); `https://<intern domain>/config.js` shows the runtime configuration; the Dozzle address answers `401` without its login. Before the first downsync the database is empty, so nobody can log in.

**Throwaway servers.** To try the pipeline on a server that will not exist tomorrow, use the stage `test` with the `.invalid` domains and the staging CA and, if its disk is smaller than 60 GiB, `min_free_disk_gb=0` ([5.1](#51-prerequisites)): nothing real is touched, and Caddy simply cannot obtain certificates. Never use the stage `production` for this: it would put the real secrets on the throwaway server.

### 5.3 Secrets

```bash
# [workstation]
ansible-vault edit ../vb-access/sites/<stage>/secrets/vb-api.env.j2
PAGER=cat ansible-vault view ../vb-access/sites/<stage>/secrets/vb-api.env.j2
./vb deploy <stage>      # distribute the change
```

Files ending in `.env.j2` hold stage and domain dependent content; the deploy decrypts them transparently and then fills in the template variables. In `vb-access` all real files are vault-encrypted; the unencrypted `.example` files exist only here in `site-template/` and document the target structure of every stage type. `EDITOR=nano ansible-vault edit ...` selects another editor for one call. Change secrets only with `ansible-vault edit`, never by decrypting a file in place. A pre-commit hook in `vb-access` rejects any unencrypted secret file and any private key in plain text, and a hook in this repository rejects any vault file here: **this public repository never holds a vault file**. The required variables per stage type are in [Appendix A](#appendix-a-secrets-and-required-variables).

### 5.4 Garage on non-production stages

`test` and `qa` run [Garage](https://garagehq.deuxfleurs.fr/) as an S3 compatible stand-in for AWS S3; production never runs Garage. Garage joins `vb-api-pod`, like Postgres and Valkey, so `vb-api` reaches it over `localhost`: this traffic must stay inside the pod. A separate pod would only be reachable through the public domain, which does not work with rootless networking (every pod has a private loopback and there is no hairpin NAT).

- The `--single-node --default-bucket` flags create the bucket and an access key on first start. `deploy.yml` additionally sets the bucket's CORS policy (so browsers may read presigned URLs) through the real S3 API of `vb-api`'s own `boto3`, on every deploy, and denies the application key the right to create buckets.
- **Port 9000 (S3 API)** is public behind Caddy and TLS on `storage_domain`, because presigned links are fetched by the visitor's browser. `vb-api` itself talks to Garage over `http://127.0.0.1:9000` inside the pod.
- **Port 9001 (admin API)** is loopback only; reach it through an SSH tunnel with the operator key.

### 5.5 Downsync

Every non-production stage refreshes itself from production once a day (`downsync` job, one hour after the production backup): it mirrors the whole production bucket into the stage's Garage, restores the newest production dump and runs `alembic upgrade head`. With `DOWNSYNC_LIGHT=true` in `vb-api.env.j2` (or `--light` on the command line) it mirrors only the member images, the public gallery and the newest dump: about 50 MB instead of 43 GB, but archive files are then missing on the stage. On demand:

```bash
# [host] on the stage; --yes because podman exec without -it has no terminal for the question
podman exec vb-api python scripts/downsync_prod.py --yes
```

or the button `Downsync jetzt durchführen` on **System → Scheduler**. Production refuses both. The data is real, not anonymised.

### 5.6 Running the day-to-day

Everything in [2.1](#21-first-checks-read-only) to [2.7](#27-the-vb-commands-you-will-need) applies to every stage; only the stage name changes.

### 5.7 Local development environment

The development stage runs independently of Ansible and Vault, directly on the developer host, with its own Quadlets (live-mounted source code, Vite dev servers). The templates live under `dev/`:

```
dev/quadlets/api/      vb-api, vb-api-pg, vb-api-valkey, vb-api-worker and the pod
dev/quadlets/intern/   vb-intern (Vite dev server)
dev/quadlets/www/      vb-www (Vite dev server)
dev/quadlets/garage/   vb-garage (S3 stand-in) and its own pod
dev/postgres/          pg_hba.conf for vb-api-pg
dev/env/               *.env.example for every container
```

**Setup**

1. Copy every `*.example` of `dev/quadlets/` (without `.example`) to `~/.config/containers/systemd/vb/<component>/`, every `*.example` of `dev/env/` to `~/.env/`, `config/garage/garage.toml` to where the Garage Quadlet's config volume points, and `dev/postgres/pg_hba.conf` to `~/data/vb-api/postgres-config/pg_hba.conf`.
2. Replace the placeholders in the Quadlets: `<path-to-vb-fastapi-vue>` (the checkout containing the repositories), `<your-mail-dev-domain>`, `<your-garage-dev-domain>`, and the Garage data and meta directories.
3. Replace every `change-me` in the env files.

**Garage on development.** Here Garage runs in its **own pod** (`vb-garage-pod`), reached through a local domain that resolves to the host gateway (the `AddHost` lines of the pod files). The stages that this repository deploys keep Garage inside `vb-api-pod` ([5.4](#54-garage-on-non-production-stages)).

**Ports and routing.** Development listens on `127.0.0.1`: API 20000, intern 20001, www 20002, Garage 9000 and 9001. For real domain names (to test cookies and CORS as in production), set up your own DNS and reverse proxy, for example Caddy; that part is not in this repository.

**First start**

```bash
# [host] build the development image (no Quadlet for this on purpose)
podman build --target dev -t vb-api:dev <path-to-vb-fastapi-vue>/vb-api

systemctl --user daemon-reload
systemctl --user start vb-garage-pod vb-api-pod vb-intern vb-www

# create the schema
podman exec vb-api alembic upgrade head

# load real data (needs ~/.env/vb-api-aws-prod.env, see the vb-api README)
podman exec vb-api python scripts/downsync_prod.py --yes
```

Never run `pip install` or `npm install` on the host; use `podman exec`.

---

## Appendix A: Secrets and required variables

Which variables need a real value differs between production (real AWS S3, SMTP and Google) and `test`/`qa` (own Garage, usually no mail server or OAuth app). "Optional" means the setting has a working default in `app/core/config.py`. The files live in `sites/<stage>/secrets/` of `vb-access`; the templates are in `site-template/<stage>/secrets/` here.

| File | Variable(s) | Production | `test`/`qa` |
|---|---|---|---|
| `caddy.env` | `LOGGING_USER`, `LOGGING_PASSWORD_HASH` | required | required |
| `vb-api-pg.env` | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | required | required |
| `vb-api.env.j2` | `SECRET_KEY` | required | required |
| `vb-api.env.j2` | `DATABASE_URL` | required (role `vb_app`, split off from the bootstrap superuser by `config/postgres/split-app-role.sql`) | required |
| `vb-api.env.j2` | `VALKEY_URL` | required | required |
| `vb-api-valkey.env` | `VALKEY_PASSWORD` | required, must match `VALKEY_URL` | required |
| `vb-api.env.j2` | `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` | required (real AWS) | required (this stage's Garage key, matching `vb-garage.env.j2`) |
| `vb-api.env.j2` | `S3_ENDPOINT_URL`, `S3_PUBLIC_ENDPOINT_URL` | not set (AWS default) | required (`http://127.0.0.1:9000` and `https://{{ storage_domain }}`) |
| `vb-api.env.j2` | `S3_REGION` | required (`eu-central-1`) | required (`us-east-1`, must match `s3_region` in `config/garage/garage.toml`) |
| `vb-api.env.j2` | `S3_PATH_*` | optional | optional |
| `vb-api.env.j2` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_BUCKET`, `AWS_REGION` | not applicable (downsync refuses on production) | required for the downsync (read-only production credentials) |
| `vb-api.env.j2` | `DOWNSYNC_LIGHT` | not applicable | optional (`true`: the downsync mirrors only the member images, the public gallery and the newest dump, not the archive) |
| `vb-api.env.j2` | `BACKUP_ENABLED`, `BACKUP_HOUR`, `BACKUP_RETENTION_DAYS` | `true`; daily at `BACKUP_HOUR` | optional (`false`: a disposable stage's own backups have little value) |
| `vb-api.env.j2` | `SMTP_*` | required | not set |
| `vb-api.env.j2` | `GOOGLE_CLIENT_ID` | required (a public identifier, not a secret) | not set |
| `vb-api.env.j2` | `AKTIVITAS_ACCOUNT_HOLDER`, `AKTIVITAS_IBAN`, `AKTIVITAS_BIC`, `AKTIVITAS_FEE_TEXT` | required (payment page; `deploy.yml` refuses a file without them) | required (a well-formed sample is enough: an IBAN that passes the checksum, for example `AT61 1904 3002 3457 3201`, and a BIC of 8 or 11 characters, for example `BKAUATWW`) |
| `vb-intern.env.j2` | `API_BASE_URL`, `PASSWORD_MIN_LENGTH`, `APP_ENVIRONMENT`, `GOOGLE_CLIENT_ID` (optional) | required | required |
| `vb-www.env.j2` | `API_BASE_URL` | required | required |
| `vb-garage.env.j2` | `GARAGE_RPC_SECRET`, `GARAGE_ADMIN_TOKEN`, `GARAGE_DEFAULT_ACCESS_KEY`, `GARAGE_DEFAULT_SECRET_KEY`, `GARAGE_DEFAULT_BUCKET` | file does not exist | required |

`deploy.yml` refuses a file that still contains a placeholder value, a Valkey password that differs between its two files, or a `PASSWORD_MIN_LENGTH` that differs between API and frontend.

**Changing a secret.** Edit the file with `ansible-vault edit`, then run `./vb deploy <stage>`. The deploy detects which environment files changed and restarts exactly the pods that read them.

| Secret | Notes |
|---|---|
| `SECRET_KEY` | Signs the short-lived access tokens (15 minutes). A new key makes every issued access token invalid; browsers fetch a new one with their refresh cookie, so **users stay logged in**. Use a random value of at least 32 characters without quotes (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`). |
| `DATABASE_URL` (password of `vb_app`) | The deploy sets the new password on the database role. Use 16 or more characters from `A-Za-z0-9._~-`. |
| `VALKEY_PASSWORD` and `VALKEY_URL` | Change both files in the same step; the deploy checks that they match. |
| Dozzle login | Generate a hash with `podman run --rm docker.io/library/caddy:2.11 caddy hash-password` and put it into `LOGGING_PASSWORD_HASH` in `caddy.env`, without quotes. Without Podman on the workstation any bcrypt hash works, for example `python3 -c "import bcrypt, getpass; print(bcrypt.hashpw(getpass.getpass().encode(), bcrypt.gensalt()).decode())"` (the module usually comes with Ansible). |
| AWS keys, SMTP password | Change them in the AWS console or at the mail provider first, then here, then `./vb deploy`. |

## Appendix B: Pitfalls

Collected from real incidents.

- **Pod options apply only when the pod is created.** `./vb deploy` starting an already running pod does not re-create it with new options. After a change to a pod-level option, restart the pod unit itself (`systemctl --user restart vb-api-pod.service`).
- **Restart pods through the pod unit, not a container** ([2.2](#22-restart-a-service-or-pod)).
- **Pod exit policy.** The pods set `--exit-policy=continue` through `PodmanArgs`. With Quadlet's default (`stop`) a nightly auto-update, which stops and recreates one container, would take the whole pod down for good.
- **Rootless ownership of the Postgres directory.** After `initdb`, the directory belongs to the mapped `postgres` user (UID 999), not to `service`. Any host-side `mv`, `rm` or `cp` needs `podman unshare`; a fresh directory needs `podman unshare chown -R 999:999 ~/data/vb/<stage>/postgres` and `chmod 700` before the very first start (`deploy.yml` does this on first setup).
- **Postgres mount.** The volume mounts one level above the versioned data directory (`~/data/vb/<stage>/postgres:/var/lib/postgresql`); Postgres creates its own subdirectory.
- **The first start of Postgres is slow.** It creates its data directory and took about 40 seconds on a small server; the deploy waits up to three minutes for the database role. If a deploy still stops there, run it again.
- **Let's Encrypt rate limits.** Five duplicate certificates per identical set of domains per week. Many reinstalls or restarts with wrong DNS can exhaust it; Caddy then cannot get a certificate for that set until the window passes. Test with `curl -k --resolve ...` instead of retrying, or use the staging CA on throwaway stages.
- **The application images are x86_64 only.** An ARM server fails at the start of `./vb deploy` with a clear message; order an x86_64 plan.
- **The provider's root password may be expired.** The first SSH login then demands a new password, and Ansible cannot answer. Log in once by hand, set a new one, and use that ([3.2](#32-install-the-operating-system)).
- **The preview of `setup` lists one change that is not real.** On a host that is up to date, `./vb check` shows one `ufw` change on Debian: the preview reports the removal of the old rate-limit rule even when it is already gone. The real run reports no change.
- **Two runs within a minute.** Ansible keeps SSH connections open for 60 seconds. The proof steps of `setup_vps.yml` therefore open their own connection, so an earlier login cannot stand in for the key being proven; do not remove that.
- **Never edit `operator_public_keys` and then run Ansible by hand.** Exactly the listed keys remain on the host. `./vb setup` refuses a list that does not contain the key it connects with; a manual `ansible-playbook` run does not.
- **SELinux.** On Red Hat and SUSE family hosts the Quadlets' bind mounts carry `:Z`/`:z`; Dozzle runs without label confinement. Some provider images ship with SELinux in permissive mode (logging, not enforcing); the stack was also verified with enforcing.
- **Migrations run once per rollout.** Only `vb-api` migrates; `vb-api-worker` has `SKIP_MIGRATIONS=true`. Alembic has no distributed lock, so two migrating containers would be a real risk.
- **Password login is off.** After `./vb setup` nothing but the operator key gets you onto the host. Losing the key means the provider's rescue mode ([Appendix C](#appendix-c-access-and-handover)).
- **A rebuilt server with an old address.** SSH reports a changed host key. Compare the fingerprint with the provider console first, then `ssh-keygen -R <address>`.

## Appendix C: Access and handover

Three kinds of information keep this system running. They are kept apart on purpose:

| Kind | Where | Who can read it |
|---|---|---|
| How the system is built and operated | this repository and the three application repositories (public) | everyone |
| The data of this installation: server and domains, the secrets, the operator key | `vb-access`, folder `sites/` (vault-encrypted, private) | owners of the organization with the Vault password |
| The accounts that are not machine-readable: hosting, domain, AWS console, Google, GitHub owners, with logins, second factors and recovery codes | `vb-access`, the note `credentials.md` (free text, vault-encrypted, private) | the same people |

**A successor needs three things, and nothing else:**

1. The address of the GitHub organization: [K-O-St-V-Vindobona-II](https://github.com/K-O-St-V-Vindobona-II).
2. The login of the shared owner account of the organization (the pool account), including the recovery codes of its second factor. It gives access to the private repository `vb-access`.
3. The Vault password, handed over through a different channel than the login.

With these, [the handover sheet](HANDOVER.md) leads to a working shell on the production host in a few minutes, and chapter 3 rebuilds the whole system on a new server. Everything else, including the logins of the hosting, AWS and domain accounts, is in the access note. Read it with `PAGER=cat ansible-vault view credentials.md` in `vb-access`.

**The chain of access.**

```
owner login ──> private repository vb-access ──(Vault password)──> operator_key.vault ──> SSH to the host
                                  |                                      secrets/ ──> ./vb deploy
                                  └──(Vault password)──> credentials.md (accounts, 2FA, recovery codes)
```

**The Vault password** is the only secret a person has to remember or keep safe. Use a long random one (at least 20 characters; the tool refuses less when it creates one), keep it in a password manager and on paper in a safe place, never in a repository, mail or chat. Keep it with at least one other trusted person, and keep **at least two owners** of the GitHub organization.

**The operator key.** One key pair serves for everything on the host. Its private half is `sites/<stage>/operator_key.vault` in `vb-access`; its public half is the entry under `operator_public_keys` in `sites/<stage>/inventory.ini`, and that list is exactly what `./vb setup` installs on the host. To replace the key, for example after a person left, run `./vb rotate-key production`: it adds the new key, checks that it logs in, stores it, and only then removes the old one from the host. Commit the changed files in `vb-access` afterwards.

**Add a second operator.** Two ways: give the person access to `vb-access` and the Vault password (they then use the same key), or give them a key of their own. For the second way the person creates a key pair on their workstation (`ssh-keygen -t ed25519`) and sends only the `.pub` file; add its single line to `operator_public_keys` in `sites/production/inventory.ini` (the list is a JSON list on one line, keys separated by commas) and run `./vb setup production`. Remove the entry and run `./vb setup` again to take the access away.

**If no key works any more** (the operator key is not on the host, or `vb-access` or the Vault password is lost): use the provider's console or rescue system. Boot the rescue system, mount the root file system, put a new public key into `/home/service/.ssh/authorized_keys` and `/home/admin/.ssh/authorized_keys`, set owner and group to that user's numeric ids (`ls -ln /home/service`) and the mode (`700` for the directory, `600` for the file), on SELinux hosts run `restorecon -R` on both `.ssh` directories, unmount and reboot into the normal system. The provider's root password still works at the provider's console. The exact steps differ per provider and for encrypted or LVM root file systems. Afterwards add the new key to `operator_public_keys` and run `./vb setup` so the lists match.

**If GitHub is not reachable** or the organization is lost, the offline copy helps: keep a clone of `vb-deploy` and `vb-access` (the private one holds only vault-encrypted files) on an encrypted stick in a safe place and renew it after every change in `vb-access`.

**Checklist on a handover**

1. The new person is owner of the GitHub organization and knows the Vault password.
2. The new person ran [the handover sheet](HANDOVER.md) and reached the host with `./vb ssh production`.
3. Every second factor works for the new person; the recovery codes are in the access note.
4. The role mailbox of every account reaches the new person.

**Checklist when a person leaves**

1. Remove them from the GitHub organization if they have a personal account there. If they knew the login of the shared owner account, change its password, renew its second factor and recovery codes, and write the new codes into the access note.
2. Replace the operator key: `./vb rotate-key production`, and commit the result in `vb-access`. If they had a key of their own, also delete its line from `operator_public_keys` in `sites/production/inventory.ini` and run `./vb setup production`.
3. Change the Vault password: `ansible-vault rekey` on every vault file in `vb-access` (`credentials.md`, `sites/*/operator_key.vault`, `sites/*/secrets/*`; the command is in the `vb-access` README), commit and push the changed files in `vb-access`, and tell the others in person or over a channel you trust. Old versions of the files stay readable in the Git history with the old password, which is why step 4 is not optional.
4. Rotate the secrets they knew ([Appendix A](#appendix-a-secrets-and-required-variables), "Changing a secret"), starting with the AWS keys and the SMTP password, and the Dozzle login if they knew it.
5. Take them out of the accounts in the access note (hosting, domain, AWS, Google), and update the note. Renew the offline copy on the stick if they had one.

## Appendix D: Case studies

Typical situations, in the order of the steps you take.

### 1. You take over the system (day one)

1. Get the three things from your predecessor: organization address, owner login with recovery codes, Vault password.
2. Follow [the handover sheet](HANDOVER.md): install the tools, clone `vb-deploy` and `vb-access` next to each other.
3. Prove access: `./vb ssh production`, then `podman ps` (all service containers `healthy`).
4. Read the access note (`PAGER=cat ansible-vault view credentials.md`) and make sure you can open each account; check the second factors.
5. Run `./vb check production` and read the output once: this is what a normal run looks like.
6. Skim chapter 2 (when something is down) and [2.4](#24-the-database-is-damaged-backup-and-restore); both matter most in an emergency.

### 2. Someone leaves

Follow the checklist "when a person leaves" in [Appendix C](#appendix-c-access-and-handover). The order matters: first remove access (organization, key), then change the Vault password, then rotate what they knew.

### 3. The server is gone

The host is lost (hardware, provider, an update). The data is safe: the database is in the nightly dumps in S3, the files were never on the host, the configuration is in Git.

1. Order a new x86_64 server with Debian 13 ([3.2](#32-install-the-operating-system)).
2. If the production IAM user in AWS is restricted to an IP address, allow the new host's address first, otherwise the restore fails ([3.1](#31-preparation-before-the-old-host-is-touched)).
3. Copy the site: `cp -r ../vb-access/sites/production ../vb-access/sites/production-new`, put the new IP in the new `inventory.ini`.
4. `./vb setup production-new`, then `./vb deploy production-new`.
5. Restore the newest dump that was created before the failure ([3.7](#37-restore-the-database-on-the-new-host), name the dump).
6. Point DNS at the new host and wait for the certificates ([3.8](#38-switch-dns-and-obtain-certificates)); verify ([3.9](#39-verification)).
7. Make `production-new` the site `production` ([3.11](#311-clean-up-the-old-host)).

### 4. Move to another provider

Same as case 3, but the old host still runs: take the maintenance window of chapter 3 ([3.6](#36-maintenance-window-freeze-the-old-host-and-take-the-final-backup)), so the final backup is current and no data is lost. Lower the DNS TTL a day before.

### 5. The Vault password is lost

Without it nothing in `vb-access` can be opened: not the operator key, not the secrets, not the access note. The running system is not affected. Recovery: another owner may still know the password (that is why two people should); otherwise start fresh: recreate the secrets from the provider accounts, `./vb init production` (after you set the old `sites/production` aside) makes a new data directory with a new key and a new password, and the provider's console puts the new public key on the host ([Appendix C](#appendix-c-access-and-handover), "If no key works any more"). Plan a maintenance window; rotate every secret.

### 6. A secret leaked

Change it at its source first (AWS console, mail provider), then in `vb-access` with `ansible-vault edit`, then `./vb deploy production` ([Appendix A](#appendix-a-secrets-and-required-variables)). A leaked `SECRET_KEY` is replaced the same way; users stay logged in. A leaked operator key or Vault password means: `./vb rotate-key production`, a new Vault password (`ansible-vault rekey` on every vault file), and a look at the logs for the time since the leak.

### 7. You need a test copy of the system

Use the stage `test` with its own server: [5.2](#52-set-up-a-test-or-qa-stage). For a short experiment with throwaway servers use the `.invalid` domains and the staging CA; never copy the production secrets to such a server.

## Appendix E: Frequently asked questions

**Why are there two operations repositories?** `vb-deploy` is code and can be public; `vb-access` holds what is specific and sensitive and is private. Anyone can read how the system is built, and only owners of the organization can read the data.

**Why does `vb` live in `vb-deploy` and not in `vb-access`?** It contains no secrets, only the logic, and it is useful for anyone who runs the system with their own private repository. `vb-access` only stores data.

**Can I use my own SSH key?** For daily work use `./vb ssh`, which uses the operator key from the vault. A personal key can be added to `operator_public_keys` ([Appendix C](#appendix-c-access-and-handover)), but it is not necessary.

**Why does `admin` have `sudo` without a password?** Nobody can log in as `admin` without the operator key, and passwords are locked, so a `sudo` password would protect nothing that the key does not already protect, and it would be one more secret to hand over.

**Why must the server be x86_64?** The application images are built for `linux/amd64` only. `./vb deploy` stops with a message on ARM.

**Do I need Python or any special software?** Ansible (which includes Python 3), `git`, an SSH client, `curl` and `dig` for the checks, and `sshpass` for the very first run on a new host. `./vb` needs nothing else.

**What if GitHub is down?** The running system does not depend on GitHub. For changes you need your offline copy of the two repositories ([Appendix C](#appendix-c-access-and-handover)).

**Is it safe to run `./vb deploy` at any time?** Yes. It only changes what differs and restarts only the pods whose files changed. `./vb check production` shows beforehand what that would be.

**What does `./vb check` change?** Nothing. It runs both playbooks in preview mode.

**How do I find out which version runs?** `podman inspect vb-api --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'` on the host gives the commit (more in [2.3](#23-roll-back-a-faulty-release)), and `podman exec vb-api alembic current` the database revision.

**Where are the logs?** `journalctl --user -u <service>`, `podman logs <container>`, or Dozzle in the browser ([2.1](#21-first-checks-read-only)).

**What happens to the access when I run `./vb setup` with a different key list?** The host ends up with exactly the keys in the list. The tool refuses the run if your own key is not in it.

**Where do I report a mistake in this handbook?** Fix it in a pull request on `development` of `vb-deploy`; the English and the German file must keep the same structure (a test checks it).
