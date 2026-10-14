# Übergabeblatt

[English](HANDOVER.md) | [Deutsch](HANDOVER.de.md)

Eine Seite für die Person, die das Vindobona-II-System (`vindobona2.at`) übernimmt. Sie enthält keine Geheimnisse. Das vollständige Handbuch ist das [README](README.de.md).

## Was du bekommst

Drei Dinge, getrennt übergeben:

1. **Die Adresse der GitHub-Organisation:** <https://github.com/K-O-St-V-Vindobona-II>
2. **Das Login des gemeinsamen Owner-Kontos der Organisation** (Pool-Konto), einschließlich der Wiederherstellungscodes seines zweiten Faktors.
3. **Das Vault-Passwort.** Es öffnet alles, was privat ist: den Schlüssel zum Server, die Secrets des Systems und die Liste aller anderen Accounts.

Das ist alles. Alle weiteren Logins (Hosting, Domain, AWS, Google) stehen in einer Notiz, die du in Schritt 3 liest.

## Bevor du anfängst: Werkzeuge auf deinem Computer

Linux, macOS oder Windows mit WSL. Du brauchst `git`, einen SSH-Client, **Ansible** (bringt Python 3 mit), `curl` und `dig` für die Prüfung der Seiten und ihrer DNS-Einträge und, nur für den Neuaufbau eines Servers von Grund auf, `sshpass`. Unter Debian oder Ubuntu:

```bash
sudo apt install git openssh-client ansible curl dnsutils sshpass
```

Auf einem anderen System installierst du dieselben Werkzeuge mit dessen Paketverwaltung; [3.3 im README](README.de.md#33-voraussetzungen-am-arbeitsplatz) nennt die Einzelheiten.

## Schritt 1: die beiden Repositories holen

Das Repository `vb-access` ist privat. GitHub erlaubt das Klonen nur mit einem SSH-Schlüssel, der zu deinem GitHub-Account gehört. Hast du noch keinen (`ls ~/.ssh/*.pub` zeigt nichts), lege einen an; er hat mit dem Server nichts zu tun:

```bash
# [Arbeitsplatz] der Schlüssel für GitHub; drücke Enter für den Dateinamen und wähle, wenn gefragt, eine Passphrase (empfohlen)
ssh-keygen -t ed25519
cat ~/.ssh/id_ed25519.pub
```

Melde dich bei GitHub mit dem Owner-Login an (oder mit deinem eigenen Account, sobald ein Owner dich zum Owner der Organisation gemacht hat), öffne *Settings → SSH and GPG keys → New SSH key* und füge die Zeile ein. Dann klonst du **beide Repositories nebeneinander** in einen leeren Ordner deiner Wahl (zum Beispiel `~/work`):

```bash
# [Arbeitsplatz]
git clone git@github.com:K-O-St-V-Vindobona-II/vb-deploy.git
git clone git@github.com:K-O-St-V-Vindobona-II/vb-access.git
```

## Schritt 2: den Produktions-Host erreichen

```bash
# [Arbeitsplatz]
cd vb-deploy
./vb ssh production
```

Das Werkzeug fragt nach dem **Vault-Passwort**, öffnet den Betriebsschlüssel aus `vb-access` im Arbeitsspeicher und meldet dich am Produktions-Host an. Bei falschem Passwort bricht es mit `Cannot decrypt operator_key.vault: wrong vault password or damaged file.` ab; führe den Befehl einfach noch einmal aus. Prüfe, ob das System gesund ist:

```bash
# [Host]
podman ps        # erwartet: die 8 Dienst-Container "healthy" (Liste in README 1.4); die Helfer mit der Endung -pod-infra zeigen nur "Up"
exit
```

Beim ersten Mal merkt sich SSH den Host-Schlüssel, ohne zu fragen. Meldet es später `REMOTE HOST IDENTIFICATION HAS CHANGED`, höre auf und lies [Anhang B](README.de.md#anhang-b-fallstricke), bevor du etwas entfernst.

Wenn das klappt, hast du vollen Betriebszugang. `./vb check production` zeigt eine Vorschau, was ein Deployment ändern würde; es ändert nichts und dauert etwa eine Minute. Ein gesundes System beendet beide Teile (erst `setup_vps.yml`, dann `deploy.yml`) mit `failed=0` und `changed=0` in der `PLAY RECAP`-Zeile (unter Debian kann der erste Teil bei einer `ufw`-Aufgabe `changed=1` zeigen, eine bekannte Falschmeldung, siehe [Anhang B](README.de.md#anhang-b-fallstricke)).

## Schritt 3: die Zugangs-Notiz lesen

```bash
# [Arbeitsplatz]
cd ../vb-access
PAGER=cat ansible-vault view credentials.md
```

`PAGER=cat` sorgt dafür, dass lange Zeilen vollständig erscheinen; der Standard-Pager schneidet sie an der Breite des Terminals ab.

Sie listet jeden Account, von dem das System abhängt (Hosting, Domain, AWS, Google, GitHub), mit Login, zweitem Faktor und dem Ort der Wiederherstellungscodes. Stelle sicher, dass du jeden öffnen kannst und dass die Rollen-Postfächer dich erreichen.

## Wie es weitergeht

| Situation | Lesen im [README](README.de.md) |
|---|---|
| Etwas ist ausgefallen oder kaputt | [2. Wenn etwas ausfällt](README.de.md#2-wenn-etwas-ausfällt) |
| Der Server ist weg oder muss umziehen | [3. Neuaufbau oder Umzug auf einen neuen Server](README.de.md#3-neuaufbau-oder-umzug-auf-einen-neuen-server) |
| Eine Person scheidet aus | [Anhang C](README.de.md#anhang-c-zugang-und-übergabe), "wenn eine Person ausscheidet" |
| Du willst wissen, wann die letzte Datenbanksicherung lief | [2.4](README.de.md#24-die-datenbank-ist-beschädigt-sicherung-und-wiederherstellung): `podman exec vb-api python scripts/restore_db.py --list` (nur lesend) |
| Du willst das System verstehen | [1. Architektur-Übersicht](README.de.md#1-architektur-übersicht) |
| Typische Situationen, Schritt für Schritt | [Anhang D: Fallbeispiele](README.de.md#anhang-d-fallbeispiele) |

## Worum du dich kümmern musst

- Bewahre das **Vault-Passwort** sicher auf (Passwortmanager und eine Kopie auf Papier an einem sicheren Ort) und teile es mit mindestens einer weiteren vertrauenswürdigen Person.
- Halte **mindestens zwei Owner** in der GitHub-Organisation.
- Halte eine **Offline-Kopie** beider Repositories auf einem verschlüsselten Stick, erneuert nach jeder Änderung in `vb-access`, für den Fall, dass GitHub nicht erreichbar ist.
