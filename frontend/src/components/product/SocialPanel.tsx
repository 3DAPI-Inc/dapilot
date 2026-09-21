import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ExternalLink } from 'lucide-react';
import { getSocial, publishSocialPost, refreshSocialMetrics, saveSocialPost, type Social, type SocialPost, type SocialSession } from '@/api/social';
import { Notice } from '@/components/site/ConsoleShell';
import { Zoomable } from '@/components/site/Lightbox';
import { Spinner } from '@/components/site/Spinner';
import { Button } from '@/components/ui/button';
import { formatWhen } from '@/lib/format';
import { cn } from '@/lib/utils';

/*
  The product on social platforms. A post is made of what the Etsy listing
  already carries, so there is nothing to upload here: the words are edited,
  and Post is the one decision. The server refuses to send anything the
  platform would refuse, and never sends the same post twice.
*/

const h3 = 'font-display text-[0.9375rem] font-semibold tracking-tight text-navy';
const FORMAT: Record<SocialPost['format'], string> = { reel: 'Reel', carousel: 'Carousel', image: 'Photo' };
const PLATFORM: Record<string, string> = { instagram: 'Instagram', tiktok: 'TikTok', x: 'X', linkedin: 'LinkedIn' };
const SESSION: Record<SocialSession['state'], { dot: string; tone: string; phrase: string }> = {
  ok: { dot: 'bg-ok', tone: 'text-slate', phrase: 'Signed in' },
  expiring: { dot: 'bg-ok', tone: 'text-slate', phrase: 'Signed in, renewing' },
  needs_login: { dot: 'bg-destructive', tone: 'text-destructive', phrase: 'Needs a sign-in' },
  challenge: { dot: 'bg-warn', tone: 'text-warn', phrase: 'The platform is asking for a person' },
};
const STATE: Record<SocialPost['state'], { dot: string; tone: string; phrase: string }> = {
  draft: { dot: 'bg-slate/40', tone: 'text-slate', phrase: 'Not posted' },
  pending: { dot: 'bg-warn', tone: 'text-warn', phrase: 'Sent, waiting for the platform to show it' },
  posted: { dot: 'bg-ok', tone: 'text-ok', phrase: 'Posted' },
};

function Dot({ dot, tone, children }: { dot: string; tone: string; children: React.ReactNode }) {
  return (
    <span className={cn('inline-flex items-center gap-2 font-sans text-sm', tone)}>
      <span className={cn('size-1.5 shrink-0 rounded-full', dot)} aria-hidden="true" />
      {children}
    </span>
  );
}

