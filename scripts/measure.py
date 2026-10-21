#!/usr/bin/env python3
"""
video-to-ui :: measure.py  (v2 — motion / stagger / easing-fit / palette)
Turn a dense (ideally native-fps) frame sequence into MEASURED timing, easing,
MOTION, STAGGER, and COLOR — instead of eyeballing. Pure ffmpeg+Pillow, zero extra deps.

METRICS (--metric):
  edges     edge-energy (sharpness)      -> blur / un-blur / focus
  ink       dark-pixel count             -> content fill / height growth / text-in
  bright    mean luminance               -> fade to white (rises) / black (falls)
  color     sampled hex (--pick)         -> one color, honest (ink|red|mean)
  palette   median-cut palette + tokens  -> bg/text/accent design tokens
  motion    centroid x/y per frame       -> SLIDE / translate / collapse (position!)
  stagger   per-band onsets              -> "N items appear every X ms" (sequences!)

ADD-ON (--fit): on a scalar metric (edges/ink/bright) or a motion axis, grid-fit a
  CSS cubic-bezier to the measured progress and report it + RMS + nearest named curve.

Usage:
  measure.py FRAMES_DIR --metric edges  --box 0.03,0.42,0.56,0.70 --fit
  measure.py FRAMES_DIR --metric motion --box 0.3,0.1,0.7,0.9 --fit
  measure.py FRAMES_DIR --metric stagger --box 0.28,0.12,0.62,0.9 --bands 8
  measure.py FRAMES_DIR --metric palette --box 0.0,0.0,1.0,1.0 --colors 8
  measure.py FRAMES_DIR --metric ink --box ... --debug-crop   # verify the box

--box x0,y0,x1,y1 normalized 0..1 (default full frame).

CRITICAL: region contamination is the #1 failure. A box catching static+high-contrast
stuff (search text, cursor, another element, busy blurred bg) swamps the signal. Use
--debug-crop to dump box_first.jpg / box_last.jpg and Read them. Check the S/N line.
Auto-easing is a FIRST PASS (15-85% core) — eyeball the printed progress table.
"""
import argparse, glob, os, re, sys
try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("ERROR: Pillow not installed. Run: pip install pillow")


# ---------- frames / geometry ----------
def frames(fdir):
    fs = sorted(glob.glob(os.path.join(fdir, "f*.jpg")))
    out = []
    for f in fs:
        m = re.search(r"_t([\d.]+)s", os.path.basename(f))
        if m:
            out.append((float(m.group(1)), f))
    if not out:
        sys.exit(f"ERROR: no fNNNN_t<sec>s.jpg frames in {fdir}")
    return out


def box_px(W, H, box):
    x0, y0, x1, y1 = box
    return int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)


# ---------- scalar metrics ----------
def edge_energy(im, b):
    px = im.load(); x0, y0, x1, y1 = b; e = 0
    for y in range(y0, y1, 2):
        for x in range(x0, x1 - 2, 2):
            d = px[x + 2, y] - px[x, y]
            e += d if d >= 0 else -d
    return e


def ink_count(im, b, thr):
    px = im.load(); x0, y0, x1, y1 = b; c = 0
    for y in range(y0, y1, 2):
        for x in range(x0, x1, 2):
            if px[x, y] < thr:
                c += 1
    return c


def brightness(im, b):
    px = im.load(); x0, y0, x1, y1 = b; s = n = 0
    for y in range(y0, y1, 2):
        for x in range(x0, x1, 2):
            s += px[x, y]; n += 1
    return s / max(1, n)


# ---------- centroid (for motion) ----------
def centroid(im, b, thr, W, H):
    """Darkness-weighted centroid within box, returned normalized to FULL frame (0..1)."""
    px = im.load(); x0, y0, x1, y1 = b
    sw = sx = sy = 0.0
    for y in range(y0, y1, 2):
        for x in range(x0, x1, 2):
            v = px[x, y]
            if v < thr:
                w = (thr - v)
                sw += w; sx += w * x; sy += w * y
    if sw == 0:
        return None
    return (sx / sw) / W, (sy / sw) / H, sw


