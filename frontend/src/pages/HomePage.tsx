import {
  ArrowDownUp,
  Calendar,
  ChevronDown,
  Loader2,
  Plus,
  Users,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createRoute, createScan, getRoutes, getSavedTrips } from '../api/client'
import { AppShell } from '../components/AppShell'
import { formatPrice } from '../lib/flight'
import type { RouteInfo, SavedTrip } from '../types'

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

function normalizeDay(token: string): string {
  const t = token.trim().slice(0, 3)
  return t ? t[0].toUpperCase() + t.slice(1).toLowerCase() : ''
}

/** Mirrors the backend's POST /api/routes validation so most errors never leave the client. */
function validateRouteForm(destinations: string, airline: string, weekdays: string): string | null {
  const codes = destinations.split(',').map((c) => c.trim()).filter(Boolean)
  if (!codes.length) return 'Enter at least one destination airport code'
  if (codes.length > 5) return 'At most 5 destinations per route'
  if (codes.some((c) => !/^[A-Z]{3}$/.test(c))) {
    return 'Destinations must be 3-letter IATA codes, e.g. CDG or LIS,OPO'
  }
  if (airline && !/^[A-Z0-9]{2}$/.test(airline)) {
    return 'Airline must be a 2-letter IATA code, e.g. FR'
  }
  if (weekdays) {
    const days = weekdays.split(',').map(normalizeDay).filter(Boolean)
    if (!days.length || days.some((d) => !WEEKDAYS.includes(d))) {
      return 'Weekdays must be 3-letter day names, e.g. Fri,Sat,Sun'
    }
  }
  return null
}

function routeDisplayTo(route: RouteInfo): string {
  const airports = route.destinations.join(', ')
  if (route.airline_name) return `${airports} · ${route.airline_name}`
  if (route.airline) return `${airports} · ${route.airline} only`
  return airports
}

