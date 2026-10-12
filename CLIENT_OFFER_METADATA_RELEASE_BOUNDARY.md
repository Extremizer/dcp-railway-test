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

## What is still unproven for 2670-265

Read-only inspection on 2026-10-12 confirmed production reference weight 0.13 kg
and cached dealer price USD 30.05. The current authorized DCP form for this OEM
has no explicit public retail/MSRP metadata. An unauthenticated HTTP read was
blocked with HTTP 403. This change does **not** invent a current MSRP for that
filter. The USD 50.95 MSRP used in regression tests is a synthetic fixture,
not a verified current MSRP. Until an approved source supplies MSRP, its lines
remain absent for that filter.

## Validation and preserved boundaries

- Exact approved card parity, missing/lower/equal MSRP and no-DP regressions.
- Disposable reference/cache DB read-back hashes unchanged after card rendering.
- Conflicting/missing weights, current/requested OEM fallback, volume maximum.
- Signed real WEB endpoint on disposable SQLite; explicit MSRP cache persistence,
  unsigned rejection, finished-request replay rejection, legacy DP compatibility,
  and unchanged business table rows.
- FinanceEngine and legacy checkout fingerprints remain unchanged.
- Probnik bytes outside `_compose` remain at the original accepted checkpoint.
- WEB ADMIN/checkout bytes outside the DP result model/handler remain exactly
  those of the accepted Point 10 candidate. The scope guard retains that baseline
  fingerprint and rejects changes to other routes.

No production writes, upload, Apply, checkout, Supplier sends, settlements,
cleanup, repin, WEB2 enablement, merge or deployment were performed for this PR.

## Rollout requirements

The server release makes weight available immediately. MSRP transport also
requires the approved local agent files `dp_sync_agent.py`,
`oemixibot_dcp_price.py` and `client_offer_metadata.py` to be updated together
in the actual agent runtime directory. Existing Chrome authentication and
tokens remain in place. An older agent continues to return DP without MSRP.
The producer update and server deployment are separate authorized release
actions; a server deployment alone is not proof of live MSRP ingestion.
