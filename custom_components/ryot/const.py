"""Constants for the Ryot integration."""

from datetime import timedelta

DOMAIN = "ryot"

CONF_API_TOKEN = "api_token"

DEFAULT_SCAN_INTERVAL = timedelta(minutes=15)

# Rolling windows the summary sensors cover, and how far back workouts are fetched.
WINDOW_SHORT_DAYS = 7
WINDOW_LONG_DAYS = 30
# Latest value per measurement statistic is taken from this far back.
MEASUREMENT_LOOKBACK_DAYS = 365

CONF_WEEKLY_GOAL = "weekly_goal"
DEFAULT_WEEKLY_GOAL = 3
# How many weeks of per-week totals the weekly sensor carries as an attribute.
HISTORY_WEEKS = 52
# The whole workout history is refetched this often; polls in between only
# fetch the last WINDOW_LONG_DAYS.
FULL_REFRESH_INTERVAL = timedelta(hours=24)
