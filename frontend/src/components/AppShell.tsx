import type { ReactNode } from 'react'
import { BottomNav } from './BottomNav'

export function AppShell({
  children,
  hideNav,
}: {
  children: ReactNode
  hideNav?: boolean
}) {
  return (
    <div className="mx-auto min-h-full w-full max-w-md bg-slate-100 shadow-xl shadow-slate-300/30">
      <div className={hideNav ? 'min-h-full' : 'min-h-full pb-24'}>{children}</div>
      {!hideNav && <BottomNav />}
    </div>
  )
}
