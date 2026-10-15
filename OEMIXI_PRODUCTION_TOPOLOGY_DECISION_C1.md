# OEMixiBOT Production Topology Decision Package — Stage C1

Status: **OWNER DECISION REQUIRED**

Stage C1 proves the secure transport technically. It does not choose or switch production topology.

## Option A — OEMixiBOT remains on Windows

### Topology

`Windows OEMixiBOT -> HTTPS Secure Gateway -> Railway Shared Gateway/DB -> existing Producer`

### Advantages

- lowest change to current installed OEMixiBOT host;
- preserves current local operational environment;
- no bot migration required before using server price authority;
- secure transport can be injected through existing PR #38 runtime binding;
- Windows has no DCP cookies or producer permissions.

### Requirements

- stable outbound HTTPS from Windows to Railway;
- dedicated scoped consumer credential;
- secure startup configuration;
- credential storage/rotation procedure;
- explicit Dealer/MSRP freshness policies on server;
- one installed OEMixi polling process only;
- gateway availability monitoring;
- bound-mode verification at startup;
- no legacy direct-browser fallback while secure gateway mode is selected.

### Risks

- Windows availability/power/network become part of OEMixi availability;
- split runtime/server operational troubleshooting;
- credential lives on Windows consumer host;
- temporary Railway/network loss means price lookup fails closed;
- startup mistakes could accidentally start old unbound configuration unless launch procedure is controlled.

### Safe migration outline

1. freeze current OEMixi startup;
2. verify no second polling instance;
3. provision scoped transport credential;
4. configure explicit gateway URL + freshness policies;
5. start one bound runtime;
6. test read-only admin/dealer OEM;
7. verify no direct fallback and no duplicate polling;
8. only then resume normal operation.

No execution authorized in Stage C1.

---

## Option B — OEMixiBOT moves to approved server environment

### Topology

`Server-hosted OEMixiBOT -> local/internal Shared Gateway boundary -> shared Railway DB -> existing Producer`

### Advantages

- fewer network hops between bot price consumer and shared server components;
- centralized process supervision/logging;
- easier controlled startup/restart;
- easier singleton enforcement if one deployment owns Telegram polling;
- consumer credential exposure may be reduced or transport can become an internal service boundary.

### Requirements

- verify Telegram runtime dependencies in target environment;
- confirm writable/persistent OEMixi application data requirements;
- decide whether OEMixi dealer/cart DB is migrated or separately mounted;
- runtime bundle/version pinning;
- environment configuration;
- single polling instance enforcement;
- migration/checkpoint plan;
- shutdown proof for Windows bot before server polling starts;
- rollback procedure that never runs both pollers simultaneously.

### Risks

- accidental dual polling during migration;
- local Windows-only assumptions may surface;
- dealer/cart/history DB migration can become a separate risk;
- a server deployment could increase blast radius if bundled with unrelated services;
- rollback is more complex if state moves at the same time as runtime.

### Safe migration outline

1. inventory runtime files/dependencies/state;
2. create server candidate without polling;
3. run isolated startup/import tests;
4. checkpoint dealer/order state;
5. stop Windows polling and prove it stopped;
6. start exactly one server poller;
7. read-only admin/dealer OEM smoke;
8. verify singleton/gateway/finance regressions;
9. keep rollback package but never run two pollers.

No execution authorized in Stage C1.

---

## Comparative summary

| Criterion | A. Windows stays | B. Server move |
|---|---|---|
| Immediate runtime change | Low | High |
| Secure network transport required | Yes | Possibly internal/simpler |
| Windows availability dependency | Yes | No |
| Migration risk | Lower | Higher |
| Dual-polling migration risk | Low if no move | High during cutover |
| Centralized supervision | Weaker | Stronger |
| Existing local state continuity | Strongest | Must be planned |
| Production activation authorized now | No | No |

## Recommendation status

**No topology is selected by Theme 1.**

Owner/Theme 4 must choose only after:

- confirming where OEMixi durable state should live;
- deciding acceptable Windows operational dependency;
- approving credentials/freshness;
- confirming Theme 6 first-switch safety;
- selecting maintenance/cutover procedure.

## Common release blockers for either option

1. production Dealer freshness TTL;
2. production MSRP freshness TTL;
3. scoped production credential provisioning;
4. single producer proof;
5. first-switch DCP gates;
6. queue/admission/recovery health;
7. exact runtime bundle;
8. Telegram singleton proof;
9. maintenance/checkpoint plan;
10. Release Control + owner GO.
