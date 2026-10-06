# Commit hash map - 2026-09-27

`phase4-integration` was rewritten twice on 2026-09-27, both times on the owner's
instruction, and both times leaving every tree, message byte and date unchanged:

- **D-010:** the `Co-Authored-By: Claude` trailer was removed from the commit
  messages after `5b01d9a`;
- **D-011:** the author and committer `Codex Local <codex-local@localhost>` became
  `asmaasamyhagag22-design <asmaasamyhagag22-design@users.noreply.github.com>` on
  the same commits. Only the name and email fields changed.

Documents written before a rewrite cite the hashes of their time and are not
edited (amendments are append-only). This map resolves every generation. The old
objects remain in the owner's local repository under
`backup/phase4-integration-pre-trailer-20260927` (original) and
`backup/phase4-integration-pre-author-20260927` (after D-010). Neither is pushed.

## Hashes cited in committed files

| Original | After D-010 | Current (after D-011) | Cited in |
|---|---|---|---|
| `6e2cfc17c41e481c0797e2d44cde602490ae91bb` | `77320a2b1e401986944cc5ede805c111a788bc94` | `2cc51662bc01fe85e3c9a63de6b38344ee157cf9` | configs/phase4_audit_manifest.json `git_commit`; docs/release/incident_2026-09-23_and_27.md |
| `9da8bbb6cb5ff8d4df7aaa3018161c38ba54275c` | `fab48e6e473f628878df9f85f1672bce0e4cddfa` | `224094b832b2f582e49a5d75ee7102409f94b96f` | configs/phase4_audit_rule_v1.json amendment 7e part B `procedure` |
| `381d5c97d26b99b26b395a96e9ff8fe9c1062b4c` | `5e9f1f956ca3a6d91585e4831d8f64b3608e93de` | `2b833927537e5257f2219795627a6a396c3391a4` | reports/phase2m_h.md, reports/phase2m_h_metrics.json, scripts/phase2m_h_verify.py `ENTRY_COMMIT` (updated to the current hash) |
| `43718dba3ffeb14cda9da5d66b26d01fdd6af000` | `fab48afbbbe231062db037e33b6e3b75d681657e` | `dbb796b0084d79323d1afdb3fc7720c48dc76c28` | reports/phase2m_h_metrics.json |
| `b53dca673d3aad8afe39d15a49b8301e183d0441` | `9ce9e5d0f6f3693bed986835d729741fa23994bd` | `c2b782bcb2de6ffc829853324300334e03b16777` | reports/phase2m_h_setup_gate.md |

## Every rewritten commit, oldest first

