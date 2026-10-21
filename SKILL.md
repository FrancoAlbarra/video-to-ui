---
name: video-to-ui
version: 2.2.0
description: "Drop a video (file path or Twitter/X/YouTube URL) and get a frontend implementation brief — layout, a timestamped animation timeline with PIXEL-MEASURED durations & easing curves, sampled colors, and a paste-ready build prompt. Watches by extracting frames; measures timing by analyzing pixels (not eyeballing)."
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

The user gives you a **video** — a local path (`./ref.mp4`, a dragged file) or a **URL**
(Twitter/X, YouTube, …). You "watch" it by extracting timestamped frames, and you **measure**
its motion by analyzing pixels. Output:

1. **`brief.md`** — layout, components, typography, sampled colors, spacing, layering, and a
   per-effect breakdown. Every value tagged **[OBS]** (directly seen / measured) or **[EST]**
   (inferred) — never blur the two.
2. **`timeline.json`** — machine-readable timestamped events with measured durations/easing.
3. **A self-contained, tiered build prompt** — paste-ready for Cursor/Claude Code.

No second AI, no API keys. Frame extraction is local (`ffmpeg`/`yt-dlp`); measurement is local
(`measure.py`, Pillow); *you* do the visual reading. **The whole value is honesty about what is
observed vs guessed — and turning "it feels like ~0.3s ease-out" into a measured number.**

## Step 1 — Extract an overview

```bash
bash ~/.claude/skills/video-to-ui/scripts/extract.sh "<SOURCE>" --fps 3
```
Writes to `./.video-to-ui/<name>_<srchash>/`: `frames/fNNNN_t<sec>s.jpg` (timestamp in filename),
`contactsheet_NN.jpg` (tiled overview), `manifest.json` (native fps/res/duration — note the
**native fps**, you'll want it later; the dir is hashed per source so clips never collide). Flags:
`--fps N`, `--start S --end E`, `--width W` (default 960), `--max-frames N` (default 90), and
**`--scenes`** (ffmpeg scene-cut detection → `scene_cuts_s` in the manifest; for long clips, use the
cut timestamps to split work per distinct screen/state before measuring).

## Step 2 — Watch (overview pass)

Read `contactsheet_*.jpg` first — cheap. Understand the whole clip: layout, what moves, roughly
when, and **how many distinct interactions** there are. Each cell steps `1/fps` seconds.

Note these common realities and don't fake them:
- **The clip may open mid-flow** (e.g. a command bar already open). If there's no entry animation
  in frame, say so — don't invent one.
- **One visible "effect" is often two phases** (e.g. a fast *blur* then a separate slow *fade*).
  Watch for a plateau between them.
- **Cards/panels often stream** — they re-render token-by-token as a query is typed (title,
  totals, rows, CTA label changing live). That behavior is usually the point; capture it.

## Step 3 — Measure timing & easing (do NOT eyeball)

For each transition that matters, re-extract that window at **the source's native fps** (from the
manifest — usually 60 or 30) and a bit wider than the move, then measure:

```bash
bash ~/.claude/skills/video-to-ui/scripts/extract.sh "<SOURCE>" --start 3.0 --end 3.4 --fps 60 --width 1400 --out ./.video-to-ui/win1
python3 ~/.claude/skills/video-to-ui/scripts/measure.py ./.video-to-ui/win1/frames --metric edges --box 0.03,0.42,0.56,0.70
```

`measure.py` prints a per-frame progress table **and** MEASURED: direction, onset, end, duration
(the 15–85% core), shape points, an auto-easing label, and an **S/N ratio**.

**MEASURE THE HEADLINE INTERACTION FIRST, not the easy one.** The trap is measuring a convenient
fade/fill and estimating the real star (a slide, a collapse, a cascade). Identify the signature
motion, then pick the metric that actually captures it:

| animation signature | metric | notes |
|--------|----------|---------|
| blur / un-blur / focus | `edges` | sharpness (edge-energy) in box |
| content fill / height grow / text-in | `ink` | dark-pixel count in box |
| fade to white/black | `bright` | mean luminance (rises/falls) |
| **slide / translate / collapse / move** | **`motion`** | centroid x/y per frame → dx,dy,direction,duration,easing. x/y only (rotation UNSUPPORTED) |
| **cascade / list or grid reveal / stagger** | **`stagger`** | per-band onsets → items, inter-onset ms, regularity (CV). Self-flags if NOT a clean cascade |
| one exact color | `color` `--pick ink\|red\|sat\|mean` | darkest / reddest / most-saturated / avg; use a TIGHT box |
| whole palette / design tokens | `palette` `--colors N` | median-cut bg + sparse-sampled text/accent tokens (overview; verify) |
| **WHAT changed between two moments** | **`diff`** | bbox (normalized) of the changed region first→last frame → localizes the interaction, then measure THAT box |
| **WHERE the UI blocks are (layout)** | **`regions`** | connected-components → list of blocks: bbox + area% + color. `--fg-thr` ~12 for light/low-contrast UIs, ~45 for high-contrast. `--debug-crop` writes a boxed overlay to verify |

