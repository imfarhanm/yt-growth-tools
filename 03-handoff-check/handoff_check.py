"""Handoff Check: does your video's first frame keep the promise your thumbnail made?

It compares your thumbnail with the first frame of your video and gives a score out of 100.

    Double click RUN.bat, it sets up everything. Or by hand:
    pip install pillow numpy imageio-ffmpeg
    python handoff_check.py thumb.png video.mp4

Output: the score here, and handoff_report.png with both pictures side by side.
"""
import argparse, shutil, subprocess, sys, tempfile
from pathlib import Path

import numpy as np
from PIL import Image

SIZE = (64, 36)                           # small on purpose: this is what the eye catches in a feed
STEPS = ["Reading thumbnail", "Reading first frame", "Comparing colors", "Comparing layout"]


def progress(done):
    """A bar that moves when a real step finishes."""
    pct = done * 100 // len(STEPS); fill = done * 20 // len(STEPS)
    name = "Done" if done == len(STEPS) else STEPS[done]
    print(f"\r[{'#' * fill}{'-' * (20 - fill)}] {pct:3d}%  {name:<22}", end="\n" if done == len(STEPS) else "", flush=True)


def ffmpeg():
    """ffmpeg if the PC has it, otherwise the copy that comes with imageio-ffmpeg."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg not found. Run: pip install imageio-ffmpeg")


def first_frame(video, out):
    run = subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", str(video), "-frames:v", "1", str(out)], capture_output=True, text=True)
    if run.returncode or not Path(out).exists():
        sys.exit(f"Could not read the video: {run.stderr.strip()[:200]}")


def small(img):
    return np.asarray(img.convert("RGB").resize(SIZE, Image.LANCZOS), dtype=float) / 255


def color_score(a, b):
    """How close the same spots are."""
    return max(0.0, float(1 - np.abs(a - b).mean() * 2)) * 100


def layout_score(a, b):
    """Whether light and dark sit in the same places."""
    ga, gb = a.mean(axis=2).ravel(), b.mean(axis=2).ravel()
    sa, sb = ga.std(), gb.std()
    layout = 0.0 if sa < 1e-6 or sb < 1e-6 else ((ga - ga.mean()) * (gb - gb.mean())).mean() / (sa * sb)
    return max(0.0, float(layout)) * 100


def report(thumb, frame, score, out):
    w, h = 960, 540
    sheet = Image.new("RGB", (w * 2 + 60, h + 40), (16, 18, 24))
    sheet.paste(thumb.convert("RGB").resize((w, h), Image.LANCZOS), (20, 20))
    sheet.paste(frame.convert("RGB").resize((w, h), Image.LANCZOS), (w + 40, 20))
    sheet.save(out)


def main():
    ap = argparse.ArgumentParser(description="Compare a thumbnail with a video's first frame.")
    ap.add_argument("thumb"); ap.add_argument("video"); ap.add_argument("--out", default="handoff_report.png")
    a = ap.parse_args()
    for f in (a.thumb, a.video):
        if not Path(f).exists():
            sys.exit(f"File not found: {f}")
    progress(0)
    thumb = Image.open(a.thumb); thumb.load()
    progress(1)
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "first.png"; first_frame(a.video, f); frame = Image.open(f); frame.load()
    progress(2)
    st, sf = small(thumb), small(frame)
    color = color_score(st, sf)
    progress(3)
    layout = layout_score(st, sf)
    progress(4)
    score = round((color + layout) / 2)
    print(f"Color match   {color:.0f}/100")
    print(f"Layout match  {layout:.0f}/100")
    print(f"HANDOFF SCORE {score}/100")
    print("Verdict:", "PASS, the promise carries over" if score >= 80 else "WEAK, it only partly matches" if score >= 50 else "FAIL, viewers see a different video")
    report(thumb, frame, score, a.out)
    print("Saved", a.out)


if __name__ == "__main__":
    main()
