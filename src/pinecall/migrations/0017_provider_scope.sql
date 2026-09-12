-- 0017: `keys` becomes the org's own API keys, and the provider keys get a scope of their own.
--
-- Until here one scope named both: `keys` opened /v1/provider-keys, and an org could not issue an
-- API key for itself at all — only the operator could, on the box's ops key. A tenant that
-- deploys needs to mint the key its server runs on, and that is a different right from keeping a
-- vendor's secret, so it is a different word. `keys` is the org's API keys from here, `providers`
-- is what it used to mean, and types/key.py is where the thirteen are spelled.
--
-- Every row that held the old `keys` was a manager's or an admin's — the two roles that preset it
-- — and under the new cut those two hold both. So the backfill ADDS `providers` and keeps `keys`:
-- nothing a live key could do yesterday is refused today, which is the only thing a migration
-- over a running box may promise.

UPDATE api_keys
   SET scopes = array_append(scopes, 'providers')
 WHERE 'keys' = ANY (scopes) AND NOT ('providers' = ANY (scopes));
