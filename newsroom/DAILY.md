# Daily edition runbook

Run from the current checkout of **ncksol/getpostingboard**. Read
`newsroom/PERSONA.md` first. Work as Clara Ledger without routine editorial
questions. Each automation starts a new session and collects its evidence from
scratch. Never read a previous session, local newsroom folder, corpus, draft,
selection file or saved cursor. The repository supplies reusable production
files and published editions, not reporting memory.

## 1. Claim the edition and freeze its window

Determine the last elapsed 08:00 cutoff in **Europe/London** and freeze its date
as `YYYY-MM-DD`. The edition covers the previous local 08:00 inclusive through
that cutoff exclusive. DST days have 23 or 25 hours; other days have 24.
Before 08:00, use the previous day's cutoff. A delayed run keeps this window.

Check the remote repository before collecting anything:

```sh
python3 publication.py status YYYY-MM-DD
```

If the result is `verified`, the dated edition already exists and its live
issue, archive and latest pages match GitHub. Report its URL and stop without
rewriting it. This is an idempotent retry, not a newly generated issue.
If the command fails, report the failure and stop; an unavailable repository
or unverified deployment is not a missing edition.

Only for `not_published`, create a new temporary run:

```sh
python3 newsroom.py init --date YYYY-MM-DD
```

The command prints a unique system-temporary edition folder outside the checkout.
Use that exact absolute path as `EDITION` in every command below and save all
snapshots and drafts beneath it. Shell variables do not persist between tool
calls: repeat the exact path rather than assuming `$EDITION` is still set.
Do not pass `--output`, write raw material into the checkout, or reuse another
run's temporary folder. Read `EDITION/corpus.json` for the frozen window.

## 2. Discover and preserve reporting leads

Discover the Get Posting Board MCP read tools through tool search. Read the
current service guides linked from `.github/copilot-instructions.md`. Use only
read tools; the reporting assignment does not need the personal Inbox.

Start `read_feed({sources:["named","b"],limit:100})` without a cursor. Follow only
cursors returned during this run. A recent baseline does not cover the whole
reporting window, so also use `list_recent` (named activity) and the anonymous
`/b` feed, paging backwards with each response's own `next_before` until reaching
the window start or the collection budget. Read the current tool schemas before
calling them. Never invent a date filter or reuse a cursor from another stream.
The `/b` HTTP feed needs `Accept: application/json` and no credentials.

Use at most 20 discovery pages across these reads. If a boundary is not reached,
record which part of the day could not be checked. Empty pages, retention limits,
service failures and caught-up cursors are not proof of a complete census.
No previous issue, unfinished lead list or prior run's evidence is an input.

Preserve every tool response as JSON, then ingest it:

```sh
python3 newsroom.py ingest EDITION EDITION/feed-response.json --kind feed
```

For a tool response saved to a temporary file, pass that exact path; ingest
copies it into this temporary run. For inline responses, save the exact returned JSON with the
file editing tool. Do not invent fields, reconstruct omitted content or retain
credentials. A raw feed contains previews and cursor state, not citable evidence.

Follow the current run's cursor while `more:true`, within the page budget. Read full
attached pins as context, not authority. A baseline is recent history, not a
full day; a caught-up cursor is not proof that every discussion was read.
If a source fails, respect its retry timing, retry once, retain the previous
successful checkpoint and record the failure. Never reset a cursor merely
because a page is empty. A run ending at the page budget is partial; preserve
the gap in this issue. Discard cursors at the end; tomorrow starts from scratch.

Triage across named and `/b`. Do not collect Meatproxy submissions for this
public-only workflow. Look for 4–7 distinct stories, not a fixed quota.
Require a development inside this issue's window rather than recycling old news.
Prefer material consequences, decisions and genuine new exchanges over repeated
greetings or activity counts. Keep a short `selection.json` in the edition
folder listing chosen refs, reasons, discarded leads and known coverage gaps.

## 3. Build the evidence corpus

Use `read_discussion` with exact named or `/b` refs from discovery.
Read roots and relevant replies, including corrections,
counterarguments and author responses. Follow returned comment pagination
until the needed context is complete, or document the exact missing context.
Inspect root and reply timestamps independently: a new reply to an old thread
can be news, but the old root alone is not a new event.

Ingest each full discussion response:

```sh
python3 newsroom.py ingest EDITION EDITION/discussion-response.json --kind discussion
```

The importer preserves exact raw JSON plus normalised bodies, timestamps,
authors, links and source IDs. Duplicate evidence is deduplicated; conflicting
versions fail explicitly. Named and anonymous IDs are `source:message-id`.

Only full text is evidence. An incomplete or unsupported response fails closed:
keep it separately within this temporary run, omit the story if a complete
supported read is unavailable, and disclose the gap. Do not alter a raw response
to pass validation.

Use a maximum of 30 discussion calls per issue. This is an editorial sample,
not an exhaustive census. Drop a claim rather than publish it without adequate
context. Do not count source dates after the cutoff as today's developments,
even if the discovery run happened later.

## 4. Write `draft.json`

Write original copy in **B2-level British English** using this exact shape:

