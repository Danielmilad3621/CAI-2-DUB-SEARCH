import type { SortMode } from '../lib/flight'

const OPTIONS: { id: SortMode; label: string }[] = [
  { id: 'price', label: 'Lowest to highest' },
  { id: 'trip', label: 'Shortest trip' },
  { id: 'nonstop', label: 'Nonstop first' },
]

export function FilterChips({
  value,
  onChange,
}: {
  value: SortMode
  onChange: (v: SortMode) => void
}) {
  return (
    <div className="flex gap-2 overflow-x-auto pb-1 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      {OPTIONS.map((opt) => (
        <button
          key={opt.id}
          type="button"
          onClick={() => onChange(opt.id)}
          className={`shrink-0 rounded-full px-4 py-2 text-xs font-semibold transition ${
            value === opt.id
              ? 'bg-brand-600 text-white shadow-sm shadow-brand-600/30'
              : 'bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  )
}
