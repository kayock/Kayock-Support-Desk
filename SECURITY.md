# Security Policy

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub
issues, discussions, or pull requests.**

This repository does not yet publish a dedicated private contact address
(the project owner should add one here — e.g. a security.txt file, a
maintainer email, or GitHub Private Vulnerability Reporting — when the
repository is made public). Until then, use the most private channel
available: contact a maintainer directly through their GitHub profile, or
mark an issue so the maintainers can move it to a private track.

Please include as much of the following as you can:

- The type of issue and its impact
- Steps to reproduce or a proof of concept
- The affected version/commit and your platform
- Any relevant log output (redact personal data)

## Scope and trust model

Kayock Support Desk is a **home-network application**. It intentionally
ships without authentication: it binds to your LAN so phones and tablets
can reach it, and it trusts whoever can reach that network. Things that are
in scope for security reports:

- Anything that lets a remote party read or delete data beyond the app's
  intended LAN trust model
- Path traversal or file-read escapes in the document/viewer endpoints
- Injection via uploaded manuals or user input
- The managed AI process lifecycle (e.g. killing or escaping into
  unrelated processes)
- Cross-origin/script-injection issues in the web UI or bundled pdf.js

Out of scope:

- "The app has no login" by itself (documented design; see the README's
  privacy section — do not expose it to the internet)
- Vulnerabilities in user-supplied model servers (llama-server/Ollama) or
  in Python packages installed at runtime — report those upstream, though
  we will help assess impact on Kayock

## Supported versions

Security fixes are made against the latest main branch. Because the
project ships as a release archive rather than versioned binaries, always
run the most recent published release or a current checkout of main.
