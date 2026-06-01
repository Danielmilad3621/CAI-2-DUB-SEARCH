import type { FlightEntry, FlightResult } from '../types'

export function topEntry(row: FlightResult): FlightEntry | undefined {
  return row.entries?.[0]
}

export function formatPrice(amount: number | null | undefined, currency = 'EUR') {
  if (amount == null) return '—'
  const sym = currency === 'EUR' ? '€' : currency
  return `${sym}${amount.toLocaleString()}`
}

export function routeLabel(row: FlightResult) {
  const o = row.origin ?? 'DUB'
  const d = row.dest ?? 'CAI'
  return `${o} → ${d}`
}

export function formatDateShort(iso?: string) {
  if (!iso) return '—'
  const d = new Date(iso + 'T12:00:00')
  return d.toLocaleDateString('en-IE', { day: 'numeric', month: 'short' })
}

export function extractLeaveTime(snippet?: string): string | null {
  if (!snippet) return null
  const m = snippet.match(/at (\d{1,2}:\d{2} [AP]M)/i)
  return m?.[1] ?? null
}

export function airlineColor(name?: string | null) {
  const n = (name ?? 'X').toLowerCase()
  if (n.includes('egypt')) return 'bg-red-500'
  if (n.includes('turkish')) return 'bg-rose-600'
  if (n.includes('lufthansa')) return 'bg-yellow-500'
  if (n.includes('british')) return 'bg-blue-700'
  if (n.includes('ryan')) return 'bg-blue-500'
  return 'bg-brand-500'
}

export function airlineInitials(name?: string | null) {
  if (!name) return '?'
  const parts = name.trim().split(/\s+/)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return name.slice(0, 2).toUpperCase()
}

export type SortMode = 'price' | 'trip' | 'nonstop'

export function sortResults(rows: FlightResult[], mode: SortMode) {
  const copy = [...rows]
  if (mode === 'price') {
    copy.sort((a, b) => (a.min_price ?? 1e9) - (b.min_price ?? 1e9))
  } else if (mode === 'trip') {
    copy.sort((a, b) => (a.trip_days ?? 99) - (b.trip_days ?? 99))
  } else {
    copy.sort((a, b) => {
      const an = topEntry(a)?.stops === 'Nonstop' ? 0 : 1
      const bn = topEntry(b)?.stops === 'Nonstop' ? 0 : 1
      if (an !== bn) return an - bn
      return (a.min_price ?? 1e9) - (b.min_price ?? 1e9)
    })
  }
  return copy
}
