# Manual setup: embedding deployment for the Data Security Policy Copilot (UC6)

**Owner: Shiraz (manual, in the Foundry portal).** Claude Code does not create, register or
configure this deployment. No script, CLI, SDK or template in this repository provisions it.

UC6's dense and hybrid retrieval legs need an embedding model. Everything else in UC6 (corpus,
chunking, BM25, reranking, evaluation) already runs without it. Until the deployment exists and
vectors are recorded, `dataguard-policy eval retrieval` (replay) refuses to run and says why.

## 1. Recommended model

| Setting | Recommendation | Why |
|---|---|---|
| Model | **`text-embedding-3-small`** (Azure OpenAI, in the Foundry model catalog) | Strong general-purpose English retrieval quality, and it is served on the same `/openai/v1/embeddings` route and `api-key` auth the UC4 client already uses. `text-embedding-3-large` (3072 dims) costs more and is not needed at ~75 chunks. |
| Dimensions | native (1536); leave `dimensions: null` in `config/policy/policy.v1.yaml` | No reason to truncate at this corpus size. |
| Deployment name | **`uc6-embed-small`** | Matches `embedding.replay_model_id`, so recorded vectors are filed under the deployment that produced them. |
| Resource | the **same** Foundry resource/project as `uc4-llm-medium` (`dataguard-resource` / project `dataguard`) | The existing `DATAGUARD_FOUNDRY_ENDPOINT` and `DATAGUARD_FOUNDRY_API_KEY` then work unchanged. |
| Deployment type | Global Standard (or Standard if your region/quota only offers that) | Pay-per-token, no reserved capacity. |
| Rate limit | the smallest the portal offers (e.g. 10K–50K TPM) | The whole recording is about 5K tokens: 74 chunks plus about 70 query texts. |

Cost: at published list prices for `text-embedding-3-small`, the recording run costs a fraction of
a cent. Check the pricing page for your region; nothing here depends on the price.

## 2. Steps in the Foundry portal

Portal labels change between versions, so the wording below may not match exactly.

1. Open <https://ai.azure.com> and select the project **`dataguard`**. This is the project that
   already holds `uc4-llm-medium`.
2. Open **Models + endpoints** (in the new portal: **Build → Models**, or **Discover → Model
   catalog**), then **+ Deploy model → Deploy base model**.
3. Search for **`text-embedding-3-small`**, select it, then **Confirm / Deploy**.
4. In the deployment dialog:
   * **Deployment name:** `uc6-embed-small`
   * **Deployment type:** Global Standard (or Standard)
   * **Model version:** the default offered
   * **Tokens per minute:** the smallest available value
   * **Connected resource:** the same AI resource as `uc4-llm-medium`
5. Select **Deploy** and wait for the status to show **Succeeded**.
6. Open the new deployment and note:
   * the **model name and version** it shows (for example `text-embedding-3-small`, version `1`)
   * the **Target URI** host. It should be the same `https://<resource>.services.ai.azure.com` /
     `.openai.azure.com` host that `uc4-llm-medium` uses.
7. Optional: use the deployment's **Test / Playground** tab to embed one short phrase and confirm
   that it returns a vector of 1536 numbers. Don't type any real sensitive text there.

You don't need to change content filters for this deployment. It returns vectors, not generated
text, so there is no output to filter. Guardrails for UC6 are a separate checkpoint.

## 3. What to send back (no secrets)

Paste these into the chat:

1. **Deployment name**, e.g. `uc6-embed-small`
2. **Model name and version** as shown on the deployment page
3. **Deployment type** (Global Standard / Standard) and **region**
4. **Same resource as `uc4-llm-medium`?** (yes/no). If no, I'll need the endpoint host, which is
   not a secret, and the key will be a different one.

**Do not paste the API key or a connection string.** The key is typed only into the local
recording script, which reads it with `read -s` and never writes it anywhere.

## 4. What happens next (Claude Code, after you confirm)

1. I give you a small scratchpad `zsh` script. You run it in a normal Terminal window. It prompts
   silently for the endpoint and key, sets `DATAGUARD_EMBEDDING_DEPLOYMENT`, and runs
   `dataguard-policy embed record` once. That embeds the frozen corpus (74 chunks) and every golden
   query text, and stores the vectors under `data/embedding_cache/uc6-embed-small/`. Only hashes
   and vectors are stored, never text.
2. You paste back the printed JSON block: counts, dimensions and tokens used. No secrets appear in
   it.
3. I run `dataguard-policy eval retrieval` in REPLAY mode (no Azure) and commit the real
   Naive vs Sparse vs Hybrid vs Advanced results to `docs/uc6/results/retrieval.md`.

From then on, dense and hybrid retrieval replay offline for every golden question. A question that
was never recorded falls back to sparse-only retrieval in REPLAY, and the trace says so
(`dense_status: replay_miss`).

## 5. Environment variables used at runtime (names only)

| Variable | Holds | Already set for UC4? |
|---|---|---|
| `DATAGUARD_FOUNDRY_ENDPOINT` | resource or project endpoint | yes |
| `DATAGUARD_FOUNDRY_API_KEY` | resource key (secret) | yes |
| `DATAGUARD_EMBEDDING_DEPLOYMENT` | `uc6-embed-small` | **new** |