# ---------- easing classification (first pass) ----------
def classify_easing(series):
    ts = [t for t, _ in series]; vs = [v for _, v in series]
    k = max(3, len(vs) // 10)
    base = sorted(vs[:k])[k // 2]
    plat = sorted(vs[-k:])[k // 2]
    rng = plat - base
    direction = "rise" if rng >= 0 else "fall"
    arng = abs(rng) or 1

    def prog(v):
        return (v - base) / rng if rng else 0.0

    n = len(series)
    def persists(i, test):
        return all(test(series[j][1]) for j in range(i, min(n, i + 2)))
    onset = ts[0]; end = ts[-1]
    for i, (t, v) in enumerate(series):
        if abs(prog(v)) >= 0.15 and persists(i, lambda vv: abs(prog(vv)) >= 0.12):
            onset = t; break
    for i, (t, v) in enumerate(series):
        if t >= onset and abs(prog(v)) >= 0.85 and persists(i, lambda vv: abs(prog(vv)) >= 0.82):
            end = t; break
    dur = max(0.0, end - onset)

    def prog_at(frac):
        tt = onset + frac * dur
        return max(0.0, min(1.0, prog(min(series, key=lambda s: abs(s[0] - tt))[1])))
    p25, p50, p75 = prog_at(.25), prog_at(.50), prog_at(.75)
    if dur <= 0:
        label = "instant / sub-frame"
    elif p50 > 0.65:
        label = "ease-out (front-loaded)"
    elif p50 < 0.35:
        label = "ease-in (back-loaded)"
    elif p25 < 0.32 and p75 > 0.68:
        label = "ease-in-out (sigmoid)"
    else:
        label = "~linear"
    return dict(direction=direction, base=base, plat=plat, onset=onset, end=end,
                dur=dur, label=label, p25=p25, p50=p50, p75=p75)


# ---------- cubic-bezier fit (CSS easing), pure python ----------
def _bez(u, c1, c2):              # cubic bezier axis, endpoints 0->1
    mt = 1 - u
    return 3 * mt * mt * u * c1 + 3 * mt * u * u * c2 + u * u * u

def _solve_u(x, x1, x2):          # invert Bx(u)=x
    lo, hi = 0.0, 1.0
    for _ in range(18):
        mid = (lo + hi) / 2
        if _bez(mid, x1, x2) < x:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2

NAMED = {
    "linear": (0.0, 0.0, 1.0, 1.0),
    "ease": (0.25, 0.1, 0.25, 1.0),
    "ease-in": (0.42, 0.0, 1.0, 1.0),
    "ease-out": (0.0, 0.0, 0.58, 1.0),
    "ease-in-out": (0.42, 0.0, 0.58, 1.0),
    "easeOutQuart": (0.25, 1.0, 0.5, 1.0),
    "easeOutExpo": (0.16, 1.0, 0.3, 1.0),
    "easeOutBack": (0.34, 1.3, 0.64, 1.0),
}

def fit_bezier(samples):
    """samples: list of (t_norm 0..1, progress 0..1). Returns (x1,y1,x2,y2), rms, nearest_named."""
    s = [(t, p) for t, p in samples if 0.0 <= t <= 1.0]
    if len(s) < 4:
        return None
    if len(s) > 16:                      # subsample for speed
        step = len(s) / 16
        s = [s[int(i * step)] for i in range(16)]
    xs = [i / 10 for i in range(0, 11)]          # 0..1
    ys = [i / 10 for i in range(-2, 14)]         # -0.2..1.3 (allow over/undershoot)
    best = None
    for x1 in xs:
        for x2 in xs:
            for y1 in ys:
                for y2 in ys:
                    err = 0.0
                    for t, p in s:
                        u = _solve_u(t, x1, x2)
                        d = _bez(u, y1, y2) - p
                        err += d * d
                    if best is None or err < best[0]:
                        best = (err, x1, y1, x2, y2)
    rms = (best[0] / len(s)) ** 0.5
    cb = (best[1], best[2], best[3], best[4])
    # nearest named curve by control-point distance
    nn = min(NAMED.items(), key=lambda kv: sum((a - b) ** 2 for a, b in zip(kv[1], cb)))
    return cb, rms, nn[0]


def progress_samples(series, info):
    """Normalize to (t_norm, progress) over the FULL transition (3%..97%, incl. tails)
    so a 4-param bezier has enough shape to fit — the 15-85% core has too few points."""
    base, plat = info["base"], info["plat"]
    rng = (plat - base) or 1
    def prog(v):
        return (v - base) / rng
    lo_t, hi_t = series[0][0], series[-1][0]
    for t, v in series:
        if abs(prog(v)) >= 0.03:
            lo_t = t; break
    for t, v in series:
        if abs(prog(v)) >= 0.97:
            hi_t = t; break
    dur = (hi_t - lo_t) or 1
    out = []
    for t, v in series:
        tn = (t - lo_t) / dur
        if -0.05 <= tn <= 1.05:
            out.append((min(1.0, max(0.0, tn)), min(1.1, max(-0.1, prog(v)))))
    return out


# ---------- color / palette ----------
def _satv(c):
    mx, mn = max(c), min(c)
    return (mx - mn) / (mx or 1)

def sample_color(fr, box, pick, frac=None):
    """pick: ink (darkest) | red | sat (most saturated) | mean. frac overrides the
    percentile — tighten it (e.g. 0.005) when ink/accent is SPARSE over a wide box."""
    px_all = []
    for t, f in fr:
        im = Image.open(f).convert("RGB"); W, H = im.size
        px_all += list(im.crop(box_px(W, H, box)).getdata())
    def topk(key, default_frac):
        k = max(1, int(len(px_all) * (frac if frac else default_frac)))
        return sorted(px_all, key=key)[:k]
    if pick == "ink":
        s = topk(lambda c: c[0] + c[1] + c[2], 1 / 30)
    elif pick == "lite":                       # brightest pixels — for white text on dark UIs
        s = topk(lambda c: -(c[0] + c[1] + c[2]), 1 / 30)
    elif pick == "red":
        s = topk(lambda c: -(c[0] - (c[1] + c[2]) / 2), 1 / 30)
    elif pick == "sat":
        s = topk(lambda c: -_satv(c), 1 / 50)
    else:
        s = px_all
    r = sum(c[0] for c in s) // len(s); g = sum(c[1] for c in s) // len(s); b = sum(c[2] for c in s) // len(s)
    return r, g, b

def palette(fr, box, n):
    """Median-cut palette from the last frame (most-rendered) + crude token roles."""
    t, f = fr[-1]
    im = Image.open(f).convert("RGB"); W, H = im.size
    im = im.crop(box_px(W, H, box))
    q = im.quantize(colors=n, method=Image.MEDIANCUT)
    pal = q.getpalette()
    colors = q.getcolors() or []
    out = []
    total = sum(c for c, _ in colors) or 1
    for count, idx in sorted(colors, reverse=True):
        r, g, b = pal[idx * 3:idx * 3 + 3]
        out.append((count / total, (r, g, b)))
    return out


def luma(c): return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
def sat(c):
    mx, mn = max(c), min(c)
    return (mx - mn) / (mx or 1)
def hexs(c): return f"#{c[0]:02X}{c[1]:02X}{c[2]:02X}"


# ---------- diff (localize WHAT changed between two frames) ----------
def diff_bbox(fa, fb, thr):
    """Bounding box (normalized) of pixels that changed from frame fa -> fb."""
    a = Image.open(fa).convert("L"); b = Image.open(fb).convert("L")
    if a.size != b.size:
        b = b.resize(a.size)
    W, H = a.size; pa = a.load(); pb = b.load()
    minx = miny = 10 ** 9; maxx = maxy = -1; changed = 0; total = 0
    for y in range(0, H, 2):
        for x in range(0, W, 2):
            total += 1
            if abs(pa[x, y] - pb[x, y]) > thr:
                changed += 1
                if x < minx: minx = x
                if x > maxx: maxx = x
                if y < miny: miny = y
                if y > maxy: maxy = y
    if maxx < 0:
        return None, 0.0
    return (minx / W, miny / H, maxx / W, maxy / H), changed / (total or 1)


# ---------- regions (segment UI blocks via connected components) ----------
def bg_color(small):
    px = small.load(); w, h = small.size
    cs = [px[2, 2], px[w - 3, 2], px[2, h - 3], px[w - 3, h - 3]]
    return (sum(c[0] for c in cs) // 4, sum(c[1] for c in cs) // 4, sum(c[2] for c in cs) // 4)

def detect_regions(f, fg_thr, scale_w, min_area_frac, max_regions):
    im = Image.open(f).convert("RGB"); W, H = im.size
    sw = min(scale_w, W); sh = max(1, int(H * sw / W))
    small = im.resize((sw, sh)); px = small.load()
    bg = bg_color(small)
    def isfg(x, y):
        c = px[x, y]
        return abs(c[0] - bg[0]) + abs(c[1] - bg[1]) + abs(c[2] - bg[2]) > fg_thr
    seen = [[False] * sw for _ in range(sh)]
    comps = []
    total = sw * sh
    for sy in range(sh):
        for sx in range(sw):
            if seen[sy][sx] or not isfg(sx, sy):
                continue
            stack = [(sx, sy)]; seen[sy][sx] = True
            xs = []; ys = []; rs = gs = bs = n = 0
            while stack:
                x, y = stack.pop(); xs.append(x); ys.append(y)
                c = px[x, y]; rs += c[0]; gs += c[1]; bs += c[2]; n += 1
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < sw and 0 <= ny < sh and not seen[ny][nx] and isfg(nx, ny):
                        seen[ny][nx] = True; stack.append((nx, ny))
            if n < min_area_frac * total:
                continue
            comps.append(dict(
                box=(min(xs) / sw, min(ys) / sh, (max(xs) + 1) / sw, (max(ys) + 1) / sh),
                area=n / total, color=(rs // n, gs // n, bs // n), px=n))
    comps.sort(key=lambda c: -c["area"])
    return im, comps[:max_regions], bg


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("frames_dir")
    ap.add_argument("--metric", required=True,
                    choices=["edges", "ink", "bright", "color", "palette", "motion", "stagger",
                             "diff", "regions"])
    ap.add_argument("--box", default="0,0,1,1")
    ap.add_argument("--thr", type=int, default=110)
    ap.add_argument("--pick", choices=["ink", "lite", "red", "sat", "mean"], default="ink")
    ap.add_argument("--colors", type=int, default=8)
    ap.add_argument("--bands", type=int, default=8)
    ap.add_argument("--fit", action="store_true", help="fit a cubic-bezier to the measured progress")
    ap.add_argument("--debug-crop", action="store_true", help="dump box/overlay images to verify")
    # diff / regions tuning
    ap.add_argument("--diff-thr", type=int, default=28, help="per-pixel change threshold (diff)")
    ap.add_argument("--fg-thr", type=int, default=25, help="foreground vs bg threshold (regions); ~12 for very light/low-contrast UIs, ~45 for high-contrast/noisy")
    ap.add_argument("--min-area", type=float, default=0.003, help="min region area fraction (regions)")
    ap.add_argument("--max-regions", type=int, default=20)
    ap.add_argument("--scale-w", type=int, default=260, help="labeling downscale width (regions)")
    args = ap.parse_args()
    box = tuple(float(x) for x in args.box.split(","))
    fr = frames(args.frames_dir)

    if args.debug_crop and args.metric not in ("diff", "regions"):
        for tag, (t, f) in (("first", fr[0]), ("last", fr[-1])):
            im = Image.open(f).convert("RGB"); W, H = im.size
            im.crop(box_px(W, H, box)).save(os.path.join(args.frames_dir, f"box_{tag}.jpg"))
        print(f"wrote box_first.jpg / box_last.jpg in {args.frames_dir} — Read them to confirm the box.")

    # ---- color ----
    if args.metric == "color":
        r, g, b = sample_color(fr, box, args.pick)
        print(f"color ({args.pick}): {hexs((r,g,b))}   (rgb {r},{g},{b})")
        return

    # ---- palette / tokens ----
    if args.metric == "palette":
        pal = palette(fr, box, args.colors)
        print("PALETTE (median-cut, last frame):")
        for frac, c in pal:
            print(f"  {hexs(c)}  {frac*100:5.1f}%  (luma {luma(c):3.0f}, sat {sat(c):.2f})")
        if pal:
            # bg from palette (frequent + light); text/accent from DIRECT sparse-pixel
            # sampling because UI ink/accents are too sparse for median-cut buckets.
            bg = max(pal, key=lambda p: luma(p[1]) * p[0])[1]
            text = sample_color(fr, box, "ink", frac=0.004)   # tight: UI ink is sparse
            accent = sample_color(fr, box, "sat", frac=0.004)
            print("\nTOKENS (bg=palette; text/accent=sparse-pixel sampled — verify):")
            print(f"  bg      : {hexs(bg)}")
            print(f"  text    : {hexs(text)}")
            acc_sat = _satv(accent)
            print(f"  accent  : {hexs(accent)}  (sat {acc_sat:.2f}" +
                  ("" if acc_sat >= 0.15 else " — LOW, likely no real accent; treat as none") + ")")
            print("NOTE: bg = lightest frequent bucket; text = darkest 3% of pixels; accent = most-saturated 2%.")
        return

    # ---- diff (localize the changed region between first & last frame) ----
    if args.metric == "diff":
        box_r, frac = diff_bbox(fr[0][1], fr[-1][1], args.diff_thr)
        print(f"compared t={fr[0][0]:.2f}s -> t={fr[-1][0]:.2f}s   changed {frac*100:.1f}% of pixels")
        if box_r is None:
            print("no change detected — widen the time window or lower --diff-thr."); return
        print(f"changed region (normalized box): {box_r[0]:.3f},{box_r[1]:.3f},{box_r[2]:.3f},{box_r[3]:.3f}")
        print("-> feed this box to --metric ink/motion/edges to measure the interaction precisely.")
        if args.debug_crop:
            im = Image.open(fr[-1][1]).convert("RGB"); W, H = im.size
            d = ImageDraw.Draw(im)
            d.rectangle([box_r[0] * W, box_r[1] * H, box_r[2] * W, box_r[3] * H], outline=(255, 0, 0), width=3)
            op = os.path.join(args.frames_dir, "diff_overlay.jpg"); im.save(op)
            print(f"wrote {op} — Read it to see the changed region boxed.")
        return

    # ---- regions (segment UI blocks: bbox + size + color) ----
    if args.metric == "regions":
        im, comps, bg = detect_regions(fr[-1][1], args.fg_thr, args.scale_w, args.min_area, args.max_regions)
        print(f"bg ~ {hexs(bg)}   detected {len(comps)} region(s) (last frame, area-sorted):")
        print(f"{'#':>2} {'box (x0,y0,x1,y1 norm)':<34} {'area%':>6} {'color':>8}")
        for i, c in enumerate(comps):
            b = c["box"]
            print(f"{i+1:>2} {b[0]:.3f},{b[1]:.3f},{b[2]:.3f},{b[3]:.3f}".ljust(37) +
                  f"{c['area']*100:6.1f} {hexs(c['color']):>8}")
        print("-> each box is a UI block; use its box with other metrics, or map to divs/cards in the build.")
        if args.debug_crop:
            W, H = im.size; d = ImageDraw.Draw(im)
            for i, c in enumerate(comps):
                b = c["box"]
                d.rectangle([b[0] * W, b[1] * H, b[2] * W, b[3] * H], outline=(255, 0, 0), width=2)
                d.text((b[0] * W + 3, b[1] * H + 2), str(i + 1), fill=(255, 0, 0))
            op = os.path.join(args.frames_dir, "regions_overlay.jpg"); im.save(op)
            print(f"wrote {op} — Read it to verify the detected blocks.")
        print("NOTE: segmentation is approximate (downscaled CC on bg-contrast). Tune --fg-thr/--min-area; verify with --debug-crop.")
        return

    # ---- motion (centroid tracking) ----
    if args.metric == "motion":
        series_x = []; series_y = []
        last = None
        for t, f in fr:
            im = Image.open(f).convert("L"); W, H = im.size
            c = centroid(im, box_px(W, H, box), args.thr, W, H)
            if c is None:
                if last is None:
                    continue
                cx, cy = last
            else:
                cx, cy, _ = c; last = (cx, cy)
            series_x.append((t, cx)); series_y.append((t, cy))
        if not series_x:
            print("no ink in box — nothing to track. Re-pick --box (use --debug-crop)."); return
        dx = series_x[-1][1] - series_x[0][1]
        dy = series_y[-1][1] - series_y[0][1]
        axis = "x" if abs(dx) >= abs(dy) else "y"
        series = series_x if axis == "x" else series_y
        info = classify_easing(series)
        print(f"{'t(s)':>8} {'cx':>7} {'cy':>7}")
        for (t, cx), (_, cy) in zip(series_x, series_y):
            print(f"{t:8.3f} {cx:7.3f} {cy:7.3f}")
        dirn = ({"x": "right" if dx > 0 else "left", "y": "down" if dy > 0 else "up"})[axis]
        print("\n--- MEASURED (motion) ---")
        print(f"dx (norm) : {dx:+.3f}   dy (norm): {dy:+.3f}")
        print(f"dominant  : {axis}-axis, moving {dirn}")
        print(f"onset/end : t={info['onset']:.3f}s -> t={info['end']:.3f}s   duration {info['dur']*1000:.0f} ms")
        print(f"easing    : {info['label']} (on {axis}-axis)")
        if args.fit:
            fit = fit_bezier(progress_samples(series, info))
            if fit:
                cb, rms, nn = fit
                print(f"bezier    : cubic-bezier({cb[0]:.2f}, {cb[1]:.2f}, {cb[2]:.2f}, {cb[3]:.2f})  "
                      f"RMS={rms:.3f}  ~{nn}")
        print("NOTE: centroid-based; translation (x/y) only. Rotation/perspective = UNSUPPORTED (needs numpy).")
        return

    # ---- stagger (per-band onsets) ----
    if args.metric == "stagger":
        x0, y0, x1, y1 = box
        nb = args.bands
        onsets = []
        for bi in range(nb):
            by0 = y0 + (y1 - y0) * bi / nb
            by1 = y0 + (y1 - y0) * (bi + 1) / nb
            ser = []
            for t, f in fr:
                im = Image.open(f).convert("L"); W, H = im.size
                ser.append((t, ink_count(im, box_px(W, H, (x0, by0, x1, by1)), args.thr)))
            vs = [v for _, v in ser]
            base = min(vs); mx = max(vs); rng = mx - base
            if rng < 8:                 # band never meaningfully fills
                continue
            on = None
            for i, (t, v) in enumerate(ser):
                p = (v - base) / rng
                nxt = (ser[i + 1][1] - base) / rng if i + 1 < len(ser) else p
                if p >= 0.4 and nxt >= 0.3:
                    on = t; break
            if on is not None:
                onsets.append(on)
        onsets.sort()
        print(f"bands tested: {nb}   bands that fired: {len(onsets)}")
        print("onsets (s) :", ", ".join(f"{o:.3f}" for o in onsets) or "(none)")
        if len(onsets) >= 2:
            diffs = [onsets[i + 1] - onsets[i] for i in range(len(onsets) - 1)]
            diffs_ms = [d * 1000 for d in diffs]
            med = sorted(diffs_ms)[len(diffs_ms) // 2]
            print("inter-onset (ms):", ", ".join(f"{d:.0f}" for d in diffs_ms))
            mean = sum(diffs_ms) / len(diffs_ms)
            var = sum((d - mean) ** 2 for d in diffs_ms) / len(diffs_ms)
            cv = (var ** 0.5) / (mean or 1)
            print(f"\n--- MEASURED (stagger) ---")
            print(f"items     : {len(onsets)}")
            print(f"stagger   : ~{med:.0f} ms median between items")
            print(f"total span: {(onsets[-1]-onsets[0])*1000:.0f} ms")
            print(f"regularity: CV={cv:.2f} " + (
                "(regular -> OBSERVED clean stagger)" if cv <= 0.6 else
                "(!! IRREGULAR — not a clean cascade; likely expand/collapse/scroll/replace. "
                "The 'stagger' number is unreliable here — measure per-event onsets instead.)"))
            print("NOTE: tune --bands to ~item count; overlapping/fading items can merge -> ESTIMATED.")
        else:
            print("Not enough firing bands to measure stagger. Adjust --box / --bands.")
        return

    # ---- scalar metrics (edges/ink/bright) ----
    series = []
    for t, f in fr:
        im = Image.open(f).convert("L"); W, H = im.size
        b = box_px(W, H, box)
        v = edge_energy(im, b) if args.metric == "edges" else (
            ink_count(im, b, args.thr) if args.metric == "ink" else brightness(im, b))
        series.append((t, v))
    vs = [v for _, v in series]; lo, hi = min(vs), max(vs); rng = (hi - lo) or 1
    info = classify_easing(series)
    print(f"{'t(s)':>8} {'value':>12} {'progress':>8}")
    for t, v in series:
        p = (v - lo) / rng
        print(f"{t:8.3f} {v:12.1f} {p:8.2f} {'#'*int(p*40)}")
    flat = sorted(vs)[:max(3, len(vs) // 8)]
    noise = (max(flat) - min(flat)) or 1
    snr = abs(hi - lo) / noise
    print("\n--- MEASURED ---")
    print(f"direction : {info['direction']}")
    print(f"onset     : t={info['onset']:.3f}s")
    print(f"end       : t={info['end']:.3f}s")
    print(f"duration  : {info['dur']*1000:.0f} ms")
    print(f"shape pts : p25={info['p25']:.2f} p50={info['p50']:.2f} p75={info['p75']:.2f}")
    print(f"easing    : {info['label']}")
    print(f"S/N ratio : {snr:.1f}x  " + ("(clean)" if snr >= 6 else "(!! LOW — box contaminated or window wrong; re-pick)"))
    if args.fit:
        fit = fit_bezier(progress_samples(series, info))
        if fit:
            cb, rms, nn = fit
            print(f"bezier    : cubic-bezier({cb[0]:.2f}, {cb[1]:.2f}, {cb[2]:.2f}, {cb[3]:.2f})  RMS={rms:.3f}  ~{nn}")
            print(f"            (RMS<0.05 = good fit -> OBSERVED; higher -> ESTIMATED)")
    span = series[-1][0] - series[0][0]
    if span > 0 and info['dur'] > 0.70 * span:
        print("HINT      : motion fills >70% of window — lead-in/out clipped; re-run on a tighter window.")
    if 0 < info['dur'] < 2.5 / 60:
        print("HINT      : <~2 frames even here — sample higher fps (source native) to resolve the curve.")
    print("NOTE      : duration is the 15-85% core; auto-easing is a FIRST PASS — eyeball the table above.")


if __name__ == "__main__":
    main()
