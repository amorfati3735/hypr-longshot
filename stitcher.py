import os
import sys

import cv2
import numpy as np

CROP = 10                    # 边框裁剪像素
TARGET_SAMPLE_FPS = 10       # 目标采样帧率
MIN_MOVEMENT = 5             # 最小有效滚动像素
MIN_RESPONSE = 0.20          # phaseCorrelate 置信度阈值
STATIC_DY = 1.0              # |dy| 小于此值视为静态
STICKY_CALIB_FRAMES = 6      # sticky 区域校准所用采样帧数
STICKY_ROW_THRESH = 1.5      # 平均逐行差异低于此值的行视为不动
PREV_AGING_LIMIT = 30        # 连续未匹配次数上限,超过则强制刷新 prev
VSTACK_BATCH = 64            # 增量合并阈值,控制 slices 列表长度

cv2.ocl.setUseOpenCL(False)


def detect_sticky_rows(grays: list[np.ndarray], h: int, thresh: float) -> tuple[int, int]:
    """根据校准帧序列推断 sticky top/bottom 行索引。

    思路:累计相邻帧的逐行 abs diff,从顶部/底部开始连续接近 0 的行视为 sticky。
    返回 (active_top, active_bot),拼接时只使用 [active_top, active_bot) 区域。
    """
    if len(grays) < 2:
        return 0, h

    accum = np.zeros(h, dtype=np.float64)
    for a, b in zip(grays[:-1], grays[1:]):
        accum += np.abs(a.astype(np.int32) - b.astype(np.int32)).mean(axis=1)
    accum /= len(grays) - 1

    top = 0
    while top < h and accum[top] < thresh:
        top += 1

    bot = h
    while bot > top and accum[bot - 1] < thresh:
        bot -= 1

    return top, bot


def stitch_video(video_path: str, output_path: str) -> None:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"[ERROR] Cannot open video: {video_path}", file=sys.stderr)
        sys.exit(1)

    try:
        ret, first_frame = cap.read()
        if not ret:
            print("[ERROR] Video file is empty.", file=sys.stderr)
            sys.exit(1)

        first_frame = first_frame[CROP:-CROP, CROP:-CROP]
        h, w = first_frame.shape[:2]
        center_start, center_end = int(w * 0.25), int(w * 0.75)

        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        sample_interval = max(1, round(src_fps / TARGET_SAMPLE_FPS))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        print(
            f"[INFO] {w}x{h} @ {src_fps:.1f}fps | "
            f"~{total_frames} frames | sample every {sample_interval}"
        )

        # ---------- Phase 1: Sticky 区域校准 ----------
        gray_first = cv2.cvtColor(first_frame, cv2.COLOR_BGR2GRAY)
        calib_grays = [gray_first]
        frame_count = 0

        while len(calib_grays) < STICKY_CALIB_FRAMES:
            ret, fr = cap.read()
            if not ret:
                break
            frame_count += 1
            if frame_count % sample_interval != 0:
                continue
            fr = fr[CROP:-CROP, CROP:-CROP]
            calib_grays.append(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY))

        sticky_top, sticky_bot = detect_sticky_rows(calib_grays, h, STICKY_ROW_THRESH)
        active_h = sticky_bot - sticky_top

        if active_h < h * 0.3:
            print(
                f"[WARN] Sticky detection unreliable ({active_h}px active), "
                f"falling back to full frame",
                file=sys.stderr,
            )
            sticky_top, sticky_bot = 0, h
            active_h = h
        else:
            print(
                f"[INFO] Active region: rows [{sticky_top}, {sticky_bot}) "
                f"({active_h}/{h}px)"
            )

        active_w = center_end - center_start
        # Hanning 窗减小 FFT 边缘伪影,提升 phaseCorrelate 精度
        hann = cv2.createHanningWindow((active_w, active_h), cv2.CV_32F)

        # ---------- Phase 2: 拼接初始化 ----------
        # 第一帧只保留到 sticky_bot,删除底部固定栏避免出现在结果中间
        slices: list[np.ndarray] = [first_frame[:sticky_bot, :]]

        gray_prev = calib_grays[-1]
        gray_prev_active = gray_prev[sticky_top:sticky_bot, center_start:center_end]

        stitch_count = 0
        aging = 0

        # ---------- Phase 3: 主循环 ----------
        while True:
            ret, curr_frame = cap.read()
            if not ret:
                break
            frame_count += 1
            if frame_count % sample_interval != 0:
                continue

            curr_frame = curr_frame[CROP:-CROP, CROP:-CROP]
            gray_curr = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)
            gray_curr_active = gray_curr[sticky_top:sticky_bot, center_start:center_end]

            # 一次相位相关同时拿到位移和置信度,取代两段 matchTemplate
            (_dx, raw_dy), response = cv2.phaseCorrelate(
                np.float32(gray_prev_active),
                np.float32(gray_curr_active),
                hann,
            )
            dy = -raw_dy  # 正值 = 内容向上滚动,出现新内容

            # 静态/反向/低置信度统一处理
            if abs(dy) < STATIC_DY or dy < MIN_MOVEMENT or response < MIN_RESPONSE:
                aging += 1
                if aging > PREV_AGING_LIMIT:
                    # prev 太老,强制刷新避免越拖越无法对齐
                    gray_prev = gray_curr
                    gray_prev_active = gray_curr_active
                    aging = 0
                continue

            dy_int = int(round(dy))
            if dy_int >= active_h:
                # 单步滚动超出有效高度,无法保证连续 -> 仅刷新 prev
                gray_prev = gray_curr
                gray_prev_active = gray_curr_active
                aging = 0
                continue

            # 从 curr 的 active 区域底部取新出现的内容
            new_content = curr_frame[sticky_bot - dy_int : sticky_bot, :]
            if new_content.shape[0] > 0:
                slices.append(new_content)
                stitch_count += 1
                # 增量合并,防止 list 过长 + 让大数组连续分配
                if len(slices) >= VSTACK_BATCH:
                    slices = [np.vstack(slices)]

            gray_prev = gray_curr
            gray_prev_active = gray_curr_active
            aging = 0

        print(f"[INFO] Stitching {stitch_count} segments...")
        result_img = np.vstack(slices)

        out_dir = os.path.dirname(os.path.abspath(output_path))
        if not os.path.isdir(out_dir):
            print(f"[ERROR] Output directory does not exist: {out_dir}", file=sys.stderr)
            sys.exit(1)

        ok = cv2.imwrite(output_path, result_img)
        if not ok:
            print(f"[ERROR] Failed to write image: {output_path}", file=sys.stderr)
            sys.exit(1)

        print(f"[OK] {result_img.shape[1]}x{result_img.shape[0]}px → {output_path}")

    finally:
        cap.release()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python stitcher.py <input_video> <output_image>", file=sys.stderr)
        sys.exit(1)

    stitch_video(sys.argv[1], sys.argv[2])
