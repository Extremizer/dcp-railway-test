# EXTREMIZER — Stage 10.2 offline Compose isolation, evidence and two-host proposal

BASE: PR #66 exact HEAD `7307f6b37295400a2181edc9825c17b7bbae1177`.
Scope: GitHub Actions CI only. NO Railway, production, real Chromium, DCP network, real OEM, cookies, credentials, cart or Dealer FOUND.

## Offline test topology

`stage10_2_offline/compose.yaml` declares exactly three isolated Python containers:
- **broker**: synthetic signed HTTP endpoint + SQLite mock journal, volume `broker_state`.
- **producer_old**: signed mock ping, isolated volume `old_state` and synthetic journal.
- **producer_new**: signed mock ping, isolated volume `new_state` and synthetic journal.

Exactly one Compose network, `internal: true`, `enable_ipv6: false`. Each container connects only to that internal network. No ports, host network, external network, bind mounts, production values or container capabilities. Root filesystems are read-only and local named volumes preserve each synthetic journal. Mock signing keys are in test code and cannot authorize any production request.

## Gates / evidence interpretation

`static_gate.py` fails when a service, network, mount, port, network mode, capability or privilege differs from allowlist, BEFORE starting any containers.
`verify.py` reads actual Docker inspect state: all three services share one Docker-internal network; IPv6 disabled; no published ports. All three containers attempt TCP connections to RFC 5737 TEST-NET-1 (`192.0.2.10`) and TEST-NET-2 (`198.51.100.10`) and RFC 3849 documentation IPv6 (`2001:db8::10`); no connections may succeed. These probes send **no data to DCP or production**. Mock producers successfully POST signed synthetic pings to the broker by Compose internal DNS.
The inherited Stage 10/10.1 adversarial tests separately exercise SQLite epoch acquisition concurrency, wrong owner / stale epoch, replay, signed v2 GET integrity, signed results, fake cessation, in-flight quarantine and lost ACK journal. They use disposable test DB and synthetic data, NOT production data.
Containers and volumes are removed via `docker compose down -v --remove-orphans` even on CI failure.

### Limits — no claim of two physical hosts

All three Docker containers run on **one GitHub Actions runner**. Compose internal networking is a per-runner software boundary; it does not demonstrate infrastructure-layer network controls or independent failure domains. Reserved-address connection refusals corroborate effective isolation *for tested destinations at test time*, not a universal firewall policy or impossibility of every outbound path. The source image build still fetches Python packages from the public internet on the GitHub host/build network; this is NOT supplier network access from running producer containers. Dependencies should be pinned and build origin reviewed before any future staging.
The broker is synthetic, not real signed-v2 production broker and no real cache writer. Test-only controller cessation receipt is synthetic; cannot prove an actual remote host terminated. CI cannot prove Railway infrastructure egress restrictions, independent controller identity or genuine network partition of two distinct machines.
This offline result is **not authorization for hosting, LIVE DCP, real browser, real Dealer FOUND or production admission**.

## Future genuinely isolated two-host environment (separate Theme 4 authorization)

1. Obtain two genuinely independent Linux VM hosts (or separately managed nodes) and a third broker/controller in a private network with cloud/VPC or host nftables/eBPF firewall **default-deny outbound IPv4 and IPv6**, allowlisting only exact private controller/broker endpoints. Explicitly block all DNS and public routes except a separately reviewed resolver if required.
2. Independently enforce deny-all for each host at the infrastructure security-group/network-policy layer, including traffic across deployments; prevent operators from adding unauthorized NICs, addresses, routes or public proxies. Security policy changes require separate reviewed authority.
3. Use non-production identities, signed per-host keys provisioned out-of-band, separate encrypted persistent disks, immutable mock-only application sources and zero production endpoint/IP/mount/secret dependencies.
4. Inspect real routing tables, namespaces, host firewall counters and packet captures to prove private broker connectivity + blocked reserved IPv4/IPv6, public IP and DNS attempts. No probes to actual DCP or production.
5. Simulate hard kill, network partition, lost ACK, restart with pending journal, duplicate ownership acquisition, stale claim replay and owner revocation. Accept independent cessation only with controller-issued verifiable host-poweroff/eviction evidence; expired lease and agent health never suffice.
6. Perform external independent security review and exact source/release identity confirmation **before** authorizing a separate mock-only staging deployment. No LIVE DCP, no browser or real dealer account until later separate owner approval.

RELEASE HOLD. NO MERGE / NO DEPLOY / NO PRODUCTION CHANGE / PHONE-FIRST PAUSED.
