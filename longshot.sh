#!/bin/bash

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_EXEC="$SCRIPT_DIR/.venv/bin/python"
OVERLAY_BIN="$SCRIPT_DIR/longshot_overlay"
TEMP_VIDEO="/tmp/longshot_temp.mp4"
WF_LOG="/tmp/longshot_wf.log"
OUTPUT_DIR="$HOME/Pictures/longshots"
PID_FILE="/tmp/longshot_recording.pid"
STITCH_LOG="/tmp/longshot_stitch.log"
# Border is drawn exactly over the recorded box (0 = no framing margin).
PADDING=0

mkdir -p "$OUTPUT_DIR"

die() {
  notify-send -u critical "Longshot" "$1" 2>/dev/null
  echo -e "\033[1;31m[ERROR] $1\033[0m" >&2
  exit 1
}

cleanup_overlay() {
  pkill -9 -f "$OVERLAY_BIN" 2>/dev/null || true
}

cleanup_temp() {
  # Keep the source video + logs by default while debugging. Set LONGSHOT_KEEP_VIDEO=0
  # (or rm /tmp/longshot_temp.mp4 manually) to free space afterwards.
  rm -f "$WF_LOG"
  [ "${LONGSHOT_KEEP_VIDEO:-1}" = "0" ] && rm -f "$TEMP_VIDEO"
}


cmd_status() {
  if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE" 2>/dev/null)" 2>/dev/null; then
    echo "recording"
  elif pgrep -f "stitcher.py" >/dev/null; then
    echo "stitching"
  elif pgrep -x "slurp" >/dev/null; then
    echo "selecting"
  else
    echo "idle"
  fi
}

cmd_cancel() {
  rm -f "$PID_FILE"
  pkill -x slurp 2>/dev/null

  local rec_pid
  rec_pid=$(pgrep -x wf-recorder)
  if [ -n "$rec_pid" ]; then
    kill -SIGINT $rec_pid 2>/dev/null
    while kill -0 $rec_pid 2>/dev/null; do sleep 0.1; done
  fi

  pkill -f "stitcher.py" 2>/dev/null
  cleanup_overlay
  cleanup_temp

  notify-send -u normal "Longshot" "Cancelled — recording discarded"
  echo "Cancelled all operations."
}

cmd_start() {
  if [ "$(cmd_status)" != "idle" ]; then
    notify-send -u normal "Longshot" "A longshot task is already in progress"
    exit 1
  fi

  GEOMETRY=$(slurp 2>/dev/null) || exit 0

  if ! [[ "$GEOMETRY" =~ ^([0-9]+),([0-9]+)\ ([0-9]+)x([0-9]+)$ ]]; then
    die "Unexpected geometry format from slurp"
  fi
  X="${BASH_REMATCH[1]}"
  Y="${BASH_REMATCH[2]}"
  W="${BASH_REMATCH[3]}"
  H="${BASH_REMATCH[4]}"

  wf-recorder -g "$GEOMETRY" -f "$TEMP_VIDEO" >"$WF_LOG" 2>&1 &
  REC_PID=$!
  echo "$REC_PID" >"$PID_FILE"

  local oX=$((X > PADDING ? X - PADDING : 0))
  local oY=$((Y > PADDING ? Y - PADDING : 0))
  local oW=$((W + PADDING * 2))
  local oH=$((H + PADDING * 2))

  # Spawn the blinking-border overlay and place it over the recorded area.
  # NOTE: `hyprctl eval` (Lua) is unavailable on classic hyprlang configs, so we
  # spawn via `dispatch exec` and position with xdotool. Hyprland maps new
  # floating windows CENTERED, so poll until the window exists before moving it;
  # the single-shot 0.5s wait was too short for raylib to finish mapping.
  (hyprctl dispatch exec -- "$OVERLAY_BIN" "$oW" "$oH" >/dev/null 2>&1 &)
  OWID=""
  for _ in $(seq 1 15); do          # wait up to ~3s for the overlay to map
    OWID=$(xdotool search --class longshot_overlay 2>/dev/null | tail -1)
    [ -n "$OWID" ] && break
    sleep 0.2
  done
  if [ -n "$OWID" ]; then
    xdotool windowmove "$OWID" "$oX" "$oY" >/dev/null 2>&1
    xdotool windowsize "$OWID" "$oW" "$oH" >/dev/null 2>&1
  else
    echo "[warn] overlay window not detected in time; it may stay at center" >&2
  fi

  notify-send -t 3000 "🔴 Longshot" "Recording started — scroll the page slowly, then press the key again to stop"
  echo "Started recording. PID: $REC_PID"
}

cmd_stop() {
  if [ "$(cmd_status)" != "recording" ]; then
    echo "Not recording, cannot stop."
    exit 1
  fi

  local rec_pid
  rec_pid=$(cat "$PID_FILE" 2>/dev/null)

  kill -SIGINT "$rec_pid" 2>/dev/null

  while kill -0 "$rec_pid" 2>/dev/null; do
    sleep 0.2
  done

  cleanup_overlay
  rm -f "$PID_FILE"

  notify-send -t 3000 "Longshot" "Stitching long image, please wait..."
  local out_img="$OUTPUT_DIR/longshot_$(date +%s).png"

  if [ ! -f "$TEMP_VIDEO" ]; then
    die "Temp video missing, stitching aborted."
  fi

  # Run the stitcher and capture everything (incl. per-segment debug) to a log.
  LONGSHOT_DEBUG="${LONGSHOT_DEBUG:-1}" \
    "$PYTHON_EXEC" "$SCRIPT_DIR/stitcher.py" "$TEMP_VIDEO" "$out_img" >"$STITCH_LOG" 2>&1
  local rc=$?

  if [ -f "$out_img" ]; then
    notify-send -i "$out_img" "✅ Longshot" "Longshot saved: $out_img"
    cleanup_temp
  else
    echo "----- stitcher log (last 40 lines) -----" >&2
    tail -40 "$STITCH_LOG" >&2
    die "Failed to generate longshot (see stitcher log /tmp/longshot_stitch.log, rc=$rc)"
  fi
}

CMD="${1:-help}"
case "$CMD" in
start) cmd_start ;;
stop) cmd_stop ;;
cancel) cmd_cancel ;;
status) cmd_status ;;
*)
  echo "Usage: $0 {start|stop|cancel|status}"
  exit 1
  ;;
esac
