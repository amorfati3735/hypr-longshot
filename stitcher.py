import os
import sys

import cv2
import numpy as np

# ---- Robust scrolling-longshot stitcher -------------------------------------
# Replaces the phase-correlation approach (which was direction-locked and
# fragile on real content) with a two-pass method:
#   Pass 1: find the ACTIVE (scrolling) band by accumulating row-level change
#           across the whole video -> sticky header/footer are excluded.
#   Pass 2: measure the vertical shift between consecutive sampled frames with
#           1D normalized cross-correlation of the row profile over that band,
#           and stitch by appending/prepending the newly-revealed strip. Both
#           scroll directions are supported.

CROP = 10                 # px trimmed from each edge (recording border)
TARGET_SAMPLE_FPS = 10.0  # desired sampling rate for analysis/stitching
STICKY_THRESH = 1.5       # avg per-row |diff| below which a row counts as sticky
STATIC_TOL = 2.0          # |dy| below this is considered static
MIN_CONF = 0.40           # min NCC confidence to trust a shift
REFRESH_CONF = 0.25       # below this, don't even refresh the reference frame
MAX_SHIFT_FRAC = 0.55     # max |dy| as a fraction of active height (no-overlap guard)
BATCH = 64                # consolidate strips every BATCH to bound memory

DEBUG = os.environ.get("LONGSHOT_DEBUG") == "1"


def _info(msg):
    print(f"[INFO] {msg}")


def _active_band(video, si):
    """Return (top, bot) rows of the region that actually scrolls."""
    cap = cv2.VideoCapture(video)
    ok, first = cap.read()
    if not ok:
        print("[ERROR] Cannot open video: " + video, file=sys.stderr)
        sys.exit(1)
    first = first[CROP:-CROP, CROP:-CROP]
    h, w = first.shape[:2]
    c0, c1 = int(w * 0.20), int(w * 0.80)
    gprev = cv2.cvtColor(first, cv2.COLOR_BGR2GRAY)
    acc = np.zeros(h, np.float64)
    n = 0
    fc = 0
    while True:
        ret, fr = cap.read()
        if not ret:
            break
        fc += 1
        if fc % si != 0:
            continue
        g = cv2.cvtColor(fr[CROP:-CROP, CROP:-CROP], cv2.COLOR_BGR2GRAY)
        acc += np.abs(gprev[:, c0:c1].astype(np.int32) - g[:, c0:c1].astype(np.int32)).mean(axis=1)
        gprev = g
        n += 1
    cap.release()
    acc /= max(1, n)
    top = 0
    while top < h and acc[top] < STICKY_THRESH:
        top += 1
    bot = h
    while bot > top and acc[bot - 1] < STICKY_THRESH:
        bot -= 1
    return top, bot, (c0, c1), n


def _row_profile(gray, c0, c1):
    return gray[:, c0:c1].mean(axis=1, dtype=np.float32)


def _ncc_shift(pa, pb, maxshift):
    """Best integer vertical shift + NCC confidence between two row profiles."""
    am = pa - pa.mean()
    bm = pb - pb.mean()
    if np.linalg.norm(am) < 1e-6 or np.linalg.norm(bm) < 1e-6:
        return 0, 0.0
    best_c, best_dy = 0.0, 0
    for dy in range(-maxshift, maxshift + 1):
        if dy < 0:
            a, b = am[:dy], bm[-dy:]
        elif dy > 0:
            a, b = am[dy:], bm[:-dy]
        else:
            a, b = am, bm
        if len(a) < 10:
            continue
        c = float(np.dot(a, b)) / (float(np.linalg.norm(a)) * float(np.linalg.norm(b)) + 1e-9)
        if c > best_c:
            best_c, best_dy = c, dy
    return best_dy, best_c


def stitch_video(video_path, output_path):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"[ERROR] Cannot open video: {video_path}", file=sys.stderr)
        sys.exit(1)
    ok, first = cap.read()
    if not ok:
        print("[ERROR] Video file is empty.", file=sys.stderr)
        sys.exit(1)
    first = first[CROP:-CROP, CROP:-CROP]
    h, w = first.shape[:2]
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()
    si = max(1, round(src_fps / TARGET_SAMPLE_FPS))
    _info(f"{w}x{h} @ {src_fps:.1f}fps | sample every {si}")

    # ---- Pass 1: active band ----
    top, bot, (c0, c1), n = _active_band(video_path, si)
    ah = bot - top
    if ah < int(h * 0.15):
        _info(f"Active band too small ({ah}px), using full frame")
        top, bot, ah = 0, h, h
    else:
        _info(f"Active region: rows [{top}, {bot}) ({ah}/{h}px)")

    # ---- Pass 2: stitch ----
    cap = cv2.VideoCapture(video_path)
    cap.read()  # discard duplicate first frame (already read)
    gprev = cv2.cvtColor(first, cv2.COLOR_BGR2GRAY)
    strips: list = [first]
    max_shift = int(ah * MAX_SHIFT_FRAC)
    appended = 0
    prepended = 0
    fc = 0
    try:
        while True:
            ret, fr = cap.read()
            if not ret:
                break
            fc += 1
            if fc % si != 0:
                continue
            fr = fr[CROP:-CROP, CROP:-CROP]
            g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
            pa = _row_profile(gprev, c0, c1)[top:bot]
            pb = _row_profile(g, c0, c1)[top:bot]
            dy, conf = _ncc_shift(pa, pb, max_shift)
            if DEBUG:
                print(f"[DBG] f{fc} dy={dy:7.1f} conf={conf:.3f}")
            if conf >= MIN_CONF and abs(dy) >= STATIC_TOL:
                k = int(round(abs(dy)))
                if 0 < k <= ah:
                    if dy > 0:  # content moved up -> new content at bottom
                        strips.append(fr[bot - k:bot, :])
                        appended += 1
                    else:       # content moved down -> new content at top
                        strips.insert(0, fr[top:top + k, :])
                        prepended += 1
                if len(strips) >= BATCH:
                    strips = [np.concatenate(strips, axis=0)]
                gprev = g
            elif conf >= REFRESH_CONF:
                gprev = g
    finally:
        cap.release()

    _info(f"Stitched {appended} bottom + {prepended} top segments")
    result_img = np.concatenate(strips, axis=0) if len(strips) > 1 else strips[0]

    out_dir = os.path.dirname(os.path.abspath(output_path))
    if not os.path.isdir(out_dir):
        print(f"[ERROR] Output directory does not exist: {out_dir}", file=sys.stderr)
        sys.exit(1)
    if not cv2.imwrite(output_path, result_img):
        print(f"[ERROR] Failed to write image: {output_path}", file=sys.stderr)
        sys.exit(1)
    print(f"[OK] {result_img.shape[1]}x{result_img.shape[0]}px -> {output_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python stitcher.py <input_video> <output_image>", file=sys.stderr)
        sys.exit(1)
    stitch_video(sys.argv[1], sys.argv[2])
