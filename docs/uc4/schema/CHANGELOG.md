# Classification schema changelog

The request and result schemas are versioned `MAJOR.MINOR` (`schema_version`). The frozen JSON
Schemas in this directory are what consumers code against.

* **Minor bump** (backward compatible): adding an OPTIONAL field, or relaxing a required one.
* **Major bump** (breaking): removing or renaming a field, changing a type, tightening an enum or
  constraint, or adding a REQUIRED field.
* A change is made deliberately: bump `SCHEMA_VERSION`, run `dataguard-uc4 schema export` and
  `dataguard-uc4 schema examples`, and add an entry here. CI runs `dataguard-uc4 schema check`, which
  fails on ANY difference between the models and these files.
* Consumers must ignore fields they do not know only within the same major version and only if the
  service's minor is newer than theirs; the service itself rejects a request whose minor is newer than
  it understands (`unsupported_schema_version`) instead of silently dropping fields.

## 1.0 (first frozen version)

Frozen from the models as they stood at the end of Phase 8. Files: `classification-request.v1.json`,
`classification-result.v1.json`; golden examples for every status under `examples/`.
