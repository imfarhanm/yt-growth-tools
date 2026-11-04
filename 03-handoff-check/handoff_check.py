"""Handoff Check: does your video's first frame keep the promise your thumbnail made?

It compares your thumbnail with the first frame of your video at feed size, gives a score out of 100
and tells you which part to fix first.

    Double click RUN.bat, it sets up everything. Or by hand:
    pip install pillow numpy imageio-ffmpeg
    python handoff_check.py thumb.png video.mp4

Output: the score here, and handoff_report.png with both pictures, large and at feed size.
"""
import argparse, shutil, subprocess, sys, tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FEED = (168, 94)                          # about the size of a thumbnail in a phone feed
LATER = (0.5, 1.0)                        # also checked: a slow fade or a late match shows up here
STEPS = ["Reading thumbnail", "Reading first frames", "Comparing colors", "Finding the subject", "Comparing text and shapes"]
FIX = {"Color match": "use the thumbnail's main colors in the first frame.",
       "Subject position": "put the subject where the thumbnail has it.",
       "Text and shapes": "bring the thumbnail's text or big shapes in."}


def progress(done):
    """A bar that moves when a real step finishes."""
    pct = done * 100 // len(STEPS); fill = done * 20 // len(STEPS)
    name = "Done" if done == len(STEPS) else STEPS[done]
    print(f"\r[{'#' * fill}{'-' * (20 - fill)}] {pct:3d}%  {name:<26}", end="\n" if done == len(STEPS) else "", flush=True)


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


def frame_at(video, seconds, out):
    """One frame, or None when the video is shorter than that."""
    run = subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-ss", str(seconds), "-i", str(video), "-frames:v", "1", str(out)], capture_output=True, text=True)
    if run.returncode or not Path(out).exists():
        return None, run.stderr.strip()[:200]
    img = Image.open(out); img.load()
    return img, ""


def small(img):
    return np.asarray(img.convert("RGB").resize(FEED, Image.LANCZOS), dtype=float) / 255


def color_score(a, b):
    """How much of the two pictures uses the same colors, wherever they sit."""
    def hist(x):
        q = np.minimum((x * 8).astype(int), 7); h = np.bincount((q[..., 0] * 64 + q[..., 1] * 8 + q[..., 2]).ravel(), minlength=512).astype(float)
        return h / h.sum()
    return float(np.minimum(hist(a), hist(b)).sum()) * 100


def cells(m, across, down):
    """The average of a map over a grid."""
    h, w = m.shape; ys = np.linspace(0, h, down + 1).astype(int); xs = np.linspace(0, w, across + 1).astype(int)
    return np.array([[m[ys[r]:ys[r + 1], xs[c]:xs[c + 1]].mean() for c in range(across)] for r in range(down)])


def same_places(x, y):
    """Whether the strong areas of two grids are in the same places, 0 to 100. Two flat pictures count as a match only if both are flat."""
    x, y = x.ravel(), y.ravel(); sx, sy = x.std(), y.std()
    if sx < 1e-3 or sy < 1e-3:
        return 100.0 if sx < 1e-3 and sy < 1e-3 else 0.0
    return max(0.0, float(((x - x.mean()) * (y - y.mean())).mean() / (sx * sy))) * 100


def subject_score(a, b):
    """The subject is what stands out from the picture's average color. Is it in the same part of the frame?"""
    stand_out = lambda x: np.abs(x - x.mean(axis=(0, 1))).sum(axis=2)
    return same_places(cells(stand_out(a), 8, 5), cells(stand_out(b), 8, 5))


def edge_score(a, b):
    """Text and big shapes make edges. Are the edges in the same places?"""
    def edges(x):
        g = x.mean(axis=2); dy, dx = np.gradient(g)
        return np.hypot(dx, dy)
    return same_places(cells(edges(a), 16, 9), cells(edges(b), 16, 9))


def measure(thumb_small, frame):
    f = small(frame)
    parts = {"Color match": color_score(thumb_small, f), "Subject position": subject_score(thumb_small, f), "Text and shapes": edge_score(thumb_small, f)}
    return round(sum(parts.values()) / len(parts)), parts


def report(thumb, frame, score, out):
    """Large pair on top, the same pair at feed size below: that small row is what the viewer really sees."""
    w, h = 800, 450; fw, fh = FEED[0] * 2, FEED[1] * 2; gap = 40
    sheet = Image.new("RGB", (w * 2 + gap * 3, h + fh + 170), (16, 18, 24)); d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(28)
    except TypeError:                     # very old Pillow
        font = ImageFont.load_default()
    for i, (img, name) in enumerate(((thumb, "THUMBNAIL"), (frame, "FIRST FRAME"))):
        x = gap + i * (w + gap)
        d.text((x, 16), name, fill=(240, 240, 240), font=font)
        sheet.paste(img.convert("RGB").resize((w, h), Image.LANCZOS), (x, 56))
        sheet.paste(img.convert("RGB").resize((fw, fh), Image.LANCZOS), (x, h + 110))
    d.text((gap, h + 70), f"AT FEED SIZE            HANDOFF SCORE {score}/100", fill=(240, 240, 240), font=font)
    sheet.save(out)


def main():
    ap = argparse.ArgumentParser(description="Compare a thumbnail with a video's first frame.")
    ap.add_argument("thumb"); ap.add_argument("video"); ap.add_argument("--out", default="handoff_report.png")
    a = ap.parse_args()
    for f in (a.thumb, a.video):
        if not Path(f).exists():
            sys.exit(f"File not found: {f}")
    progress(0)
    try:
        thumb = Image.open(a.thumb); thumb.load()
    except OSError:
        sys.exit(f"Could not read the thumbnail: {a.thumb}")
    progress(1)
    with tempfile.TemporaryDirectory() as d:
        frame, why = frame_at(a.video, 0, Path(d) / "f0.png")
        if frame is None:
            sys.exit(f"Could not read the video: {why}")
        later = [(s, frame_at(a.video, s, Path(d) / f"f{i + 1}.png")[0]) for i, s in enumerate(LATER)]
    progress(2)
    st = small(thumb); f0 = small(frame)
    color = color_score(st, f0)
    progress(3)
    subject = subject_score(st, f0)
    progress(4)
    edges = edge_score(st, f0)
    progress(5)
    parts = {"Color match": color, "Subject position": subject, "Text and shapes": edges}
    score = round(sum(parts.values()) / len(parts))
    print(f"Color match      {color:3.0f}/100")
    print(f"Subject position {subject:3.0f}/100")
    print(f"Text and shapes  {edges:3.0f}/100")
    print(f"HANDOFF SCORE {score}/100")
    print("Verdict:", "PASS, the promise carries over" if score >= 80 else "WEAK, it only partly matches" if score >= 50 else "FAIL, viewers see a different video")
    if score < 80:
        weak = min(parts, key=parts.get)
        print("Fix first:", FIX[weak])
    best = max(((measure(st, f)[0], s) for s, f in later if f is not None), default=(0, 0))
    if best[0] >= score + 15:
        print(f"Note: the frame at {best[1]} s scores {best[0]}/100. Start the video there.")
    report(thumb, frame, score, a.out)
    print("Saved", a.out)


if __name__ == "__main__":
    main()
