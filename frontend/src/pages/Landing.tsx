import { Link } from 'react-router-dom';
import { SiteHeader, headerLink } from '@/components/site/SiteHeader';

/*
  One page, three blocks: the claim, what "the rest" actually is, and the one
  promise worth making. Copy lives inline because there is little enough of it
  to read in a single pass, and every line has to earn its place.

  The eight stage names are the canonical ones from api/jobs.ts.
*/
const STAGES = 'intake · analyze · slice · preview · research · price · render · copy · publish';

/** The real 3DAPI wordmark, set as a word. See `.logo-word` in index.css. */
function LogoWord() {
  return <span className="logo-word" role="img" aria-label="3DAPI" />;
}

/** The one button on the page, so it is defined once. */
function Cta() {
  return (
    <Link
      to="/upload"
      className="inline-flex h-13 items-center rounded-full bg-navy px-8 font-display text-[1.0625rem] font-semibold tracking-tight text-paper transition-transform hover:-translate-y-0.5 focus-visible:outline-2 focus-visible:outline-offset-3 focus-visible:outline-ice motion-reduce:hover:translate-y-0"
    >
      Open the console
    </Link>
  );
}

export default function Landing() {
  return (
    <div className="min-h-svh bg-paper text-navy">
      <SiteHeader>
        <Link to="/upload" className={headerLink}>
          Open the console
        </Link>
      </SiteHeader>

      <main className="mx-auto max-w-[940px] px-5 sm:px-10">
        {/* The claim. The headline prints itself; nothing else here moves. */}
        <section className="flex min-h-[78svh] flex-col justify-center py-16">
          <h1 className="font-display text-poster font-extrabold">
            <span className="print-line">You design.</span>
            <span className="print-line" style={{ animationDelay: '0.18s' }}>
              <LogoWord /> does
            </span>
            <span className="print-line" style={{ animationDelay: '0.36s' }}>
              the rest.
            </span>
          </h1>

          <p className="mt-9 max-w-[42ch] font-sans text-lg leading-relaxed text-slate">
            It prices your models, photographs them, writes the listings and prints them near the
            buyer.
          </p>

          <div className="mt-10">
            <Cta />
          </div>
        </section>

        {/* The headline promises "the rest". This is the receipt for it. */}
        <section className="border-t border-mist py-20 sm:py-28">
          <h2 className="font-display text-statement font-semibold">The rest.</h2>
          <p className="mt-8 max-w-[46ch] font-sans text-lg leading-relaxed text-slate">
            Reads the geometry. Slices it. Prices it against dapi. Renders the photographs. Finds
            what it competes with. Writes the copy. Publishes when you approve. Drives the print job
            when it sells.
          </p>
          <p className="mt-10 font-sans text-[0.6875rem] uppercase tracking-[0.22em] text-slate/50">
            {STAGES}
          </p>
        </section>

        {/* The one promise. It is also what makes the rest safe to hand over. */}
        <section className="border-t border-mist py-20 sm:py-28">
          <p className="max-w-2xl font-display text-statement font-semibold">
            Nothing goes live until you say so.
          </p>
          <p className="mt-6 max-w-[42ch] font-sans text-lg leading-relaxed text-slate">
            The agent does the work. You approve the calls that spend money or reach a customer.
          </p>
          <div className="mt-10">
            <Cta />
          </div>
        </section>
      </main>

      <footer className="mx-auto max-w-[940px] px-5 pb-12 sm:px-10">
        <div className="flex flex-col gap-2 border-t border-mist pt-7 font-sans text-xs text-slate/60 sm:flex-row sm:items-center sm:justify-between">
          <p>Intake is live today. Orders and fulfilment are next.</p>
          <p>© {new Date().getFullYear()} dapi</p>
        </div>
      </footer>
    </div>
  );
}
