import type { ReactNode } from 'react';
import { Link, NavLink } from 'react-router-dom';
import { cn } from '@/lib/utils';

/** Shared so the landing and the console cannot drift apart at the top. */
export const headerLink =
  'font-sans text-sm text-slate underline-offset-4 transition-colors hover:text-navy hover:underline';

/** Every room in the console, in the order work moves through them. */
const CONSOLE = [
  { to: '/upload', label: 'Upload' },
  { to: '/agent-runs', label: 'Agent runs' },
  { to: '/catalog', label: 'Catalogue' },
];

/**
 * The console's own nav. The current page stays in the list rather than being
 * dropped from it, so the set of rooms does not change shape as you move.
 */
export function ConsoleNav() {
  return (
    <>
      {CONSOLE.map(({ to, label }) => (
        <NavLink
          key={to}
          to={to}
          className={({ isActive }) => cn(headerLink, isActive && 'text-navy')}
        >
          {label}
        </NavLink>
      ))}
    </>
  );
}

export function SiteHeader({ children }: { children: ReactNode }) {
  return (
    <header className="mx-auto flex max-w-[940px] items-center justify-between gap-6 px-5 py-7 sm:px-10">
      <Link
        to="/"
        className="font-display text-[0.9375rem] font-semibold tracking-tight text-navy"
      >
        dapilot
      </Link>
      <nav className="flex items-center gap-6">{children}</nav>
    </header>
  );
}
