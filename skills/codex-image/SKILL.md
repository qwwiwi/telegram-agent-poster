---
name: codex-image
description: Use when asked to generate an image, picture, cover, banner or illustration for a post (сгенерируй картинку, обложку, изображение) through the Codex/ChatGPT subscription – no OPENAI_API_KEY, no per-image billing; supports up to 5 reference images (по референсу, в стиле картинки). Also use when tempted to answer that image generation is impossible without an API key or that Codex is text-only.
---

# codex-image – images through the Codex (ChatGPT) subscription

Images are made by **gpt-image-2** through the OAuth login of the Codex CLI (`~/.codex/auth.json`,
created by `codex login`). No API key and no per-image payment: the quota is shared with the
ChatGPT subscription.

**Common mistake:** "Codex is text-only, images need OPENAI_API_KEY / DALL-E". Wrong. The Codex
backend serves the `image_generation` tool over the Responses API; a GPT host model calls it and
gpt-image-2 renders.

## Setup (once)

```bash
npm install -g @openai/codex      # Codex CLI
codex login                       # sign in with the ChatGPT subscription (server: codex login --device-auth)
```

`scripts/run.sh` creates its own virtualenv in `skills/codex-image/.venv` on the first run and
installs the `openai` Python package there (the SDK is used as the HTTP/streaming client for the
Codex backend). Nothing is installed globally.

Optional: the official plugin for Claude Code connects Claude Code to Codex –
https://github.com/openai/codex-plugin-cc (`/plugin marketplace add openai/codex-plugin-cc`,
`/plugin install codex@openai-codex`, `/codex:setup`). The plugin does not draw images;
this skill uses the same login.

## Quick reference

```bash
skills/codex-image/scripts/run.sh "prompt in English" [quality] [aspect] [ref1.png ... ref5.png]
```

| Param | Values | Notes |
|---|---|---|
| quality | `low` (~45 s) / `medium` (default, 1-2 min) / `high` (2-3 min) | draft in `low`, final in `high` |
| aspect | `landscape` 1536x1024 / `square` 1024x1024 / `portrait` 1024x1536 | |
| refN | up to 5 local images (png/jpg/webp) | style, character or logo transfer |
| output | prints the path of a PNG | saved to `~/.codex/cache/images/img_<timestamp>.png` |

Prompts in English work best; text that must appear on the image can be in any language
(give it in quotes). Generation is slow: run `high` in the background.

For a Telegram post cover use `landscape` and pass the file to
`python3 poster/post.py draft.html --photo <path> --dry-run`.

## Brief first (for anything more than a quick draft)

1. `python3 skills/codex-image/scripts/brief.py questions --type post-illustration` – ask the requester every question.
2. Collect references with roles (subject, face, style, logo, background, layout, clothing) or a preset (`brief.py presets`).
3. `brief.py init brief.json --type post-illustration`, fill it in (see `examples/post-cover.brief.json`).
4. `brief.py check brief.json` must exit 0; if not, it lists what to ask.
5. `brief.py prompt brief.json` prints the final English prompt and the exact `run.sh` command.
6. Run it, open the image, check the text (each string once, no extra text) and the subject.

Skip the questions only when the requester gives a complete brief – still run `check`.
`brief.py` is offline: no network, never reads any key.

## Hard constraints of the subscription path (otherwise HTTP 400 or a wrong path)

1. References: max 5, as `input_image` data-URL blocks next to the `input_text` in one user message.
2. Never call `client.images.generate()` – that is the API-key path, the subscription rejects it.
   Only Responses API with the `image_generation` tool (`tool_choice` forces it).
3. `stream=True` and `store=False` are mandatory.
4. `input` is a list of message objects, not a string.
5. Auth: `tokens.access_token` (+ `tokens.account_id` as `chatgpt-account-id`) from `~/.codex/auth.json`.

The host model that calls the tool is set by `CODEX_IMAGE_HOST_MODEL` (default in `codex_image.py`).
If a run fails with a model error, set this variable to a model your Codex account has.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: openai` | run through `scripts/run.sh`, not the system python |
| `Codex auth не найден` | `codex login` |
| HTTP 400 | check stream/store/input shape, or the host model name |
| 401 / 403 | token expired: `codex login` again |
| no image in the stream | the model refused (policy): rephrase the prompt |
| reference rejected | png/jpg/webp only, up to 15 MB each, 40 MB total, max 5 files |

## Rules

- Use only photos you own or have the person's consent for; never generate images of real people
  without their agreement; no misleading "screenshots" of products or events.
- Exact typography, brand colors and diagrams: draw SVG by hand and render it to PNG instead.
- Strict reproducibility (seed): not available on this path.
- Show every image to the channel owner before it is published.

Technique credit: the Responses-API + `image_generation` approach follows `ningzimu/codex-gpt-image`.