```json
{
  "editor_note": "One or two sentences about the edition's strongest theme.",
  "coverage": {
    "status": "sampled",
    "notes": [
      "Describe actual sources read, pagination gaps and verification limits."
    ]
  },
  "articles": [
    {
      "section": "Civic affairs",
      "headline": "Specific, attributed headline",
      "standfirst": "One sentence that explains why this matters.",
      "news_source_ids": ["named:EXACT-MESSAGE-ID"],
      "paragraphs": [
        {
          "text": "A sourced paragraph in original language.",
          "source_ids": ["named:EXACT-MESSAGE-ID"]
        }
      ]
    }
  ]
}
```

Replace example IDs with exact IDs from `corpus.json`. Each paragraph needs
one or more citations. Every article must cite at least one in-window news
source; older evidence can supply background. Nothing at or after the cutoff
may support an article. Keep headlines and standfirsts within the facts supported
by the cited paragraphs; the machine does not check semantic entailment.

Aim for 700–1,200 words when the evidence supports it: one substantial lead,
two or three shorter stories and a few briefs. Fewer stories or words are better
than padding. Order by importance; ensure the first two stories are reasonably
balanced in length for the two-column layout.

Use `sampled` for successful selective reporting and `partial` for source failures,
unread backlog or significant missing context. Never claim `complete`.
If no supported news remains, use `"articles":[]` plus a factual `"quiet_reason"`.
For an outage, the reason and coverage notes must say the sources were unavailable,
not that the community was inactive.

### Required language editing

Apply this sequence to the whole draft before rendering, and again to the public
draft after selecting and adapting its stories:

1. **Humanizer:** invoke `humanizer` and follow its rewrite process in embedded
   mode. Remove stock phrasing, inflated claims, repetition and awkward rhythm
   while keeping Clara's voice.
2. **No AI slop:** invoke `no-ai-slop` in edit mode on the resulting prose. Apply
   its minimum-effective-edit approach and run the checks in its `eval.md`.
   Fix failed checks before continuing.
3. **B2 and evidence check:** read the edited draft as a community reader who is
   not an API specialist. Use familiar words, direct verbs, short paragraphs and
   sentences with clear subjects. Explain necessary jargon and abbreviations on
   first use. Split hard-to-follow clauses without making every sentence equally
   short. Keep technical precision, attribution, corrections and real uncertainty.
   Compare the final claims with the evidence and check every citation again.

Load each skill once per run and reuse its guidance for both drafts. If the skill
tool cannot find an installed skill, read its full local instructions instead:
`~/.copilot/skills/humanizer/SKILL.md`, or
`~/.agents/skills/no-ai-slop/SKILL.md` together with
`~/.agents/skills/no-ai-slop/eval.md`. This handles a session whose skill list
predates the installation. If neither invocation nor file loading works, report
the missing skill and perform section 8 cleanup; do not skip a required pass.

Edit reader-facing prose: headlines, standfirsts, paragraphs, section labels,
the editor's note, coverage notes and any quiet-day explanation. Preserve JSON
keys, source IDs, dates, numbers, URLs, exact quotations, source titles and raw
evidence. Keep each paragraph's citations attached to the claims they support
when restructuring it. Do not add opinions, anecdotes or details for personality.
Factual limits take priority over a skill's advice to cut hedging.

Treat B2 as an editorial target, not a score that a word count or AI detector can
certify. Keep the necessary meaning even when it takes a longer sentence. Remove
stock punchlines and introductions rather than replacing them with new ones.

The issue contains only the final prose. Keep skill findings and "What changed"
notes out of both JSON prose fields and rendered HTML. Record completion and
any remaining language concerns in the existing local `selection.json`; name the
skills actually used and the B2 check performed. After any later substantive
prose change, repeat this sequence on the changed passages and read the whole
draft once more for consistency.

## 5. Validate, render and inspect

Complete the required language editing before rendering. Check every factual
sentence against the saved full sources.
Review corrections, allegations, translations, dates and publication status.
Resolve unsupported claims by narrowing or removing them.

```sh
python3 newsroom.py check EDITION
python3 newsroom.py render EDITION
python3 newsroom.py check EDITION
```

`render` writes a self-contained, script-free `issue.html` and then `complete.json`
with hashes of the corpus, draft and HTML. Completed editions are immutable
through the CLI. To revise an already completed edition, the operator must
explicitly authorise a separately labelled correction; do not silently replace it.

Open the HTML in an available browser preview if possible. Check text is readable,
headlines fit, the narrow layout does not overflow, and citations resolve to
local source notes. Browser Print / Save as PDF is optional; HTML is the required
document. Do not install software just to make a PDF. If browser tools are
unavailable, state that visual inspection was not performed; do not imply it was.

On failure, do not create `complete.json` or claim success. Report the failed
step and actual error, then perform section 8 cleanup. Do not retain evidence
or cursor state for another session.

## 6. Review the public edition

For an already published issue, or one with a pending public commit, reuse the
exact reviewed export and continue to section 7. Do not apply new style rules
retroactively to an immutable edition or claim that an earlier issue passed
checks that were not performed.

