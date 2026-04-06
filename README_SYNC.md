# icloudpd-sync

A new sync architecture for iCloud Photos. Downloads photos and videos to a local directory, organizes them with symlinks, and optionally runs on a schedule for always-on NAS deployments.

## Quick start

### One-shot sync (download once and exit)

```bash
icloudpd-sync -u alice@icloud.com -d /photos/alice
```

### Scheduled sync (NAS / Docker)

```bash
icloudpd-sync --watch \
  -u alice@icloud.com -d /photos/alice \
  -u bob@icloud.com -d /photos/bob
```

This starts a long-running process with two tiers of sync:

- **Daily runs** - download recent photos only (2-day lookback by default), minimal API load
- **Weekly runs** - full library sync including albums and deletion reconciliation

Users are spread across different hours and weekdays automatically to avoid hitting API limits.

### Config file (recommended for NAS)

```bash
icloudpd-sync --config /etc/icloudpd/config.yaml
```

See [Configuration file](#configuration-file) below.

## How it works

### Five-phase sync

Each sync run executes five phases:

| Phase | What it does | I/O |
|-------|-------------|-----|
| 1. Asset collection | Fetch photo metadata from iCloud, determine versions to download | DB only |
| 2. Album sync | Sync album and folder structure | DB only |
| 3. Deletion reconciliation | Detect deleted photos, mark tombstones (soft delete) | DB only |
| 4. Download | Parallel asset downloads (5 concurrent, 3 retries) | Network + Disk |
| 5. Filesystem sync | Create/update symlink structure | Disk only |

Phases 1-3 only touch the local SQLite database. Phase 4 downloads files. Phase 5 builds the user-facing directory layout from symlinks.

### Directory layout

```
/photos/alice/
  _metadata.sqlite          # sync state database
  _data/                    # flat blob storage (never touch these directly)
    <base64_id>-original.jpg
    <base64_id>-adjusted.jpg
    <base64_id>-live_photo.mov
  Library/                  # symlinks organized by date
    2024/
      01/
        20240115_143022_IMG_7409.JPG -> ../../_data/...
  Albums/                   # symlinks mirroring iCloud album hierarchy
    Vacation/
      20240115_143022_IMG_7409.JPG -> ../../_data/...
```

`Library/` and `Albums/` are symlink trees rebuilt each sync. Your actual files live in `_data/`. Point your photo viewer at `Library/` or `Albums/`.

### Scheduling

When `--watch` is enabled (or `schedule:` is present in the config file), `icloudpd-sync` runs as a long-lived daemon:

- **Daily runs**: recent photos only (configurable lookback window), light on API calls
- **Weekly runs**: full library scan, album sync, deletion reconciliation
- **Jitter**: deterministic per-user-per-day offset (0-3 hours by default) so multiple users don't all hit the API at the same instant
- **Deduplication**: on the weekly sync day, the daily run is automatically skipped since the weekly run is a superset

With multiple users, each is assigned a different hour and weekday automatically.

## Configuration file

For NAS deployments, a YAML config file is cleaner than long command lines. Pass it with `--config`:

```bash
icloudpd-sync --config /path/to/config.yaml
```

### Full example

```yaml
log_level: info
domain: com
password_providers: [keyring, webui]
mfa_provider: webui

schedule:
  daily_preferred_hour: 3
  weekly_preferred_day: 1         # 0=Monday, 6=Sunday
  jitter_max_hours: 2.0
  daily_lookback_days: 3

notification:
  script: /usr/local/bin/notify.sh    # called when auth requires user interaction

# Shared defaults applied to every user (can be overridden per user)
defaults:
  cookie_directory: /data/cookies

users:
  - username: alice@icloud.com
    directory: /photos/alice

  - username: bob@icloud.com
    directory: /photos/bob
    recent: 500

  - username: carol@icloud.com
    directory: /photos/carol
    skip_created_before: "30d"
```

### Config file reference

#### Global settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `log_level` | `debug` / `info` / `error` | `debug` | Log verbosity |
| `domain` | `com` / `cn` | `com` | iCloud domain (`cn` for mainland China) |
| `password_providers` | list of strings | `[parameter, keyring, console]` | Password sources, tried in order. Values: `console`, `keyring`, `parameter`, `webui` |
| `mfa_provider` | `console` / `webui` | `console` | Where to enter MFA codes |

#### Schedule settings

Presence of the `schedule:` section enables watch mode (equivalent to `--watch` on the CLI). Omit it entirely for one-shot mode.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `daily_preferred_hour` | 0-23 | `2` | Preferred start hour for daily runs |
| `weekly_preferred_day` | 0-6 | `0` (Monday) | Preferred weekday for full sync |
| `jitter_max_hours` | float | `3.0` | Max jitter offset in hours |
| `daily_lookback_days` | int | `2` | How many days back daily runs check |

#### Notification settings

When a session expires, `icloudpd-sync` blocks waiting for the user to re-authenticate via the web UI. The `notification:` section lets you get alerted when this happens, so the sync doesn't stall silently on a headless NAS.

You can use a script, SMTP email, or both:

```yaml
# Option A: custom script (receives no arguments, keep it simple)
notification:
  script: /usr/local/bin/notify.sh

# Option B: email via SMTP
notification:
  smtp_username: me@gmail.com
  smtp_password: app-password
  email: alerts@example.com

# Option C: both
notification:
  script: /usr/local/bin/notify.sh
  smtp_username: me@gmail.com
  smtp_password: app-password
  email: alerts@example.com
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `script` | string | - | Path to script executed when auth requires interaction |
| `smtp_username` | string | - | SMTP login username |
| `smtp_password` | string | - | SMTP login password |
| `smtp_host` | string | `smtp.gmail.com` | SMTP server host |
| `smtp_port` | int | `587` | SMTP server port |
| `smtp_no_tls` | bool | `false` | Disable STARTTLS |
| `email` | string | SMTP username | Recipient email address |
| `email_from` | string | auto | From address for notification emails |

The notification fires when two-factor or two-step authentication is required (i.e., when a session has expired and the user must interact with the web UI or console).

#### Defaults section

Any user option placed under `defaults:` applies to all users. Per-user values override defaults.

#### User settings

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `username` | string | yes | Apple ID email address |
| `directory` | string | yes* | Local download directory. *Not required if `auth_only: true` |
| `auth_only` | bool | no | Only create/refresh authentication cookies |
| `cookie_directory` | string | no | Cookie storage path (default: `~/.pyicloud`) |
| `recent` | int | no | Only sync the N most recent photos |
| `skip_created_before` | string | no | Skip photos older than this. ISO timestamp (`2024-01-15T00:00:00`) or interval (`30d`) |

### Passwords are never stored in the config file

The config file **rejects** any `password` or `passwords` keys with a hard error. Passwords must come through one of the configured `password_providers`:

- **`keyring`** - system keyring (recommended for NAS)
- **`webui`** - enter via the web UI at port 8080
- **`console`** - interactive terminal prompt
- **`parameter`** - CLI `-p` flag (only useful for scripting, not config files)

To set up keyring-based passwords:

```bash
# Store password in system keyring
icloudpd-sync -u alice@icloud.com --auth-only
```

### CLI overrides

CLI arguments take precedence over the config file:

```bash
# Use config file but override log level
icloudpd-sync --config config.yaml --log-level error

# Use config file but force watch mode even if schedule: is absent
icloudpd-sync --config config.yaml --watch
```

When `-u` flags are passed alongside `--config`, CLI users **replace** the config file users entirely (no merging):

```bash
# Ignores users from config.yaml, syncs only this user
icloudpd-sync --config config.yaml -u override@icloud.com -d /tmp/test
```

## CLI reference

```
icloudpd-sync [GLOBAL] [COMMON] [<-u user1> [COMMON] ...] [<-u user2> [COMMON] ...]
```

### Global options

| Flag | Description |
|------|-------------|
| `--config PATH` | Path to YAML config file |
| `--log-level {debug,info,error}` | Log verbosity (default: debug) |
| `--domain {com,cn}` | iCloud domain (default: com) |
| `--password-provider {console,keyring,parameter,webui}` | Repeatable. Default: parameter, keyring, console |
| `--mfa-provider {console,webui}` | MFA input method (default: console) |
| `--watch` | Enable scheduled watch mode |
| `--daily-hour N` | Preferred hour for daily runs (default: 2) |
| `--weekly-day N` | Preferred weekday 0=Mon..6=Sun (default: 0) |
| `--jitter-hours N` | Max jitter hours (default: 3.0) |
| `--daily-lookback-days N` | Lookback window for daily runs (default: 2) |
| `--smtp-username USER` | SMTP username for email notifications |
| `--smtp-password PASS` | SMTP password |
| `--smtp-host HOST` | SMTP server (default: smtp.gmail.com) |
| `--smtp-port PORT` | SMTP port (default: 587) |
| `--smtp-no-tls` | Disable STARTTLS |
| `--notification-email ADDR` | Notification recipient (default: SMTP username) |
| `--notification-email-from ADDR` | Notification sender address |
| `--notification-script PATH` | Script to run when auth needs interaction |

### User options

| Flag | Description |
|------|-------------|
| `-u, --username EMAIL` | Apple ID. Starts a new user group |
| `-p, --password PASS` | Password (for `parameter` provider) |
| `-d, --directory DIR` | Download directory |
| `--auth-only` | Only create/refresh cookies |
| `--cookie-directory DIR` | Cookie storage (default: ~/.pyicloud) |
| `--recent N` | Download only N most recent photos |
| `--skip-created-before VAL` | ISO timestamp or interval (e.g. `20d`) |

User options placed *before* the first `-u` become defaults for all users.

## Docker

The container image includes the `icloudpd-sync` binary:

```bash
docker run -v /photos:/photos -v /config:/config \
  icloudpd/icloudpd icloudpd-sync --config /config/config.yaml
```

A typical `docker-compose.yml`:

```yaml
services:
  icloudpd:
    image: icloudpd/icloudpd
    command: icloudpd-sync --config /config/config.yaml
    volumes:
      - /path/to/photos:/photos
      - /path/to/config:/config
      - /path/to/cookies:/cookies
    ports:
      - "8080:8080"   # web UI (for webui password/MFA provider)
    environment:
      - TZ=Europe/Berlin
    restart: unless-stopped
```

With a matching `/path/to/config/config.yaml`:

```yaml
log_level: info
password_providers: [keyring, webui]
mfa_provider: webui

schedule:
  daily_preferred_hour: 3

defaults:
  cookie_directory: /cookies

users:
  - username: alice@icloud.com
    directory: /photos/alice
```

## Web UI

When `password_providers` includes `webui` or `mfa_provider` is `webui`, a web server starts on port 8080 providing:

- Password entry form (when keyring has no stored password)
- MFA / two-factor code entry with SMS fallback
- Sync status dashboard with per-user schedule info
- Log viewer
- Manual sync triggers (delta or full sync per user)

## Differences from legacy `icloudpd`

`icloudpd-sync` is a ground-up rewrite focused on unattended NAS operation:

| | Legacy `icloudpd` | `icloudpd-sync` |
|---|---|---|
| Storage | Direct files | Flat blob store + symlinks |
| State | Stateless (re-scans every run) | SQLite database |
| Scheduling | External cron | Built-in two-tier scheduler |
| Downloads | Sequential | Parallel (5 concurrent) |
| Deletions | Optional `--auto-delete` | Soft-delete tombstones |
| Config | CLI only | CLI + YAML config file |
| Album support | `--album` filter | Full album tree sync |
| Web UI | Password/MFA only | Status dashboard + log viewer + manual triggers |
