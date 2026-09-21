# macOS Runtime Architecture

Prime Observer's supported multi-user runtime is a hybrid launchd design:

- four core machine services belong to the `system` bootstrap domain and run
  as the unprivileged `mbeason` account
- optional provider refresh and Operator Assistant work remain per-user
  LaunchAgents in `gui/501`
- restore, rebuild, reconciliation, and operator-feedback commands remain
  interactive/manual

The system jobs are source-controlled under `launchd/system/`. After the
one-time privileged installation, they do not run
as root, contain secrets, inherit an interactive shell, or depend on the login
Keychain. `bin/prime_runtime.sh` is the supported install, start, stop, restart,
status, and rollback surface.

## Lifecycle facts

macOS launchd distinguishes these concepts:

- The foreground console user is the Aqua session currently on screen.
- A logged-in user may retain an Aqua session while another user's session is
  foreground through Fast User Switching.
- `gui/<uid>` addresses that user's GUI login domain. It is created by GUI
  login and disappears when the GUI login session ends.
- `user/<uid>` is a separate background user domain. It may exist without a
  logged-in user, but a plist in `~/Library/LaunchAgents` is still loaded as
  part of user login and does not provide a boot-time machine-service owner.
- `system` is the machine bootstrap domain. `/Library/LaunchDaemons` jobs can
  use `UserName` and `GroupName` to drop privileges before executing.

The previously installed Prime jobs were all in `gui/501`. Live inspection on
macOS 27.0 showed Matthew and a second local account logged in concurrently,
with Prime's collector, transform, HTTP server, optional refresh, and backup
still owned only by `gui/501`. Collector and transform run counts and SQLite and
artifact timestamps continued advancing while both sessions existed. This
proves that Fast User Switching does not stop Matthew's retained GUI domain; it
does not make those jobs survive Matthew's full logout or start before his first
login after reboot.

The resulting lifecycle is:

| Condition | Core system daemons | Optional LaunchAgents |
| --- | --- | --- |
| Matthew logged in, not foreground | Continue | Continue while his GUI session exists |
| Another user foreground | Continue; no second instance | Continue while Matthew remains logged in |
| Screen locked | Continue | Continue while Matthew remains logged in |
| Matthew fully logs out | Continue | Stop and become stale/unavailable |
| Login window with no GUI users | Continue when the data volume and network are available | Not loaded |
| Reboot before Matthew logs in | Start from the system domain after boot/data-volume availability | Not loaded |

Sleep is separate from login state. `StartInterval` firings missed during sleep
are not replayed; `StartCalendarInterval` is coalesced and fires after wake.

## Component classification

| Component | Classification | Owner and reason |
| --- | --- | --- |
| Collector | Core machine service | `system`, as `mbeason`; sole SQLite writer path |
| Deterministic transform | Core machine service | `system`, as `mbeason`; local files and SQLite only |
| HTTP server | Core machine service | `system`, as `mbeason`; exactly one loopback-only listener |
| Local storage backup | Core machine service | `system`, as `mbeason`; daily verified local backup, no iCloud dependency |
| Mesh Signal consumption | Optional context provider within transform | Read-only; missing/inaccessible source emits bounded unavailable context and cannot stop transform |
| NextDNS refresh | Optional context provider | `gui/501`; credentials remain outside plist and stale/unavailable is non-fatal |
| Cloudflare/Internet Conditions | Optional context provider | Same fail-safe refresh LaunchAgent |
| APS/power context | Optional context provider | Same fail-safe refresh LaunchAgent |
| Application probes | Optional context provider | Same fail-safe refresh LaunchAgent |
| OpenRouter synthesis worker | Optional context provider | `gui/501`; a missing provider/session preserves deterministic fallback and last-known-good output |
| iCloud backup replication | Optional context provider | Not part of the system backup job; current TCC denial remains separate from local restore readiness |
| Restore/rebuild/ingest, explicit investigation, impact feedback | Interactive/manual | Potentially destructive or operator-directed work does not belong in unattended launchd execution |

## Session-bound dependency audit

The four core jobs use absolute repository paths and `/usr/bin/python3` (Apple's
Python 3.9.6 on the audited host). The collector wrapper also uses an absolute
interpreter path. Core collection calls absolute `/sbin/ping` and
`/usr/sbin/traceroute`; optional Ookla collection still follows the existing
launchd `PATH` behavior and is not changed by this migration.

No core path calls the `security` CLI, login Keychain, AppleScript, `osascript`,
Finder, clipboard, notifications, GUI applications, an interactive credential
prompt, or shell startup files. The jobs do not require inherited `HOME` or a
relative working directory. Storage uses `Path.home()` only to derive Matthew's
local Application Support and optional iCloud destinations; the daemon's
configured UID resolves that home consistently.

The system transform may read the ignored `.env.mesh` and the owner-only Mesh
Signal artifact/history as optional, read-only evidence. Failure is projected
as unavailable/stale context. It does not change network health or fail the
core transform.

## Credentials

No credential is required by the four core system services.

Optional credentials remain in ignored repository-local files:

- `.env.nextdns`: profile identifier and API key
- `.env.cloudflare`: API token and provider scope
- `.env.openrouter`: API key
- `.env.application_experience`, when configured: probe configuration
- `.env.mesh`: read-only Mesh Signal path configuration, not router credentials

These files must be owned by `mbeason` and mode `0600`. They are never copied to
a plist, source control, or a system-owned secret store. The audit found no
Prime runtime use of the login Keychain. Mesh Signal may use its own credential
mechanism, but Prime only consumes its minimized artifact/history and never
contacts the router.

## Filesystem and privilege model

