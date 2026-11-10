"""CLI entry: freesounder-ble-provisioning."""

from __future__ import annotations

import argparse
import asyncio
import logging


def main() -> None:
    parser = argparse.ArgumentParser(description="FreeSounder Improv Wi-Fi BLE provisioning")
    parser.add_argument(
        "--poll",
        type=float,
        default=2.0,
        help="Seconds between online/offline checks (default 2)",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Print adapter, rfkill, unit, and network diagnostics and exit",
    )
    args = parser.parse_args()
    if args.doctor:
        from freesounder_ble_provisioning.doctor import main as doctor_main

        doctor_main()
        return
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger().setLevel(logging.INFO)
    from freesounder_ble_provisioning.service import run_forever

    asyncio.run(run_forever(poll_seconds=args.poll))


if __name__ == "__main__":
    main()
