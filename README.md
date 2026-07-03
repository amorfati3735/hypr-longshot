# "Hypr"LongShot 

This is my custom-built scrolling screenshot tool designed specifically for `Hyprland`. 

This tool combines a `bash` script for workflow management, a `C` program (`raylib`) for drawing the recording overlay, and a `Python` program (OpenCV) for stitching the video frames into a signle long image.

## Disclaimer

This is NOT a universal, plug-and-play `Wayland` tool. I built this primarily to suit my personal `archlinux` + `Hyprland` setup. Please read the following before `git clone`.

1. **Hyprland Exclusive:** Parts of the program rely on `hyprctl` commands to position the recording overlay (`longshot_overlay`). It will not work on `Sway`, `Niri`, `GNOME` or `KDE Wayland`. It also relies on the `hyprctl eval` dispatch API introduced in `Hyprland 0.55`, so older versions won't work either.
2. **Waybar Integration:** `longshot.sh` exposes a `status` subcommand (`idle`/`selecting`/`recording`/`stitching`) meant to be polled by a Waybar custom module — see the example under [Usage](#usage). **If you don't use Waybar, you can safely ignore this and just drive the tool with `start`/`stop`/`cancel`.**
3. **Hardcoded Paths:** Captured images are saved to `$HOME/Pictures/longshots/`. Ensure this path works for you.

## Dependencies

Before installing, make sure you have the following system packages installed (names may vary depending on your distro, below are for `archlinux`):

* `slurp` (selecting the area)
* `wf-recorder` (capturing the scrolling video)
* `libnotify` (for `notify-send` notifications)
* `raylib` (required to compile `C` overlay)
* `python`, `python-pip`, `python-venv` (for the stitching logic)

## Installation and setup

### Clone the repository

```bash
git clone https://github.com/Horizon0427/hypr-longshot.git
cd hypr-longshot
``` 
### Compile the C Overlay

```bash
gcc overlay.c -o longshot_overlay -lraylib -lGL -lm -lpthread -ldl -lrt -lX11
```
### Set up Python Virtual Environment
The image stitching uses OpenCV. To keep your system clean, the script is configured to use a local `.venv`:
```bash
python -m venv .venv
```
```bash
source .venv/bin/activate
```
### Make the script exeutable
```bash
chmod +x longshot.sh
```

## Usage

Hyprland has moved to a `Lua`-based config (`hyprlang` block syntax is deprecated). Add this window rule wherever you keep your Lua config (e.g. `~/.config/hypr/configuration/rules.lua`):
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

`longshot.sh` is driven by subcommands rather than being run bare:
```bash
./longshot.sh start   # select an area and begin recording
./longshot.sh stop    # stop recording and stitch the result
./longshot.sh cancel  # abort and discard whatever is in progress
./longshot.sh status  # print idle | selecting | recording | stitching
```

To drive it from Waybar, poll `status` for the icon/class and dispatch `start`/`stop`/`cancel` on click, e.g. in your Waybar config:
```jsonc
"custom/longshot": {
    "format": "{icon}",
    "format-icons": {
        "idle": "󰹑",
        "selecting": "󰆞",
        "recording": "🔴",
        "stitching": "󰑮"
    },
    "exec": "state=$(~/hypr-longshot/longshot.sh status); echo \"{\\\"alt\\\": \\\"$state\\\", \\\"class\\\": \\\"$state\\\"}\"",
    "return-type": "json",
    "interval": 1,
    "on-click": "if [ $(~/hypr-longshot/longshot.sh status) = 'idle' ]; then ~/hypr-longshot/longshot.sh start; else ~/hypr-longshot/longshot.sh stop; fi &",
    "on-click-right": "~/hypr-longshot/longshot.sh cancel &",
    "tooltip": true,
    "tooltip-format": "左键: 开始/结束长截图\n右键: 直接取消并丢弃"
}
```

**How to capture:**
1. run `longshot.sh start` (or click the Waybar module)
2. select the area you want to capture
3. Slowly scroll down the page you want to capture.
4. run `longshot.sh stop` (or click the Waybar module again) to stop
5. wait a seconds for the stitching work.

**How it works**
1. `longshot.sh start` triggers `slurp` to get geometry.
2. It starts `wf-recorder` to record that specific region into a temporary `/tmp/longshot_temp.mp4`.
3. It dispatches a `raylib` C window (`longshot_overlay`) via `hyprctl eval` to strictly cover the selected area with a blinking red border.
4. Once `longshot.sh stop` is run, the Python script (`stitcher.py`) reads the video, calibrates a "sticky" region (fixed headers/footers that shouldn't be duplicated), tracks scroll offsets between sampled frames with OpenCV's phase correlation (`cv2.phaseCorrelate`), and stitches the unique parts together into a `PNG`.

## Known Bugs & Quirks

**Waybar Cold Start Issue**
When triggering the screenshot tool via a custom Waybar module right after a system boot or a Waybar "cold start", the script might occasionally misbehave on the very first attempt.

* **Symptoms:** The red recording overlay window might spawn out of place , or the left-click action to stop the recording might become unresponsive.
* **Workaround:** Simply reload/refresh Waybar or try triggering the script one more time. The tool typically stabilizes and works after this initial hiccup.

https://github.com/user-attachments/assets/76bef915-ea18-46d6-a734-45ba7eff75c2
