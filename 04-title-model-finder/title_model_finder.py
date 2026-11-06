"""Title Model Finder: which videos beat their own channel's normal, and what title pattern did they use?

Put up to 10 channels in channels.txt. The tool reads their recent videos, finds the outliers against
each channel's own median, guesses the title framework, and puts the frameworks that repeat across channels on top.

    Double click RUN.bat, it sets up everything. Or by hand:
    pip install yt-dlp
    python title_model_finder.py channels.txt --title "My title idea"

Put 2-3 thumbnails in the thumbnails folder and it also checks each one against your title.

Output: report.html, and prompt.txt to paste into any AI chat. Everything runs on your own PC.
It never predicts views or picks a winning thumbnail. Nobody can.
"""
import argparse, base64, html, io, re, statistics, sys, unicodedata, webbrowser
from pathlib import Path

try:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError
except ImportError:
    sys.exit("yt-dlp not found. Run: pip install yt-dlp")

MAX_CHANNELS = 10
THIN = 15                                 # fewer videos than this: the median can't be trusted much
LENGTH = (45, 55)                         # Jake Thomas's rule, built on English titles
OTHER = "Other"

# First match wins, so the specific shapes come before the general ones.
FRAMEWORKS = [
    ("I thought X, but Y", "I thought this would happen. It didn't.",
     r"\b(i|we) (thought|assumed|believed)\b|\bmujhe laga\b|\bhume laga\b|मुझे लगा|हमें लगा"),
    ("I did X, here is what happened", "I put in the time or effort, and here is what I got.",
     r"\b(i|we) (spent|tried|tested|asked|talked|lived|used|built|made|did|quit|bought|interviewed|researched|analy[sz]ed|read|watched)\b|\bmaine\b|\bhumne\b|मैंने|हमने"),
    ("Hard thing in N minutes", "A big job, done in a short time.",
     r"\b(in|under|within) (\d+|one|two|five|ten|a|just \d+)\s*(sec|secs|seconds?|min|mins|minutes?|hrs?|hours?|days?|weeks?)\b|\b\d+\s*(min|mins|minute|minutes|ghante|din)\s*(me|mein)\b|\d+\s*(मिनट|घंटे|दिन)\s*(में|मे)"),
    ("N things / mistakes", "A numbered list.",
     r"\b(top )?\d+\s+(\w+\s+){0,2}(things?|mistakes?|reasons?|ways?|tips?|rules?|signs?|lessons?|habits?|steps?|secrets?|tricks?|facts?|ideas?|books?|stocks?|skills?|items?|terms?)\b|\b\d+\s+(galtiyan|galtiyaan|tarike|tareeke|kaaran|baatein)\b|\d+\s*(गलतियां|गलतियाँ|ग़लतियाँ|तरीके|तरीक़े|कारण|बातें)"),
    ("X vs Y", "Two options, one winner.",
     r"\b(vs\.?|versus)\b"),
    ("Best X", "The best one, already chosen for you.",
     r"\bbest\b|\bsabse (accha|achha|acha|badhiya)\b|सबसे (अच्छा|अच्छे|अच्छी|बढ़िया)"),
    ("How to X", "A direct answer to a search.",
     r"\bhow to\b|\bkaise\b|कैसे"),
    ("Why X", "Open question: the reason behind something.",
     r"\bwhy\b|\bkyun\b|\bkyon\b|\bkyu\b|क्यों|क्यूं"),
]
FRAMEWORKS = [(n, d, re.compile(p, re.I)) for n, d, p in FRAMEWORKS]

