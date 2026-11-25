# Security & Public-Repository Notice

This repository is the **public, sanitized edition** of the AdGuard P2 project.

## What is not included

- No passwords, API keys, JWT secrets, cloud credentials, SSH private keys, certificates, or model weights.
- No production `.env` files; deployment uses `deploy/ecs/config/*.env.example` placeholders only.
- No customer data; evaluation/test assets are synthetic.

## Sanitization performed

Production identifiers were replaced with reserved documentation values or placeholders:

| Original kind | Public placeholder |
|---|---|
| Public ECS IPs | `203.0.113.10` – `203.0.113.14` (TEST-NET-3) |
| VPC private IP | `192.0.2.10` (TEST-NET-1) |
| ECS / RDS instance IDs | `i-bp-redacted`, `rm-redacted` |
| OSS bucket | `adguard-assets-example` |
| VPC / vSwitch IDs | `vpc-redacted`, example names |
| AutoDL GPU instance ID | `autodl-instance-redacted` |

The Git history was rewritten so these identifiers do not appear in old commits either,
and commit author/committer emails were changed to the GitHub noreply address.
Local pre-sanitization backups are **not** part of this repository.

## Reporting a security issue

Please report suspected vulnerabilities privately via GitHub Security Advisories
("Security" tab → "Report a vulnerability") or email `Liyou2778@users.noreply.github.com`.
Please do not open a public issue for sensitive reports.

## Scope note

The code is provided for portfolio/demonstration and interview review purposes.
It is not a production-hardened deployment; see the honest limitations documented in
`docs/M11-7B混合门禁报告.md` and `docs/M12-体验增强报告.md`.
