#!/usr/bin/env bash
# video-to-ui :: extract.sh
# Resolve a video source (local path OR url), probe it, and extract timestamped
# frames + a contact sheet so Claude can "watch" it by reading images.
#
# Usage:
#   extract.sh SOURCE [--fps N] [--start S] [--end E] [--width W]
#              [--out DIR] [--sheet-cols C] [--no-sheet] [--max-frames N]
#
# SOURCE: a local file path, or an http(s) URL (Twitter/X, YouTube, etc. via yt-dlp)
#
# Frames are named:  f0001_t0.00s.jpg   (index + source timestamp in seconds)
# so the filename itself tells Claude exactly when the frame occurs.

set -euo pipefail

# ---- defaults ----
FPS=3
START=0
END=""
WIDTH=960
OUT=""
SHEET_COLS=6
MAKE_SHEET=1
MAX_FRAMES=90          # hard cap so token cost stays bounded
SCENES=0               # --scenes : detect scene cuts (distinct screens/states)
SCENE_THR=0.30
SOURCE=""

die() { echo "ERROR: $*" >&2; exit 1; }

# ---- parse args ----
while [[ $# -gt 0 ]]; do
  case "$1" in
    --fps)         FPS="$2"; shift 2;;
    --start)       START="$2"; shift 2;;
    --end)         END="$2"; shift 2;;
    --width)       WIDTH="$2"; shift 2;;
    --out)         OUT="$2"; shift 2;;
    --sheet-cols)  SHEET_COLS="$2"; shift 2;;
    --no-sheet)    MAKE_SHEET=0; shift;;
    --max-frames)  MAX_FRAMES="$2"; shift 2;;
    --scenes)      SCENES=1; shift;;
    --scene-thr)   SCENE_THR="$2"; shift 2;;
    -*)            die "unknown option: $1";;
    *)             SOURCE="$1"; shift;;
  esac
done

[[ -n "$SOURCE" ]] || die "no SOURCE given. Pass a file path or a video URL."
command -v ffmpeg  >/dev/null || die "ffmpeg not found (brew install ffmpeg)"
command -v ffprobe >/dev/null || die "ffprobe not found (brew install ffmpeg)"

