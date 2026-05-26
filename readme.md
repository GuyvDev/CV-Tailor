# CV Docker

Local-first AI resume tailoring app.

The app runs as a Docker Compose stack:

`React UI -> FastAPI orchestrator -> resume generator/scorer -> Typst compiler -> downloadable one-page PDF`

## What It Does

- Accepts a pasted job description.
- Reads private profile files from `data/profile/` or another `PROFILE_DIR`.
- Uses OpenAI or an OpenAI-compatible Abacus endpoint to draft a tailored resume.
- Scores the draft for role fit, ATS clarity, credibility, and one-page discipline.
- Renders a deterministic Typst resume and compiles it to PDF.
- Stores generated artifacts under `outputs/<job_id>/`.
- Provides a Personalize tab for editing your local profile files without committing them.

## Privacy Model

This repository is designed to be publishable without personal resume data.

Private/local files are ignored by Git:

- `.env` and `.env.*`
- `data/profile/`
- `data/now/`
- `temp/`
- `outputs/`
- `docker-compose.override.yml`

Public-safe sample data lives in `data/profile.example/`.

Never commit your real API keys, contact details, grades, generated resumes, scanner reports, or source CV material.

## Setup

1. Copy `.env.example` to `.env`.
2. Choose `LLM_PROVIDER=openai` or `LLM_PROVIDER=abacus`.
3. Optionally set `OPENAI_API_KEY` or `ABACUS_API_KEY`, or keep `ENABLE_DEMO_MODE=true` for a smoke test. You can also add API keys later from the web UI.
4. Start the stack:

```bash
docker compose up --build
```

5. Open `http://localhost:3000`.
6. Use the Personalize tab to create or switch profiles, configure provider/model/API keys, output filename, and private profile data.

The API is available at `http://localhost:8000`.

## Profile Files

The default private profile lives under `PROFILE_DIR`, which defaults to `/app/data/profile` inside Docker and `./data/profile` on your machine. Additional users/profiles are stored under `data/profiles/<profile-id>/` and selected from the Personalize tab.

Each profile can contain:

- `master_profile.md`
- `projects.json`
- `skills.json`
- `rules.md`
- `research_guidelines.md`
- `personalization.json`
- `app_settings.json`
- `templates/base_resume.typ`
- optional `examples/*.md`
- optional `notes/*.md`

You can copy the fake public example:

```bash
mkdir -p data/profile
cp -R data/profile.example/. data/profile/
```

Or use the web Personalize tab, which writes to the configured private `PROFILE_DIR` and initializes templates when needed.

## Personalization

Keep resume-specific personalization in your private profile folder, not in source code. The web Personalize tab edits `data/profile/personalization.json` for you.

Useful personalization options include:

- required profile phrase
- recommendation when that phrase is missing
- regex cleanup rule for content that should not appear in generated JSON
- fixed education or honors Typst block
- fallback profile sentence
- extra prompt notes for your writing preferences

Provider, model, API keys, and output filename can be edited in the web UI and are saved locally in `data/profile/app_settings.json`. `.env` remains a fallback for Docker/server defaults and for users who prefer file-based configuration.

The AI Setup Draft tool can convert rough notes, an existing CV, and preferences into draft profile files. It loads the draft into the editor for review; it does not save until you press Save.

## API Keys

The web UI intentionally does not display stored secret values. Edit `.env`, then restart Docker:

```bash
docker compose restart api telegram-bot
```

## Telegram Bot Setup

The Telegram bot is optional. It talks to the API service inside Docker and uses the active profile selected in the web UI. Configure the web profile first, then start the bot.

1. In Telegram, open `@BotFather`, run `/newbot`, choose a name and username, and copy the token. Treat this token like a password.
2. Find your numeric Telegram user id. You can use a trusted user-info bot, or send a message to your new bot and inspect updates during local testing.
3. Set these values in `.env` or in your private deployment environment:

```env
TELEGRAM_BOT_TOKEN=123456:your-token
TELEGRAM_ALLOWED_USER_IDS=123456789
API_URL=http://api:8000
```

4. Start the stack with the bot:

```bash
docker compose up --build frontend api typst-compiler telegram-bot
```

5. Open the web UI, choose the active profile, verify the output filename, and save settings.
6. In Telegram, send or paste a job description using the commands shown by the bot. Generated PDFs are produced by the same API pipeline as the web UI.

Operational notes:

- Keep `TELEGRAM_ALLOWED_USER_IDS` set; otherwise anyone who gets the bot token may use your generator.
- Rotate the bot token in BotFather if it is ever committed, shared, or exposed.
- Restart `telegram-bot` after changing Telegram env values: `docker compose restart telegram-bot`.
- The bot uses the active profile selected in the web UI. Switch profiles in the Personalize tab before sending Telegram jobs.

AI helper prompt for Telegram setup:

```text
I am configuring a local Docker CV tailoring app with an optional Telegram bot. Guide me step by step. Ask me for my BotFather token, my numeric Telegram user id, and whether I run Docker Compose locally or on a server. Then help me set TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS, and API_URL=http://api:8000 in .env, restart docker compose, verify the bot responds, and explain how to rotate the token if I accidentally expose it. Do not ask me to paste secrets into a public chat.
```

## End-to-End Test

Run the full local smoke test after changing profile, model, rendering, or Telegram-adjacent behavior:

```bash
docker compose up -d frontend api typst-compiler
python3 scripts/e2e_full_smoke.py
```

The test creates a temporary profile under `data/profiles/`, switches to it, changes app settings, uses AI Setup Draft, saves generated profile files, creates a tailored CV, renders the PDF to `temp/e2e/*.png`, checks the image is nonblank, and restores the previously active profile.

## Publish Checklist

Before the first GitHub commit:

```bash
./scripts/publish_audit.sh
```

Then initialize Git only after reviewing the staged file list:

```bash
git init
git status --short --ignored
git add .
git status --short
git commit -m "Initial public release"
```

If a secret or personal file is ever committed, remove it from history before pushing and rotate the affected key.

## Current Boundaries

- File-backed job metadata, not a production database.
- No authentication; run locally or add auth before deploying publicly.
- The profile editor is local-first and writes to `PROFILE_DIR`.
- Demo mode is for smoke testing, not final resume quality.
