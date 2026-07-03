#!/bin/bash

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_EXEC="$SCRIPT_DIR/.venv/bin/python"
OVERLAY_BIN="$SCRIPT_DIR/longshot_overlay"
TEMP_VIDEO="/tmp/longshot_temp.mp4"
WF_LOG="/tmp/longshot_wf.log"
OUTPUT_DIR="$HOME/Pictures/longshots"
PID_FILE="/tmp/longshot_recording.pid"
PADDING=10

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
  rm -f "$TEMP_VIDEO" "$WF_LOG"
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

  notify-send -u normal "Longshot" "长截图已取消"
  echo "Cancelled all operations."
}

cmd_start() {
  if [ "$(cmd_status)" != "idle" ]; then
    notify-send -u normal "Longshot" "当前已有任务正在进行"
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

  hyprctl eval "hl.dispatch(hl.dsp.exec_cmd('[no_anim; move $oX $oY; size $oW $oH] ${OVERLAY_BIN} $oW $oH'))" \
    >/dev/null 2>&1

  notify-send -t 3000 "🔴 Longshot" "开始录制，请缓慢向下滚动网页\n完成后再次点击图标"
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

  notify-send -t 3000 "Longshot" "正在拼接图像，请稍候..."
  local out_img="$OUTPUT_DIR/longshot_$(date +%s).png"

  if [ ! -f "$TEMP_VIDEO" ]; then
    die "Temp video missing, stitching aborted."
  fi

  "$PYTHON_EXEC" "$SCRIPT_DIR/stitcher.py" "$TEMP_VIDEO" "$out_img"

  if [ -f "$out_img" ]; then
    notify-send -i "$out_img" "✅ Longshot" "长截图已生成: $out_img"
    cleanup_temp
  else
    die "Failed to generate longshot"
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
