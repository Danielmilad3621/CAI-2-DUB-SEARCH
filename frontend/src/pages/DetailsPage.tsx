import { ArrowLeft, Download, ExternalLink } from 'lucide-react'
import { useMemo } from 'react'
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { AppShell } from '../components/AppShell'
import {
  airlineColor,
  airlineInitials,
  extractLeaveTime,
  formatDateShort,
  formatPrice,
  routeLabel,
  topEntry,
} from '../lib/flight'
import type { FlightResult } from '../types'

export function DetailsPage() {
  const navigate = useNavigate()
  const { jobId } = useParams<{ jobId: string }>()
  const [params] = useSearchParams()
  const location = useLocation()
  const flight = (location.state as { flight?: FlightResult } | null)?.flight

  const entry = flight ? topEntry(flight) : undefined
  const airline = entry?.airline ?? 'Airline'
  const leaveTime = extractLeaveTime(entry?.snippet_head ?? entry?.snippet)

  const flightId = useMemo(
    () => `ID${(jobId ?? 'local').slice(0, 8).toUpperCase()}`,
    [jobId],
  )

  if (!flight) {
    return (
      <AppShell hideNav>
        <div className="px-4 py-20 text-center">
          <p className="text-sm text-slate-600">Flight not found. Go back and select a result.</p>
          <button
            type="button"
            onClick={() => navigate(-1)}
            className="mt-4 text-sm font-semibold text-brand-600"
          >
            Back
          </button>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell hideNav>
      <header className="flex items-center gap-3 px-4 pb-4 pt-10">
        <button
          type="button"
          onClick={() => navigate(-1)}
          className="flex h-10 w-10 items-center justify-center rounded-full bg-white text-slate-700 shadow-sm ring-1 ring-slate-200"
        >
          <ArrowLeft className="h-5 w-5" />
        </button>
        <h1 className="text-base font-bold text-slate-900">Your flight details</h1>
      </header>

      <div className="px-4 pb-8">
        <article className="ticket-notch overflow-hidden rounded-3xl bg-white shadow-lg shadow-slate-300/30 ring-1 ring-slate-100">
          <div className="border-b border-dashed border-slate-200 p-5">
            <div className="flex items-center gap-3">
              <div
                className={`flex h-12 w-12 items-center justify-center rounded-full text-sm font-bold text-white ${airlineColor(airline)}`}
              >
                {airlineInitials(airline)}
              </div>
              <div>
                <p className="font-semibold text-slate-900">{airline}</p>
                <p className="text-xs text-slate-500">{flightId}</p>
              </div>
            </div>

            <div className="mt-6 flex items-center justify-between">
              <div>
                <p className="text-3xl font-bold text-slate-900">DUB</p>
                <p className="text-sm text-slate-600">{leaveTime ?? flight.dep_wd}</p>
                <p className="text-xs text-slate-400">{formatDateShort(flight.dep)}</p>
              </div>
              <div className="text-center">
                <p className="text-xs font-medium text-brand-600">{flight.trip_days} days</p>
                <div className="my-1 text-slate-300">✈</div>
                <p className="text-[10px] text-slate-400">{entry?.stops ?? '—'}</p>
              </div>
              <div className="text-right">
                <p className="text-3xl font-bold text-slate-900">{flight.dest ?? 'CAI'}</p>
                <p className="text-sm text-slate-600">{flight.ret_wd}</p>
                <p className="text-xs text-slate-400">{formatDateShort(flight.ret)}</p>
              </div>
            </div>

            <div className="mt-6 grid grid-cols-3 gap-2 rounded-2xl bg-slate-50 p-3 text-center">
              <div>
                <p className="text-[10px] uppercase text-slate-400">Terminal</p>
                <p className="text-sm font-semibold text-slate-800">1</p>
              </div>
              <div>
                <p className="text-[10px] uppercase text-slate-400">Gate</p>
                <p className="text-sm font-semibold text-slate-800">—</p>
              </div>
              <div>
                <p className="text-[10px] uppercase text-slate-400">Class</p>
                <p className="text-sm font-semibold text-slate-800">{entry?.class ?? 'Economy'}</p>
              </div>
            </div>
          </div>

          <div className="p-5">
            <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
              Passengers
            </p>
            <div className="flex items-center gap-3 rounded-2xl bg-slate-50 p-3">
              <img
                src="https://api.dicebear.com/7.x/avataaars/svg?seed=passenger1"
                alt=""
                className="h-10 w-10 rounded-full bg-white"
              />
              <div className="flex-1">
                <p className="text-sm font-semibold text-slate-900">Adult 1</p>
                <p className="text-xs text-slate-500">{routeLabel(flight)}</p>
              </div>
              <span className="rounded-lg bg-white px-2 py-1 text-xs font-bold text-slate-700 ring-1 ring-slate-200">
                3A
              </span>
            </div>

            <div className="mt-6 flex justify-between text-sm">
              <span className="text-slate-500">Total</span>
              <span className="font-bold text-slate-900">{formatPrice(flight.min_price)}</span>
            </div>

            <div className="barcode mx-auto mt-6 h-16 w-full max-w-[200px] rounded-md opacity-80" />
          </div>
        </article>

        <div className="mt-6 space-y-3">
          {flight.url && (
            <a
              href={flight.url}
              target="_blank"
              rel="noreferrer"
              className="flex w-full items-center justify-center gap-2 rounded-2xl bg-slate-900 py-3.5 text-sm font-semibold text-white"
            >
              <ExternalLink className="h-4 w-4" />
              Open on Google Flights
            </a>
          )}
          <button
            type="button"
            onClick={() => window.print()}
            className="flex w-full items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white py-3.5 text-sm font-semibold text-slate-800"
          >
            <Download className="h-4 w-4" />
            Download & save pass
          </button>
        </div>

        <p className="mt-4 text-center text-[10px] text-slate-400">
          {params.get('dep')} → {params.get('ret')} · Prices from Google Flights
        </p>
      </div>
    </AppShell>
  )
}
