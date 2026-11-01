# Ryot for Home Assistant

Custom integration that turns [Ryot](https://github.com/IgnisDa/ryot) fitness
data (workouts and body measurements) into sensors, for health dashboards.

## Install

HACS → Custom repositories → `https://github.com/tylerhartwig/ha-ryot`
(type *Integration*), install, restart HA, then *Settings → Devices & services →
Add integration → Ryot*.

Enter the URL you open Ryot at (e.g. `https://ryot.example.com`) and either:

- **username + password** (recommended): the integration logs in again when
  the session expires, or
- an **API token** from Ryot → Settings → Integrations → *Create API token*.
  Ryot expires these after `USERS_TOKEN_VALID_FOR_DAYS` (default 90); HA then
  asks you to re-authenticate.

## Sensors

Polled every 15 minutes. Windows are rolling (last 7 / 30 days from now).

| Sensor | Notes |
|---|---|
| Last workout | timestamp of its end; attributes: name, duration, exercises, muscles, reps, volume, calories, PBs |
| Last workout name / duration / volume / calories | |
| Total workouts | all time |
| Workouts, Active days, Workout time, Volume, Calories burnt, Personal bests (7d and 30d) | |
| Reps, Distance (7d and 30d) | disabled by default |
| One sensor per measurement statistic (Weight, Body fat, …) | created when a value first appears; latest value in the past year |

Volume is Σ reps × weight. Units follow your Ryot unit system (kg/km or lb/mi);
Ryot stores what you typed, so nothing is converted.

## Development

Everything is self-contained in this repo: its own venvs, `.env`, and a
throwaway Ryot. Nothing touches your real HA or Ryot except
`scripts/smoke_live.py`, which only reads.

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
cp .env.example .env

.venv/bin/python -m pytest            # boots an isolated Ryot, runs everything
.venv/bin/python scripts/ryot_stack.py --port 8055   # same Ryot, for poking
.venv/bin/python scripts/smoke_live.py               # read-only, real Ryot
```

`scripts/ryot_stack.py` needs no Docker or root: it pulls the Ryot backend
binary out of the `ghcr.io/ignisda/ryot` image (version `RYOT_TEST_VERSION`,
layer digests verified) and Debian's `postgresql-17` package via
`apt-get download`, caches both in `.cache/`, and runs them in a temp dir on
random loopback ports with a `/backend` proxy like the real container's Caddy.
Every run starts from an empty database and is deleted on exit; every test
registers its own Ryot user. Home Assistant is the in-process test instance
from `pytest-homeassistant-custom-component` (pinned to the HA version in
`requirements_test.txt`).
