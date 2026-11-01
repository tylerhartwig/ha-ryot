"""Read-only check of the client against your real Ryot (RYOT_* in .env).

    .venv/bin/python scripts/smoke_live.py

Only runs queries; never writes to Ryot.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys

from aiohttp import ClientSession

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.ryot.api import RyotClient  # noqa: E402
from custom_components.ryot.const import WINDOW_LONG_DAYS  # noqa: E402


def _env() -> dict[str, str]:
    out = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return {**out, **{k: v for k, v in os.environ.items() if k.startswith("RYOT_")}}


async def main() -> None:
    env = _env()
    now = datetime.now(timezone.utc)
    async with ClientSession() as s:
        client = RyotClient(s, env["RYOT_URL"], api_token=env.get("RYOT_API_TOKEN"),
                            username=env.get("RYOT_USERNAME"), password=env.get("RYOT_PASSWORD"))
        user = await client.get_user()
        print(f"user: {user.name} ({user.id}), units {user.unit_system}")
        workouts, total = await client.get_recent_workouts(now - timedelta(days=WINDOW_LONG_DAYS))
        print(f"workouts: {total} total, {len(workouts)} fetched for the {WINDOW_LONG_DAYS}d window")
        for w in workouts[:5]:
            print(f"  {w.start:%Y-%m-%d} {w.name!r} {w.duration_s // 60} min, volume {w.volume:g}, {len(w.exercises)} exercises")
        for name, m in (await client.get_latest_measurements(now - timedelta(days=365))).items():
            print(f"measurement {name}: {m.value:g} ({m.timestamp:%Y-%m-%d})")


if __name__ == "__main__":
    asyncio.run(main())
