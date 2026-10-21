# video-to-ui

A [Claude Code](https://claude.com/claude-code) skill that turns a **video of a UI or
animation** into a **frontend implementation brief** — layout, a timestamped animation
timeline with **pixel-measured durations & easing curves**, sampled colors, detected UI
blocks, and a paste-ready build prompt.

Drop in a local video file *or* a Twitter/X / YouTube URL. The skill extracts timestamped
frames locally with `ffmpeg`, Claude **watches** them, and then **measures** the motion by
analyzing pixels — so timings and easings are observed numbers, not eyeballed guesses.

> **No second AI, no API keys.** Frame extraction (`ffmpeg`/`yt-dlp`) and measurement
> (`measure.py`, Pillow) are local; Claude does the visual reading. Nothing about your video
> leaves your machine except the `yt-dlp` download when you pass a URL.

---

## Why

You see a slick UI in a video and want to recreate it in code. Instead of eyeballing it
frame by frame, drop the video into Claude Code and get back:

1. **`brief.md`** — layout & structure, sampled colors, typography, spacing/layering, and a
   per-effect breakdown. **Every value is tagged `[OBS]` (observed/measured) or `[EST]`
   (inferred)** — it never presents a guessed easing or duration as fact.
2. **`timeline.json`** — machine-readable, timestamped animation events with measured
   durations, easing (cubic-bezier when fit), and the evidence frames.
3. **A tiered build prompt** — split into `=== OBSERVED ===` (build to this) and
   `=== ESTIMATED (tune) ===`, paste-ready for Cursor / Claude Code (defaults to
   React + Tailwind + Framer Motion; reuses your project's design tokens inside a repo).

**The whole point is honesty** — turning "it feels like ~0.3s ease-out" into
`cubic-bezier(0.16, 1, 0.3, 1)` measured at 60fps, and clearly flagging what *can't* be
measured from the source.

---

## Install

**Requirements**

- [Claude Code](https://claude.com/claude-code)
- [`ffmpeg`](https://ffmpeg.org) + `ffprobe` — `brew install ffmpeg`
- [`Pillow`](https://python.org) — `pip install pillow` *(for `measure.py`)*
- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) — `brew install yt-dlp` *(only for URLs)*

**Add the skill**

```bash
git clone https://github.com/FrancoAlbarra/video-to-ui.git ~/.claude/skills/video-to-ui
```

or drop it in a project-local `.claude/skills/video-to-ui/`. Restart Claude Code and it's
available as `/video-to-ui`.

---

## Usage

```
/video-to-ui ./references/animation.mp4   recreate the card expansion, keep our colors
/video-to-ui https://x.com/user/status/123   build this hover effect in React
```

Plain language works too — *"recreate this video"*, *"turn this into code"* — with a path or
URL. Outputs land in `./.video-to-ui/<name>_<hash>/` (brief, timeline, frames, contact sheets).

---

## How it works

1. **Extract** — `scripts/extract.sh` resolves the source (downloads URLs via `yt-dlp`),
   probes it, and samples frames into `frames/` named with the timestamp baked in
   (`f0012_t4.00s.jpg`), plus a tiled **contact sheet**. The output dir is hashed per source
   so different clips never collide. `--scenes` adds ffmpeg scene-cut detection for long clips.
2. **Watch** — Claude reads the contact sheet for the overall shape and the distinct
   interactions.
3. **Measure (don't eyeball)** — for each transition that matters, re-extract that window at
   the source's **native fps** and run `measure.py` to get real numbers: duration, easing,
   motion, stagger, colors, layout.
4. **Write** — the brief, timeline, and tiered build prompt, with `[OBS]`/`[EST]` tags.

### `measure.py` — the measurement engine (ffmpeg + Pillow, zero extra deps)

```bash
python3 scripts/measure.py FRAMES_DIR --metric <metric> --box x0,y0,x1,y1 [--fit] [--debug-crop]
```

`--box` is normalized `0..1`. Pick the metric by the animation's signature:

| metric | measures | use for |
|---|---|---|
| `edges` | edge-energy (sharpness) | blur / un-blur / focus |
| `ink` | dark-pixel count | content fill / height growth / text streaming in |
| `bright` | mean luminance | fade to white (rises) / black (falls) |
| `motion` | centroid x/y per frame | **slide / translate / collapse** (dx, dy, direction, easing) |
| `stagger` | per-band onsets | **cascades / list reveals** (items, inter-onset ms, regularity CV) |
| `diff` | changed-region bbox | **localize an interaction** between two frames |
| `regions` | connected-components | **UI-block layout** (bbox + area% + color; `--debug-crop` overlay) |
| `color` | one sampled hex | `--pick ink\|lite\|red\|sat\|mean` (darkest/brightest/reddest/most-saturated/avg) |
| `palette` | median-cut palette | bg + text/accent design tokens |

Add **`--fit`** to any scalar metric or motion axis to grid-fit a **CSS cubic-bezier** to the
measured progress (prints bezier + RMS + nearest named curve). `--debug-crop` writes
verification images so you can confirm a measurement box is on-target before trusting it.

It outputs an **S/N ratio** and an auto-easing label as a *first pass* — the skill cross-checks
the per-frame table and corrects it, and reports the full-motion duration plus a matching bezier.

### Extractor flags

| Flag | Default | Purpose |
|---|---|---|
| `--fps N` | `3` | Sampling rate. Use the source's **native fps** (30/60) on windows you measure. |
| `--start S --end E` | full clip | Extract only a time range — zoom into one effect. |
| `--width W` | `960` | Frame width in px (use `1920` for precise color sampling). |
| `--max-frames N` | `90` | Hard cap; sampling auto-reduces to stay under it. |
| `--scenes` | off | Detect scene cuts (distinct screens/states) → `scene_cuts_s` in the manifest. |
| `--out DIR` | `./.video-to-ui/<name>_<hash>` | Output directory. |

---

## Honesty & limitations

- **Timing is measured, not guessed** — via pixel analysis at native fps, with every value
  tagged `[OBS]` or `[EST]`. But a source's framerate is the ceiling: at **30fps**, sub-~100ms
  curves are reported as bounds, not beziers. Measure at 60fps where you can.
- **Scope (honestly flagged):** `motion` is centroid translation only — **rotation /
  perspective is UNSUPPORTED** (would need numpy). `stagger` is for distinct items that appear
  once; on an expand/collapse/scroll/replace region it self-flags "not a clean cascade." **OCR
  is not included** (needs tesseract) — Claude reads text from the frames directly.
- **Region contamination** is the #1 measurement failure — a box catching static high-contrast
  stuff swamps the signal. Use `--debug-crop` + the S/N readout; budget 2–3 box tries.
- **`./.video-to-ui/` is scratch** — gitignore it. Frames/measurements regenerate from source.
- **URL downloads** go to a temp dir via `yt-dlp`; the resolved path is in `manifest.json`. If
  a URL is login-walled and `yt-dlp` fails, download the video and pass the local path.
- Built and tested on macOS (Apple Silicon); works anywhere `bash` + `ffmpeg` + Python/Pillow do.

---

## License

MIT — see [LICENSE](LICENSE).
