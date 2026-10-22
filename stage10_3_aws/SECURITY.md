# Stage 10.3 AWS mock-only, NO APPLY

This Terraform is a **review candidate**, not runnable production authorization. It requires separately reviewed AWS account, immutable AMI preloaded offline with signed mocks, preconfigured identity keys, root-disk isolation and a validated provider plan. mock_only_approval defaults false. Never invoke terraform apply in CI.

**Network:** isolated dedicated VPC, no IGW/NAT/transit gateway, separate old/new subnets and encrypted disks; no IPv6 VPC CIDR, no public addresses. Producer SG permits only TCP 8800 to broker SG; broker ingress permits TCP 8800 from producer SG. Review all actual plan resources and AWS default security-group rules. Explicit no-elevation IAM and account-level SCP enforcement need independent configuration, not assumed from Terraform.

**Important residual issue:** AWS VPC route tables have implicit local reachability and SG group membership does not prevent producer-to-producer traffic if additional SG rules are introduced. DNS and IMDS remain separate exceptions. IMDS is disabled here, which blocks role delivery and may break AWS tooling; this must be reconciled before implementation, not silently enabled. Build/retrieve dependencies before snapshotting AMI, no runtime package installation.

**Cessation:** an AWS controller should use a separate least-privilege identity to verify EC2 instance identity, exact instance ID, request epoch and final EC2 state (e.g. stopped/terminated) and attach AWS-signed/verifiable evidence under a fresh nonce. Merely reading API status from a stale response or collecting mock receipts is not strong enough. Old-host loss of network connectivity does NOT imply host is stopped. A future signer must cryptographically bind time, account, region, instance ID, epoch and independently verified state. This candidate intentionally does not mint real receipts and MUST NOT call AWS.

**Cost categories** (no current price assertion): EC2 three instances, three encrypted gp3 EBS root disks + snapshots, VPC monitoring/CloudWatch logs and any private endpoint costs, AMI/ECR storage, AWS API requests, cross-AZ data transfer. No NAT gateway/Internet gateway budgeted.

**Rollback:** no infrastructure exists to roll back. If separately deployed later: STOP all producers, quarantine pending claims, revoke test keys, preserve immutable audit evidence, snapshot isolated encrypted disks after approval, destroy only resources tagged with exact mock project+account IDs and independently verify deletion. Never modify production resources.

**Future mandatory gate:** independent AWS account tenancy, SCP/permissions guard, IAM boundaries, immutable supply chain, VPC Flow Logs + host nftables default-deny, egress/route/ENI live inspection, independent host kill/partition proof and signed controller evidence, rollback rehearsal. NO LIVE DCP, real Chromium, cart, dealer FOUND, production changes, or deploy.
