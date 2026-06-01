import { Home, Map, Plane, User } from 'lucide-react'
import { NavLink } from 'react-router-dom'

const items = [
  { to: '/', icon: Home, label: 'Home' },
  { to: '/results', icon: Plane, label: 'Flights' },
  { to: '/', icon: Map, label: 'Map', disabled: true },
  { to: '/', icon: User, label: 'Profile', disabled: true },
]

export function BottomNav() {
  return (
    <nav className="fixed bottom-0 left-0 right-0 z-40 border-t border-slate-200/80 bg-white/95 backdrop-blur-md">
      <div className="mx-auto flex max-w-md items-center justify-around px-2 py-2 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
        {items.map(({ to, icon: Icon, label, disabled }) =>
          disabled ? (
            <span
              key={label}
              className="flex flex-col items-center gap-0.5 px-3 py-1 text-slate-300"
            >
              <Icon className="h-5 w-5" strokeWidth={1.75} />
              <span className="text-[10px] font-medium">{label}</span>
            </span>
          ) : (
            <NavLink
              key={label}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex flex-col items-center gap-0.5 px-3 py-1 transition-colors ${
                  isActive ? 'text-brand-600' : 'text-slate-400 hover:text-slate-600'
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <Icon className="h-5 w-5" strokeWidth={isActive ? 2.25 : 1.75} />
                  <span className="text-[10px] font-medium">{label}</span>
                </>
              )}
            </NavLink>
          ),
        )}
      </div>
    </nav>
  )
}
