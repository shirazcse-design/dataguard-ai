# UC1 synthetic DLP data

SYNTHETIC. Fictional company "Harbourline Group"; every person, user id, host and figure is invented.

* `identities.json` - read-only profiles for `get_user_profile`.
* `activity.json` - 7-day activity features for the deterministic behaviour bands and `get_user_activity`.
* `exceptions.json` - the DLP exception register (DLP Policy 5) read by `check_dlp_exception`.
* `documents/` - NEW documents written for UC1 (the flagship `Acquisition_Targets_2027.xlsx` and
  a few others). Their text is what UC4 classifies (UC4 works on extracted text; binary parsing is
  out of scope). Most UC1 cases instead reference UC4 dev-split documents by `uc4:<doc_id>`, whose
  classifications are already recorded.
