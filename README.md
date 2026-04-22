# nws-alerts

`nws-alerts` is a lightweight Python 3.12+ service that polls the National Weather Service active alerts endpoint for a configurable point and prints a thermal receipt only when a new qualifying alert is issued.

It is intended for always-on Linux use, with a simple local JSON state file for dedupe, network ESC/POS thermal printer support, configurable filters, and a spoof mode for full end-to-end testing.

## What it does

- Polls `https://api.weather.gov/alerts/active?point={LATITUDE},{LONGITUDE}`
- Sends a unique `User-Agent` and `Accept: application/geo+json`
- Defaults to a 30-second polling interval to respect NWS alerts guidance
- Retries transient failures with exponential backoff and honors `Retry-After` on `429`
- Prints only new alerts by default
- Optionally reprints meaningful alert updates
- Persists handled alert fingerprints across restarts
- Filters alerts with case-insensitive comma-separated allowlists and blocklists
- Renders clean ESC/POS thermal receipts for a network printer
- Supports dry-run and spoofed GeoJSON for safe testing

## Project layout

```text
app/
  main.py
  config.py
  nws_client.py
  filtering.py
  state.py
  printing.py
  timefmt.py
examples/
  spoof-alert-new.geojson
  spoof-alert-updated.geojson
systemd/
  nws-alerts.service
```

## Requirements

- Python 3.12+
- A network-reachable ESC/POS thermal printer
- Linux with `systemd` for service deployment

## Setup

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Copy the example environment file and edit it:

```bash
cp .env.example .env
```

At minimum, set:

- `NWS_USER_AGENT`
- `NWS_LATITUDE`
- `NWS_LONGITUDE`
- `PRINTER_IP`

## Configuration notes

### Coordinates

The app polls the point configured by:

```env
NWS_LATITUDE=40.016869
NWS_LONGITUDE=105.270546
```

### Polling

- `POLL_INTERVAL_SECONDS` defaults to `30`
- Live polling below 30 seconds is rejected to stay within NWS guidance
- In spoof mode, you can lower the interval for faster testing

### Choosing what prints

Filtering is case-insensitive and trims whitespace around comma-separated values.

Common patterns:

Only warnings:

```env
ALLOWED_EVENTS=Severe Thunderstorm Warning,Tornado Warning,Flash Flood Warning
BLOCKED_EVENTS=
```

Warnings plus watches:

```env
ALLOWED_EVENTS=Severe Thunderstorm Warning,Severe Thunderstorm Watch,Tornado Warning,Tornado Watch,Flash Flood Warning,Flash Flood Watch
```

Exclude Special Weather Statements:

```env
BLOCKED_EVENTS=Special Weather Statement
```

Print everything for the point:

```env
ALLOWED_EVENTS=
ALLOWED_SEVERITIES=
ALLOWED_URGENCIES=
ALLOWED_CERTAINTIES=
ALLOWED_STATUSES=
ALLOWED_MESSAGE_TYPES=
BLOCKED_EVENTS=
```

### Dedupe behavior

- `PRINT_ON_NEW_ONLY=true` means only the first qualifying appearance of an alert ID prints
- `PRINT_ON_UPDATES=true` allows reprinting only when meaningful content changes for the same alert ID
- The app records print attempts before it writes to the printer so a partial printer failure does not automatically cause duplicate reprints after restart

### Spoof mode

Set `SPOOF_ALERTS_FILE` to a local GeoJSON file to bypass live NWS polling:

```env
SPOOF_ALERTS_FILE=./examples/spoof-alert-new.geojson
```

You can then change the file contents between polls to simulate:

- a brand-new alert
- the same alert seen again
- an updated alert with the same `id`

The included sample files are:

- `examples/spoof-alert-new.geojson`
- `examples/spoof-alert-updated.geojson`

## Running manually

Run continuously:

```bash
python -m app.main
```

Run one cycle and exit:

```bash
python -m app.main --once
```

Smoke test without printing:

```bash
python -m app.main --once --skip-print
```

Smoke test with spoofed alerts:

```bash
python -m app.main --once --skip-print --spoof-file examples/spoof-alert-new.geojson
```

## How receipts are rendered

Receipts use:

1. `EVENT` as the primary large bold heading
2. `STARTS` when onset/effective is not effectively immediate
3. `EXPIRES`
4. `DESCRIPTION`
5. `INSTRUCTION` when present

The receipt may also include:

- a compact severity / urgency / certainty line
- sender office near the footer
- horizontal rules between sections

Text is sanitized for thermal printing by normalizing punctuation, removing unsupported control characters, and stripping problematic non-ASCII symbols.

## systemd deployment

The included unit assumes deployment at `/opt/nws-alerts` and a dedicated `weather` user.

Copy the project to Linux, create the user if needed, and install the service:

```bash
sudo useradd --system --home /opt/nws-alerts --shell /usr/sbin/nologin weather
sudo mkdir -p /opt/nws-alerts
sudo rsync -a ./ /opt/nws-alerts/
cd /opt/nws-alerts
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
sudo chown -R weather:weather /opt/nws-alerts
sudo cp systemd/nws-alerts.service /etc/systemd/system/nws-alerts.service
sudo systemctl daemon-reload
sudo systemctl enable --now nws-alerts.service
```

Useful commands:

```bash
sudo systemctl status nws-alerts.service
journalctl -u nws-alerts.service -f
```

## Troubleshooting

`Configuration error: NWS_USER_AGENT is required`

- Set a unique `NWS_USER_AGENT` in `.env`

`POLL_INTERVAL_SECONDS must be at least 30 seconds`

- Increase the live polling interval
- Or enable spoof mode for faster local testing

`Printer failure`

- Check the printer IP, port, and network reachability
- Use `PRINTER_DRY_RUN=true` or `--skip-print` to validate everything except the socket write

`State file is not valid JSON`

- Inspect `STATE_FILE`
- Fix or remove the corrupted file if you intentionally want to reset dedupe history

No alerts are printing:

- Check filters in `.env`
- Try blanking allowlists to print everything for the configured point
- Run once with `--skip-print` and review the logged skip reasons

## Notes on NWS API usage

This app follows the documented NWS guidance for:

- unique `User-Agent` identification
- `Accept: application/geo+json`
- active alerts polling no more than once every 30 seconds
- backing off on rate limiting and transient server failures
