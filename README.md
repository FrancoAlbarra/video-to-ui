# video-to-ui

A [Claude Code](https://claude.com/claude-code) skill that turns a **video of a UI or
animation** into a **frontend implementation brief** — layout, a timestamped animation
timeline, and a paste-ready build prompt.

Drop in a local video file *or* a Twitter/X / YouTube URL. The skill extracts timestamped
frames locally with `ffmpeg`, Claude **watches** them (reading the frames as images), and
writes up what's happening and how to rebuild it.

> **No second AI, no API keys.** Frame extraction is local; Claude itself does the visual
> analysis. Nothing about your video leaves your machine except the `yt-dlp` download when
> you pass a URL.

---

## Why

You see a slick UI effect in a video and want to recreate it in code. Instead of
eyeballing it frame by frame, drop the video into Claude Code and get back:

1. **`brief.md`** — layout, component structure, typography, color, spacing, layering,
   and a per-effect breakdown (trigger, start/end state, movement, opacity, scale, blur,
   estimated duration, easing, stagger).
2. **`timeline.json`** — machine-readable, timestamped animation events with evidence frames.
3. **A self-contained build prompt** — paste into Cursor / Claude Code to implement it in
   your stack (defaults to React + Tailwind + Framer Motion; reuses your project's design
   tokens when you run it inside a repo).

---

## Install

**Requirements**

- [Claude Code](https://claude.com/claude-code)
- [`ffmpeg`](https://ffmpeg.org) + `ffprobe` — `brew install ffmpeg`
- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) — `brew install yt-dlp` *(only needed for URLs)*

**Add the skill**

Clone into your Claude Code skills directory:

```bash
git clone https://github.com/FrancoAlbarra/video-to-ui.git ~/.claude/skills/video-to-ui
```

or drop it in a project-local `.claude/skills/video-to-ui/` instead. Restart Claude Code
and it'll be available as `/video-to-ui`.

---

## Usage

In Claude Code:

```
/video-to-ui ./references/animation.mp4   recreate the card expansion, keep our colors
/video-to-ui https://x.com/user/status/123   build this hover effect in React
```

You can also just say it in plain language — *"recreate this video"*, *"what's happening
in this video"*, *"turn this into code"* — with a path or URL, and the skill triggers.

Outputs land in `./.video-to-ui/<name>/` (brief, timeline, frames, contact sheets).

---

## How it works

1. **Extract** — `scripts/extract.sh` resolves the source (downloads URLs via `yt-dlp`),
   probes it, and samples frames into `./.video-to-ui/<name>/frames/`. Each frame is named
   with its timestamp baked in — `f0012_t4.00s.jpg` — so Claude knows exactly when each one
   occurs. It also builds a tiled **contact sheet** for a cheap overview.
2. **Watch (two passes)** — Claude reads the contact sheet for the overall shape, then
   **re-extracts the interesting window densely** (`--fps 12`) and reads individual frames
   to nail timing and easing.
3. **Write** — Claude produces the brief, timeline, and build prompt.

### Extractor flags

```bash
bash scripts/extract.sh "<SOURCE>" [options]
```

| Flag | Default | Purpose |
|---|---|---|
| `--fps N` | `3` | Sampling rate. Bump to `8–12` for fast motion. |
| `--start S --end E` | full clip | Extract only a time range (seconds) — zoom into one effect. |
| `--width W` | `960` | Frame width in px (lower = cheaper for Claude to read). |
| `--max-frames N` | `90` | Hard cap; sampling auto-reduces to stay under it. |
| `--out DIR` | `./.video-to-ui/<name>` | Output directory. |
| `--no-sheet` | — | Skip the contact sheet. |

The script runs standalone too — handy for scripting frame dumps without Claude.

---

## Notes & limitations

- **Timing is estimated.** At a low sample rate a whole transition can fall between two
  frames. Re-extract the window at higher `--fps` before trusting durations/easing — the
  skill tells you when a value is an estimate.
- **`./.video-to-ui/` is scratch output** — gitignore it. Frames regenerate from the source.
- **URL downloads** go to a temp dir via `yt-dlp`; the resolved path is in `manifest.json`.
  If a URL is login-walled and `yt-dlp` fails, download the video manually and pass the path.
- Built and tested on macOS (Apple Silicon). Should work anywhere `bash` + `ffmpeg` do;
  font/path specifics are macOS-oriented but the pipeline uses filename timestamps (no
  `drawtext` dependency).

---

## License

MIT — see [LICENSE](LICENSE).
