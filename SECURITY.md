# Security policy

## Reporting a vulnerability

Please do not open a public issue for an undisclosed vulnerability. Use
GitHub's private vulnerability reporting feature for this repository when it
is available. If private reporting is unavailable, contact the repository
maintainer through a private GitHub channel before disclosure.

Include the affected version or commit, reproduction steps, impact, and any
minimal proof of concept that can be shared safely. Do not include passwords,
browser cookie databases, access tokens, or private music-library data.

## Scope

Security reports are especially useful for:

- unsafe Exportify ZIP extraction or path handling
- arbitrary file access through Flask endpoints
- command injection through metadata or source URLs
- credential or cookie exposure
- denial of service through uploads, decompression, downloads, or retries

spotm3u is a local application. Do not expose its development server to an
untrusted network.

## Supported versions

Only the latest repository state and the latest published release are
maintained for security fixes. Please include the version in every report.

Maintainers will acknowledge and investigate valid reports privately and will
coordinate disclosure after a fix is available. No fixed response time is
promised.