The core daemon runs as `mbeason:staff`, not root. That preserves existing
ownership and access for:

- repository and code: `/Users/mbeason/Projects/prime-observer`
- authoritative database: `data/prime_observer.db`
- optional CSV exports: `data/bakeoff_*.csv`
- generated dashboard artifacts: `viz/`
- runtime logs: `logs/`
- local verified backups:
  `/Users/mbeason/Library/Application Support/Prime Observer/Backups`
- optional Mesh Signal evidence under Matthew's Application Support or its
  explicitly configured path

No directory is made globally writable. Installed daemon plists are
`root:wheel` mode `0644`, as required for `/Library/LaunchDaemons`. Local backup
directories remain mode `0700`; backup databases and manifests remain `0600`.
The repository stays writable only by Matthew under its existing ownership.

The daemon intentionally does not use Desktop, Documents, iCloud Drive, or
other TCC-gated locations for core work. The local backup job passes
`--no-replicate`. iCloud replication remains optional and may report
`permission_denied` without changing local backup health or restore readiness.

## HTTP access and single-instance ownership

The system HTTP plist binds `127.0.0.1:8000`. It is reachable by browser
sessions on this Mac, including another foreground local user, but is not
advertised on LAN interfaces. No firewall change is required. This narrows the
legacy listener, which bound all interfaces even though documented usage was
`http://localhost:8000`.

Only the system domain owns core labels after installation. Installation:

1. boots out and disables the four legacy `gui/501` labels
2. moves their plists out of `~/Library/LaunchAgents` into an owner-only
   rollback directory instead of deleting them
3. installs root-owned system plists
4. bootstraps and checks the system services

This makes duplicate SQLite writers, transforms, listeners, and backups
structurally difficult. launchd also does not start a second instance of one
loaded interval job while its prior invocation is still running.

## Operator workflow

Review status without privilege:

```bash
bin/prime_runtime.sh status
```

Install or control the system services:

```bash
sudo bin/prime_runtime.sh install
sudo bin/prime_runtime.sh stop
sudo bin/prime_runtime.sh start
sudo bin/prime_runtime.sh restart
```

Rollback restores the saved LaunchAgent plists and removes the installed
system plists without touching SQLite, generated artifacts, logs, or backups:

```bash
sudo bin/prime_runtime.sh rollback
```

Root authorization is needed to modify the system launchd domain and installed
configuration. Core Prime processes still execute as `mbeason`.

## Manual lifecycle validation

Do not automate user switching, logout, or reboot. After system installation,
record `bin/prime_runtime.sh status`, then use these checks.

Fast User Switching:

1. Leave Matthew logged in and switch to the second user.
2. Wait three minutes.
3. Confirm `http://127.0.0.1:8000/` loads in the second user's browser.
4. Return to Matthew and run `bin/prime_runtime.sh status`.
5. Confirm the SQLite latest observation and `viz/latest.csv` timestamp advanced
   and `lsof` shows one listener.

Full logout:

1. Record the SQLite observation count/latest timestamp and transform mtime.
2. Log Matthew out completely and remain at the login window for three minutes.
3. Log back in and run `bin/prime_runtime.sh status`.
4. Confirm the core labels remain in `system`, observations and transform
   advanced through the logout interval, and optional user providers either
   became stale/unavailable or resumed after login without affecting core.

Reboot-before-login (only during an approved maintenance window):

1. Record current status and shut down/reboot normally.
2. Leave the machine at the login window for three minutes after the encrypted
   data volume is available.
3. Log in and run `bin/prime_runtime.sh status`.
4. Confirm system-domain ownership, advancing observations/artifacts, one
   loopback listener, SQLite `quick_check=ok`, and backup restore readiness.

If FileVault or another boot policy keeps the data volume unavailable until a
user authenticates, Prime cannot access a repository located on that volume
before unlock. That is a storage availability boundary, not a GUI-session
dependency.

## Runtime log rotation

The Prime rotation configuration is `launchd/rotation/newsyslog.conf`. A
separate system launchd job runs its root-owned installed script every 15
minutes. It covers collector, transform, HTTP, storage backup, NextDNS refresh,
and the optional Operator Assistant worker logs. Every existing log rotates at
5,120 KiB or after 24 hours, retaining eight gzip-compressed files (`.0`
through `.7`, the macOS result for newsyslog count 7) beside the active log in
`logs/`. Active and archived files use `mbeason:staff` mode
`0600`; the rotator also tightens pre-existing active log modes. The `logs/`
directory remains mode `0700`.

HTTP keeps launchd's stdout/stderr descriptors open. Before rotating either
HTTP file, the rotation job checks newsyslog's dry run, stops only the HTTP
service, rotates its files, and bootstraps that same system service again. A
root-owned recovery marker ensures a failed rotation run restores HTTP on the
next run. Interval and optional provider jobs exit after each invocation.

`sudo bin/prime_runtime.sh install-rotation` installs the root-owned
configuration and script in `/Library/Application Support/Prime Observer/`,
plus the job plist in `/Library/LaunchDaemons/`. Installation accepts existing
files only when they match the repository source. `install` also installs
rotation during a new core migration. `bin/prime_runtime.sh status` reports its
loaded/config state. Inspect the policy without changing logs using:

```bash
sudo newsyslog -n -v -f '/Library/Application Support/Prime Observer/newsyslog.conf'
```

Run a real policy-based cycle with `sudo bin/prime_runtime.sh rotate-logs`.
`sudo bin/prime_runtime.sh uninstall-rotation` removes only matching installed
files and leaves logs and archives intact. Core rollback retains rotation
because the restored LaunchAgents write to the same log files.