Add **`--fit`** to any scalar metric or motion axis to grid-fit a **CSS cubic-bezier** to the
measured progress (prints the bezier + RMS + nearest named curve). RMS < 0.05 → OBSERVED-quality;
higher → ESTIMATED. Turns "ease-out (est.)" into `cubic-bezier(0.16,1,0.3,1), RMS 0.03`.

`--box x0,y0,x1,y1` is NORMALIZED (0..1). For `stagger`, `--bands N` ≈ expected item count.

**Scope / honesty:** `motion` is centroid translation only (no rotation/perspective — needs numpy,
UNSUPPORTED). `stagger` is for distinct items that appear once; on an expand/collapse/scroll/replace
region it will report `CV` high and warn "not a clean cascade" — believe it, and measure per-event
instead. At 30fps native, sub-~100ms curves are bounds, not beziers — say so.

**Copying LAYOUT/structure (not just motion):** to rebuild faithfully you need geometry, not only
timing. The fast path:
1. `--scenes` (at extract) to split a long clip into distinct screens/states.
2. `diff` between a before/after frame → the normalized bbox of what the interaction touched; feed
   that box straight into `ink`/`motion`/`edges` to measure it precisely (no guessing the box).
3. `regions` on the most-rendered frame → the list of UI blocks (cards/rows/panels) with their
   normalized bboxes + colors; `--debug-crop` to eyeball the boxed overlay. Map each block to a
   div/card in the build, and read spacing/alignment off the box coordinates. This is what turns a
   brief from "there's a card" into "card at x:0.31–0.68, two stacked, 4% gap".

**The #1 failure is region contamination.** A box that catches something static+high-contrast
(search-bar text, the cursor, another element, a busy blurred bg) swamps the signal — you'll get a
nonsense curve. Defenses, in order:
1. Put the box ON the moving thing and OFF everything else (e.g. a card's empty-then-filling left
   column, *below* the input; the heatmap band, *away* from centered text).
2. Run with `--debug-crop` — it writes `box_first.jpg` / `box_last.jpg`; **Read them** to confirm
   the box frames the target. Budget 2–3 tries; this is normal.
3. Check the printed **S/N ratio** — if LOW, the box or window is wrong. Re-pick.

**The auto-easing label is a FIRST PASS.** It reports the 15–85% core, so it can miss a slow-in
or slow-out tail (a true sigmoid may print as "ease-out"). **Always eyeball the progress table**
and correct: slow→fast→slow = ease-in-out; fast then settle = ease-out; slow build = ease-in;
even steps = linear. Report the full-motion duration (including tails) plus a `cubic-bezier` that
matches the table.

If a transition is still <~2 frames even at native fps, say the curve is unresolved — don't guess.

Sample colors the same way: `--metric color --pick ink` (text), `--pick red` (accents), from a
hi-res frame (`extract.sh … --width 1920`).

## Step 4 — Decide the target stack

If the user named a stack or you're inside a project, use it — detect the framework and reuse their
existing tokens/components (don't invent tokens; honor the user's design-system instructions). Else
ask briefly with AskUserQuestion, or default to **React + Tailwind + Framer Motion** and say so.

## Step 5 — Write the outputs (into `./.video-to-ui/<name>/`)

**`brief.md`** — sections: Overview · Clip entry (note if it opens mid-flow) · Layout & structure
(+ z-order) · Visual design (**sampled** colors) · Interactions (hover, modals, streaming) ·
Animation timeline (table) · Implementation notes · Known gaps.
Tag **every** value **[OBS]** or **[EST]**. For measured rows write the basis, e.g.
`60fps pixel-measured`. Never present a guessed easing/duration as fact.

Timeline table columns: `timestamp | element | property | from → to | duration | easing | trigger | basis`.

**`timeline.json`** — per event include measured fields when you have them:
```json
{
  "duration_s": 0.0,
  "colors": { "text_primary": "#030303", "accent": "#A90A2D" },
  "events": [
    { "t_start": 3.08, "t_end": 3.22, "element": "background", "property": "filter (blur)",
      "from": "blur(0)", "to": "blur(~10px)", "duration_ms": 130,
      "easing": "ease-in-out (sigmoid)", "easing_evidence": "60fps edge-energy: 50% @t3.125",
      "trigger": "typing", "basis": "OBS curve (60fps)" }
  ]
}
```

**The build prompt** — one fenced, self-contained block, **tiered into `=== OBSERVED ===`
(build to this) and `=== ESTIMATED (tune) ===`** so whoever pastes it knows what's firm. Restate
layout, measured timeline, sampled colors, chosen stack, and "preserve the reference; honor
existing project tokens."

## Step 6 — Report

Short chat summary: what the UI is, the headline interaction, the **measured** headline timings,
and paths to `brief.md`/`timeline.json`/frames. Then the paste-ready prompt. Call out whatever is
still [EST].

## Notes
- `./.video-to-ui/` is scratch; suggest gitignoring it. Frames/measurements regenerate from source.
- URLs download to a temp dir via `yt-dlp`; resolved path is in the manifest. If `yt-dlp` fails
  (login wall), ask the user to download and pass the local path.
- `measure.py` needs Pillow (`pip install pillow`).
- Cost discipline: keep the overview at 3fps; only spend native-fps frames on the few windows you
  actually measure.
