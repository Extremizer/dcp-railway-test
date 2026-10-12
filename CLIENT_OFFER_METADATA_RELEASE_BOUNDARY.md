# Client offer weight and verified MSRP

Base release: `74ce11eb864ee4d852c60e9ffdb140ef73a46517`.

Probnik and Procenka now share read-only offer metadata. For the already stored
`2670-265` reference weight of 0.13 kg, both cards show reference weight 0.13 kg
and estimated customer delivery weight 0.2 kg. This estimate is per piece and
does not create a delivery quote, charge or final shipment weight. Missing or
conflicting reference weights are omitted. Existing source databases are not
reimported or updated by rendering a card.

Explicit public retail/MSRP metadata on an exact matched DCP item is carried
separately from dealer price by the local agent and the signed result endpoint.
Only `data-retail`, `input[name="retail"]`, `input[name="msrp"]` or nested
`data-retail` values can supply it. The authorized dealer-price cell `.dbl` and
generic product `.price` cells never supply MSRP. Successful DP results without
MSRP remain supported; MSRP never substitutes for a missing DP.

MSRP is saved through the existing catalog cache and alias writer. There is no
new schema or business migration. Card fallback reads verified MSRP for the
same manufacturer/current/requested OEM, with the existing 168-hour cache age
as its default. Both cards convert MSRP to RUB with a common 50-RUB ceiling;
customer DP pricing continues to use its existing 100-RUB ceiling. MSRP and
savings are shown only when MSRP exceeds the customer price.

## WOPS Retail: independent cache enrichment

Read-only inspection on 2026-10-12 confirmed reference weight 0.13 kg, DCP DP
USD 30.05 and WOPS Retail USD 50.95 for Arctic Cat 2670-265. WOPS here is World
of Powersports / DealerOrders, not Western Power Sports. The observed public
detail route also supplies Honda 91303-PA9-004 Retail USD 10.38 without login.
These are source observations; all HTTP/cache integration tests still use
disposable synthetic fixtures, not production writes.

The authorized DCP form has no explicit MSRP for the filter. An ordinary HTTP
client receives 403 from WOPS. `wops_retail.py` therefore reads only the observed
public GET detail route through an existing, separate DealerOrders Chrome tab.
It never opens, focuses or navigates tabs, logs out DCP, reads cookies/session
IDs, submits cart forms, purchases pricebooks, or calls ordering endpoints.
Fetch aborts after 2.5 seconds, CDP tab lookup after 2 seconds, and evaluation
after 4 seconds. An absent/unresponsive tab, 403, challenge, unsupported brand,
wrong supplier/OEM, missing/ambiguous Retail or invalid price yields no value.

Only Arctic Cat/AC and Honda/HO are enabled, backed by observed supplier/OEM
identity. BRP and other manufacturer prefixes are not guessed. Availability
text is not treated as stock. WOPS Cost is never used as DCP DP. Source data
retains `dealerorders_public_retail`, USD, supplier SKU, exact detail URL and
verification time; Retail is not claimed to be independently certified factory
MSRP. No paid pricebook or old import is required.

The agent's independent idle Retail cycle runs at most every 30 seconds and
throttles unsuccessful identities for 15 minutes. Candidate batches are visited
in round-robin order, so expired negative retries do not continually restart at
the first failed identity. Each batch is limited to the 100 most recent eligible
DP cache entries. It reads signed
`/internal/retail-sync/next` candidates: only already verified, fresh DCP cache
entries of supported brands that have missing/stale RRP. No live-DP request is
made for a cache hit and no customer request waits for WOPS. Successful Retail
is posted to signed `/internal/retail-sync/result`; this writes only the existing
COMMON CORE catalog cache/aliases, preserves existing description/catalog and
does not modify DP, its freshness, orders, finance, stock or DP request history.
Both cards read this cache on the next request. Cache lifetime is 168 hours.
Evidence must be no older than 5 minutes, exact manufacturer/OEM/SKU/URL/USD
must agree, and an already fresh catalog value rejects a later duplicate.
Retail POSTs are serialized within the current single WEB process.

WOPS lookup failures never alter DCP health. A foreign WOPS Cloudflare tab no
longer blocks the independently authorized DCP tab. DCP's own challenge/auth
checks remain active. Older servers returning 404 and older local agents remain
compatible. There is no new schema or migration and no hardcoded live price.

Live provider extraction and prices were observed during the read-only audit.
A later attempt to exercise the exact new producer against the current PC
browser encountered a Runtime.evaluate timeout even for `1+1`; no tab was
focused/restarted and no runtime file was changed. Exact producer live E2E and
live signed ingestion remain release checks, not claimed PASS by this PR.

## Validation and preserved boundaries

- Exact approved card parity, missing/lower/equal MSRP and no-DP regressions.
- Disposable reference/cache DB read-back hashes unchanged after card rendering.
- Conflicting/missing weights, current/requested OEM fallback, volume maximum.
- Signed real WEB endpoint on disposable SQLite; explicit MSRP cache persistence,
  unsigned rejection, finished-request replay rejection, legacy DP compatibility,
  and unchanged business table rows.
- Independent WOPS GET transport, strict identity/Retail parsing, missing-tab/
  challenge failures, negative retry, no DCP health coupling, cache-hit enrichment
  through signed real HTTP endpoints to identical Probnik/Procenka cards.
- Unsigned/wrong identity/currency/source/stale/future/boolean payload rejection,
  duplicate rejection, read-only candidate selection, unchanged DP/business rows.
- FinanceEngine and legacy checkout fingerprints remain unchanged.
- Probnik bytes outside `_compose` remain at the original accepted checkpoint.
- WEB ADMIN/checkout bytes outside the DP result model/handler and the exact
  two-line signed Retail route registration remain exactly
  those of the accepted Point 10 candidate. The scope guard retains that baseline
  fingerprint and rejects changes to other routes.

No production writes, upload, Apply, checkout, Supplier sends, settlements,
cleanup, repin, WEB2 enablement, merge or deployment were performed for this PR.

## Rollout requirements

The server release makes weight available immediately. MSRP transport also
requires the approved local agent files `dp_sync_agent.py`,
`oemixibot_dcp_price.py`, `client_offer_metadata.py` and `wops_retail.py` to be updated together
in the actual agent runtime directory. Existing Chrome authentication and
tokens remain in place. An older agent continues to return DP without MSRP.
The producer update and server deployment are separate authorized release
actions; a server deployment alone is not proof of live MSRP ingestion.
The producer needs an existing usable DealerOrders tab in the same CDP browser.
Enabling this producer after release intentionally permits verified pricing
cache enrichment only; business write tests remain a separate authorization.
