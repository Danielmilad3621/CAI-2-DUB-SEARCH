"""Built-in route definitions.

Phase 1: the three legacy routes as Route instances, hardcoded here so the
engine has a single lookup point. Phase 2 replaces this module with a JSON
registry (app/routes.json) + CRUD endpoints; name/subtitle/eta_minutes
mirror the SCANNERS display metadata in app/main.py until then.
"""

from __future__ import annotations

from app.engine import Route

# Aug 2026 weekend + bank-holiday long-weekend combinations
# (Mon 3 Aug 2026 is the Irish August Bank Holiday).
TURKEY_AUGUST_2026_PAIRS = (
    ("2026-07-31", "2026-08-03"),  # Fri -> Mon
    ("2026-07-31", "2026-08-04"),  # Fri -> Tue
    ("2026-08-01", "2026-08-03"),  # Sat -> Mon
    ("2026-08-01", "2026-08-04"),  # Sat -> Tue
    ("2026-08-07", "2026-08-09"),  # Fri -> Sun
    ("2026-08-07", "2026-08-10"),  # Fri -> Mon
    ("2026-08-14", "2026-08-16"),
    ("2026-08-14", "2026-08-17"),
    ("2026-08-21", "2026-08-23"),
    ("2026-08-21", "2026-08-24"),
    ("2026-08-28", "2026-08-30"),
    ("2026-08-28", "2026-08-31"),
)

BUILTIN_ROUTES: dict[str, Route] = {
    "turkey": Route(
        id="turkey",
        name="Dublin → Turkey",
        subtitle="Aug 2026 weekends · IST, SAW, AYT",
        origin="DUB",
        destinations=("IST", "SAW", "AYT"),
        airline=None,
        date_strategy="fixed_pairs",
        fixed_pairs=TURKEY_AUGUST_2026_PAIRS,
        configurable_destinations=True,
        eta_minutes=5,
    ),
    "egyptair": Route(
        id="egyptair",
        name="Dublin → Cairo",
        subtitle="EgyptAir only · Sat/Sun/Tue/Thu",
        origin="DUB",
        destinations=("CAI",),
        airline="MS",
        airline_name="EgyptAir",
        date_strategy="window",
        weekdays=None,  # honors config["weekdays"]; API default is Sat,Sun,Tue,Thu
        eta_minutes=30,
    ),
    "ams": Route(
        id="ams",
        name="Dublin → Amsterdam",
        subtitle="All airlines · daily",
        origin="DUB",
        destinations=("AMS",),
        airline=None,
        date_strategy="window",
        weekdays="Mon,Tue,Wed,Thu,Fri,Sat,Sun",  # pinned: the API ignores config weekdays for AMS
        eta_minutes=90,
    ),
}
