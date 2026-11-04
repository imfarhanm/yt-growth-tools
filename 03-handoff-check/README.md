# Handoff Check

![Handoff Check running](demo.gif)

Your thumbnail makes a promise. Does the first frame of your video keep it? This tool compares the two and gives you a score out of 100.

Everything runs on your own PC. No account, no API key, and nothing is uploaded.

## Quick start

### 1. Install Python (first time only)
1. Go to [python.org/downloads](https://www.python.org/downloads/) and download Python.
2. On the first installer screen, tick **Add python.exe to PATH**.
3. Click **Install Now**.

### 2. Download the tool
1. On the main page of this repo, click the green **Code** button, then **Download ZIP**.
2. Right-click the ZIP and choose **Extract All**.
3. Open the `03-handoff-check` folder.

### 3. Run it
1. Double-click **RUN.bat**. The first run installs what it needs, which takes 1-2 minutes.
2. Drag your thumbnail into the window and press **Enter**.
3. Drag your video into the window and press **Enter**.
4. You get the score and what to fix first. `handoff_report.png` opens with both pictures, large and at feed size.

Want to try it first? Leave the thumbnail empty and press **Enter**. The tool runs on the `sample` folder.

## What the score means

| Score | Result | What to do |
|---|---|---|
| 80 or more | PASS: the promise carries over | Change nothing |
| 50 to 79 | WEAK: it only partly matches | Bring the thumbnail's main subject into the first frame |
| Under 50 | FAIL: viewers see a different video | Rebuild the first frame to match the thumbnail, then run it again |

The tool checks three things at feed size, the size a viewer really sees:

- **Color match:** do both pictures use the same colors?
- **Subject position:** is the main subject in the same part of the frame?
- **Text and shapes:** are the text and big shapes in the same places?

The score is the average of the three. Below 80, **Fix first** tells you which one to change. If a frame in the first second scores much higher, a **Note** tells you to start the video there, which usually means a fade-in or a slow start. The tool measures how closely the pictures match, not how good the video is.

## Real results from the sample

```
python handoff_check.py sample\thumb.png sample\video_before.mp4
Color match       15/100
Subject position   0/100
Text and shapes    0/100
HANDOFF SCORE 5/100
Verdict: FAIL, viewers see a different video
Fix first: put the subject where the thumbnail has it.

python handoff_check.py sample\thumb.png sample\video_after.mp4
Color match       83/100
Subject position  62/100
Text and shapes   97/100
HANDOFF SCORE 81/100
Verdict: PASS, the promise carries over
```

## Troubleshooting

| What you see | What to do |
|---|---|
| `Python was not found` | Install Python with **Add python.exe to PATH** ticked, then run RUN.bat again. |
| The Microsoft Store opens | Python is not installed yet. Follow step 1 above. |
| `Setup failed` | Check your internet connection and run RUN.bat again. |
| `File not found` | The path is wrong. Drag the file into the window instead of typing it. |
| `Could not read the video` | The video is damaged or in an unusual format. Export it as MP4 and try again. |
| `Could not read the thumbnail` | Save the thumbnail as PNG or JPG and try again. |
| Windows says "protected your PC" | Click **More info**, then **Run anyway**. You can read the code first: `RUN.bat` and `handoff_check.py` are plain text. |

## Command line

```
pip install pillow numpy imageio-ffmpeg
python handoff_check.py thumb.png video.mp4 --out report.png
```