# ---- resolve source: download if it's a URL ----
VIDEO=""
if [[ "$SOURCE" =~ ^https?:// ]]; then
  command -v yt-dlp >/dev/null || die "yt-dlp not found (brew install yt-dlp) — needed for URLs"
  DLDIR="$(mktemp -d)"
  echo ">> downloading $SOURCE" >&2
  yt-dlp -q --no-playlist -o "$DLDIR/video.%(ext)s" "$SOURCE" >&2 || die "yt-dlp download failed"
  VIDEO="$(ls "$DLDIR"/video.* | head -1)"
  [[ -n "$VIDEO" ]] || die "download produced no file"
else
  VIDEO="$SOURCE"
  [[ -f "$VIDEO" ]] || die "file not found: $VIDEO"
fi

# ---- default output dir ----
# Hash the SOURCE (not the temp filename) so different URLs/paths never collide
# (URLs all download to "video.mp4"), while re-running the same source is stable.
BASE="$(basename "$VIDEO")"
BASE="${BASE%.*}"
if [[ -z "$OUT" ]]; then
  SRCHASH="$(printf '%s' "$SOURCE" | { md5sum 2>/dev/null || md5; } | awk '{print $1}' | cut -c1-6)"
  OUT="./.video-to-ui/${BASE}_${SRCHASH}"
fi
FRAMES="$OUT/frames"
rm -rf "$OUT"
mkdir -p "$FRAMES"

# ---- probe ----
read_probe() { ffprobe -v error -select_streams v:0 -show_entries "$1" -of default=nw=1:nk=1 "$VIDEO" 2>/dev/null | head -1; }
DUR="$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$VIDEO" 2>/dev/null | head -1)"
VW="$(read_probe stream=width)"
VH="$(read_probe stream=height)"
RFPS_RAW="$(read_probe stream=r_frame_rate)"   # e.g. 30000/1001
CODEC="$(read_probe stream=codec_name)"
# evaluate fractional fps
if [[ "$RFPS_RAW" == */* ]]; then
  RFPS="$(awk -F/ 'BEGIN{OFMT="%.3f"} {print ($2==0?0:$1/$2)}' <<<"$RFPS_RAW")"
else
  RFPS="$RFPS_RAW"
fi
DUR="${DUR:-0}"

# ---- clamp sampling so we never exceed MAX_FRAMES ----
EFF_END="${END:-$DUR}"
SPAN="$(awk -v e="$EFF_END" -v s="$START" 'BEGIN{d=e-s; print (d>0?d:0)}')"
WANT="$(awk -v sp="$SPAN" -v f="$FPS" 'BEGIN{printf "%d", (sp*f)+0.999}')"
if [[ "$WANT" -gt "$MAX_FRAMES" && "$(awk -v sp="$SPAN" 'BEGIN{print (sp>0)}')" == "1" ]]; then
  FPS="$(awk -v mf="$MAX_FRAMES" -v sp="$SPAN" 'BEGIN{printf "%.4f", mf/sp}')"
  echo ">> span ${SPAN}s would exceed ${MAX_FRAMES} frames; reduced sampling to ${FPS} fps" >&2
fi

# ---- build ffmpeg seek/trim args ----
SEEK=()
[[ "$(awk -v s="$START" 'BEGIN{print (s>0)}')" == "1" ]] && SEEK+=(-ss "$START")
TRIM=()
[[ -n "$END" ]] && TRIM+=(-to "$(awk -v e="$END" -v s="$START" 'BEGIN{print e-s}')")

echo ">> extracting frames at ${FPS} fps (width ${WIDTH}px) ..." >&2
ffmpeg -hide_banner -loglevel error \
  ${SEEK[@]+"${SEEK[@]}"} -i "$VIDEO" ${TRIM[@]+"${TRIM[@]}"} \
  -vf "fps=${FPS},scale=${WIDTH}:-2:flags=bicubic" \
  -q:v 3 "$FRAMES/raw_%05d.jpg"

# ---- rename raw_NNNNN.jpg -> fNNNN_t<seconds>s.jpg (timestamp in filename) ----
i=0
for f in "$FRAMES"/raw_*.jpg; do
  [[ -e "$f" ]] || break
  t="$(awk -v i="$i" -v fps="$FPS" -v st="$START" 'BEGIN{printf "%.2f", st + i/fps}')"
  idx="$(printf "%04d" "$((i+1))")"
  mv "$f" "$FRAMES/f${idx}_t${t}s.jpg"
  i=$((i+1))
done
COUNT="$i"
[[ "$COUNT" -gt 0 ]] || die "no frames extracted"

# ---- contact sheet(s) for a cheap overview ----
SHEET_NOTE="(disabled)"
if [[ "$MAKE_SHEET" -eq 1 ]]; then
  ROWS=6
  PERSHEET=$((SHEET_COLS*ROWS))
  ffmpeg -hide_banner -loglevel error \
    -framerate 1 -pattern_type glob -i "$FRAMES/f*.jpg" \
    -vf "scale=300:-2,tile=${SHEET_COLS}x${ROWS}:padding=4:color=black" \
    -q:v 3 "$OUT/contactsheet_%02d.jpg" 2>/dev/null || true
  NSHEETS="$(ls "$OUT"/contactsheet_*.jpg 2>/dev/null | wc -l | tr -d ' ')"
  SECS_PER_CELL="$(awk -v f="$FPS" 'BEGIN{printf "%.2f", 1/f}')"
  SHEET_NOTE="${NSHEETS} sheet(s), ${SHEET_COLS} cols x ${ROWS} rows, left-to-right top-to-bottom, ~${SECS_PER_CELL}s between cells (first cell = t${START}s)"
fi

# ---- scene detection (optional): find cuts between distinct screens/states ----
SCENE_CUTS="[]"
if [[ "$SCENES" -eq 1 ]]; then
  echo ">> detecting scene cuts (threshold ${SCENE_THR}) ..." >&2
  # fail-safe: no scene cuts => grep exits 1; || true so pipefail doesn't abort the script
  CUTS="$(ffmpeg -hide_banner -nostats -i "$VIDEO" \
    -filter:v "select='gt(scene,${SCENE_THR})',showinfo" -f null - 2>&1 \
    | { grep -oE 'pts_time:[0-9.]+' || true; } | cut -d: -f2 \
    | awk 'BEGIN{ORS=""} {printf (NR>1?", ":"")"%.2f",$1}' || true)"
  SCENE_CUTS="[${CUTS}]"
  echo ">> scene cuts at (s): ${CUTS:-none}" >&2
fi

# ---- manifest ----
cat > "$OUT/manifest.json" <<JSON
{
  "source": "$SOURCE",
  "resolved_video": "$VIDEO",
  "duration_s": ${DUR:-0},
  "native_resolution": "${VW}x${VH}",
  "native_fps": ${RFPS:-0},
  "codec": "${CODEC:-unknown}",
  "sampled": {
    "fps": ${FPS},
    "start_s": ${START},
    "end_s": "${END:-null}",
    "frame_width_px": ${WIDTH},
    "frame_count": ${COUNT}
  },
  "frames_dir": "$FRAMES",
  "frame_naming": "fNNNN_t<seconds>s.jpg  (timestamp is in the filename)",
  "contact_sheet": "$SHEET_NOTE",
  "scene_cuts_s": ${SCENE_CUTS}
}
JSON

echo "" >&2
echo "DONE" >&2
cat "$OUT/manifest.json"
echo "" >&2
echo ">> ${COUNT} frames in: $FRAMES" >&2
[[ "$MAKE_SHEET" -eq 1 ]] && echo ">> overview: $OUT/contactsheet_*.jpg" >&2
