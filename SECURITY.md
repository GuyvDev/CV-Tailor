# Security Policy

This project is intended for local-first use with private resume data and API keys stored outside Git. Public deployments should use stateless mode unless the stateful API is protected by `API_AUTH_TOKEN`, a VPN, or an external authentication layer.

## Do Not Commit

- `.env` or `.env.*` files
- real profile data under `data/profile/`
- generated outputs under `outputs/`
- scanner reports, grades, source CVs, or extracted personal text
- production values for `API_AUTH_TOKEN`, model provider keys, or Telegram bot tokens

## Deployment Rules

- Use `docker-compose.stateless.yml` or the Vercel stateless function for public unauthenticated demos.
- Keep the regular `docker-compose.yml` stack private; it is stateful, mounts profile/output data, and powers the Telegram workflow.
- Do not expose the stateful API unless `APP_ENV=production`, `REQUIRE_API_AUTH=true`, and `API_AUTH_TOKEN` are set, or a reverse proxy/VPN provides equivalent protection.
- Keep request-body logging disabled because resume and job-description text may be private.
- Use the Output Retention panel or `/api/outputs/cleanup` to remove generated resume archives that no longer need to be stored.

## Reporting

If you find a security issue in a public fork, open a private advisory or contact the maintainer without posting secrets or personal data in an issue.
