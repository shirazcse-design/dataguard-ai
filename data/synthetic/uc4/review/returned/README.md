# Returned blind-review sheets

Completed sheets returned by reviewers live here, **never** in `review/blind/` (that directory is what
a reviewer is handed and must stay blank).

| file | reviewer_id | provenance |
|---|---|---|
| `blind_review_sheet.completed.shiraz-ahmed.csv` | `Shiraz Ahmed` | Uploaded to `review/blind/` through the GitHub web UI on 2026-09-21 and moved here. On all 33 documents its labels, flags and rationale text are identical to an earlier AI-completed sheet (reviewer id `ChatGPT-GPT-5.6-Sol`), so it is **not** independent human validation. See `review/blind_results/blind_review_comparison.md`. |
| `blind_review_round2_sheet.csv` | `Rahul` | Round 2 sheet (28 documents), uploaded to `main` by the coordinator through the GitHub UI on 2026-09-21 (received as `blind_review_round2_sheet_Rahul_approved.csv`; do not confuse it with the blank package sheet of the same name in `review/blind_round2/`). Checked by the analyst: well-formed; no rationale duplicates any earlier sheet (highest similarity 0.86, none >= 0.9). That the reviewer worked independently and without AI help is the coordinator's statement and cannot be verified from the file; per the coordinator (2026-09-21), "approved" in the original file name means the reviewer approved a draft he prepared himself, not one prepared by someone else. |

Compare with: `dataguard-uc4 review blind-compare [--variant round2] --sheet review/returned/<file>.csv`
