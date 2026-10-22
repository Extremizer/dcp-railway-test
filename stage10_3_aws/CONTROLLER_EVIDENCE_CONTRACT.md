# Independent EC2 host cessation evidence contract — design only

**NO AWS API calls, no signing credentials and no host operations are implemented by Stage 10.3.**

The mock `dp_owner_cessation_receipts` inserted by Stage 10.1 tests are synthetic. They are NOT valid as physical EC2 termination proof.

A future independent controller must satisfy all conditions before issuing any attestation:

1. Controller identity is independently authorized using a dedicated, non-producer IAM role, isolated signing key (KMS-backed or equivalent), and explicit AWS account/region allowlist. Never trust producer-supplied `ceased_host`, status, evidence digest, or lease expiration.
2. Server-side immutable mapping binds `producer_id`, exact EC2 `instance_id`, account ID, region, boot identity, and ownership `epoch` before host admission.
3. Controller checks EC2 control-plane `DescribeInstances` with independently obtained AWS-signed API authentication and corroborates a **stopped or terminated** state for the exact mapped instance. A transient missing response, SSM health, lost network, expired lease, shutdown message, network partition or stale event cannot qualify. A stopped state must be cross-checked with source/account/instance identity and failure-domain policy. A stronger termination/eviction proof may be required.
4. Controller issues a signed canonical record binding account ID, region, instance ID, owner, epoch, state, source event/request ID, observation time, expiry, unique nonce, and evidence digest. Server verifies the signature using an independently configured pinned verifier identity; checks exact binding, freshness (short window), nonce uniqueness, one-time consumption and independently reconciled in-flight/orphan claims **in the same atomic revocation transaction**.
5. A hostile or unverified record fails closed. There must be no fallback that accepts the Stage 10.1 fixture injection as a real receipt. Only offline negative tests may create fixture receipts.
6. Real hosting gate must test controller key revocation, cross-account impersonation, wrong region/instance/epoch, stale observation, replay, concurrent takeover, network partition and persistent journal uncertainty. All tests use mock resources until separate approval.

## Required AWS plan inspection

- Read-only `terraform validate`, `terraform plan -refresh=false` only with an isolated mocked provider, if approved in the future. No actual AWS credentials and no `terraform apply` in this CI.
- Review EC2 tenancy and instance identity, two availability zones, no public IP or public routes, no IGW/NAT/TGW/peering/VPC endpoints unless separately allowlisted.
- Verify effective SGs, subnet NACLs and host nftables (default-deny IPv4+IPv6) independently, including loopback, metadata 169.254.169.254, DNS 169.254.169.253/VPC+2 and any secondary NIC.
- Never assume route-table isolation alone blocks all private VPC local traffic. Allow exactly the intended broker port on explicit identities.
- Container/AMI images must use reviewed immutable digest, offline preloaded dependencies, encrypted distinct disks, independent test-only signing keys, no startup package download.
- AWS cost remains hypothetical: three EC2 nodes, gp3 disks, logs/metrics, snapshots, AMI/ECR storage and cross-AZ private traffic. No billable resources were created here.

## Rollback / teardown for a separately approved future deployment

Before any deletion, revoke mock keys, preserve cryptographically verifiable evidence, freeze producer owner epochs and quarantine uncommitted journals. Destroy only inventory-tagged isolated resources from a verified AWS account; verify no changes to production by comparing immutable resource inventory before/after. Revoke control-plane role permissions and delete scoped mock secrets only after audit preservation. No recovery may convert failed supplier evidence into positive Dealer FOUND.

**STAGE 10.3 remains REVIEW ONLY / RELEASE HOLD.**