| Original | After D-010 | Current | Subject |
|---|---|---|---|
| `7ebb7a704206` | `65f77a3ed830` | `51e3d53c7922` | feat: move the standing rules into the harness |
| `b53dca673d3a` | `9ce9e5d0f6f3` | `c2b782bcb2de` | fix: the tier 2 fixture stopped being new the moment it was committed |
| `3fdfe7266e2e` | `8d442c152b3f` | `fc60fed6a640` | feat: the governance guard moves into the process, and the privacy scan stops skipping |
| `3f921915a451` | `772eaec091e2` | `3bf6d4ae2088` | docs: the Part 0 setup gate, with its evidence |
| `af8aee0bf7da` | `020764d96515` | `abf4831117ba` | test: narrow the two production-unchanged pins instead of moving them |
| `381d5c97d26b` | `5e9f1f956ca3` | `2b833927537e` | docs: Part 0 passes - the harness branch is prepared and main has not moved |
| `ccd01dfec3ec` | `eadadc89c110` | `2d2439747e95` | config: the Phase 2M-H rule, committed before any decision-bearing number |
| `8dec07999c2d` | `66d8777d58e9` | `6c4a8374765c` | feat: the Phase 2M-H entry gate and the TTA latency harness |
| `a0a7d5f05cec` | `e8ef95420c2b` | `668a4eeced9c` | feat: items 2, 3 and 4 - TTA settled, the two margin values frozen, transfer measured |
| `53bb9de8d1d3` | `42194614f447` | `c6bc7189be46` | fix: route the new phase arrays through the guard |
| `7ea0ad11872f` | `953574725315` | `9f5062d30bb4` | feat: amendment 4, the frozen candidate, shadow integration and the audit protocol |
| `ca8cbeaa29f8` | `0f7a142a0aa9` | `187bd4ae12d0` | docs: correct the Phase 2M-G next-action note (item 7) |
| `f8a46e783b47` | `3662ee86b922` | `3ac27a01b2f1` | feat: the Phase 2M-H report, metrics and decision token |
| `f1ce3c9b0654` | `0ae272fa512b` | `8334b39e572c` | fix: guard the verifier's array reads |
| `2ac02a36189a` | `23438ddb7983` | `01ee0a4866dc` | fix: close the stage-and-commit hole in the guard, and two reporting defects |
| `a91596f8ec79` | `91fee7d91733` | `38d9a9267996` | config: amendment 5 - the swap is reversed and two audit criteria are re-grounded |
| `d0e960db8283` | `fe6b392d4d9c` | `0d2e97fddfb3` | docs: the report tells the corrected story, including its own reversal |
| `2ecebe34ed3a` | `e13c2c06f9d7` | `ead42f4e7433` | test: re-pin the commit guard hash after closing the stage-and-commit hole |
| `bd0a90c49157` | `2b098fa05ec5` | `2547ede40519` | chore: regenerate the metrics after the hook re-pin |
| `41f8c6693ecb` | `4064aea3e2fa` | `d0628c2f9d13` | config: amendment 6 - the instrument validation, and the interval that failed it |
| `43718dba3ffe` | `fab48afbbbe2` | `dbb796b0084d` | docs: the report carries the exact intervals and the generator correction |
| `1b47c8d3f594` | `353c0bc075dc` | `47627b97d5b7` | chore: regenerate the metrics at exit |
| `86c8d9f6fb60` | `057edf162753` | `7fd2b044d2ed` | feat: a gallery of the images BC-final reads wrong |
| `0cdbe6be2d82` | `28494452c0c7` | `2ae7811bac21` | config: amendment 7 - the audit's label source, descriptive quantities and crash policy |
| `d033c0569a12` | `37c2a2b7ce19` | `5632bc8f223f` | feat: the Phase 4 audit read, sealed before any label is read |
| `f56c14479e27` | `29c1d9b09230` | `4377a28bf1a5` | config: the audit manifest - every input hashed, the command, the outputs |
| `2915dd393b42` | `e87fe8356a02` | `353b751d05a7` | config: amendment 7b - the stats review, resolved before the read |
| `0935503e996a` | `7e78986f2853` | `607ddd135a7f` | feat: amendment 7b in code - count-judged typo criterion, band admission, pre-read blocks |
| `1182500dc4c3` | `17867842faf3` | `f42e41dc1b06` | config: regenerate the audit manifest for amendment 7b |
| `36677613099e` | `b140c8e2a385` | `0c97ccc8ceff` | config: amendment 7c - the re-review's pre-read gaps, closed before the read |
| `29ed9d3ff0b0` | `1a5df6665bed` | `aeeadedbd53a` | feat: amendment 7c in code - every refusal before the ledger, clean tree, re-verified inputs |
| `1d734ec56c09` | `715cb71862b4` | `5b7832ee792a` | config: regenerate the audit manifest for amendment 7c |
| `0abac51f6129` | `799bd386a9d6` | `691dd6956de1` | config: amendment 7d - untracked files also block the read |
| `be25a5e2e73a` | `1b108afefe90` | `eb3be2d22461` | feat: the read refuses untracked files too (7d) |
| `001faea7bba5` | `c65b4b95f009` | `a43a4441a626` | config: regenerate the audit manifest for amendment 7d |
| `2391e25cdb24` | `fcb442d01832` | `3513ce4a3e83` | config: amendment 7e part A - owner-adopted criteria changes and the declared design procedure |
| `6cd8a337fb3d` | `825734000f5b` | `566fb52a01ca` | feat: the criterion-4 design calculator (7e part A), before any real number |
| `a779b8e145bd` | `f582ccaff209` | `0daa57fd4d15` | feat: the 7e verdict - retake share descriptive, criterion 4 read from part B |
| `9da8bbb6cb5f` | `fab48e6e473f` | `224094b832b2` | fix: the worse scenario's gap is over all 377 cards, and an undeclared tie stops |
| `a1c677830da4` | `64f2893c6e6e` | `98c7ca28df6a` | fix: generated JSON is written with LF, as the repo is |
| `3ce6b3ee1124` | `9539b8fa02bc` | `9c18d117548f` | config: amendment 7e part B - criterion 4 is non-inferiority at 3 points |
| `1bb5ec8c4664` | `27e46ce865d0` | `dc48edb073dc` | config: regenerate the audit manifest for amendment 7e (and write it with LF) |
| `95b3a25a0eb0` | `468d4c2706e5` | `c0997f29e6f8` | config: amendment 7f - what a result means, and two pre-read hardenings |
| `6e2cfc17c41e` | `77320a2b1e40` | `2cc51662bc01` | feat: 7f - criterion 4 resolved before the ledger, and the read refuses code changed since the manifest |
| `8753469655cd` | `3443c5764602` | `1f54c740fb28` | config: regenerate the audit manifest for amendment 7f |
| `13721feb7e54` | `c6dade1fb17a` | `aba1b80532fb` | docs: D-005 and D-006 - the missing audit card, and the second model as a component |
| `41755ea7b215` | `96ecded8b155` | `91eb731cf235` | docs: incident record - two audit read attempts, neither read an image or label |
| `c3e4ec3f2470` | `697b4865612a` | `20852d172217` | feat: D-005 in code - exactly one pinned audit card may be missing |
| `809add48fd70` | `d55733901c2a` | `f758f5d89984` | docs: D-007 and D-008 - open points settled, and the contributor's update |
| `07b8312e77ca` | `4a2c7241c8ca` | `bb82bb567da2` | feat: the missing-image pin is found in any amendment, and missing-pin is proven write-free |
| `e4c280e40384` | `2f6b9a86a181` | `2fbd70b9f7a8` | config: amendment 7g - one read seals BC-final and the fused system; the gate stays on BC-final |
| `8559a62db964` | `8f2634b02a53` | `4a886359cc5f` | config: the Phase 4-I integration rule, committed before any number |
| `cd4389c26003` | `3afdb4b79807` | `36b4470f63f5` | config: allowlist the three synthetic fixtures the pre-push scan flagged |
| `2abdeeaea957` | `c13d1877b649` | `a9a2a8738e21` | chore: a fresh clone keeps its bytes and skips, loudly, only what needs artifacts/ |
| `bf4c6097199d` | `ba0b2ea7ecaa` | `94843d1cad0e` | config: amendment 1 - three routing variants, chosen by safety then friction (D-009) |
| `a77c1e6ef463` | `8ccadd2379f3` | `b8d455cb7729` | config: amendment 2 - Linux parity criteria, declared before any Linux number |
| `26d5879ea003` | `2251f26770b7` | `a9499bbf6ad8` | feat: the Linux environment record and the amendment-2 parity checks |
| (made after D-010) | `d117796b285b` | `e438cdd2bed3` | docs: D-010 - the Claude trailer removed from history, with an old-to-new hash map |
| (made after D-010) | `451c285140f2` | `3458e3f82df9` | fix: the parity script's array reads go through the governance guard |
