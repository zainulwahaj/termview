# termview

View images, animated GIFs, and videos in your terminal. Audio + keyboard controls included.

```bash
tv photo.jpg
tv animation.gif
tv movie.mp4
```

## Install

```bash
pip install termview[video]
```

Video playback also wants `ffmpeg` for audio. Without it, video plays silently:

```bash
brew install ffmpeg              # macOS
apt install ffmpeg               # Debian/Ubuntu
```

## How it picks a renderer

`tv` auto-detects the best graphics protocol your terminal supports and falls
back gracefully. The four paths, in quality order:

| Renderer | Used when | Quality |
|---|---|---|
| **kitty** | Kitty, WezTerm, Ghostty (sets `$KITTY_WINDOW_ID` or `$TERM_PROGRAM`) | pixel-perfect |
| **iterm2** | iTerm2, Warp (sets `$TERM_PROGRAM=iTerm.app`) | pixel-perfect |
| **sixel** | xterm, foot, Windows Terminal, mlterm (queried via DA1) | pixel-perfect |
| **block** | everywhere else (universal fallback) | ANSI background fills, one image pixel per cell |

The **block** renderer auto-switches between truecolor (`\033[48;2;R;G;Bm`) and
xterm 256-color with Floyd-Steinberg dithering depending on what your terminal
actually supports — macOS Terminal.app gets dithered output, everything else
gets full 24-bit.

Force a renderer:

```bash
tv photo.jpg --renderer kitty
tv photo.jpg --renderer block --depth 256
```

## Video playback

```bash
tv movie.mp4               # plays with audio (if ffmpeg installed)
tv movie.mp4 --no-audio    # silent
tv movie.mp4 --fps 8       # throttle frame rate
```

### Keyboard controls

| Key | Action |
|---|---|
| `space` | play / pause |
| `←` `→` | seek -5s / +5s |
| `↓` `↑` | seek -30s / +30s |
| `,` `.` | previous / next frame (while paused) |
| `m` | mute / unmute |
| `+` `-` | volume up / down |
| `0` | restart from beginning |
| `q` / `esc` | quit |

Add `--no-controls` to disable for scripting / asciinema recording.

## Cross-terminal notes

| Environment | Behavior |
|---|---|
| **tmux** | Forces the block renderer. Pixel-protocol passthrough is fragile across tmux versions; `--renderer kitty` etc. can still be forced if you've enabled `allow-passthrough on` (tmux 3.4+). |
| **SSH** | Forces the block renderer. Inline-image protocols don't survive most SSH chains. |
| **macOS Terminal.app** | Auto-detected as 256-color. Floyd-Steinberg dithering kicks in for stills; video uses no-dither for stability and an automatic 12fps cap. |
| **Windows Terminal** | Auto-detected via `$WT_SESSION`, uses sixel. |
| **non-TTY stdout** (`tv x.png > out`) | Video playback refuses. Images write a renderable stream that's only meaningful when re-played to a terminal. |

## CLI reference

```text
usage: tv [-h] [--renderer NAME] [--depth DEPTH] [--width COLS] [--no-crop]
          [--fps N] [--no-audio] [--no-controls] [--loop] [-v]
          file

rendering:
  --renderer NAME    kitty | iterm2 | sixel | block  (default: auto)
  --depth DEPTH      truecolor | 256                 (default: auto)
  --width COLS       override terminal width
  --no-crop          disable automatic border cropping

video / animation:
  --fps N            limit playback frame rate
  --no-audio         disable audio
  --no-controls      disable keyboard controls
  --loop             loop animated images (default: on)

  -v, --verbose      print detection diagnostics
```

## Library use

```python
from termview import load_image, fit_image, get_renderer, detect_renderer, terminal_size

img = load_image("photo.jpg")
cols, rows = terminal_size()
renderer_type = detect_renderer()
fitted = fit_image(img, cols, rows, renderer_type)
get_renderer(renderer_type).display(fitted)
```

Video and animation playback have higher-level entry points
(`stream_video`, `stream_animation`) that bundle the playback loop, audio
process management, keyboard input, and terminal state restoration.
