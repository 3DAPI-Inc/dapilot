#!/usr/bin/env python3
"""Triage: what kind of stop a stage hit, and whether a retry could end differently.

  triage.py classify "<reason>" [--error CODE] [--marker failed|blocked] [--retries N] [--changed]
                                                      one stop, as the supervisor would see it; prints the decision
  triage.py codes                                     the catalogue: code, what it means, its policy
  triage.py selftest                                  the fixture table of real reasons; exit 1 on any miss

The supervisor (docs/hosting.md, "Supervisor") reads one message per stopped stage and has two
possible answers: send a `retry` for that stage, or leave it as it is and say why. Which one is
not a judgement about the stage, it is a judgement about whether anything can change between
this attempt and the next. A missing variable fails identically two thousand times; an
overloaded API fails differently a minute later. This module turns a stop into that answer.

Every kind of stop is a class. The abstract `Failure` fixes the shape: a `code` (what a script
would put in its result line's `error` field, tier one), `patterns` on the reason text for
results without a code (tier two: older plugin versions, free text from a worker), and
`decide()`, the policy. A new kind of stop is a new subclass registered in KINDS, with its
patterns, its policy and a line in the self-test; nothing else changes. Order in KINDS matters
only for the text tier, where the first match wins, so the specific kinds come before the
general ones.

Two kinds are deliberately dumb. `Blocked` is a question a stage asked a person
(`needs_human`): no policy retries it, because a retry is not an answer. `Unknown` is every
reason nothing matched: left alone and logged, and that log is the backlog where new codes
and patterns come from. There is no "escalate": a stop the supervisor cannot make work is
already visible on the dashboard exactly as it is.

What a decision needs from the message, all optional (`Stop` below): the reason text, the
`error` code when the script gave one, the marker kind, how many times the stage was already
retried (`retries`, counted by `ledger.py retry`), what it ended with the time before
(`previous`), and whether the seller's configuration fingerprint moved since the stop. The
fingerprint is the backend's: which credential and limit keys are present, the key preview,
the plugin release. It is not computed here; the message carries `changed`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field


# ---------------------------------------------------------------------- the shapes

@dataclass(frozen=True)
class Stop:
    """One stopped stage, as the supervise message describes it."""
    reason: str = ""
    error: str | None = None          # tier one: the script's code, when it gave one
    marker: str = "failed"            # failed | blocked | review
    retries: int = 0                  # how often ledger.py retry already took this stage up again
    previous: str | None = None       # what it ended with the time before, from the ledger record
    changed: bool = False             # the seller's configuration fingerprint moved since the stop
    stage: str | None = None


@dataclass(frozen=True)
class Decision:
    action: str                       # retry | leave
    code: str                         # the kind that decided
    why: str                          # one sentence a person can read on the dashboard
    delay_s: int = 0                  # for a retry: wait this long before sending it
    wait_for: str | None = None       # for a leave: the change that would make a retry worth it
    note: str = ""                    # for a retry: carried as the message's `reason`, so the ledger keeps it


# ---------------------------------------------------------------------- the base class

class Failure:
    """One kind of stop. Subclasses set `code`, `meaning`, `patterns` and override `decide`."""

    code: str = ""
    meaning: str = ""
    patterns: tuple[str, ...] = ()
    _compiled: tuple[re.Pattern[str], ...] = ()

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        cls._compiled = tuple(re.compile(p, re.IGNORECASE) for p in cls.patterns)

    @classmethod
    def matches(cls, stop: Stop) -> bool:
        """Tier one when the code is there, tier two on the text otherwise. A wrong code is still a code:
        a script that names its error is trusted over what the text looks like."""
        if stop.error:
            return stop.error == cls.code
        text = stop.reason or ""
        return any(p.search(text) for p in cls._compiled)

    def decide(self, stop: Stop) -> Decision:
        raise NotImplementedError

    # helpers for the subclasses
    def leave(self, stop: Stop, why: str, wait_for: str | None = None) -> Decision:
        return Decision("leave", self.code, why, wait_for=wait_for)

    def retry(self, stop: Stop, why: str, delay_s: int = 0) -> Decision:
        return Decision("retry", self.code, why, delay_s=delay_s, note=f"{self.code}: {why}")

    def same_as_before(self, stop: Stop) -> bool:
        """The stage already failed this way once and a retry proved nothing: the ledger's `previous` is the
        reason it ended with before the last retry, and `retries` says there was one."""
        return stop.retries > 0 and bool(stop.previous) and _norm(stop.previous) == _norm(stop.reason)


def _norm(s: str | None) -> str:
    return re.sub(r"\d+", "N", (s or "").strip().lower())


# ---------------------------------------------------------------------- the kinds

class Blocked(Failure):
    code = "blocked"
    meaning = "a question a stage asked a person (needs_human); a retry is not an answer"
    patterns = ()

    @classmethod
    def matches(cls, stop: Stop) -> bool:
        return stop.marker == "blocked"

    def decide(self, stop: Stop) -> Decision:
        return self.leave(stop, "the stage asked a person; it waits for the answer", wait_for="a person")


class Review(Failure):
    """A done stage whose output a check found worth a look (`<stage>.review.json`, ledger.py 2026-10-23). The
    run went on; the question is only whether a fresh attempt would plainly do better. Two problems say so,
    a price under its own floor and a design with nothing kept, and only once; everything else is noted on
    the dashboard and left, because a review is information for the seller, not a stop. The redo itself is
    `ledger.py retry`, which refuses when a later stage already used the outputs, and that refusal is an
    answer too."""
    code = "review"
    meaning = "a done stage whose output a check flagged; redone once for a plain defect, otherwise noted"
    patterns = ()
    redo_patterns = (r"under its own floor", r"no photos kept", r"no product name")
    _redo = tuple(re.compile(p, re.IGNORECASE) for p in redo_patterns)

    @classmethod
    def matches(cls, stop: Stop) -> bool:
        return stop.marker == "review"

    def decide(self, stop: Stop) -> Decision:
        if stop.retries == 0 and any(p.search(stop.reason or "") for p in self._redo):
            return self.retry(stop, "a plain defect in the output; one redo while nothing later has used it")
        return self.leave(stop, "done, with a note for the seller: " + (stop.reason or "see the review marker")[:200],
                          wait_for="the seller's judgement; Redo on the dashboard")


class ConfigMissing(Failure):
    code = "config.env_missing"
    meaning = "a credential or variable the stage needs is not set for this seller or this runner"
    patterns = (
        r"must be set", r"\bnot set\b", r"is not set", r"no api key found", r"missing (api key|credential|token)",
        r"--billing api but", r"not configured on this server",
    )

    def decide(self, stop: Stop) -> Decision:
        if stop.changed:
            return self.retry(stop, "the configuration changed since the stop")
        return self.leave(stop, "a value is missing and a retry cannot supply it",
                          wait_for="the missing value to be set")


class KeyRejected(Failure):
    code = "config.key_rejected"
    meaning = "a credential is present and the platform behind it refuses it"
    patterns = (
        r"invalid api key", r"fix external api key", r"invalid or inactive api key", r"did not accept the key",
        r"\b401\b", r"\b403\b", r"unauthori[sz]ed", r"authentication_error", r"invalid_grant",
    )

    def decide(self, stop: Stop) -> Decision:
        if stop.changed:
            return self.retry(stop, "the configuration changed since the stop")
        return self.leave(stop, "a credential is refused and a retry sends the same one",
                          wait_for="the credential to be replaced")


class UpstreamQuota(Failure):
    code = "upstream.quota"
    meaning = "a platform limit that opens again on its own: a daily cap, a window, a count of prior calls"
    patterns = (r"daily (quote )?cap", r"quota", r"ten (prior )?quotes", r"limit reached", r"too many requests")

    def decide(self, stop: Stop) -> Decision:
        if stop.retries >= 2:
            return self.leave(stop, "the limit held through two retries", wait_for="the next day")
        return self.retry(stop, "a limit that opens again with time", delay_s=6 * 3600)


class UpstreamTransient(Failure):
    code = "upstream.transient"
    meaning = "the platform behind the stage did not answer properly this time"
    patterns = (
        r"timed? ?out", r"rate limited", r"connection failed", r"temporarily", r"service unavailable",
        r"\b5\d\d\b", r"\b429\b", r"overloaded", r"api unavailable after retries", r"api error \d+ before any work",
        r"still \w+ after \d+s", r"try again later", r"internal error", r"deadline of .* reached",
        r"exceeded \d+(\.\d+)?s$",
    )
    backoff_s = (120, 600, 3600)

    def decide(self, stop: Stop) -> Decision:
        if stop.retries >= len(self.backoff_s):
            return self.leave(stop, f"still failing after {len(self.backoff_s)} spaced retries",
                              wait_for="the platform to recover; retry by hand")
        return self.retry(stop, "a transient answer from the platform", delay_s=self.backoff_s[stop.retries])


class ContentFile(Failure):
    code = "content.file"
    meaning = "the model file itself: geometry the slicer or the analysis refuses"
    patterns = (
        r"no geometry", r"not watertight", r"can ?not be parsed", r"no valid geometry", r"exceeds the build plate",
        r"empty mesh", r"unsupported format", r"skip this model", r"slicer exited with code", r"analysis says \d+ bod",
    )

    def decide(self, stop: Stop) -> Decision:
        return self.leave(stop, "the file is the problem; the same bytes fail the same way",
                          wait_for="a different file, which is a different item")


class ContentMarket(Failure):
    code = "content.market"
    meaning = "the market gave nothing to work with: no priced comparables, no viable price"
    patterns = (r"no priced comparables", r"no comparables", r"no sellable price", r"below the floor")

    def decide(self, stop: Stop) -> Decision:
        return self.leave(stop, "the market answer does not change by asking again",
                          wait_for="a person's price or a different product")


class WorkerTimeout(Failure):
    code = "worker.timeout"
    meaning = "the worker ran out of wall clock or budget before it finished"
    patterns = (r"exceeded \d+s wall clock", r"process group killed", r"exceeded \d+ minutes with no result",
                r"budget", r"max turns", r"turns exhausted")

    def decide(self, stop: Stop) -> Decision:
        if stop.retries >= 1:
            return self.leave(stop, "ran out of time twice; the stage needs a smaller job or a larger budget",
                              wait_for="a change to the stage's budget")
        return self.retry(stop, "one more attempt with the time it has")


class WorkerMalformed(Failure):
    code = "worker.malformed"
    meaning = "the worker ended without a usable result: no JSON, no result line, outputs it claimed are missing"
    patterns = (
        r"non-json output", r"without a result line", r"claimed ok but outputs missing", r"malformed result",
        r"printed no json", r"produced no json", r"result is not json", r"attempts exhausted",
    )

    def decide(self, stop: Stop) -> Decision:
        if self.same_as_before(stop) or stop.retries >= 1:
            return self.leave(stop, "the worker failed the same way after a retry with the reason attached",
                              wait_for="a change to the skill or the plugin release")
        return self.retry(stop, "one retry with the reason in the brief, as the hierarchy allows")


class RuntimeToolMissing(Failure):
    code = "runtime.tool_missing"
    meaning = "the runner image lacks a tool the stage needs: the CLI, a browser, a Python package"
    patterns = (r"claude cli not on path", r"not on path", r"no such file or directory", r"command not found",
                r"modulenotfounderror", r"no module named", r"chromium", r"no plugin at")

    def decide(self, stop: Stop) -> Decision:
        return self.leave(stop, "the image is missing a tool; every retry on this image fails the same way",
                          wait_for="a new runner image")


class Unknown(Failure):
    code = "unknown"
    meaning = "nothing matched; left alone and logged, so the reason can become a code or a pattern"

    @classmethod
    def matches(cls, stop: Stop) -> bool:
        return True

    def decide(self, stop: Stop) -> Decision:
        return self.leave(stop, "a reason the supervisor has no rule for", wait_for="a person")


# Order matters for the text tier only: specific before general, and Unknown last.
KINDS: tuple[type[Failure], ...] = (
    Blocked, ConfigMissing, KeyRejected, UpstreamQuota, ContentFile, ContentMarket,
    WorkerTimeout, WorkerMalformed, RuntimeToolMissing, UpstreamTransient, Unknown,
)
BY_CODE = {k.code: k for k in KINDS}


def classify(stop: Stop) -> Failure:
    """The kind of this stop. A known code wins outright; otherwise the first kind whose text pattern matches."""
    if stop.marker == "blocked":
        return Blocked()
    if stop.marker == "review":
        return Review()
    if stop.error and stop.error in BY_CODE:
        return BY_CODE[stop.error]()
    for kind in KINDS:
        if kind is Blocked:
            continue
        if kind.matches(stop):
            return kind()
    return Unknown()


def decide(stop: Stop) -> Decision:
    return classify(stop).decide(stop)


# ---------------------------------------------------------------------- the self-test
# Real reasons, mostly from the staging runs of 2026-10-21 and 2026-10-22, plus the director's and
# the ledger's own templates. The expected code is the contract; a change here is a policy change.
FIXTURES: tuple[tuple[str, str], ...] = (
    ("ETSY_KEYSTRING and ETSY_SHARED_SECRET must be set (or pass --env store/.env)", "config.env_missing"),
    ("DAPI_API_KEY or DAPI_API_URL not set", "config.env_missing"),
    ("--billing api but ANTHROPIC_API_KEY is not set", "config.env_missing"),
    ("worker ended without a result line: Invalid API key · Fix external API key", "config.key_rejected"),
    ("rejected: the platform did not accept the key", "config.key_rejected"),
    ("{\"message\":\"Invalid or inactive API key.\",\"statusCode\":401}", "config.key_rejected"),
    ("API error 529 before any work; retrying launch in 60s (3/3)", "upstream.transient"),
    ("API unavailable after retries", "upstream.transient"),
    ("still processing after 600s; quote.pending.json kept, rerun resumes polling", "upstream.transient"),
    ("connection failed: [Errno 111] Connection refused", "upstream.transient"),
    ("rate limited twice", "upstream.transient"),
    ("fal queue: deadline of 900s reached before lifestyle_2; run produce again", "upstream.transient"),
    ("daily quote cap reached for this key", "upstream.quota"),
    ("Slicer exited with code 1 without reporting; analysis says 300 bodies, 258 closed. Skip this model?", "content.file"),
    ("no geometry in file", "content.file"),
    ("model exceeds the build plate on every axis", "content.file"),
    ("no priced comparables for 'articulated cat'; the market band is empty", "content.market"),
    ("claude -p exit 1, non-JSON output: Error: ...", "worker.malformed"),
    ("worker ended without a result line: I have completed the analysis", "worker.malformed"),
    ("claimed ok but outputs missing: ['comparables.json']", "worker.malformed"),
    ("attempts exhausted", "worker.malformed"),
    ("worker exceeded 900s wall clock; process group killed", "worker.timeout"),
    ("attempt 2 exceeded 60 minutes with no result", "worker.timeout"),
    ("claude CLI not on PATH (/usr/bin:/bin) nor in ~/.local/bin", "runtime.tool_missing"),
    ("ModuleNotFoundError: No module named 'trimesh'", "runtime.tool_missing"),
    ("something nobody has seen before", "unknown"),
)


def selftest() -> int:
    bad = 0
    for reason, expected in FIXTURES:
        got = classify(Stop(reason=reason)).code
        mark = "ok  " if got == expected else "MISS"
        if got != expected:
            bad += 1
        print(f"{mark}  {expected:22s} {got:22s} {reason[:70]}")
    # the code tier beats the text tier, and a blocked marker beats both
    assert classify(Stop(reason="timed out", error="content.file")).code == "content.file"
    assert classify(Stop(reason="timed out", marker="blocked")).code == "blocked"
    # a review marker: one redo for a plain defect, a note for everything else and after the redo
    r0 = decide(Stop(reason="price.json: the price 7.5 is under its own floor 8.99", marker="review"))
    r1 = decide(Stop(reason="price.json: the price 7.5 is under its own floor 8.99", marker="review", retries=1))
    r2 = decide(Stop(reason="research says not viable: the market median sits under the one-off break-even", marker="review"))
    assert r0.action == "retry" and r0.delay_s == 0 and r1.action == "leave" and r2.action == "leave" and r2.wait_for
    # policies: a transient retries with a growing delay and then stops; a config stop waits for a change
    d0, d1, d3 = (decide(Stop(reason="timed out", retries=n)) for n in (0, 1, 3))
    assert (d0.action, d0.delay_s) == ("retry", 120) and d1.delay_s == 600 and d3.action == "leave"
    c0 = decide(Stop(reason="X must be set"))
    c1 = decide(Stop(reason="X must be set", changed=True))
    assert c0.action == "leave" and c0.wait_for and c1.action == "retry"
    # a worker that fails the same way after its one retry is left alone
    w = decide(Stop(reason="claude -p exit 1, non-JSON output: x", retries=1, previous="claude -p exit 1, non-JSON output: y"))
    assert w.action == "leave"
    print(f"\n{len(FIXTURES) - bad} of {len(FIXTURES)} fixtures classified as expected; policy checks passed")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("classify", help="decide one stop")
    c.add_argument("reason"); c.add_argument("--error"); c.add_argument("--marker", default="failed", choices=("failed", "blocked", "review"))
    c.add_argument("--retries", type=int, default=0); c.add_argument("--previous"); c.add_argument("--changed", action="store_true")
    sub.add_parser("codes", help="the catalogue of kinds")
    sub.add_parser("selftest", help="classify the fixture reasons; exit 1 on a miss")
    a = ap.parse_args()
    if a.cmd == "classify":
        stop = Stop(reason=a.reason, error=a.error, marker=a.marker, retries=a.retries, previous=a.previous, changed=a.changed)
        print(json.dumps(asdict(decide(stop)), indent=2))
        return 0
    if a.cmd == "codes":
        for k in KINDS:
            print(f"{k.code:22s} {k.meaning}")
        return 0
    return selftest()


if __name__ == "__main__":
    sys.exit(main())
