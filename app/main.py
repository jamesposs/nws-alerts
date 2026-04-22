from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .config import ConfigError, load_config
from .filtering import evaluate_alert
from .nws_client import NwsClient, NwsClientError
from .printing import EscPosPrinter, PrinterError
from .state import StateError, StateStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Poll NWS active alerts and print new matching alerts.")
    parser.add_argument("--env-file", default=".env", help="Path to the environment file to load.")
    parser.add_argument("--once", action="store_true", help="Run a single poll cycle and exit.")
    parser.add_argument(
        "--skip-print",
        action="store_true",
        help="Do all logic except opening the printer socket. Useful for smoke tests.",
    )
    parser.add_argument(
        "--spoof-file",
        help="Override SPOOF_ALERTS_FILE for testing with local GeoJSON.",
    )
    return parser.parse_args()


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


def run_poll_cycle(client: NwsClient, printer: EscPosPrinter, state: StateStore) -> None:
    logger = logging.getLogger(__name__)
    logger.info(
        "Polling %s for active alerts at point %s.",
        "spoof file" if client.config.spoof_alerts_file else "NWS API",
        client.config.point,
    )

    alerts = client.fetch_active_alerts()
    logger.info("Received %s active alert(s).", len(alerts))

    now = datetime.now(timezone.utc)
    for alert in alerts:
        filter_result = evaluate_alert(alert, client.config)
        if not filter_result.should_print:
            logger.info("Skipping alert %s (%s): %s", alert.alert_id, alert.event, filter_result.reason)
            continue

        dedupe = state.plan(
            alert=alert,
            print_on_new_only=client.config.print_on_new_only,
            print_on_updates=client.config.print_on_updates,
        )
        if not dedupe.should_print:
            logger.info("Skipping alert %s (%s): %s", alert.alert_id, alert.event, dedupe.reason)
            continue

        logger.info("Printing alert %s (%s): %s", alert.alert_id, alert.event, dedupe.reason)
        state.record_attempt(alert)
        try:
            printer.print_alert(alert, now=now)
        except PrinterError as exc:
            state.record_failure(alert, str(exc))
            logger.error("Printer failure for alert %s (%s): %s", alert.alert_id, alert.event, exc)
            continue

        state.record_success(alert)
        logger.info("Printed alert %s (%s).", alert.alert_id, alert.event)


def main() -> int:
    args = parse_args()
    if args.skip_print:
        os.environ["PRINTER_DRY_RUN"] = "true"

    try:
        config = load_config(args.env_file)
        if args.spoof_file:
            config = replace(config, spoof_alerts_file=Path(args.spoof_file).expanduser())
        if args.skip_print:
            config = replace(config, printer_dry_run=True)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    configure_logging(config.log_level)
    logger = logging.getLogger(__name__)
    logger.info("Starting nws-alerts service.")
    logger.info("Configuration summary: %s", config.summary)

    try:
        state = StateStore(config.state_file)
    except StateError as exc:
        logger.error("Unable to initialize state store: %s", exc)
        return 2

    client = NwsClient(config=config)
    printer = EscPosPrinter(config=config)

    while True:
        started = time.monotonic()
        cycle_failed = False
        try:
            run_poll_cycle(client=client, printer=printer, state=state)
        except NwsClientError as exc:
            logger.error("Polling failed: %s", exc)
            cycle_failed = True
        except StateError as exc:
            logger.error("State persistence failed: %s", exc)
            return 2
        except Exception:
            logger.exception("Unexpected fatal error in poll cycle.")
            return 1

        if args.once:
            return 1 if cycle_failed else 0

        elapsed = time.monotonic() - started
        sleep_for = max(0.0, config.poll_interval_seconds - elapsed)
        logger.debug("Sleeping %.1fs until next poll.", sleep_for)
        time.sleep(sleep_for)


if __name__ == "__main__":
    raise SystemExit(main())
