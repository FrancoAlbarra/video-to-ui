---
name: video-to-ui
version: 1.0.0
description: "Drop a video (file path or Twitter/X/YouTube URL) and get a frontend implementation brief — layout, a timestamped animation timeline, and a paste-ready build prompt — by extracting frames and visually analyzing them."
allowed-tools:
  - Bash
  - Read
  - Write
  - Glob
  - AskUserQuestion
triggers:
  - video to ui
  - recreate this video
  - analyze this video
  - recreate the effect
  - implement this animation
  - build this from a video
  - what's happening in this video
  - turn this video into code
---

## What this does

The user gives you a **video** — a local path (e.g. `./ref.mp4`, a file dragged into
the terminal) or a **URL** (Twitter/X, YouTube, etc.). You "watch" it by extracting
timestamped frames with `ffmpeg` and reading them as images, then produce:

1. **`brief.md`** — layout, component structure, typography, color, spacing, layering,
   and a per-effect breakdown (trigger, start/end state, movement, opacity, scale,
   blur, estimated duration, easing, stagger).
2. **`timeline.json`** — machine-readable timestamped animation events.
3. **A self-contained build prompt** — paste-ready for Cursor/Claude Code to implement
   it in the user's stack.

No second AI, no API keys — frame extraction is local (`ffmpeg`/`yt-dlp`) and *you* do
the visual analysis by reading the frames.

## Step 1 — Extract frames

Run the extractor. Pass the source verbatim (quote it — paths may have spaces):

```bash
bash ~/.claude/skills/video-to-ui/scripts/extract.sh "<SOURCE>" --fps 3
```

It prints a JSON manifest and writes to `./.video-to-ui/<name>/`:
- `frames/fNNNN_t<seconds>s.jpg` — **the timestamp is in the filename**, so when you
  Read `f0012_t4.00s.jpg` you know it's at t=4.00s.
- `contactsheet_NN.jpg` — a tiled overview grid (cheap to read).
- `manifest.json` — video metadata (native fps, resolution, duration, codec).

Useful flags:
- `--fps N` — sampling rate. Default 3. Bump to 8–12 for a short window with fast motion.
- `--start S --end E` — only extract a time range (seconds). Use this to zoom into one effect.
- `--width W` — frame width in px (default 960; lower = cheaper to read).
- `--max-frames N` — hard cap (default 90); sampling auto-reduces to stay under it.

## Step 2 — Watch it (two passes)

**Overview pass (cheap):** Read the `contactsheet_*.jpg` first to understand the whole
clip — overall layout, what moves, roughly when. Each cell steps forward by
`1/fps` seconds, left-to-right, top-to-bottom (the manifest states seconds-per-cell).

**Detail pass (precise):** Identify the window where the interesting effect happens,
then **re-extract that window densely** and Read the individual frames to nail timing:

```bash
bash ~/.claude/skills/video-to-ui/scripts/extract.sh "<SOURCE>" --start 2.0 --end 3.2 --fps 12 --out ./.video-to-ui/detail
```

Read frames in order and track, per element: position, opacity, scale, blur, color.
Compare consecutive frames to estimate duration and easing (does it start fast and
settle? → ease-out; slow-in-slow-out? → ease-in-out; constant? → linear).

> **Fast motion caveat:** at low fps a whole transition can fall between two frames.
> If a change appears to "jump" in one frame step, re-extract that window at higher
> `--fps` before trusting any timing numbers.

## Step 3 — Decide the target stack

If the user already named a stack or you're inside a project, use it — detect the
framework and reuse their existing colors/typography/components (don't invent tokens;
honor the user's design-system instructions). If the stack is genuinely unknown and it
matters, ask briefly with AskUserQuestion; otherwise default to **React + Tailwind +
Framer Motion** and say so.

## Step 4 — Write the outputs

Write all three into `./.video-to-ui/<name>/` (next to the frames).

**`brief.md`** — structure it as:
- *Overview* — what the UI is and the headline effect, in one paragraph.
- *Layout & structure* — components, hierarchy, layering/z-order.
- *Visual design* — typography, color (hex estimates), spacing, radius, shadows.
- *Animation timeline* — a table: `timestamp | element | property | from → to | duration | easing | trigger`.
- *Implementation notes* — suggested library + approach, keyed to the chosen stack.
- Mark every inferred/estimated value **`(est.)`** and separate it from what's directly
  visible. Never present a guessed easing/duration as observed fact.

**`timeline.json`** — shape:
```json
{
  "duration_s": 0.0,
  "events": [
    { "t_start": 0.0, "t_end": 0.4, "element": "card", "property": "transform",
      "from": "translateY(16px) scale(0.98)", "to": "translateY(0) scale(1)",
      "opacity": [0, 1], "easing": "ease-out (est.)", "trigger": "mount",
      "evidence": "frames t0.00–t0.40" }
  ]
}
```

**The build prompt** — end your chat reply with one fenced, self-contained prompt the
user can paste elsewhere. It must restate the layout, the timeline, the chosen stack,
and an instruction to preserve the reference design and honor existing project tokens.

## Step 5 — Report

In chat, give a short summary (what the effect is, headline timings) and the paths to
`brief.md`, `timeline.json`, and the frames dir. Then the paste-ready build prompt.
Note anything you couldn't determine from the frames (e.g. exact easing, hover vs.
scroll trigger) so the user knows what's an estimate.

## Notes
- `./.video-to-ui/` is scratch output; suggest gitignoring it. Frames can be re-created
  anytime from the source.
- URLs are downloaded to a temp dir via `yt-dlp`; the resolved file path is in the manifest.
- If `yt-dlp` fails on a URL (login-walled, etc.), ask the user to download the video
  manually and pass the local path instead.
