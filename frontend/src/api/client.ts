import type { SavedTrip, ScanCreatePayload, ScanJob, ScannerInfo } from '../types'

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '/api') as string

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!res.ok) {
    const text = await res.text()
    try {
      const body = JSON.parse(text) as { detail?: string | { msg: string }[] }
      if (typeof body.detail === 'string') throw new Error(body.detail)
      if (Array.isArray(body.detail)) {
        throw new Error(body.detail.map((d) => d.msg).join('; '))
      }
    } catch (e) {
      if (e instanceof Error && e.message !== text) throw e
    }
    throw new Error(text || res.statusText)
  }
  return res.json() as Promise<T>
}

export function getScanners() {
  return request<ScannerInfo[]>('/scanners')
}

export function getSavedTrips() {
  return request<SavedTrip[]>('/saved-trips')
}

export function createScan(body: ScanCreatePayload) {
  return request<ScanJob>('/scans', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function getScan(jobId: string) {
  return request<ScanJob>(`/scans/${jobId}`)
}

export function cancelScan(jobId: string) {
  return request<{ status: string }>(`/scans/${jobId}/cancel`, { method: 'POST' })
}
