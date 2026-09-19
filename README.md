# "Hypr"LongShot

A scrolling screenshot (longshot) tool for **Arch + Hyprland** — maintained fork of
[Horizon0427/hypr-longshot](https://github.com/Horizon0427/hypr-longshot).

It combines a `bash` script for workflow management, a `C` program (`raylib`)
for drawing the recording overlay, and a `Python` program (OpenCV) for stitching
the video frames into a single long image.

## Changes in this fork

- **The recording overlay now actually appears.** Upstream spawned it with
  `hyprctl eval` (the Lua-API), which is a silent no-op on **classic Hyprlang**
  configs — so the red border never showed up. This fork spawns it with
  `hyprctl dispatch exec` and positions it with `xdotool`.
- **The overlay no longer jumps to the center of the screen.** The spawn polls
  until the window has finished mapping before moving it to the selected area.
- **The red border matches the box you draw.** `PADDING` is `0`, so the border
  is exactly the captured area.
- **Robust, direction-agnostic stitcher.** Upstream used OpenCV
  phase-correlation, which only handles downward scrolling and is fragile on
  real content (static headers/footers, low texture) — it often produced a
  single static frame. The rewrite detects the scrolling band from the whole
  video and measures offsets with 1D cross-correlation, so it works for either
  scroll direction and ignores fixed page header/footer.
- **English notifications** (upstream was Chinese).

## Disclaimer

This is NOT a universal, plug-and-play `Wayland` tool. It is tailored to a
personal `archlinux` + `Hyprland` setup. Please read the following before `git clone`.

1. **Hyprland Exclusive:** It relies on `hyprctl`, `wf-recorder`, `slurp` and a
   small X11 (`xdotool`) window to show the recording border. It will not work
   on `Sway`, `Niri`, `GNOME` or `KDE Wayland`.
2. **Scroll slowly and continuously** for the best result. Very fast jumps
   (more than the visible window height between samples) can't be aligned
   because consecutive frames no longer overlap. Both directions work.
3. **Debugging:** the source video is kept at `/tmp/longshot_temp.mp4` and the
   stitcher's per-frame trace at `/tmp/longshot_stitch.log`. Set
   `LONGSHOT_KEEP_VIDEO=0` to auto-delete the video, or `LONGSHOT_DEBUG=0` to
   quiet the trace.
4. **Hardcoded Paths:** captured images are saved to `$HOME/Pictures/longshots/`.
   Ensure this path works for you.

## Dependencies

Arch Linux package names:

- `slurp` (selecting the area)
- `wf-recorder` (capturing the scrolling video)
- `xdotool` (positioning the recording overlay)
- `libnotify` (for `notify-send` notifications)
- `raylib` (compiling the `C` overlay)
- `python`, `python-pip`, `python-venv` (for the stitching logic)

## Installation and setup

### Clone

```bash
git clone https://github.com/amorfati3735/hypr-longshot.git
cd hypr-longshot
```

### Compile the C overlay

```bash
gcc overlay.c -o longshot_overlay -lraylib -lGL -lm -lpthread -ldl -lrt -lX11
```

### Set up the Python virtual environment

The image stitching uses OpenCV. To keep your system clean, the scripts use a
local `.venv`:

```bash
python -m venv .venv
.venv/bin/pip install opencv-python-headless numpy
```

### Make the script executable

```bash
chmod +x longshot.sh
```

## Window rule

The overlay is a small floating window that must never take focus. Add a window
rule for it.

**Classic Hyprlang config** (this fork is set up for this):

```ini
windowrule = match:class ^(longshot_overlay)$, float on
windowrule = match:class ^(longshot_overlay)$, border_size 0
windowrule = match:class ^(longshot_overlay)$, rounding 0
windowrule = match:class ^(longshot_overlay)$, no_blur on
windowrule = match:class ^(longshot_overlay)$, no_shadow on
windowrule = match:class ^(longshot_overlay)$, no_initial_focus on
windowrule = match:class ^(longshot_overlay)$, no_focus on
windowrule = match:class ^(longshot_overlay)$, pin on
windowrule = match:class ^(longshot_overlay)$, suppress_event activatefocus maximize fullscreen
```

**Lua / Hypr3 config** (alternative):

```lua
hl.window_rule({
    name  = "longshot-overlay-rules",
    match = { class = "^(longshot_overlay)$" },
    float            = true,
    border_size      = 0,
    rounding         = 0,
    no_blur          = true,
    no_shadow        = true,
    no_initial_focus = true,
    no_focus         = true,
    pin              = true,
    suppress_event   = "activatefocus maximize fullscreen",
})
```

## Usage

`longshot.sh` is driven by subcommands rather than being run bare:

```bash
./longshot.sh start   # select an area and begin recording
./longshot.sh stop    # stop recording and stitch the result
./longshot.sh cancel  # abort and discard whatever is in progress
./longshot.sh status  # print idle | selecting | recording | stitching
```

**How to capture:**

1. run `longshot.sh start` (or click the Waybar module)
2. draw the area you want to capture with `slurp`
3. scroll the page — **slowly and continuously**, in either direction
4. run `longshot.sh stop` (or click the module again) to stop
5. wait a few seconds for the stitching to finish

**How it works**

1. `longshot.sh start` triggers `slurp` to get the geometry.
2. It starts `wf-recorder` to record that specific region into
   `/tmp/longshot_temp.mp4`.
3. It spawns the `raylib` overlay (`longshot_overlay`) via `hyprctl dispatch
   exec`, waits for it to map, then positions it exactly over the selected area
   with `xdotool`.
4. On `stop`, `stitcher.py` samples the video, detects the scrolling band
   (ignoring fixed header/footer), measures the vertical offset between frames
   with 1D normalized cross-correlation, and stitches the newly-revealed strips
   into one tall `PNG` — for **either** scroll direction.

### Waybar module

The `status` subcommand can be polled by a Waybar custom module:

```jsonc
"custom/longshot": {
    "format": "{icon}",
    "format-icons": {
        "idle": "󰹑",
        "selecting": "󰆞",
        "recording": "🔴",
        "stitching": "󰑮"
    },
    "exec": "state=$(~/hypr-longshot/longshot.sh status); printf '{\"alt\":\"%s\",\"class\":\"%s\"}' \"$state\" \"$state\"",
    "return-type": "json",
    "interval": 1,
    "on-click": "state=$(~/hypr-longshot/longshot.sh status); if [ \"$state\" = idle ]; then ~/hypr-longshot/longshot.sh start; else ~/hypr-longshot/longshot.sh stop; fi &",
    "on-click-right": "~/hypr-longshot/longshot.sh cancel &",
    "tooltip": true,
    "tooltip-format": "Left-click: start/stop long screenshot\\nRight-click: cancel & discard"
}
```

(Adjust `~/hypr-longshot/longshot.sh` to wherever you cloned it.)

## Known Bugs & Quirks

**Waybar cold start** — If you trigger the tool via a Waybar custom module right
after boot or a Waybar restart, the very first attempt can occasionally misbehave
(the overlay may spawn out of place, or the stop action may be unresponsive).
Reload Waybar or trigger once more; it stabilizes after the first attempt.

**If you get a single static frame**, you almost certainly did one of these:

- scrolled **too fast** in large jumps (frames lost overlap),
- selected a region where the page didn't actually scroll,
- a fixed header/footer covered most of the selection.

Check `/tmp/longshot_stitch.log` for the per-frame `dy`/`conf` values.

## License

MIT — see [LICENSE](LICENSE). (Upstream: MIT, © 2026 Horizon.)