# Words that often carry the emotion. They show what is in a title, not whether it is good.
TRIGGERS = {
    "English": {"curiosity": "secret truth why hidden nobody actually weird strange surprising",
                "desire": "best easy free fast rich money earn profit faster simple grow win save perfect",
                "fear": "mistake mistakes stop never avoid wrong danger warning lose losing scam fail worst risk don't"},
    "Hinglish": {"curiosity": "raaz sach kyun kyon asli sachai kaise",
                 "desire": "paisa kamai kamao aasan fayda best muft free amir bachat tez",
                 "fear": "galti galtiyan mat nuksan nuksaan bachiye dhokha khatra sawdhan"},
    "Hindi": {"curiosity": "राज़ राज सच क्यों असली सच्चाई कैसे",
              "desire": "पैसा कमाई कमाएँ आसान फ़ायदा फायदा बेस्ट मुफ़्त मुफ्त अमीर बचत तेज़",
              "fear": "ग़लती गलती ग़लतियाँ गलतियां मत नुकसान नुक़सान बचिए धोखा ख़तरा खतरा सावधान"},
}
ROMAN_HINDI = re.compile(r"\b(aur|kaise|kyun|kyon|hai|hain|mein|nahi|apne|apna|karo|kya|ka|ki|ke|se)\b", re.I)
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_THUMBS = 6
READ_MIN = 0.6                            # how sure the text reader must be before a line counts
BIG, SMALL = 0.08, 0.05                   # text height against thumbnail height: at feed size, clear / small / lost
LOW_CONTRAST = 0.35
FILLER = set("a an the of in on at to for is it my your this that se ka ki ke ko me mein hai hain ye yeh".split())


class Bar:
    """A bar that moves when a real step finishes."""
    def __init__(self, total):
        self.total, self.done = total, 0

    def show(self, name):
        pct = self.done * 100 // self.total; fill = self.done * 20 // self.total
        print(f"\r[{'#' * fill}{'-' * (20 - fill)}] {pct:3d}%  {safe(name)[:34]:<34}", end="", flush=True)

    def step(self, name):
        self.done += 1
        self.show(name)
        if self.done == self.total:
            print()

    def say(self, text):
        print("\r" + " " * 64 + "\r" + safe(text), flush=True)


class Quiet:
    """yt-dlp prints through this. Errors come back as exceptions, and the tool words them itself."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def same_form(text):
    """The same Hindi letter can be typed two ways. One form, so word lists still match."""
    return unicodedata.normalize("NFC", text)


def safe(text):
    """Text this console can print, so one odd title never stops the run."""
    enc = sys.stdout.encoding or "utf-8"
    return str(text).encode(enc, "replace").decode(enc)


def videos_url(line):
    """Any channel link, @handle or plain name, as its /videos page. None when the line is a single video or playlist."""
    line = line.strip().rstrip("/")
    if re.search(r"(watch\?|youtu\.be/|/shorts/|/playlist\?|/live/)", line):
        return None
    m = re.search(r"youtube\.com/((?:@|channel/|c/|user/)[^/?#\s]+)", line, re.I)
    if m:
        return f"https://www.youtube.com/{m.group(1)}/videos"
    m = re.search(r"youtube\.com/([^/?#\s]+)", line, re.I)
    if m:
        return f"https://www.youtube.com/@{m.group(1).lstrip('@')}/videos"
    if " " in line or "/" in line:
        return None
    return f"https://www.youtube.com/@{line.lstrip('@')}/videos"


def read_channels(path):
    """(wanted, bad): lines from channels.txt, comments skipped, duplicates removed."""
    wanted, bad, seen = [], [], set()
    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        url = videos_url(line)
        if not url:
            bad.append(line)
        elif url.lower() not in seen:
            seen.add(url.lower()); wanted.append((line, url))
    return wanted, bad


def why_failed(err):
    text = str(err)
    if re.search(r"404|does not exist|not found", text, re.I):
        return "channel not found, check the handle"
    if re.search(r"does not have a videos tab|no videos", text, re.I):
        return "this channel has no Videos tab"
    if re.search(r"sign in|bot|429|too many", text, re.I):
        return "YouTube blocked the request, wait a while and try again"
    if re.search(r"getaddrinfo|timed out|connection|network|ssl", text, re.I):
        return "no connection to YouTube, check your internet"
    return "could not read it: " + text.splitlines()[0][:90]


def fetch(url, count):
    """Recent long videos of one channel. Flat listing: one page load, no video is opened."""
    opts = {"extract_flat": True, "playlistend": count, "quiet": True, "no_warnings": True, "logger": Quiet(),
            "skip_download": True, "socket_timeout": 25, "extractor_retries": 2}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    videos = [{"id": e["id"], "title": e.get("title") or "", "views": int(e["view_count"])}
              for e in (info.get("entries") or []) if e and e.get("id") and e.get("view_count")]
    return {"name": info.get("channel") or info.get("uploader") or info.get("title") or url, "videos": videos}


def framework_of(title):
    for name, _, pattern in FRAMEWORKS:
        if pattern.search(title):
            return name
    return OTHER


def outliers(channel, top, min_times):
    """Adds the channel's median, then its videos that beat that median the most."""
    views = [v["views"] for v in channel["videos"]]
    channel["median"] = statistics.median(views) if views else 0
    channel["thin"] = len(views) < THIN
    if not channel["median"]:
        channel["outliers"] = []
        return channel
    ranked = sorted(channel["videos"], key=lambda v: -v["views"])
    channel["outliers"] = [dict(v, times=round(v["views"] / channel["median"], 1), framework=framework_of(v["title"]), channel=channel["name"])
                           for v in ranked if v["views"] / channel["median"] >= min_times][:top]
    return channel


