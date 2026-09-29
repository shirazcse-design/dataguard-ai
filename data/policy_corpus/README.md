# Synthetic policy corpus (Data Security Policy Copilot)

SYNTHETIC. Written for the DataGuard AI portfolio demo. These are not any real organisation's
policies and were not copied from one. The fictional company is "Harbourline Group".

Each `*.md` file is one policy version: YAML front matter (metadata) followed by numbered sections
(`## 3.` / `### 3.2`). The chunker splits on those headings, so section numbers are the citation
unit (`POL-DLP §4.2`).

Deliberate features, used by the golden evaluation set:

* `retention-v1.md` is an OLDER version of the retention policy (`status: superseded`). Its
  retention periods conflict with `retention-v2.md`. The conflict is resolvable deterministically
  from metadata (`supersedes`, `status`, `effective_date`).
* `acceptable-use.md` §5.3 and `dlp.md` §4.2 genuinely conflict on storing *Internal* data in
  personal cloud storage. Both are current and neither supersedes the other, so this conflict is
  NOT resolvable from metadata and must go to human review.
* `vendor-faq-draft.md` is an unapproved draft (`status: draft`) that contains an embedded
  prompt-injection line. It tests that retrieved text is treated as data, not instructions.

Changing this corpus invalidates recorded embeddings and recorded answers (replay keys hash the
retrieved text). It is frozen before any recording run.
