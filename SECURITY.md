# Security Policy

## Supported Versions

Security fixes are provided for the latest `main` branch and the current minor release line:

| Version | Supported |
| --- | --- |
| `main` / latest `2.1.x` | Yes |
| `< 2.1.0` | No — please upgrade to the latest release |

## Reporting a Vulnerability

Please **do not** report security vulnerabilities through public GitHub issues or pull requests.

Instead, use GitHub's **Private Vulnerability Reporting** feature on this repository:
1. Open the **Security** tab → **Advisories** → **Report a vulnerability**.
2. Include the affected version/commit, reproduction steps, impact assessment, and any suggested fix.

You can expect an initial acknowledgement within **7 days**. Once validated, we will coordinate a fix and release advisory before public disclosure.

## Scope & Security Boundaries

VWA is a local Windows desktop application. High-priority security issues include:
- Arbitrary code execution or unsafe deserialization when opening project files (`.svp`, `.ustx`, `.vpr`, `.acep`, `.ufdata`, `.yaml`, `.json`, presets)
- Integrity bypasses in the updater or optional component downloader (SHA-256 verification, path traversal, Zip Slip)
- Privilege or boundary escapes in the optional local MCP integration
- Credential or browser session leakage in logs, diagnostics, or published artifacts