def repeats(channels):
    """Frameworks found in the outliers of 2 or more channels, widest first. 'Other' is not a framework."""
    found = {}
    for c in channels:
        for v in c["outliers"]:
            if v["framework"] != OTHER:
                found.setdefault(v["framework"], []).append(v)
    rows = [{"name": n, "channels": len({v["channel"] for v in vs}), "videos": sorted(vs, key=lambda v: -v["times"])} for n, vs in found.items()]
    rows.sort(key=lambda r: (-r["channels"], -r["videos"][0]["times"]))
    return [r for r in rows if r["channels"] >= 2], [r for r in rows if r["channels"] < 2]


def check_title(title):
    """Facts about a title. No score and no forecast, on purpose."""
    title = title.replace("﻿", "").strip()      # a pasted or piped title can carry an invisible mark
    n = len(title)
    hindi = bool(DEVANAGARI.search(title) or ROMAN_HINDI.search(title))
    lo, hi = LENGTH
    length = f"{n} characters: " + (f"inside {lo}-{hi}" if lo <= n <= hi else f"shorter than {lo}" if n < lo else f"longer than {hi}, search titles can run a little longer")
    words = re.findall(r"[\wऀ-ॿ']+", same_form(title.lower()))
    joins = [w for w in ("and", "aur", "और") if w in words] + (["&"] if "&" in title else [])
    found = {}
    for lists in TRIGGERS.values():
        for emotion, text in lists.items():
            hits = [w for w in dict.fromkeys(same_form(text).split()) if w in words]
            if hits:
                found[emotion] = list(dict.fromkeys(found.get(emotion, []) + hits))
    return {"title": title, "length": length, "hindi": hindi, "joins": joins, "triggers": found}


def title_lines(chk):
    out = [f"Title: {chk['title']}", f"  Length: {chk['length']}"]
    if chk["hindi"]:
        out.append("  Note: the 45-55 rule comes from English titles. Test it on your own Hindi channel.")
    out.append(f"  Joined with {', '.join(chk['joins'])}: this may be two promises. Keep one." if chk["joins"] else "  One promise: no 'and' or 'aur' found.")
    for emotion in ("curiosity", "desire", "fear"):
        hits = chk["triggers"].get(emotion)
        out.append(f"  {emotion.capitalize():<10}{', '.join(hits) if hits else '-'}")
    return out