export function HomePage() {
  const navigate = useNavigate()
  const [routes, setRoutes] = useState<RouteInfo[]>([])
  const [saved, setSaved] = useState<SavedTrip[]>([])
  const [routeId, setRouteId] = useState('')
  const [destinations, setDestinations] = useState('')
  const [windowDays, setWindowDays] = useState(60)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [showAddForm, setShowAddForm] = useState(false)
  const [newDestinations, setNewDestinations] = useState('')
  const [newAirline, setNewAirline] = useState('')
  const [newAirlineName, setNewAirlineName] = useState('')
  const [newWeekdays, setNewWeekdays] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)

  const active = routes.find((r) => r.id === routeId)
  const builtinRoutes = routes.filter((r) => r.builtin)
  const userRoutes = routes.filter((r) => !r.builtin)

  function selectRoute(rs: RouteInfo[], id: string) {
    const route = rs.find((r) => r.id === id) ?? rs[0]
    if (!route) return
    setRouteId(route.id)
    setDestinations(route.destinations.join(','))
  }

  useEffect(() => {
    getRoutes()
      .then((rs) => {
        setRoutes(rs)
        selectRoute(rs, rs[0]?.id ?? '')
      })
      .catch(() => {})
    getSavedTrips().then(setSaved).catch(() => {})
  }, [])

  async function handleSearch() {
    if (!active) return
    setLoading(true)
    setError(null)
    try {
      const job = await createScan({
        route_id: active.id,
        destinations: active.configurable_destinations ? destinations : undefined,
        window_days: active.date_strategy === 'window' ? windowDays : undefined,
        currency: 'EUR',
      })
      navigate(`/results/${job.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start scan')
    } finally {
      setLoading(false)
    }
  }

  async function handleCreateRoute() {
    const dests = newDestinations.toUpperCase()
    const airline = newAirline.toUpperCase()
    const weekdays = newWeekdays.split(',').map(normalizeDay).filter(Boolean).join(',')
    const clientError = validateRouteForm(dests, airline, weekdays)
    if (clientError) {
      setFormError(clientError)
      return
    }
    setCreating(true)
    setFormError(null)
    try {
      const created = await createRoute({
        destinations: dests,
        airline: airline || undefined,
        airline_name: newAirlineName.trim() || undefined,
        weekdays: weekdays || undefined,
      })
      const rs = await getRoutes()
      setRoutes(rs)
      selectRoute(rs, created.id)
      setShowAddForm(false)
      setNewDestinations('')
      setNewAirline('')
      setNewAirlineName('')
      setNewWeekdays('')
    } catch (e) {
      // Surfaces the server's messages, e.g. the 409 duplicate-id conflict
      // and the 400 origin-locked capability note.
      setFormError(e instanceof Error ? e.message : 'Could not create route')
    } finally {
      setCreating(false)
    }
  }

  return (
    <AppShell>
      <header className="relative overflow-hidden rounded-b-[2rem] bg-gradient-to-b from-brand-600 via-brand-500 to-brand-400 px-5 pb-16 pt-12 text-white">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-sm font-medium text-white/80">Dublin departures</p>
            <h1 className="mt-1 text-2xl font-bold tracking-tight">Plan your trip</h1>
          </div>
          <div className="h-11 w-11 overflow-hidden rounded-full bg-white/20 ring-2 ring-white/40">
            <img
              src="https://api.dicebear.com/7.x/avataaars/svg?seed=danie"
              alt=""
              className="h-full w-full object-cover"
            />
          </div>
        </div>
      </header>

      {/* relative: must stack above the positioned header it overlaps, or the
          card's top row (route label + add-route button) is unclickable */}
      <div className="relative -mt-10 px-4">
        <section className="rounded-3xl bg-white p-5 shadow-lg shadow-slate-300/25 ring-1 ring-slate-100">
          <div className="mb-1 flex items-center justify-between">
            <label className="block text-xs font-medium text-slate-500">Route</label>
            <button
              type="button"
              onClick={() => {
                setShowAddForm((v) => !v)
                setFormError(null)
              }}
              className="flex items-center gap-1 text-xs font-semibold text-brand-600"
            >
              <Plus className="h-3.5 w-3.5" />
              {showAddForm ? 'Close' : 'Add route'}
            </button>
          </div>
          <div className="relative mb-2">
            <select
              value={routeId}
              onChange={(e) => selectRoute(routes, e.target.value)}
              className="w-full appearance-none rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 pr-10 text-sm font-semibold text-slate-900 outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-100"
            >
              {!routes.length && <option value="">Loading routes…</option>}
              {builtinRoutes.length > 0 && (
                <optgroup label="Built-in routes">
                  {builtinRoutes.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.name} — {r.subtitle}
                    </option>
                  ))}
                </optgroup>
              )}
              {userRoutes.length > 0 && (
                <optgroup label="My routes">
                  {userRoutes.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.name} — {r.subtitle}
                    </option>
                  ))}
                </optgroup>
              )}
            </select>
            <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-400" />
          </div>

          {active && !active.persistent && (
            <p className="mb-3 rounded-xl bg-amber-50 px-3 py-1.5 text-[11px] font-medium text-amber-700">
              Ephemeral route — kept until the next backend redeploy
            </p>
          )}

          {showAddForm && (
            <div className="mb-4 space-y-3 rounded-2xl border border-brand-100 bg-brand-50/40 p-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">From</label>
                <input
                  disabled
                  value="Dublin (DUB)"
                  className="w-full rounded-2xl border border-slate-200 bg-slate-100 px-4 py-2.5 text-sm font-semibold text-slate-500"
                />
                <p className="mt-1 text-[10px] text-slate-400">
                  Origin is locked to Dublin for now — other origins unlock later
                </p>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">
                  Destinations (3-letter codes, comma-separated)
                </label>
                <input
                  value={newDestinations}
                  onChange={(e) => setNewDestinations(e.target.value.toUpperCase())}
                  placeholder="CDG or LIS,OPO"
                  className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-800"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">
                    Airline (optional)
                  </label>
                  <input
                    value={newAirline}
                    onChange={(e) => setNewAirline(e.target.value.toUpperCase())}
                    placeholder="FR"
                    maxLength={2}
                    className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-800"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-500">
                    Airline name (optional)
                  </label>
                  <input
                    value={newAirlineName}
                    onChange={(e) => setNewAirlineName(e.target.value)}
                    placeholder="Ryanair"
                    className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-800"
                  />
                </div>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-500">
                  Operating weekdays (optional)
                </label>
                <input
                  value={newWeekdays}
                  onChange={(e) => setNewWeekdays(e.target.value)}
                  placeholder="Fri,Sat,Sun — empty = flexible"
                  className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-800"
                />
              </div>
              {formError && (
                <p className="rounded-xl bg-red-50 px-3 py-2 text-xs text-red-700">{formError}</p>
              )}
              <button
                type="button"
                disabled={creating}
                onClick={handleCreateRoute}
                className="flex w-full items-center justify-center gap-2 rounded-2xl bg-brand-600 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-500 disabled:opacity-60"
              >
                {creating ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Creating route…
                  </>
                ) : (
                  'Create route'
                )}
              </button>
              <p className="text-center text-[10px] text-slate-400">
                New routes are scannable immediately but lost on the next backend redeploy
              </p>
            </div>
          )}

          <div className="relative space-y-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">From</label>
              <input
                readOnly
                value={active ? `Dublin (${active.origin})` : 'Dublin (DUB)'}
                className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-semibold text-slate-900"
              />
            </div>

            <button
              type="button"
              className="absolute right-0 top-[2.6rem] z-10 flex h-10 w-10 items-center justify-center rounded-full bg-brand-600 text-white shadow-md shadow-brand-600/40"
              aria-label="Swap airports"
              onClick={() => {}}
            >
              <ArrowDownUp className="h-4 w-4" />
            </button>

            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">To</label>
              <input
                readOnly
                value={active ? routeDisplayTo(active) : '—'}
                className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 pr-12 text-sm font-semibold text-slate-900"
              />
            </div>
          </div>

          {active?.configurable_destinations && (
            <div className="mt-3">
              <label className="mb-1 block text-xs font-medium text-slate-500">Airports</label>
              <input
                value={destinations}
                onChange={(e) => setDestinations(e.target.value.toUpperCase())}
                className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm text-slate-800"
                placeholder={active.destinations.join(',')}
              />
            </div>
          )}

          <div className="mt-4 grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Dates</label>
              <div className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-3 py-3">
                <Calendar className="h-4 w-4 shrink-0 text-brand-600" />
                {active?.date_strategy === 'window' ? (
                  <select
                    value={windowDays}
                    onChange={(e) => setWindowDays(Number(e.target.value))}
                    className="w-full appearance-none bg-transparent text-xs font-medium text-slate-700 outline-none"
                  >
                    <option value={30}>Next 30 days</option>
                    <option value={60}>Next 60 days</option>
                    <option value={90}>Next 90 days</option>
                  </select>
                ) : (
                  <span className="text-xs font-medium leading-tight text-slate-700">
                    Fixed date pairs
                  </span>
                )}
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Est. time</label>
              <div className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-3 py-3">
                <Users className="h-4 w-4 shrink-0 text-brand-600" />
                <span className="text-xs font-medium text-slate-700">
                  ~{active?.eta_minutes ?? 5} min · 1 adult
                </span>
              </div>
            </div>
          </div>

          {error && (
            <p className="mt-3 rounded-xl bg-red-50 px-3 py-2 text-xs text-red-700">{error}</p>
          )}

          <button
            type="button"
            disabled={loading || !active}
            onClick={handleSearch}
            className="mt-5 flex w-full items-center justify-center gap-2 rounded-2xl bg-slate-900 py-3.5 text-sm font-semibold text-white transition hover:bg-slate-800 disabled:opacity-60"
          >
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Starting scan…
              </>
            ) : (
              'Search flights'
            )}
          </button>

          <p className="mt-2 text-center text-[10px] text-slate-400">
            Scans Google Flights via Playwright · {active?.combinations ?? '…'} combinations
          </p>
        </section>

        {saved.length > 0 && (
          <section className="mt-8">
            <h2 className="mb-3 text-sm font-bold text-slate-800">Saved trips</h2>
            <div className="flex gap-3 overflow-x-auto pb-2 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
              {saved.map((trip) => (
                <article
                  key={trip.id}
                  className="w-44 shrink-0 rounded-2xl bg-white p-3 shadow-sm ring-1 ring-slate-100"
                >
                  <p className="text-xs font-bold text-slate-900">{trip.label}</p>
                  <p className="mt-1 text-lg font-bold text-brand-600">
                    {formatPrice(trip.price, trip.currency)}
                  </p>
                  <p className="text-[10px] text-slate-500">
                    {trip.dep} → {trip.ret}
                  </p>
                  <p className="mt-1 truncate text-[10px] text-slate-400">{trip.airline}</p>
                </article>
              ))}
            </div>
          </section>
        )}
      </div>
    </AppShell>
  )
}
