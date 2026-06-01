export type ScannerId = 'turkey' | 'egyptair'

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
  scanner: ScannerId
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

export interface ScannerInfo {
  id: ScannerId
  name: string
  subtitle: string
  origin: string
  default_dest: string
  eta_minutes: number
  combinations: number
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
  scanner: ScannerId
  destinations?: string
  currency?: string
  start?: string
  window_days?: number
  min_trip_days?: number
  max_trip_days?: number
  weekdays?: string
}