def thumb_files(where):
    """Pictures from a folder or a list of files."""
    found = []
    for item in where:
        path = Path(item)
        if path.is_dir():
            found += sorted(f for f in path.iterdir() if f.suffix.lower() in IMAGES)
        elif path.suffix.lower() in IMAGES and path.exists():
            found.append(path)
        else:
            print(safe(f"Skipped, not a picture: {item}"))
    return found


def text_reader():
    """The reader is only needed for thumbnails, so people who skip them never install it."""
    try:
        import numpy as np
        from PIL import Image
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        sys.exit("The thumbnail check needs one more install. Run: python -m pip install rapidocr-onnxruntime pillow")
    return RapidOCR(), np, Image


def read_thumb(path, reader):
    """The text on a thumbnail with how tall and how clear each line is, plus a feed-size picture for the report."""
    ocr, np, Image = reader
    try:
        img = Image.open(path).convert("RGB")
    except OSError:
        return {"name": path.name, "error": "could not read this picture, save it as PNG or JPG"}
    scale = 1280 / img.width
    img = img.resize((1280, max(1, round(img.height * scale))), Image.LANCZOS)
    pixels = np.asarray(img); grey = pixels @ np.array([0.299, 0.587, 0.114])
    found, _ = ocr(pixels)
    lines = []
    for box, text, sure in found or []:
        if float(sure) < READ_MIN:
            continue
        ys = [p[1] for p in box]; xs = [p[0] for p in box]
        crop = grey[max(0, int(min(ys))):int(max(ys)) + 1, max(0, int(min(xs))):int(max(xs)) + 1]
        spread = float(np.percentile(crop, 90) - np.percentile(crop, 10)) / 255 if crop.size else 0
        lines.append({"text": text.strip(), "height": (max(ys) - min(ys)) / img.height, "contrast": spread})
    small = img.resize((336, round(img.height * 336 / img.width)), Image.LANCZOS)
    buf = io.BytesIO(); small.save(buf, "JPEG", quality=82)
    return {"name": path.name, "lines": lines, "picture": base64.b64encode(buf.getvalue()).decode()}


def words_of(text):
    return [w for w in re.findall(r"[\w']+", same_form(text.lower())) if w not in FILLER]


def check_combo(title, thumb):
    """Facts about one title with one thumbnail: does the text repeat the title, and does it survive at feed size. No winner, on purpose."""
    big = [l for l in thumb["lines"] if l["height"] >= SMALL]
    said = list(dict.fromkeys(w for l in big for w in words_of(l["text"])))
    in_title = set(words_of(title or ""))
    same = [w for w in said if w in in_title]; new = [w for w in said if w not in in_title]
    if not said:
        repeat = "No big text found. The picture has to carry it, so check it by eye."
    elif not title:
        repeat = "No title given, so nothing to compare with."
    elif len(same) / len(said) >= 0.6:
        repeat = f"Mostly repeats the title ({', '.join(same)}). This says the same thing twice."
    elif same:
        repeat = f"Partly repeats the title ({', '.join(same)}). New words: {', '.join(new)}."
    else:
        repeat = f"Adds to the title. New words: {', '.join(new)}."
    clear = [l["text"] for l in thumb["lines"] if l["height"] >= BIG]
    tiny = [l["text"] for l in thumb["lines"] if SMALL <= l["height"] < BIG]
    lost = [l["text"] for l in thumb["lines"] if l["height"] < SMALL]
    return {"title": title, "thumb": thumb["name"], "picture": thumb["picture"], "repeat": repeat, "clear": clear, "tiny": tiny, "lost": lost,
            "words": len(said), "low": [l["text"] for l in big if l["contrast"] < LOW_CONTRAST]}


def combo_lines(c):
    out = [f"{c['thumb']} + {c['title'] or '(no title)'}", f"  {c['repeat']}", f"  Big words: {c['words']}"]
    out += [f"  {name}: {', '.join(c[key])}" for name, key in (("Clear at feed size", "clear"), ("Small at feed size", "tiny"), ("Lost at feed size", "lost"), ("Low contrast", "low")) if c[key]]
    return out


