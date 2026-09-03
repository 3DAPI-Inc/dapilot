import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

/** Shared so the landing and the console cannot drift apart at the top. */
export const headerLink =
  'font-sans text-sm text-slate underline-offset-4 transition-colors hover:text-navy hover:underline';

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