const tagsOf = (text: string) => text.split(/[\s,]+/).map((t) => t.replace(/^#/, '').trim()).filter(Boolean);

function PostCard({ jobId, post, canPost }: { jobId: string; post: SocialPost; canPost: boolean }) {
  const client = useQueryClient();
  const [caption, setCaption] = useState(post.caption);
  const [tags, setTags] = useState(post.hashtags.join(' '));
  // A save or a post answers with the whole panel; take the server's words back when they change.
  useEffect(() => setCaption(post.caption), [post.caption]);
  useEffect(() => setTags(post.hashtags.join(' ')), [post.hashtags]);

  const settle = (social: Social) => client.setQueryData(['job', jobId, 'social'], social);
  const save = useMutation({ mutationFn: () => saveSocialPost(jobId, post.id, { caption, hashtags: tagsOf(tags) }), onSuccess: settle });
  const publish = useMutation({
    mutationFn: async () => {
      if (dirty) await saveSocialPost(jobId, post.id, { caption, hashtags: tagsOf(tags) });
      return publishSocialPost(jobId, post.id);
    },
    onSuccess: (res) => settle(res.social),
  });

  const posted = post.state === 'posted';
  const dirty = caption.trim() !== post.caption || tagsOf(tags).join(' ') !== post.hashtags.join(' ');
  const state = STATE[post.state];
  const failure = publish.error ?? save.error;
  const label = `${PLATFORM[post.platform] ?? post.platform} ${FORMAT[post.format].toLowerCase()}`;

  return (
    <article className="grid gap-6 border-t border-mist pt-8 first:border-t-0 first:pt-0 md:grid-cols-[13rem_minmax(0,1fr)]">
      <div className="grid grid-cols-3 content-start gap-2">
        {post.media.map((m, i) =>
          m.url ? (
            <Zoomable
              key={m.url}
              media={{ kind: m.kind, src: m.url, label: `${label}, ${m.kind === 'video' ? 'the listing video' : `photo ${i + 1}`}` }}
              className={cn('overflow-hidden rounded-lg border border-mist bg-mist/40', i === 0 ? 'col-span-3 aspect-square' : 'aspect-square')}
            />
          ) : (
            <span key={i} className="col-span-3 rounded-lg border border-dashed border-mist p-4 font-sans text-xs text-slate">
              This file is not on the Etsy listing yet.
            </span>
          ),
        )}
      </div>

      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
          <h3 className={h3}>{label.charAt(0).toUpperCase() + label.slice(1)}</h3>
          <Dot dot={state.dot} tone={state.tone}>
            {state.phrase}
            {posted && post.postedAt ? `, ${formatWhen(post.postedAt)}` : ''}
          </Dot>
        </div>

        {posted ? (
          <>
            <p className="mt-3 max-w-[64ch] font-sans text-sm leading-relaxed whitespace-pre-line text-slate">{post.caption}</p>
            <p className="mt-2 font-sans text-sm text-slate/80">{post.hashtags.map((t) => `#${t}`).join(' ')}</p>
            {post.metrics ? (
              <dl className="mt-5 flex flex-wrap gap-x-8 gap-y-3">
                {Object.entries(post.metrics).map(([name, value]) => (
                  <div key={name}>
                    <dt className="font-sans text-xs text-slate capitalize">{name}</dt>
                    <dd className="font-display text-lg font-semibold tabular-nums text-navy">{value ?? 0}</dd>
                  </div>
                ))}
              </dl>
            ) : null}
            {post.url ? (
              <a href={post.url} target="_blank" rel="noreferrer" className="mt-4 inline-flex items-center gap-1.5 font-sans text-sm text-navy underline underline-offset-4">
                Open on {PLATFORM[post.platform] ?? post.platform}
                <ExternalLink className="size-3.5" aria-hidden="true" />
              </a>
            ) : null}
          </>
        ) : (
          <>
            <label className="mt-3 block font-sans text-xs text-slate" htmlFor={`${post.id}-caption`}>Caption</label>
            <textarea
              id={`${post.id}-caption`}
              value={caption}
              onChange={(e) => setCaption(e.target.value)}
              rows={4}
              className="mt-1.5 w-full rounded-lg border border-mist bg-white px-3 py-2 font-sans text-sm leading-relaxed text-navy outline-none focus-visible:border-navy/40 focus-visible:ring-2 focus-visible:ring-ice"
            />
            <label className="mt-3 block font-sans text-xs text-slate" htmlFor={`${post.id}-tags`}>Hashtags, separated by spaces</label>
            <input
              id={`${post.id}-tags`}
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-mist bg-white px-3 py-2 font-sans text-sm text-navy outline-none focus-visible:border-navy/40 focus-visible:ring-2 focus-visible:ring-ice"
            />
            {post.errors.length ? (
              <ul className="mt-3 grid gap-1 font-sans text-sm text-destructive">
                {post.errors.map((e) => <li key={e}>{e.charAt(0).toUpperCase() + e.slice(1)}.</li>)}
              </ul>
            ) : null}
            {failure ? <p className="mt-3 font-sans text-sm text-destructive">{failure instanceof Error ? failure.message : 'The request failed.'}</p> : null}
            <div className="mt-5 flex flex-wrap gap-3">
              <Button
                size="lg"
                className="h-11 px-6 text-[0.9375rem]"
                disabled={!canPost || publish.isPending || save.isPending || !caption.trim() || (post.errors.length > 0 && !dirty)}
                onClick={() => publish.mutate()}
              >
                {publish.isPending ? <Spinner className="size-4" /> : null}
                {publish.isPending ? 'Posting' : post.state === 'pending' ? 'Check again' : `Post to ${PLATFORM[post.platform] ?? post.platform}`}
              </Button>
              <Button size="lg" variant="outline" className="h-11 px-6 text-[0.9375rem]" disabled={!dirty || save.isPending || publish.isPending} onClick={() => save.mutate()}>
                {save.isPending ? <Spinner className="size-4" /> : null}
                Save the words
              </Button>
            </div>
          </>
        )}
      </div>
    </article>
  );
}

export function SocialPanel({ jobId }: { jobId: string }) {
  const client = useQueryClient();
  const social = useQuery({
    queryKey: ['job', jobId, 'social'],
    queryFn: () => getSocial(jobId),
    // A post the platform has not shown yet is confirmed by asking again, not by sending again.
    refetchInterval: (q) => (q.state.data?.posts.some((p) => p.state === 'pending') ? 15000 : false),
  });
  const numbers = useMutation({ mutationFn: () => refreshSocialMetrics(jobId), onSuccess: (s) => client.setQueryData(['job', jobId, 'social'], s) });

  if (social.isPending) return <Notice><span className="inline-flex items-center gap-2"><Spinner className="size-4" />Reading the accounts.</span></Notice>;
  if (social.isError) return <Notice title="The posts did not load" tone="bad">{social.error instanceof Error ? social.error.message : 'The request failed.'}</Notice>;
  const data = social.data;
  if (!data.ready) return <Notice title="Nothing to post yet">{data.reason}</Notice>;
  if (!data.posts.length) return <Notice title="No captions written">Posts appear here once their words exist. Until the caption skill is built they are written by hand.</Notice>;

  const usable = new Set(data.sessions.filter((s) => s.state === 'ok' || s.state === 'expiring').map((s) => s.platform));
  const anyPosted = data.posts.some((p) => p.state === 'posted');
  return (
    <div className="grid gap-8 p-6 sm:p-8">
      <div className="flex flex-wrap items-center justify-between gap-x-8 gap-y-2">
        <ul className="flex flex-wrap gap-x-8 gap-y-1">
          {data.sessions.map((s) => (
            <li key={`${s.platform}-${s.account}`}>
              <Dot dot={SESSION[s.state].dot} tone={SESSION[s.state].tone}>
                {PLATFORM[s.platform] ?? s.platform}: {s.state === 'ok' || s.state === 'expiring' ? s.note ?? SESSION[s.state].phrase : SESSION[s.state].phrase}
              </Dot>
            </li>
          ))}
        </ul>
        {anyPosted ? (
          <button type="button" onClick={() => numbers.mutate()} disabled={numbers.isPending} className="font-sans text-sm text-navy underline underline-offset-4 disabled:opacity-50">
            {numbers.isPending ? 'Reading the numbers' : 'Refresh the numbers'}
          </button>
        ) : null}
      </div>
      {data.posts.map((post) => <PostCard key={post.id} jobId={jobId} post={post} canPost={usable.has(post.platform)} />)}
    </div>
  );
}