def short(n):
    n = float(n)
    for size, mark in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= size:
            return f"{n / size:.1f}".rstrip("0").rstrip(".") + mark
    return f"{n:.0f}"


def clean_titles(channel):
    """Takes off what only labels the channel: a tail like '| Channel Name' or '| Ep. 12' says nothing about why people clicked."""
    def parts(title):
        return [x.strip() for x in title.split("|")]
    tails = {}
    for v in channel["videos"]:
        bits = parts(v["title"])
        if len(bits) > 1:
            tails[bits[-1].lower()] = tails.get(bits[-1].lower(), 0) + 1
    name = {w for w in re.findall(r"\w{4,}", channel["name"].lower())}
    for v in channel["outliers"]:
        bits = parts(v["title"])
        while len(bits) > 1 and (tails.get(bits[-1].lower(), 0) > 1 or name & set(re.findall(r"\w{4,}", bits[-1].lower()))
                                 or re.search(r"(?i:\b(ep\.?|episode|podcast)\b)\s*\d*|\b[A-Z]{2,5} \d+$", bits[-1])):
            bits.pop()
        v["clean"] = " | ".join(bits)


def write_prompt(path, channels, shared, single):
    for c in channels:
        clean_titles(c)
    row = lambda v: f'- "{v["clean"]}" | {v["times"]}x'
    lines = ["Help me title one YouTube video. Below are real titles that beat their own channel's normal views. They come from channels on other topics.", "",
             "MY VIDEO: [what this video really shows, and what the viewer walks away with]",
             "PROOF I REALLY HAVE: [real numbers or tests in the video, or write none]",
             "MY VIEWER: [who watches, and what they want]",
             "LANGUAGE OF MY TITLES: [for example English, or Hinglish]", "",
             "If MY VIDEO is still in brackets or doesn't say what the video shows, stop and ask me about it. Don't guess.", "",
             "HOW TO WORK",
             "1. Some titles below won on their topic or on news, such as an IPO, a market crash, a sale or a song. Skip those, they teach nothing about titles.",
             "2. From the rest, pick the 3 patterns that fit MY VIDEO best. A pattern seen in more channels counts for more, but a pattern that doesn't fit is out. Say each one as the promise it makes to the viewer, in one plain line, with no words from the original title.",
             "3. Write 3 titles on each pattern, as a creator would say them to a friend.",
             "4. Throw out a title if any of these is true:",
             "   - It is an original title with the nouns swapped. Copying the shape word for word is a fail.",
             "   - It makes no sense in my topic, or nobody would really search or click it.",
             "   - It claims a number, result or story that isn't in PROOF I REALLY HAVE, or MY VIDEO can't deliver it.",
             "   - It makes two promises.",
             "5. Hinglish means a Hindi sentence in roman script, keeping only the English words creators really use (views, thumbnail, tag). Example: 'Tag Badalne Se Views Kyun Nahi Badhte'. A sentence in English is a fail.",
             "6. Keep each title short, about 45-55 characters. Don't print character counts.",
             "7. Do not predict views. End with the 3 titles you would test against each other, and one line on why.",
             "8. For each of those 3 titles, give 2 thumbnail ideas: the text, 4 words or fewer, and one line on what the picture shows. The text must add what the title holds back, such as the fear or the surprise, and must not use the title's main words. Rule 4 applies to these too.",
             "9. You can't know which pair wins, so don't claim it. My test decides.", "",
             "THE TITLES (x = times that channel's normal video)", ""]
    for r in shared + single:
        count = r["channels"]
        lines.append(f"{r['name']}, seen in {count} channel{'s' if count > 1 else ''}. The name is a guess from word rules, so correct it if you see better.")
        lines += [row(v) for v in r["videos"][:6]]
        lines.append("")
    rest = sorted((v for c in channels for v in c["outliers"] if v["framework"] == OTHER), key=lambda v: -v["times"])
    if rest:
        lines.append("No pattern guessed. Find one yourself if it is there, and ignore guest names.")
        lines += [row(v) for v in rest[:12]]
    Path(path).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


