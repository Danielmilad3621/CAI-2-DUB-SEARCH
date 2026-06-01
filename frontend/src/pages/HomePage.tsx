import {
  ArrowDownUp,
  Calendar,
  ChevronDown,
  Loader2,
  Users,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createScan, getSavedTrips, getScanners } from '../api/client'
import { AppShell } from '../components/AppShell'
import { formatPrice } from '../lib/flight'
import type { SavedTrip, ScannerId, ScannerInfo } from '../types'

export function HomePage() {
  const navigate = useNavigate()
  const [scanners, setScanners] = useState<ScannerInfo[]>([])
  const [saved, setSaved] = useState<SavedTrip[]>([])
  const [scanner, setScanner] = useState<ScannerId>('turkey')
  const [destinations, setDestinations] = useState('IST,SAW,AYT')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const active = scanners.find((s) => s.id === scanner)

  useEffect(() => {
    getScanners().then(setScanners).catch(() => {})
    getSavedTrips().then(setSaved).catch(() => {})
  }, [])

  async function handleSearch() {
    setLoading(true)
    setError(null)
    try {
      const job = await createScan({
        scanner,
        destinations: scanner === 'turkey' ? destinations : undefined,
        currency: 'EUR',
      })
      navigate(`/results/${job.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start scan')
    } finally {
      setLoading(false)
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

      <div className="-mt-10 px-4">
        <section className="rounded-3xl bg-white p-5 shadow-lg shadow-slate-300/25 ring-1 ring-slate-100">
          <label className="mb-1 block text-xs font-medium text-slate-500">Route preset</label>
          <div className="relative mb-4">
            <select
              value={scanner}
              onChange={(e) => {
                const id = e.target.value as ScannerId
                setScanner(id)
                if (id === 'turkey') setDestinations('IST,SAW,AYT')
              }}
              className="w-full appearance-none rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 pr-10 text-sm font-semibold text-slate-900 outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-100"
            >
              {scanners.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} — {s.subtitle}
                </option>
              ))}
              {!scanners.length && (
                <>
                  <option value="turkey">Dublin → Turkey</option>
                  <option value="egyptair">Dublin → Cairo (EgyptAir)</option>
                </>
              )}
            </select>
            <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-400" />
          </div>

          <div className="relative space-y-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">From</label>
              <input
                readOnly
                value="Dublin (DUB)"
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
                value={
                  scanner === 'turkey'
                    ? `Turkey (${destinations.replace(/,/g, ', ')})`
                    : 'Cairo (CAI) · EgyptAir'
                }
                className="w-full rounded-2xl border border-slate-200 bg-white px-4 py-3 pr-12 text-sm font-semibold text-slate-900"
              />
            </div>
          </div>

          {scanner === 'turkey' && (
            <div className="mt-3">
              <label className="mb-1 block text-xs font-medium text-slate-500">Airports</label>
              <input
                value={destinations}
                onChange={(e) => setDestinations(e.target.value.toUpperCase())}
                className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm text-slate-800"
                placeholder="IST,SAW,AYT"
              />
            </div>
          )}

          <div className="mt-4 grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-500">Dates</label>
              <div className="flex items-center gap-2 rounded-2xl border border-slate-200 bg-slate-50 px-3 py-3">
                <Calendar className="h-4 w-4 shrink-0 text-brand-600" />
                <span className="text-xs font-medium leading-tight text-slate-700">
                  {scanner === 'turkey'
                    ? 'Aug 2026 weekends'
                    : 'Next 60 days · EgyptAir days'}
                </span>
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
            disabled={loading}
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
