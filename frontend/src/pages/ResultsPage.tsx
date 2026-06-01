import { ArrowLeft, Filter, Loader2, MoreHorizontal, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { cancelScan, getScan } from '../api/client'
import { AppShell } from '../components/AppShell'
import { FilterChips } from '../components/FilterChips'
import { FlightCard } from '../components/FlightCard'
import { sortResults, type SortMode } from '../lib/flight'
import type { ScanJob } from '../types'

export function ResultsPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const [job, setJob] = useState<ScanJob | null>(null)
  const [sort, setSort] = useState<SortMode>('price')
  const [error, setError] = useState<string | null>(null)

  const poll = useCallback(async () => {
    if (!jobId) return
    try {
      const data = await getScan(jobId)
      setJob(data)
      setError(null)
    } catch {
      setError('Could not load scan status. Is the API running?')
    }
  }, [jobId])

  useEffect(() => {
    poll()
    const id = setInterval(poll, 2000)
    return () => clearInterval(id)
  }, [poll])

  const running = job?.status === 'queued' || job?.status === 'running'
  const results = useMemo(
    () => sortResults(job?.top_results ?? [], sort),
    [job?.top_results, sort],
  )

  async function handleCancel() {
    if (!jobId) return
    await cancelScan(jobId)
    poll()
  }

  return (
    <AppShell>
      <header className="sticky top-0 z-30 bg-slate-100/90 px-4 pb-3 pt-10 backdrop-blur-md">
        <div className="flex items-center justify-between">
          <button
            type="button"
            onClick={() => navigate('/')}
            className="flex h-10 w-10 items-center justify-center rounded-full bg-white text-slate-700 shadow-sm ring-1 ring-slate-200"
          >
            <ArrowLeft className="h-5 w-5" />
          </button>
          <h1 className="text-base font-bold text-slate-900">Flight result</h1>
          <button
            type="button"
            className="flex h-10 w-10 items-center justify-center rounded-full bg-white text-slate-700 shadow-sm ring-1 ring-slate-200"
          >
            <MoreHorizontal className="h-5 w-5" />
          </button>
        </div>

        {running && job && (
          <div className="mt-4 rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-100">
            <div className="mb-2 flex items-center justify-between text-xs">
              <span className="flex items-center gap-1.5 font-medium text-brand-600">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Scanning…
              </span>
              <span className="text-slate-500">
                {job.done}/{job.total}
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-brand-500 transition-all duration-500"
                style={{ width: `${job.progress_pct}%` }}
              />
            </div>
            <p className="mt-2 truncate text-[10px] text-slate-500">{job.message}</p>
            <button
              type="button"
              onClick={handleCancel}
              className="mt-2 flex items-center gap-1 text-[10px] font-semibold text-red-600"
            >
              <X className="h-3 w-3" />
              Cancel scan
            </button>
          </div>
        )}

        {job?.status === 'failed' && (
          <p className="mt-3 rounded-xl bg-red-50 px-3 py-2 text-xs text-red-700">
            {job.error ?? 'Scan failed'}
          </p>
        )}

        {error && (
          <p className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800">{error}</p>
        )}

        <div className="mt-4">
          <FilterChips value={sort} onChange={setSort} />
        </div>
      </header>

      <main className="space-y-4 px-4 pb-6">
        {!results.length && !running && (
          <p className="py-12 text-center text-sm text-slate-500">No priced results yet.</p>
        )}
        {results.map((flight) => (
          <FlightCard key={`${flight.dep}-${flight.ret}-${flight.dest}`} flight={flight} jobId={jobId!} />
        ))}
      </main>

      <Link
        to="/"
        className="fixed bottom-24 right-4 z-30 flex h-14 w-14 items-center justify-center rounded-full bg-brand-600 text-white shadow-lg shadow-brand-600/40"
        aria-label="Filter"
      >
        <Filter className="h-6 w-6" />
      </Link>
    </AppShell>
  )
}
