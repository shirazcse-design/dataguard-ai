# prompts/

Versioned prompt files for the UC4 LLM classifier (Approach C). A prompt change is a new version
(`prompt.version` in `config/llm/llm.v1.yaml`); the version and the file hash are recorded in every
run and are part of the replay-cache key.

* `uc4/classifier.v1.md`: system + user templates. The taxonomy section is GENERATED from
  `config/taxonomy/taxonomy.v1.yaml` and the few-shot section from `uc4/fewshot.v1.json`, so
  there is one source of truth.
* `uc4/fewshot.v1.json`: a fixed list of TRAIN document ids and content hashes (no content).
