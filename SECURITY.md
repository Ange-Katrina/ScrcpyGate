# Security policy

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/Ange-Katrina/ScrcpyGate/security/advisories/new)
to report a suspected vulnerability. Do not open a public issue with exploit
details or sensitive deployment information.

Include the affected commit or image digest, deployment mode, expected and
observed behavior, and a minimal reproduction using synthetic data. Remove
passwords, API tokens, session cookies, private domains, device addresses,
database contents, and unrelated logs. Do not upload a complete `.env` file.

The maintainer will review reports privately and coordinate any fix and public
disclosure with the reporter. Response and resolution times depend on maintainer
availability; there is no guaranteed response time or bug bounty program.

## Supported versions

Security fixes currently target the latest `main` commit and its published
`edge` image. There are no maintained historical release branches yet.
The `dev` and `canary` image channels are experimental and are not supported
production releases. Report vulnerabilities found in any channel privately.

Deployments pinned to an older digest must explicitly update to receive fixes.
An image passing CI does not establish that all device or ALAS integrations have
been tested. See [CI/CD](docs/deployment/ci-cd.md) for the validation boundary.

## Deployment expectations

Keep ADB on a trusted network and put public HTTP/WebSocket access behind TLS
and the access controls described in the [README](README.md#security-model).
Keep credentials and runtime state out of Git, issue reports, and build artifacts.
If a credential is exposed, revoke or rotate it at its issuer; deleting a file
or repository does not invalidate the credential.
