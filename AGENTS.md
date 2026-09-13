# Agent Guidelines & Constraints

## Git, Merging & Deployment Protocol
- **MANDATORY**: Always check with the user and obtain explicit approval before committing, merging, or pushing changes to `main` / remote repository / VPS.
- Provide clear explanations and diff summaries of any proposed changes first, and wait for the user's confirmation before executing git write actions (`git commit`, `git merge`, `git push`) or restarting live production services.

## Sensitive File Protection & Confidentiality Protocol
- **STRICT PROHIBITION**: NEVER read, open, view, grep, echo, print, or output the contents of sensitive files (including `.env`, `.env.*`, credential files, private keys, API secrets, certificates, or tokens).
- When configuring or referencing environment variables, inspect only `.env.example`, documentation, or mock configs without inspecting real secrets or reading `.env` files.
- If a command, script, or tool would display `.env` or sensitive credentials in terminal output or logs, ensure values are redacted and never print or expose secret values.