CSS = """
:root{--bg:#f6f6f4;--card:#fff;--ink:#17181a;--soft:#62666d;--line:#e2e2de;--hot:#c8321e;--tag:#eeeee9}
@media(prefers-color-scheme:dark){:root{--bg:#141517;--card:#1e2023;--ink:#f0f0ee;--soft:#a2a6ad;--line:#33363a;--hot:#ff7a5e;--tag:#2b2d31}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,'Segoe UI','Nirmala UI',sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 16px 64px}h1{font-size:32px;margin:0 0 6px}h2{font-size:21px;margin:40px 0 12px}
.soft{color:var(--soft)}.small{font-size:14px}a{color:inherit;text-decoration:none}a:hover{text-decoration:underline}
.vid{display:grid;grid-template-columns:168px 1fr 92px;gap:14px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px;margin:8px 0}
.vid img{width:168px;height:94px;object-fit:cover;border-radius:6px;display:block;background:var(--tag)}
.t{font-weight:600}.x{font-size:27px;font-weight:800;color:var(--hot);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.tag{display:inline-block;background:var(--tag);border-radius:5px;padding:1px 7px;font-size:13px;margin-right:6px}
.fw{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0}.fw h3{margin:0;font-size:18px}
.warn{color:var(--hot)}
.combo{display:grid;grid-template-columns:168px 1fr;gap:16px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:10px 0}.combo img{width:168px;display:block;border-radius:6px}.combo .feedt{font-size:13px;font-weight:600;line-height:1.3;margin-top:6px}
.combo ul{margin:4px 0 0;padding-left:18px}.combo h3{margin:0;font-size:16px}pre{white-space:pre-wrap;margin:0;font:15px/1.55 Consolas,'Nirmala UI',monospace}
@media(max-width:620px){.combo{grid-template-columns:1fr}.vid{grid-template-columns:96px 1fr}.vid img{width:96px;height:54px}.x{grid-column:2;text-align:left;font-size:21px}}
"""


def vid_html(v, who=False):
    e = html.escape
    url = f"https://www.youtube.com/watch?v={e(v['id'])}"
    meta = (f"{e(v['channel'])} · " if who else "") + f"{short(v['views'])} views"
    return (f'<div class="vid"><a href="{url}" target="_blank" rel="noopener"><img loading="lazy" alt="" src="https://i.ytimg.com/vi/{e(v["id"])}/mqdefault.jpg"></a>'
            f'<div><a class="t" href="{url}" target="_blank" rel="noopener">{e(v["title"])}</a><div class="soft small"><span class="tag">{e(v["framework"])}</span>{meta}</div></div>'
            f'<div class="x">{v["times"]}x</div></div>')


def combo_html(c):
    e = html.escape
    rows = "".join(f"<li>{e(line.strip())}</li>" for line in combo_lines(c)[1:])
    return (f'<div class="combo"><div><img alt="" src="data:image/jpeg;base64,{c["picture"]}"><div class="feedt">{e(c["title"] or "")}</div></div>'
            f'<div><h3>{e(c["thumb"])}</h3><ul class="small">{rows}</ul></div></div>')


