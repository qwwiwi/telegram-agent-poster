---
name: telegram-agent-poster
description: Run a Telegram channel with an AI agent – track who subscribes and from which invite link, create named invite links for marketing, write and publish formatted posts (Telegram HTML), write headlines and generate post images through the Codex subscription. Use for any task about the channel – пост в канал, ссылка-приглашение, откуда пришли подписчики, отчёт по подписчикам, обложка для поста, заголовок, просмотры постов.
---

# telegram-agent-poster – instructions for the agent

You run a Telegram channel for its owner. All commands are run from the repository root.
Settings are in `.env` (never print or commit it). Python 3.11+, no packages needed except for images.

## Rules (read first)

1. **Review before publish.** Never publish without the owner seeing the final text (and image).
   Flow: write draft -> `--dry-run` -> show the owner -> only after "да / publish" run with `--yes`.
2. **No spam.** One useful post at a time, no more than the cadence the owner agreed on (default: at most
   1-2 posts a day). No mass invites, no buying subscribers, no pretending to be a person, no misleading claims.
3. **No secrets in posts.** Tokens, keys, chat ids of private chats, IPs, file paths, other people's private data.
   `post.py` blocks the obvious ones; you still read the text.
4. **Never print `.env` or the bot token.** Do not paste them into chats, posts or logs.
5. **Facts only.** Do not invent numbers, quotes or results. Take numbers from `tracker/report.py`.
6. Use the bot API only (this project). Do not log in as the owner's personal account.
7. The tracker stores Telegram user ids and @usernames of subscribers. Do not publish them, do not export them.

## What is where

| Task | Command |
|---|---|
| Start the subscription tracker (keeps running) | `python3 tracker/bot.py` |
| Create a named invite link | `python3 tracker/create_invite.py <name>` |
| List links made here | `python3 tracker/create_invite.py --list` |
| Subscriber report | `python3 tracker/report.py --days 30` (`--json` for machine reading) |
| Check a draft | `python3 poster/check.py draft.html` |
| Preview a post | `python3 poster/post.py draft.html --dry-run` |
| Publish a post | `python3 poster/post.py draft.html [--photo cover.png] [--button "Text\|https://..."] --yes` |
| Post views | `python3 poster/views.py` |
| Formatting cheat sheet | `poster/FORMATTING.md` |
| Headlines | `skills/headlines/SKILL.md` |
| Images | `skills/codex-image/SKILL.md` |
| Tests | `python3 tests/run_all.py` |

## Workflow: new post

1. Ask the owner for the topic, the reader, and the one action after reading (or take them from the task).
2. Headline and hook: follow `skills/headlines/SKILL.md`; give 2-3 variants and let the owner choose, or choose and say why.
3. Write the draft to `drafts/<date>-<topic>.html` (the `drafts/` folder is yours; create it). Telegram HTML only:
   `<b> <i> <u> <s> <tg-spoiler> <a href> <code> <pre> <blockquote> <blockquote expandable>` – see `poster/FORMATTING.md`.
   First ~300 characters carry the point; 400-1500 characters is a good length; end with a question or one call to action.
4. Optional image: `skills/codex-image/scripts/run.sh "<English prompt>" medium landscape` -> a PNG path.
   Look at the image. If it has wrong or garbled text, regenerate.
5. `python3 poster/post.py drafts/<file>.html --photo <png> --dry-run` – fix every `[error]`, read the warnings.
6. Show the owner the text, the image and the dry-run output. Wait for approval.
7. Publish with `--yes`. Report the printed message link back to the owner.
8. A day later run `python3 poster/views.py` and compare posts **only at the same age** (views keep growing for weeks).

## Workflow: marketing links

- One link per source: `instagram_bio`, `youtube_desc`, `ads_nov`, `friend_anna`. Name = what you will see in the report. Max 32 characters.
- Put each link only in its own place; a shared link makes the report useless.
- Options: `--limit N` (max joins), `--expire-days N`, `--request` (owner approves each join).
- After a week: `python3 tracker/report.py --days 7`. Say which link brought the most subscribers and how many left.
  Do not draw conclusions from fewer than ~20 joins.

## Tracker must be running

The report only knows what `tracker/bot.py` has seen while it was running (Telegram sends join and leave
events only to a running bot, and keeps them for about 24 hours). If the report is empty, check that the
bot process is up, that the bot is a channel administrator, and that `CHANNEL` in `.env` is correct.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `BOT_TOKEN is not set` | fill `.env` from `.env.example` |
| `Bad Request: chat not found` | wrong `CHANNEL`, or the bot is not added to the channel |
| `not enough rights` | give the bot admin rights: post messages, add members (invite users) |
| `can't parse entities` | HTML error: run `poster/check.py`, escape `<`, `>`, `&` |
| `Conflict: terminated by other getUpdates request` | two tracker instances run, or the bot has a webhook: use a dedicated bot, one process |
| views.py: no posts | the channel has no public @username or the web preview is disabled |

## Invite links

Every invite link gets a `name` (the source) and `member_limit: 99999`. Links without a limit sometimes break. `tracker/create_invite.py` sets it by default; never create an unlimited link.
