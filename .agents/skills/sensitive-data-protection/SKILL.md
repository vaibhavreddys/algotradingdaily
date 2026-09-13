---
name: sensitive-data-protection
description: Guidelines and instructions preventing the reading, exposure, or leakage of sensitive configuration files, including .env files, credentials, private keys, and API tokens.
---

# Sensitive Data Protection Skill

This skill enforces strict security hygiene and confidentiality protocols across all agent interactions.

## 1. Zero-Read Policy for Sensitive Files
- **Never Inspect Sensitive Files**: Do NOT use `view_file`, `grep_search`, `cat`, `type`, or any shell command to read `.env`, `.env.local`, `.env.production`, or any credential/secret files.
- **Never Output Secrets**: Never echo, print, or disclose passwords, TOTP keys, broker API secrets, session tokens, or private SSH keys.

## 2. Safe Configuration Workflows
- **Use Template Files**: When checking expected configuration keys or structure, refer solely to `.env.example`, `config.py`, or documentation.
- **Variable Presence Checks**: If verifying environment configuration, check only for key existence or names (e.g., `os.getenv('KEY') is not None`), never print or log the actual values.
- **Log & Terminal Hygiene**: Ensure commands do not emit secret variables to standard output, background task logs, or chat transcripts.