def write_report(path, channels, failed, shared, single, checks, count, min_times, combos=()):
    e = html.escape
    desc = {n: d for n, d, _ in FRAMEWORKS}
    b = [f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Title Model Report</title><style>{CSS}</style><main>",
         "<h1>Title Model Report</h1>",
         (f"<p class=soft>{len(channels)} channels, up to {count} recent long videos each. <b>x</b> = views against that channel's own median. Only videos at {min_times:g}x or more count. Frameworks are a guess from word rules, so check them yourself.</p>" if channels else "")]
    if checks:
        b.append("<h2>Your title</h2>")
        b += [f"<div class=fw><pre>{e(chr(10).join(title_lines(c)))}</pre></div>" for c in checks]
    if combos:
        b.append("<h2>Title + thumbnail</h2><p class='soft small'>Each pair at about the size a viewer sees it. These are facts, not a winner: the tool can't know which pair gets the click. Send your best 2-3 to YouTube's Test &amp; Compare. The text reader can misread stylised letters and does not read Devanagari, so compare with the picture.</p>")
        b += [combo_html(c) for c in combos]
    if not channels:
        if failed:
            b.append("<h2>Could not read</h2>" + "".join(f"<p class=small>{e(n)}: {e(w)}</p>" for n, w in failed))
        b.append("</main></html>")
        Path(path).write_text("".join(b), encoding="utf-8")
        return
    b.append("<h2>Frameworks that repeat across channels</h2>")
    if shared:
        for r in shared:
            b.append(f"<div class=fw><h3>{e(r['name'])}</h3><div class='soft small'>{e(desc.get(r['name'], ''))} In the outliers of {r['channels']} channels.</div>")
            b += [vid_html(v, True) for v in r["videos"][:4]]
            b.append("</div>")
    else:
        b.append("<p class=soft>No framework appeared in 2 or more channels this time. One hit can be down to the topic, so add more channels before you trust a pattern.</p>")
    if single:
        b.append("<p class='soft small'>Seen in one channel only: " + e(", ".join(f"{r['name']} ({len(r['videos'])})" for r in single)) + ". One channel can be chance.</p>")
    b.append("<h2>Channel by channel</h2>")
    for c in channels:
        flag = f" <span class=warn>Thin data: only {len(c['videos'])} videos.</span>" if c["thin"] else ""
        b.append(f"<h3>{e(c['name'])}</h3><div class='soft small'>{len(c['videos'])} videos, normal video {short(c['median'])} views.{flag}</div>")
        b += [vid_html(v) for v in c["outliers"]] or [f"<p class='soft small'>No video reached {min_times:g}x. This channel's views are steady.</p>"]
    if failed:
        b.append("<h2>Could not read</h2>" + "".join(f"<p class=small>{e(n)}: {e(w)}</p>" for n, w in failed))
    b.append("<p class='soft small' style='margin-top:40px'>View counts come rounded from YouTube's listing, such as 1.9M. The tool does not predict views. New videos are still collecting views, so they can look weaker than they will be. Nothing left your PC except the page requests to YouTube.</p></main></html>")
    Path(path).write_text("".join(b), encoding="utf-8")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Find title frameworks that beat a channel's own normal.")
    ap.add_argument("channels", nargs="?", default="channels.txt", help="text file, one channel link or @handle per line")
    ap.add_argument("--title", action="append", default=[], help="your own title to check, can be given more than once")
    ap.add_argument("--ask", action="store_true", help="ask for a title in the window (RUN.bat uses this)")
    ap.add_argument("--videos", type=int, default=60, help="recent videos per channel (default 60)")
    ap.add_argument("--top", type=int, default=5, help="outliers kept per channel (default 5)")
    ap.add_argument("--min-times", type=float, default=2.0, help="how far above the median counts as an outlier (default 2)")
    ap.add_argument("--out", default="report.html")
    ap.add_argument("--prompt", default="prompt.txt")
    ap.add_argument("--open", action="store_true", help="open the report when done")
    ap.add_argument("--thumbs", nargs="*", default=["thumbnails"], help="folder or pictures to check against your titles (default: the thumbnails folder)")
    ap.add_argument("--no-scan", action="store_true", help="skip the channels, only check titles and thumbnails")
    args = ap.parse_args()

    wanted, bad = [], []
    if not args.no_scan:
        if not Path(args.channels).exists():
            sys.exit(f"File not found: {args.channels}")
        wanted, bad = read_channels(args.channels)
        if not wanted:
            sys.exit(f"No channels in {args.channels}. Add one channel link or @handle per line.")
    for line in bad:
        print(safe(f"Skipped, not a channel link: {line}"))
    if len(wanted) > MAX_CHANNELS:
        print(f"Using the first {MAX_CHANNELS} channels of {len(wanted)}.")
        wanted = wanted[:MAX_CHANNELS]

    titles = list(args.title)
    if args.ask:
        try:
            typed = input("Your title to check (or just press Enter): ").strip()
        except EOFError:
            typed = ""
        if typed:
            titles.append(typed)

    pictures = thumb_files([t for t in args.thumbs if t != "thumbnails" or Path(t).is_dir()])
    if len(pictures) > MAX_THUMBS:
        print(f"Using the first {MAX_THUMBS} thumbnails of {len(pictures)}.")
        pictures = pictures[:MAX_THUMBS]
    if args.no_scan and not pictures and not titles:
        sys.exit("Nothing to check. Give a --title, or put thumbnails in the thumbnails folder.")

    bar = Bar(len(wanted) + len(pictures) + 3); channels, failed = [], []
    for line, url in wanted:
        bar.show(f"Reading {line}")
        try:
            channel = fetch(url, args.videos)
            if channel["videos"]:
                channels.append(channel)
                bar.step(f"Read {line}")
            else:
                failed.append((line, "no public view counts on its Videos tab"))
                bar.say(f"Skipped {line}: {failed[-1][1]}")
                bar.step(f"Skipped {line}")
        except DownloadError as err:
            failed.append((line, why_failed(err)))
            bar.say(f"Skipped {line}: {failed[-1][1]}")
            bar.step(f"Skipped {line}")
    if wanted and not channels:
        print()
        for name, why in failed:
            print(safe(f"{name}: {why}"))
        sys.exit("No channel could be read. If the handles are right, update yt-dlp: python -m pip install -U yt-dlp")

    for c in channels:
        outliers(c, args.top, args.min_times)
    bar.step("Found the outliers")
    shared, single = repeats(channels)
    bar.step("Guessed the frameworks")
    checks = [check_title(t) for t in titles]
    combos = []
    if pictures:
        bar.show("Loading the text reader")
        reader = text_reader()
        for path in pictures:
            bar.show(f"Reading {path.name}")
            thumb = read_thumb(path, reader)
            if thumb.get("error"):
                failed.append((path.name, thumb["error"]))
                bar.say(f"Skipped {path.name}: {thumb['error']}")
            else:
                combos += [check_combo(c["title"], thumb) for c in checks] or [check_combo("", thumb)]
            bar.step(f"Read {path.name}")
    write_report(args.out, channels, failed, shared, single, checks, args.videos, args.min_times, combos)
    if channels:
        write_prompt(args.prompt, channels, shared, single)
    bar.step("Wrote the report")

    print()
    for c in channels:
        best = c["outliers"][0] if c["outliers"] else None
        note = f"{best['times']}x  {best['title']}" if best else "no outlier"
        flag = "  [thin data]" if c["thin"] else ""
        print(safe(f"{c['name']}: normal {short(c['median'])}, {len(c['videos'])} videos{flag}"))
        print(safe(f"  {note}"))
    print()
    if channels:
        print("Frameworks in 2+ channels: " + (safe(", ".join(f"{r['name']} ({r['channels']})" for r in shared)) if shared else "none this time"))
    for chk in checks:
        print()
        for line in title_lines(chk):
            print(safe(line))
    for combo in combos:
        print()
        for line in combo_lines(combo):
            print(safe(line))
    if combos:
        print("\nThese are facts, not a winner. YouTube's Test & Compare decides.")
    print(f"\nReport: {args.out}" + (f"\nAI prompt: {args.prompt}" if channels else ""))
    if failed:
        print(f"Could not read {len(failed)}: " + safe(", ".join(n for n, _ in failed)))
    if args.open:
        webbrowser.open(Path(args.out).resolve().as_uri())


if __name__ == "__main__":
    main()
