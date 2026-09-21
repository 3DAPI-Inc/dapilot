import type { ReactNode } from 'react';
import { Link, NavLink } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { listJobs } from '@/api/jobs';
import { cn } from '@/lib/utils';

/*
  The console's frame. Every app screen sits in it, so the bar, the page width
  and the way a page introduces itself are decided once. The landing page keeps
  SiteHeader; this is the working side of the house.
*/

/** Every room in the console, in the order work moves through them. */
const ROOMS = [
  { to: '/upload', label: 'Upload' },
  { to: '/agent-runs', label: 'Agent runs' },
  { to: '/catalog', label: 'Catalogue' },
  // A bench tool for a big screen; it leaves the bar on a phone so the rest fits.
  { to: '/studio', label: 'Studio', desk: true },
];

/** What is waiting on a person, said once, in the bar, on every screen. */
function NeedsYou() {
  // Shares the agent-runs cache; it only polls while that page is open.
  const jobs = useQuery({ queryKey: ['jobs'], queryFn: listJobs, staleTime: 15_000, retry: false });
  const waiting = jobs.data?.filter((j) => j.status === 'blocked').length ?? 0;
  if (waiting === 0) return null;
  return (
    <Link
      to="/agent-runs?status=blocked"
      className="inline-flex shrink-0 items-center gap-2 font-sans text-sm font-medium text-navy underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ice"
    >
      <span className="size-1.5 rounded-full bg-ice" aria-hidden="true" />
      {waiting} need{waiting === 1 ? 's' : ''} you
    </Link>
  );
}

export function ConsoleShell({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return (
    <div className="min-h-svh bg-paper text-navy">
      <header className="sticky top-0 z-40 border-b border-mist bg-paper/85 backdrop-blur-md">
        <div className="mx-auto flex h-14 max-w-[1180px] items-center gap-3 px-5 sm:gap-6 sm:px-8">
          <Link to="/" className="font-display text-[0.9375rem] font-semibold tracking-tight text-navy">
            dapilot
          </Link>
          <nav className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto overflow-y-hidden [scrollbar-width:none]" aria-label="Console">
            {ROOMS.map(({ to, label, desk }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  cn(
                    'shrink-0 rounded-lg px-3 py-1.5 font-sans text-sm transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ice',
                    isActive ? 'bg-mist text-navy' : 'text-slate hover:text-navy',
                    desk && 'hidden sm:block',
                  )
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
          <NeedsYou />
        </div>
      </header>
      <main className={cn('mx-auto px-5 pt-8 pb-24 sm:px-8', wide ? 'max-w-[1180px]' : 'max-w-[1080px]')}>{children}</main>
    </div>
  );
}

/** A page says its name, one line about itself at most, and offers its one action. */
export function PageHeader({ title, note, action }: { title: string; note?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        <h1 className="font-display text-[1.75rem] font-semibold leading-tight tracking-tight">{title}</h1>
        {note ? <p className="mt-1 max-w-[60ch] font-sans text-sm leading-relaxed text-slate">{note}</p> : null}
      </div>
      {action}
    </div>
  );
}

/** The one surface: white on paper, a hairline, a soft shadow. */
export function Panel({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn('rounded-2xl border border-mist bg-white shadow-card', className)}>{children}</div>;
}

/** Loading, empty and failed states share one quiet block. */
export function Notice({ title, children, tone = 'quiet' }: { title?: string; children?: ReactNode; tone?: 'quiet' | 'bad' }) {
  return (
    <div className="px-6 py-12 text-center">
      {title ? <p className={cn('font-sans text-[0.9375rem] font-medium', tone === 'bad' ? 'text-destructive' : 'text-navy')}>{title}</p> : null}
      {children ? <div className="mx-auto mt-1 max-w-[52ch] font-sans text-sm leading-relaxed text-slate">{children}</div> : null}
    </div>
  );
}
