import { ChevronRight, Plane } from 'lucide-react'
import { Link } from 'react-router-dom'
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

export function FlightCard({ flight, jobId }: { flight: FlightResult; jobId: string }) {
  const entry = topEntry(flight)
  const airline = entry?.airline ?? 'Various'
  const leaveTime = extractLeaveTime(entry?.snippet_head ?? entry?.snippet)
  const detailPath = `/flight/${jobId}?dep=${flight.dep}&ret=${flight.ret}&dest=${flight.dest ?? 'CAI'}`

  return (
    <article className="rounded-3xl bg-white p-4 shadow-sm shadow-slate-200/60 ring-1 ring-slate-100">
      <div className="mb-3 flex items-center gap-3">
        <div
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-xs font-bold text-white ${airlineColor(airline)}`}
        >
          {airlineInitials(airline)}
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-slate-900">{airline}</p>
          <p className="text-xs text-slate-500">{entry?.stops ?? '—'} · {entry?.class ?? 'Economy'}</p>
        </div>
        <span className="rounded-full bg-brand-50 px-2 py-0.5 text-[10px] font-semibold text-brand-700">
          {routeLabel(flight)}
        </span>
      </div>

      <div className="mb-4 flex items-center justify-between gap-2">
        <div className="text-center">
          <p className="text-lg font-bold text-slate-900">{leaveTime ?? flight.dep_wd ?? '—'}</p>
          <p className="text-xs font-medium text-slate-500">DUB</p>
          <p className="text-[10px] text-slate-400">{formatDateShort(flight.dep)}</p>
        </div>

        <div className="flex flex-1 flex-col items-center px-1">
          <p className="text-[10px] text-slate-400">{flight.trip_days ?? '—'} days</p>
          <div className="relative my-1 flex w-full items-center">
            <div className="h-px flex-1 bg-slate-200" />
            <div className="mx-1 rounded-full bg-brand-50 p-1.5 text-brand-600">
              <Plane className="h-3.5 w-3.5" />
            </div>
            <div className="h-px flex-1 bg-slate-200" />
          </div>
          <p className="text-[10px] text-slate-500">Round trip</p>
        </div>

        <div className="text-center">
          <p className="text-lg font-bold text-slate-900">{flight.ret_wd ?? '—'}</p>
          <p className="text-xs font-medium text-slate-500">{flight.dest ?? 'CAI'}</p>
          <p className="text-[10px] text-slate-400">{formatDateShort(flight.ret)}</p>
        </div>
      </div>

      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="text-[10px] uppercase tracking-wide text-slate-400">From</p>
          <p className="text-xl font-bold text-slate-900">{formatPrice(flight.min_price)}</p>
          <p className="text-[10px] text-slate-500">per person</p>
        </div>
        <Link
          to={detailPath}
          state={{ flight }}
          className="inline-flex items-center gap-1 rounded-2xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-slate-800 active:scale-[0.98]"
        >
          Select flight
          <ChevronRight className="h-4 w-4" />
        </Link>
      </div>
    </article>
  )
}
