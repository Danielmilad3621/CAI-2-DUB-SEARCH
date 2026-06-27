"""Route registry: built-in routes plus user-created routes.

Built-ins load from app/routes.json (git-tracked, ships with the image).
User-created routes are persisted to app/routes.user.json inside the running
container: they survive `docker compose restart` but are LOST on the next
`docker compose up --build` (accepted v1 limitation until Phase 5 adds real
persistence). The API surfaces this via persistent=false.

User-created routes are window-only and Dublin-origin-only: fixed_pairs
stays a built-in capability (Turkey), and the origin is pinned until the
tfs= builder replaces byte-patching (Phase 4).
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.engine import _WEEKDAY_MAP, Route

log = logging.getLogger("dub.registry")

APP_DIR = Path(__file__).resolve().parent
BUILTIN_PATH = APP_DIR / "routes.json"
USER_PATH = APP_DIR / "routes.user.json"

LOCKED_ORIGIN = "DUB"
MAX_DESTINATIONS = 5


class RegistryError(ValueError):
    """Invalid route data (HTTP 400)."""


class RouteConflict(RegistryError):
    """Route id already exists (HTTP 409)."""


class BuiltinProtected(RegistryError):
    """Built-in routes cannot be deleted (HTTP 403)."""


@dataclass(frozen=True)
class RouteEntry:
    route: Route
    builtin: bool

    @property
    def persistent(self) -> bool:
        # Built-ins ship in git/the image; user routes die with the container fs.
        return self.builtin


def _route_from_dict(raw: dict[str, Any]) -> Route:
    return Route(
        id=raw["id"],
        name=raw["name"],
        subtitle=raw["subtitle"],
        origin=raw["origin"],
        destinations=tuple(raw["destinations"]),
        airline=raw.get("airline"),
        airline_name=raw.get("airline_name"),
        date_strategy=raw.get("date_strategy", "window"),
        weekdays=raw.get("weekdays"),
        fixed_pairs=tuple(tuple(p) for p in raw.get("fixed_pairs") or ()),
        configurable_destinations=bool(raw.get("configurable_destinations", False)),
        eta_minutes=int(raw.get("eta_minutes", 5)),
        nonstop=bool(raw.get("nonstop", False)),
    )


def _route_to_dict(route: Route) -> dict[str, Any]:
    return {
        "id": route.id,
        "name": route.name,
        "subtitle": route.subtitle,
        "origin": route.origin,
        "destinations": list(route.destinations),
        "airline": route.airline,
        "airline_name": route.airline_name,
        "date_strategy": route.date_strategy,
        "weekdays": route.weekdays,
        "fixed_pairs": [list(p) for p in route.fixed_pairs],
        "configurable_destinations": route.configurable_destinations,
        "eta_minutes": route.eta_minutes,
        "nonstop": route.nonstop,
    }


def _normalize_destinations(destinations: Any) -> tuple[str, ...]:
    if isinstance(destinations, str):
        codes = [c.strip() for c in destinations.split(",")]
    else:
        codes = [str(c).strip() for c in destinations]
    codes = [c.upper() for c in codes if c]
    deduped = tuple(dict.fromkeys(codes))
    if not deduped:
        raise RegistryError("at least one destination IATA code is required")
    if len(deduped) > MAX_DESTINATIONS:
        raise RegistryError(f"at most {MAX_DESTINATIONS} destinations per route")
    for code in deduped:
        if len(code) != 3 or not code.isalpha():
            raise RegistryError(f"bad destination IATA code: {code!r} (expected 3 letters)")
    return deduped


def _validate_weekdays(spec: str) -> str:
    tokens = [t.strip().title()[:3] for t in spec.split(",") if t.strip()]
    if not tokens:
        raise RegistryError("weekdays must be a comma-separated list of 3-letter day names")
    for token in tokens:
        if token not in _WEEKDAY_MAP:
            raise RegistryError(f"unknown weekday {token!r}")
    return ",".join(tokens)


class RouteRegistry:
    def __init__(self, builtin_path: Path = BUILTIN_PATH, user_path: Path = USER_PATH) -> None:
        self._lock = threading.Lock()
        self._user_path = user_path
        self._builtins = self._load_builtins(builtin_path)
        self._user = self._load_user(user_path)

    @staticmethod
    def _load_builtins(path: Path) -> dict[str, Route]:
        # Built-ins are part of the deployable artifact: fail loudly, at startup.
        data = json.loads(path.read_text())
        routes = {raw["id"]: _route_from_dict(raw) for raw in data["routes"]}
        if not routes:
            raise RuntimeError(f"{path} contains no routes")
        return routes

    def _load_user(self, path: Path) -> dict[str, Route]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            log.warning("ignoring corrupt %s: %s", path, exc)
            return {}
        routes: dict[str, Route] = {}
        for raw in data.get("routes", []):
            try:
                route = _route_from_dict(raw)
            except (KeyError, ValueError) as exc:
                log.warning("skipping bad user route %r: %s", raw.get("id"), exc)
                continue
            if route.id in self._builtins:
                log.warning("skipping user route shadowing builtin id %r", route.id)
                continue
            routes[route.id] = route
        return routes

    def _save_user(self) -> None:
        payload = {"version": 1, "routes": [_route_to_dict(r) for r in self._user.values()]}
        self._user_path.write_text(json.dumps(payload, indent=1))

    def list_entries(self) -> list[RouteEntry]:
        with self._lock:
            return [RouteEntry(r, builtin=True) for r in self._builtins.values()] + [
                RouteEntry(r, builtin=False) for r in self._user.values()
            ]

    def get_entry(self, route_id: str) -> RouteEntry | None:
        with self._lock:
            if route_id in self._builtins:
                return RouteEntry(self._builtins[route_id], builtin=True)
            if route_id in self._user:
                return RouteEntry(self._user[route_id], builtin=False)
            return None

    def get(self, route_id: str) -> Route | None:
        entry = self.get_entry(route_id)
        return entry.route if entry else None

    def create(
        self,
        *,
        destinations: Any,
        id: str | None = None,
        name: str | None = None,
        subtitle: str | None = None,
        airline: str | None = None,
        airline_name: str | None = None,
        weekdays: str | None = None,
        eta_minutes: int = 30,
        configurable_destinations: bool = False,
    ) -> RouteEntry:
        """Create a user route. Window-only by design: fixed_pairs routes stay built-in."""
        dests = _normalize_destinations(destinations)
        route_id = id or "-".join(c.lower() for c in dests)
        clean_weekdays = _validate_weekdays(weekdays) if weekdays else None

        airline_label = airline_name or (f"{airline} only" if airline else "All airlines")
        day_label = clean_weekdays.replace(",", "/") if clean_weekdays else "flexible days"
        try:
            route = Route(
                id=route_id,
                name=name or f"Dublin → {', '.join(dests)}",
                subtitle=subtitle or f"{airline_label} · {day_label}",
                origin=LOCKED_ORIGIN,
                destinations=dests,
                airline=airline,
                airline_name=airline_name,
                date_strategy="window",
                weekdays=clean_weekdays,
                configurable_destinations=configurable_destinations,
                eta_minutes=eta_minutes,
            )
        except ValueError as exc:
            raise RegistryError(str(exc)) from exc

        with self._lock:
            if route.id in self._builtins or route.id in self._user:
                raise RouteConflict(
                    f"route id {route.id!r} already exists"
                    + ("" if id else " — pass an explicit id")
                )
            self._user[route.id] = route
            self._save_user()
        return RouteEntry(route, builtin=False)

    def delete(self, route_id: str) -> None:
        with self._lock:
            if route_id in self._builtins:
                raise BuiltinProtected(f"route {route_id!r} is built-in and cannot be deleted")
            if route_id not in self._user:
                raise KeyError(route_id)
            del self._user[route_id]
            self._save_user()


registry = RouteRegistry()
