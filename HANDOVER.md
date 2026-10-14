# Handover sheet

[English](HANDOVER.md) | [Deutsch](HANDOVER.de.md)

One page for the person who takes over the Vindobona II system (`vindobona2.at`). It contains no secrets. The full handbook is the [README](README.md).

## What you receive

Three things, handed over separately:

1. **The address of the GitHub organization:** <https://github.com/K-O-St-V-Vindobona-II>
2. **The login of the shared owner account of the organization** (the pool account), including the recovery codes of its second factor.
3. **The Vault password.** It opens everything that is private: the key to the server, the secrets of the system and the list of all other accounts.

That is everything. All other logins (hosting, domain, AWS, Google) are in a note you read in step 3.

## Before you start: tools on your computer

Linux, macOS or Windows with WSL. You need `git`, an SSH client, **Ansible** (it brings Python 3), `curl` and `dig` for the checks of the sites and their DNS and, only for building a new server from scratch, `sshpass`. On Debian or Ubuntu:

```bash
sudo apt install git openssh-client ansible curl dnsutils sshpass
```

On another system install the same tools with its package manager; [3.3 in the README](README.md#33-prerequisites-on-the-workstation) lists the details.

## Step 1: get the two repositories

The repository `vb-access` is private. GitHub only lets you clone it with an SSH key that belongs to your GitHub account. If you do not have such a key yet (`ls ~/.ssh/*.pub` lists none), create one; it has nothing to do with the server:

```bash
# [workstation] the key for GitHub; press Enter for the file name and choose a passphrase when asked (recommended)
ssh-keygen -t ed25519
cat ~/.ssh/id_ed25519.pub
```

Sign in to GitHub with the owner login (or with your own account, once an owner has made you an owner of the organization), open *Settings → SSH and GPG keys → New SSH key* and paste the line. Then clone **both repositories next to each other** in one empty folder of your choice (for example `~/work`):

```bash
# [workstation]
git clone git@github.com:K-O-St-V-Vindobona-II/vb-deploy.git
git clone git@github.com:K-O-St-V-Vindobona-II/vb-access.git
```

## Step 2: reach the production host

```bash
# [workstation]
cd vb-deploy
./vb ssh production
```

The tool asks for the **Vault password**, opens the operator key from `vb-access` in memory and logs you in to the production host. With a wrong password it stops with `Cannot decrypt operator_key.vault: wrong vault password or damaged file.`; run the command again. Check that the system is healthy:

```bash
# [host]
podman ps        # expect: the 8 service containers "healthy" (list in README 1.4); the helpers ending in -pod-infra only show "Up"
exit
```

The first time, SSH remembers the host key without asking. If it ever reports `REMOTE HOST IDENTIFICATION HAS CHANGED`, stop and read [Appendix B](README.md#appendix-b-pitfalls) before you remove anything.

If this works, you have full operating access. `./vb check production` previews what a deployment would change; it changes nothing and takes about a minute. A healthy system ends both of its parts (first `setup_vps.yml`, then `deploy.yml`) with `failed=0` and `changed=0` in the `PLAY RECAP` line (on Debian the first part may show `changed=1` at a `ufw` task, a known false report, see [Appendix B](README.md#appendix-b-pitfalls)).

## Step 3: read the access note

```bash
# [workstation]
cd ../vb-access
PAGER=cat ansible-vault view credentials.md
```

`PAGER=cat` makes sure long lines are shown in full; the default pager cuts them at the width of the terminal.

It lists every account the system depends on (hosting, domain, AWS, Google, GitHub) with login, second factor and the place of the recovery codes. Make sure you can open each of them, and that the role mailboxes reach you.

## Where to go next

| Situation | Read in the [README](README.md) |
|---|---|
| Something is down or broken | [2. When something is down](README.md#2-when-something-is-down) |
| The server is gone or has to move | [3. New build or migration to a new server](README.md#3-new-build-or-migration-to-a-new-server) |
| A person leaves | [Appendix C](README.md#appendix-c-access-and-handover), "when a person leaves" |
| Check when the last database backup ran | [2.4](README.md#24-the-database-is-damaged-backup-and-restore): `podman exec vb-api python scripts/restore_db.py --list` (read-only) |
| You want to understand the system | [1. Architecture overview](README.md#1-architecture-overview) |
| Typical situations, step by step | [Appendix D: Case studies](README.md#appendix-d-case-studies) |

## What you must look after

- Keep the **Vault password** safe (a password manager and a paper copy in a safe place) and share it with at least one other trusted person.
- Keep **at least two owners** in the GitHub organization.
- Keep an **offline copy** of both repositories on an encrypted stick, renewed after every change in `vb-access`, for the case that GitHub is not reachable.
