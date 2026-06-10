export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface FlightEntry {
  price: number
  airline?: string | null
  stops?: string | null
  class?: string | null
  snippet_head?: string
  snippet?: string
}

export interface FlightResult {
  min_price: number | null
  dest?: string
  origin?: string
  dep: string
  ret: string
  dep_wd?: string
  ret_wd?: string
  trip_days?: number
  url?: string
  entries?: FlightEntry[]
  error?: string
}

export interface ScanJob {
  id: string
  route_id: string
  status: JobStatus
  total: number
  done: number
  progress_pct: number
  results_count: number
  error: string | null
  message: string | null
  config: Record<string, unknown>
  winner: FlightResult | null
  top_results: FlightResult[]
}

export interface RouteInfo {
  id: string
  name: string
  subtitle: string
  origin: string
  destinations: string[]
  default_dest: string
  airline: string | null
  airline_name: string | null
  date_strategy: 'window' | 'fixed_pairs'
  weekdays: string | null
  configurable_destinations: boolean
  eta_minutes: number
  combinations: number | null
  builtin: boolean
  /** false = user-created, lives in the container only: lost on the next backend redeploy. */
  persistent: boolean
  /** Origin is pinned to DUB until the tfs= URL builder lands (Phase 4). */
  origin_locked: boolean
}

export interface SavedTrip {
  id: string
  label: string
  price: number
  currency: string
  dep?: string
  ret?: string
  airline?: string
  source_file?: string
}

export interface ScanCreatePayload {
  route_id: string
  destinations?: string
  currency?: string
  start?: string
  window_days?: number
  min_trip_days?: number
  max_trip_days?: number
  weekdays?: string
}

export interface RouteCreatePayload {
  destinations: string
  id?: string
  name?: string
  subtitle?: string
  airline?: string
  airline_name?: string
  weekdays?: string
}
