# Incident record - two audit read attempts that failed before reading

Recorded 2026-09-27, on the owner's instruction, before any integration
measurement.

| Attempt | Date | Reported cause |
|---|---|---|
| 1 | 2026-09-23 | an audit image is missing |
| 2 | 2026-09-27 | a DLL was blocked by Windows Application Control at model load (the owner reports Paddle) |

**Finding: neither attempt read an audit image or an audit label.** One question
remains open (below); it does not change the finding.

## Why neither could have read anything

The read (`tests/test_phase4_audit.py::test_the_locked_audit_read`, at the manifest's
commit `6e2cfc1`) runs in this order:

1. refusals: read markers, ledger count, dirty worktree, code changed since the
   manifest, criterion-4 design, pinned inputs;
2. `audit_rows()` - audit membership from the split manifest, under both
   authorisations (card_ids only; no image, no label);
3. `runtime.ensure_shadow_loaded()` - **attempt 2 failed here**;
4. `resolve_audit_paths()` then `refuse_if_images_missing()` - a filesystem
   existence check - **attempt 1 failed here**;
5. `guard_audit_paths()` - the first write to the override ledger;
6. `read_started.json` is written;
7. `single_read()` - the only place an audit image is opened;
8. the label lookup is read, only after the predictions are sealed.

Both failure points come before steps 5 to 8. No image is opened before step 7,
and no label before step 8.

## What is on disk

```
$ cat artifacts/governance/override_ledger.json      # one event
  2026-09-21T22:01:29+00:00  Read  audit:card  (hook verification during the governance setup)

$ ls -la --time-style=full-iso artifacts/governance/override_ledger.json
  319 bytes, modified 2026-09-22 01:07:19 +0300

$ ls ../OCR_ID_working/artifacts/governance/
  blocklist.json                                       # no ledger in the sibling worktree

$ find /c/Users/asmaa -maxdepth 7 -name read_started.json -o -name verdict.json \
       -o -name "override_ledger*.json"
  two read_started.json, both under AppData/Local/Temp/pytest-of-asmaa/
      (test_a_second_read_is_refused - synthetic tests)
  artifacts/governance/override_ledger.json            # the one above

$ ls artifacts/phase4_audit/
  rehearsal/  typo_q.json                              # no read_started.json, predictions.npz or verdict.json
```

- The ledger has exactly the one documented setup event, so the guard step
  (step 5) never ran with the override.
- No read marker, sealed prediction or verdict exists, so step 6 never ran.

## Open question

Amendment 7c R2 (committed 2026-09-23 01:54 +0300) describes its defect as "a B2
block fired after audit_image_paths had already written 377 audit:card ledger
events". The ledger holds no such events, and it has not been modified since
2026-09-22 01:07 +0300. Two explanations fit:

- 7c R2 describes a defect found by reading the code, and "fired" overstates it;
- the events were written to a ledger that no longer exists.

The owner's terminal output from the 09-23 attempt decides which. Under either
one, the guard step opens no image: `check_card_read` checks a path string and
records it. So the finding above stands.

## Also recorded

The only Application Control failure in the repository is the `bidi` DLL
(`reports/phase2m_h.md`, 2026-09-22). The rehearsal at 2026-09-22T22:50Z loaded
the full bundle on this machine (`artifacts/phase4_audit/rehearsal/summary.json`,
`bundle_loaded: true`). The 09-27 failure is recorded only in the owner's
account.
