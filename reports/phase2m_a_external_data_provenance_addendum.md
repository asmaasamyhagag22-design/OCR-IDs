# Phase 2M-A external-data provenance addendum

This is an append-only correction to the external-data authorization/readiness
gate. It does not delete, rewrite, or reinterpret the diagnostic results in the
original Phase 2M-A report.

## Corrected decision

`EXTERNAL_DATA_AUTHORIZED_AND_TRAINING_READY`

The earlier rejection was correct under the evidence available at that time:
the archive declared a license, but owner permission, fictional-data status,
and the intended OCR/training purpose had not been documented. New
owner-supplied context now closes that provenance gap. The old conclusion
remains as the historical decision; this addendum supersedes only the external
authorization/readiness gate.

No training or fine-tuning was started. The consumed internal-validation split
and locked audit were not accessed. Production was not modified.

## Provenance evidence

The new evidence is recorded as `USER_SUPPLIED_OWNER_PROVENANCE`:

- the owners supplied the dataset directly to the user for OCR/model-training;
- displayed identities and national-ID sequences are fictional;
- semantic inconsistencies are intentional and are not recognition-label
  errors when the annotation matches the visibly printed digits;
- the dataset declares CC BY 4.0 and the user authorizes project-scoped training
  and evaluation under the owners' permission;
- raw data remains private, ignored, and uncommitted; and
- CC BY 4.0 attribution is mandatory in the future dataset card and model
  report.

Four local README records were examined without committing their contents or
source filenames. Their private content hashes are recorded in the ignored
provenance evidence file. The aggregate README-bundle SHA-256 is
`3e57ba3789252f34c91f60dec521aa6540b18588e1c78dd7aeefa9651efa074b`.
The records describe a digit-annotation computer-vision export for training;
two declare CC BY 4.0. Attribution must retain both source records:

- https://universe.roboflow.com/egyptian-national-ids/egyptian-national-ids
- https://universe.roboflow.com/egyptianids/nid-ar-digits

## Preserved structural audit

The completed structural audit was not rerun or replaced. Its findings remain:

| Finding | Preserved result |
|---|---:|
| Valid images | 4,513 |
| Annotation records | 4,512 |
| Exact-14 annotation records | 3,916 |
| Exact byte-duplicate groups / images | 300 / 600 |
| Exact dHash groups / images | 317 / 637 |
| Label-identity groups | 1,644 |
| Repeated label-identity groups / images | 1,155 / 3,427 |

Archive safety also remains unchanged: 4,529 files, 206,875,077 uncompressed
bytes, maximum compression ratio 2.62263, and no unsafe path, symlink, nested
archive, size, count, or compression-ratio finding.

## Label and layout readiness

The external-only gate separately records:

| Check | Result |
|---|---:|
| Ordered exact-14 labels mapped to valid, nonblank visible digit boxes | 3,912 |
| Malformed annotation strings | 1 |
| Invalid exact-14 box sets | 2 |
| Incomplete or unreadable exact-14 digit lines | 2 |
| Non-exact-14 annotation records | 596 |
| Images without an annotation record | 1 |
| Square-export layout mismatches among exact-14 candidates | 3,916 |

“Visible-label agreement” here means that each ordered annotation label maps to
a valid, nonblank visible digit box and that the 14-box line is complete. It is
not a semantic identity check or a claim of exhaustive human relabeling. The
square export canvas is a full-card geometry mismatch, but it does not block
bbox-derived digit-line recognition training. It must not be used to train
full-card geometry or admission logic without a separate layout review.

Fixed14 is a visual `[14,10]` recognizer. Encoded gender, dates, or other
identity semantics are outside this gate and intentional fictional
inconsistency is not treated as a label defect.

## Frozen external split proposal

After excluding the 601 records listed above, 3,912 records are eligible. A
private card-level manifest was frozen under the ignored artifact tree. It
groups protected exact-14 label identities together and links exact plus
perceptual duplicates at dHash Hamming distance four or less before assigning
deterministic hash buckets.

| Split | Eligible records |
|---|---:|
| External train | 2,892 |
| External validation | 699 |
| External test | 321 |

No identity group or duplicate group crosses a split. The private manifest
contains archive member references, protected labels, and grouping keys; none
of those card-level fields is tracked.

## Frozen model and next order

Fixed14-v2 remains unchanged: config SHA-256
`93a783f022f025f54903ebeafe6293c4dfe432dd222208923daf3b2202103267`,
weights SHA-256
`d8c6614dd4fcb02ecf1690145aa6ed5bdc15be6c9984f9516ba9078111e3da1b`,
and the frozen 93.20% internal-validation result.

The authorized subsequent order remains:

1. preprocessing/direct-field repair and freeze;
2. synthetic pretraining;
3. external real-image training using the frozen permitted split;
4. internal fine-tuning using only the 115 eligible internal examples;
5. a hard-example pass from training-authorized data only;
6. freeze Fixed14-v3; and
7. final evaluation without reusing the consumed internal-validation split.

This addendum authorizes and prepares the external dataset. It does not itself
authorize access to the consumed validation population or the locked audit, and
it performs no training.