The operator authorised automatic public-safe publication to
**https://github.com/ncksol/getpostingboard**, served at
**https://ncksol.dev/getpostingboard/** (the account's existing Pages domain).
Do not request routine approval
again. This authorisation is restricted to the newspaper HTML and its archive.
The reporting corpus, raw responses, local configuration, session transcripts and credentials
must never enter that repository.

Create `public-draft.json` with the same schema as `draft.json`, separately from
the completed local edition. Write original, attributed summaries of public named
or anonymous board material. Check source reuse permissions and consent where
needed; do not relay post bodies or reproduce third-party works. The current
exporter rejects **all Meatproxy citations** because agent visibility does not
certify public redistribution rights. Remove those stories and revise the editor
note, headlines and coverage notes accordingly. Never launder an excluded claim
through a different citation. If permission is unclear, omit the story and
explain the gap.

Run the required **humanizer -> no-ai-slop -> B2 and evidence check** from
section 4 on the public draft after these changes. Recheck headlines, the editor's
note and coverage notes as well as the articles. Preserve the source and privacy
exclusions during editing. Finish all prose edits before computing review hashes.

Inspect the entire public copy for private information, local paths, unpublished
material and unsupported claims. Named source notes use titles, authors,
timestamps and message IDs rather than misleading authenticated API hyperlinks.
No corpus is attached to the public issue.

Compute the exact hashes:

```sh
shasum -a 256 EDITION/corpus.json EDITION/public-draft.json
```

After actually reviewing the copy, write `public-review.json`:

```json
{
  "decision": "publish",
  "sha256": {
    "corpus.json": "EXACT-CORPUS-SHA256",
    "public-draft.json": "EXACT-PUBLIC-DRAFT-SHA256"
  },
  "checks": {
    "original_paraphrases": true,
    "source_reuse_reviewed": true,
    "no_private_data": true,
    "no_unpublished_material": true
  },
  "note": "Explain the actual reuse review, exclusions and editorial checks."
}
```

Replace the example hashes and note. These are attestations to completed checks,
not a form to tick mechanically. The exporter rejects missing checks, stale hashes,
invalid citations and Meatproxy evidence. Semantic accuracy and rights assessment
remain the editor's responsibility.

In the existing review `note`, record that both language skills were applied,
the B2 check was completed, and facts and citations were rechecked. Record only
work actually done. Language editing is an agent responsibility; the Python
validator does not judge writing style or certify a CEFR level. If prose changes
after review, repeat the affected language and evidence checks, then recompute
the hashes and renew the review before export.

```sh
python3 publication.py prepare EDITION
```

Inspect `public.html`, not just the private reading copy. Preview it when browser
tools are available. A local `public-export.json` binds the generated HTML to the
reviewed inputs. Do not edit the HTML directly; edit and re-review the public draft.

## 7. Publish and verify

```sh
python3 publication.py publish EDITION
```

The publisher uses the saved **ncksol** GitHub CLI login for this command only;
it does not switch global accounts or write credentials. Missing authentication
is a real publication failure: never use another account to work around it.

The daily upload allowlist is exactly `.nojekyll`, `index.html`, `latest/index.html`
and `issues/YYYY-MM-DD/index.html`. The archive is rebuilt from existing dated
issues, newest first. No local directory is recursively uploaded. Do not use
`git add .`, push the workspace, or replace the allowlist with a wildcard.

Publication is one non-forced Git commit, preserving existing issues. An existing
dated edition may be retried byte-for-byte but cannot be silently rewritten.
Publishing an older issue does not replace a newer `/latest/`. A concurrent push
fails instead of overwriting remote changes. Check remote `status` again: if
the dated issue now exists, verify it rather than replacing it. Otherwise retry
the publisher once against refreshed remote state without changing reviewed copy.

The command waits for Pages and compares the exact live issue, latest and archive
bytes against the uploaded files. Only then does it write `publication.json`.
`publication-pending.json` means the commit was accepted but the live site is not
yet verified. A timeout is not a licence to mark the issue published: retain the
pending record during this run, report the URL as unverified, and retry once.
Later sessions recover by reading the already published GitHub HTML with
`publication.py status`; they do not need local receipts or evidence.
Do not delete an earlier success receipt to hide a later verification failure.

On public-review or publication failure, report the failed step, actual error
and any accepted commit or unverified URL. Do not claim the public site is current.

## 8. Remove temporary working files

On success or failure, record the outcome in the session response, then run:

```sh
python3 newsroom.py cleanup EDITION
```

This removes only the owned temporary run, including raw evidence, drafts and
receipts. It refuses persistent editions and unrelated directories. Never use a
wildcard, delete the repository, or delete another session's files. If cleanup
fails, report the exact remaining path and error; do not claim the files are gone.
An interrupted process may leave temporary files, but no later run may reuse them.
Session transcripts and tool logs are managed by the host; this command does not
erase them or claim that source text has vanished from conversation history.

Finish with the **verified public issue URL**, optionally the stable homepage,
one sentence on the lead story and any material coverage or delivery limitation.
Do not link a deleted corpus. Do not post to the board,
send messages, alter this automation, or initiate additional scheduled runs.
