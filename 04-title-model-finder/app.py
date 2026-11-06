"""Title Model Finder, the page: type your niche, tick your Dream 10 and Model 10, then get a title and thumbnail prompt for each video.

    Double click RUN.bat. Or by hand:
    pip install yt-dlp pillow
    python app.py

It runs on your own PC at 127.0.0.1. A free Gemini key is optional. Without one, everything except the Gemini buttons still works.
"""
import base64, collections, concurrent.futures, io, json, os, re, secrets, sys, threading, time, urllib.error, urllib.parse, urllib.request, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import title_model_finder as tmf

HERE = Path(__file__).resolve().parent
SETUP = HERE / "my_channels.json"
KEY_FILE = HERE / "gemini_key.txt"
PROMPTS = HERE / "prompts"
MADE = HERE / "thumbnails_made"
TOKEN = secrets.token_urlsafe(24)
GEMINI = os.environ.get("TMF_GEMINI_BASE", "https://generativelanguage.googleapis.com/v1beta")

PER_LIST, TRY_PER_LIST = 10, 18      # ticked at most, and channels checked for each list
MIN_MEDIAN = 1000                    # below this a channel's outliers are mostly noise
MODEL_MEDIAN = 20000                 # a pattern to borrow should come from a channel that really competes
NEWS = re.compile(r"\b(news|aaj tak|ndtv|khabar|samachar|abp|zee|republic|live)\b", re.I)
TEXT_LIMIT = 12000                   # the video's text sent in a prompt
SKIP = re.compile(r"\b(bjp|modi|congress|rahul gandhi|election|epstein|osama|bin laden|iran|israel|pakistan|article 370|ram mandir|war|trump|putin|murder|hatya)\b", re.I)
DREAM_SEARCH = {"en": ["{n}", "{n} tips", "how to {n}", "{n} for beginners", "{n} mistakes", "best {n}"],
                "hi": ["{n} in hindi", "{n} kaise kare", "{n} tips hindi", "{n}", "{n} ke tarike", "{n} galtiyan"]}
MODEL_SEARCH = {"en": ["personal finance advice", "fitness science explained", "productivity habits", "how to start a business", "study tips", "career advice", "cooking tips", "tech explained", "self improvement"],
                "hi": ["paise kaise bachaye", "fitness tips hindi", "business kaise shuru kare", "study tips hindi", "career advice hindi", "health tips hindi", "tech explained hindi", "self improvement hindi", "share market basics hindi"]}
LANG_RULE = {"hi": "Hinglish: a Hindi sentence in roman script, never Devanagari, keeping only the English words people in this topic really use. A sentence in English is a fail.",
             "en": "Plain American English."}
JOB = {"running": False, "step": "", "pct": 0, "error": "", "result": None}
CANDIDATES = {}
LOCK = threading.Lock()


def word(lang, en, hi):
    return hi if lang == "hi" else en


def load_setup():
    return json.loads(SETUP.read_text(encoding="utf-8")) if SETUP.exists() else None


def gemini_key():
    return KEY_FILE.read_text(encoding="utf-8").strip() if KEY_FILE.exists() else ""


# ---------- finding channels

def search(query, count):
    with tmf.YoutubeDL({"extract_flat": True, "quiet": True, "no_warnings": True, "logger": tmf.Quiet(), "socket_timeout": 25}) as y:
        return y.extract_info(f"ytsearch{count}:{query}", download=False).get("entries") or []


def channel_url(entry):
    who = entry.get("uploader_id") or entry.get("channel_id")
    if not who:
        return None
    return f"https://www.youtube.com/{who if who.startswith('@') else 'channel/' + who}/videos"


def tally(queries, count):
    """How often each channel shows up in real search results. Searches that fail are only skipped."""
    hits = collections.Counter()
    def one(q):
        try:
            return search(q, count)
        except tmf.DownloadError:
            return []
    with concurrent.futures.ThreadPoolExecutor(5) as pool:
        for entries in pool.map(one, queries):
            for url in {channel_url(e) for e in entries if e}:
                if url:
                    hits[url] += 1
    return hits


def niche_words(niche):
    return {w for w in re.findall(r"[\w\u0900-\u097F]{3,}", niche.lower()) if w not in {"and", "the", "for", "aur", "with", "how"}}


def english_only(videos):
    """A channel whose titles are mostly Hindi doesn't belong in an English list."""
    hindi = sum(bool(tmf.DEVANAGARI.search(v["title"]) or len(tmf.ROMAN_HINDI.findall(v["title"])) >= 2) for v in videos)
    return hindi / max(1, len(videos)) < .2


def describe(url, hits, words, lang, min_median=MIN_MEDIAN):
    """One candidate's card, or None when it can't be used."""
    try:
        channel = tmf.fetch(url, 60)
    except tmf.DownloadError:
        return None
    videos = [v for v in channel["videos"] if not SKIP.search(v["title"])]
    if len(videos) < tmf.THIN or (lang == "en" and not english_only(videos)):
        return None
    channel["videos"] = videos
    tmf.outliers(channel, 5, 2.0)
    if channel["median"] < min_median or not channel["outliers"] or (min_median > MIN_MEDIAN and NEWS.search(channel["name"])):
        return None
    tmf.clean_titles(channel)
    on_topic = sum(any(w in v["title"].lower() for w in words) for v in videos) / len(videos) if words else 0
    CANDIDATES[url] = channel
    return {"url": url, "name": channel["name"], "median": tmf.short(channel["median"]), "videos": len(videos), "hits": hits, "on_topic": round(on_topic * 100),
            "best_x": channel["outliers"][0]["times"], "best": [{"title": v["clean"], "times": v["times"], "id": v["id"]} for v in channel["outliers"][:3]]}


def ai_suggest(niche, lang, need):
    """Only when search found too few: Gemini names channels, and each one must still pass the same real check."""
    key = gemini_key()
    if not key:
        return []
    ask = (f'List YouTube channels with their exact @handles as JSON: {{"handles": ["@..."]}}. {need}: niche "{niche}", language {"Hindi" if lang == "hi" else "English"}. '
           "Give 15. Only channels you are sure exist.")
    try:
        names = json.loads(_json_text(gemini_text(ask, key))).get("handles") or []
    except (RuntimeError, ValueError, AttributeError):
        return []
    return [f"https://www.youtube.com/@{str(n).strip().lstrip('@')}/videos" for n in names if re.fullmatch(r"@?[\w.\-]{3,40}", str(n).strip())]


def find_channels(niche, lang):
    words = niche_words(niche)
    set_job("search", 5)
    dream_hits = tally([q.format(n=niche) for q in DREAM_SEARCH[lang]], 30)
    set_job("search", 20)
    topics = [q for q in MODEL_SEARCH[lang] if not (words & niche_words(q))]
    model_hits = tally(topics, 20)
    for url in dream_hits:
        model_hits.pop(url, None)
    set_job("check", 35)

    def check(hit_list, part, start, width, min_median=MIN_MEDIAN):
        out, todo = [], list(hit_list)
        with concurrent.futures.ThreadPoolExecutor(5) as pool:
            futures = [pool.submit(describe, url, n, words, lang, min_median) for url, n in todo]
            for i, f in enumerate(concurrent.futures.as_completed(futures), 1):
                set_job(part, start + width * i // max(1, len(futures)))
                if f.result():
                    out.append(f.result())
        return out

    dream = check(dream_hits.most_common(TRY_PER_LIST), "dream", 35, 28)
    if len(dream) < PER_LIST:
        dream += check([(u, 0) for u in ai_suggest(niche, lang, "Channels about this niche") if u not in dream_hits], "dream", 63, 5)
    model = check(model_hits.most_common(TRY_PER_LIST + 8), "model", 68, 27, MODEL_MEDIAN)
    if len(model) < PER_LIST:
        model += check([(u, 0) for u in ai_suggest(niche, lang, "Popular channels on OTHER topics with a similar video style, not about this niche") if u not in model_hits and u not in dream_hits], "model", 95, 3, MODEL_MEDIAN)
    seen = {c["url"] for c in dream}
    model = [c for c in model if c["url"] not in seen]
    dream.sort(key=lambda c: (-(c["on_topic"] >= 10), -c["hits"], -c["on_topic"]))
    model.sort(key=lambda c: (-c["hits"], -c["best_x"]))
    return {"dream": dream, "model": model, "niche": niche, "lang": lang}


def rows_of(channel):
    return [{"title": v["clean"], "times": v["times"], "id": v["id"], "views": v["views"], "channel": channel["name"]} for v in channel["outliers"]]


def fetch_list(urls, part, start, width):
    rows, names, failed = [], [], []
    for i, url in enumerate(urls, 1):
        set_job(part, start + width * i // max(1, len(urls)))
        channel = CANDIDATES.get(url)
        if not channel:
            try:
                channel = tmf.fetch(url, 60)
                channel["videos"] = [v for v in channel["videos"] if not SKIP.search(v["title"])]
                tmf.outliers(channel, 5, 2.0); tmf.clean_titles(channel)
            except tmf.DownloadError as e:
                failed.append({"url": url, "why": tmf.why_failed(e)}); continue
        names.append({"url": url, "name": channel["name"], "median": tmf.short(channel["median"])})
        rows += rows_of(channel)
    return rows, names, failed


def save_lists(lang, niche, dream, model):
    d_rows, d_names, d_bad = fetch_list(dream, "dream", 5, 45)
    m_rows, m_names, m_bad = fetch_list(model, "model", 50, 45)
    if not d_names and not m_names:
        raise RuntimeError("none")
    setup = {"lang": lang, "niche": niche, "scanned": time.strftime("%Y-%m-%d"), "dream": d_names, "model": m_names, "outliers": {"dream": d_rows, "model": m_rows}, "failed": d_bad + m_bad}
    SETUP.write_text(json.dumps(setup, ensure_ascii=False, indent=1), encoding="utf-8")
    return setup


def set_job(step, pct):
    JOB.update(step=step, pct=pct)


def start_job(work):
    with LOCK:
        if JOB["running"]:
            return False
        JOB.update(running=True, step="start", pct=1, error="", result=None)
    def run():
        try:
            JOB["result"] = work()
        except Exception as e:
            JOB["error"] = "yt" if isinstance(e, tmf.DownloadError) else str(e)[:300]
        finally:
            JOB.update(running=False, pct=100)
    threading.Thread(target=run, daemon=True).start()
    return True


# ---------- the prompt

def clean_text(text):
    """An SRT or a description -> plain words, without numbering, times or repeated caption lines."""
    out = []
    for line in str(text or "").replace("\ufeff", "").splitlines():
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line or re.fullmatch(r"\d+", line) or "-->" in line or re.fullmatch(r"WEBVTT.*", line):
            continue
        if not out or out[-1] != line:
            out.append(line)
    text = " ".join(out) if sum(len(x) for x in out) / max(1, len(out)) < 90 else "\n".join(out)
    if len(text) > TEXT_LIMIT:
        text = text[:TEXT_LIMIT // 2] + "\n[...]\n" + text[-TEXT_LIMIT // 2:]
    return text


def safe_name(title):
    """The title as a Windows file name."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", title)
    name = " ".join(name.split()).strip(" .")[:80].strip(" .")
    if not name or re.fullmatch(r"(?i)con|prn|aux|nul|com\d|lpt\d", name):
        name = "video"
    return name


def outlier_lines(rows, count=25):
    return [f'- "{r["title"]}" | {r["times"]}x' for r in sorted(rows, key=lambda r: -r["times"])[:count]]


def build_prompt(setup, title, text, reply_json=False):
    lang = setup["lang"]; body = clean_text(text)
    lines = ["Help me title one YouTube video, and write its thumbnail.", "",
             f"MY CHANNEL'S NICHE: {setup['niche']}",
             f"MY WORKING TITLE: {title}", "",
             "WHAT THE VIDEO REALLY SAYS (its description or subtitles):",
             body or "[Nothing given. Ask me what the video shows before you write anything.]", "",
             "HOW TO WORK",
             "1. Read what the video says. Every title and thumbnail must be true of THIS video, and it must really pay it off. Use a number, result or story only if the text above has it.",
             "2. Some real titles below won on their topic or on news, such as an IPO, a crash, a sale or a song. Skip those, they teach nothing about titles.",
             "3. Find what the rest promise the viewer, and keep the 3 promises that fit this video. A promise seen on several channels counts for more, but one that doesn't fit is out. Say each in one plain line, with no words from the original titles.",
             "4. Write 3 titles on each, as a creator would say them to a friend. Throw out a title if it is a real title with the nouns swapped, makes no sense for this video, or makes two promises. My working title can stay in the running.",
             f"5. Language of titles and thumbnail text: {LANG_RULE[lang]}",
             "6. Keep titles short, about 45-55 characters. Don't print character counts.",
             "7. Choose the 3 titles most worth testing against each other, each making a different promise, with one line on why.",
             "8. For each of those, give 2 thumbnails: the text, 4 words or fewer, and an image prompt I can paste into an image tool. The text adds what the title holds back, such as the fear or the surprise, and doesn't use the title's main words. Example: title \"Why Your Bread Won't Rise\", thumbnail text \"Dead Yeast?\". The image prompt asks for a 16:9 YouTube thumbnail, one clear subject and this exact text in big letters.",
             "9. Do not predict views or say which pair will win. My test decides.", ""]
    if reply_json:
        lines += ['Return ONLY JSON: {"titles": ["9 titles"], "test": [{"title": "...", "why": "...", "thumbs": [{"text": "...", "image_prompt": "..."}, {"text": "...", "image_prompt": "..."}]}]}', ""]
    lines += ["REAL TITLES FROM MY OWN TOPIC (x = times that channel's normal video):", *outlier_lines(setup["outliers"]["dream"]), "",
              "REAL TITLES FROM OTHER TOPICS (only the pattern can move over):", *outlier_lines(setup["outliers"]["model"])]
    return "\n".join(lines) + "\n"


# ---------- Gemini, optional and free unless the key's project has billing on

def gemini_call(path, key, body=None):
    req = urllib.request.Request(f"{GEMINI}/{path}", data=json.dumps(body).encode("utf-8") if body is not None else None,
                                 headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError("key" if e.code in (400, 401, 403) and re.search(r"api.?key|permission|PERMISSION", detail, re.I) else "limit" if e.code == 429 else f"Gemini {e.code}: {detail[:160]}")
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("net")


def pick_model(key, image):
    """Asks Google which models this key has today, because the names keep changing."""
    rows = gemini_call("models?pageSize=200", key).get("models") or []
    def fits(m):
        name = m.get("name", "").lower()
        if "generateContent" not in (m.get("supportedGenerationMethods") or []) or "flash" not in name:
            return False
        return "image" in name if image else not re.search(r"image|tts|audio|live|embedding|robotics|thinking", name)
    names = sorted((m["name"].split("/")[-1] for m in rows if fits(m)), key=lambda n: ("preview" in n or "exp" in n, "lite" in n, "latest" not in n, n))
    if not names:
        raise RuntimeError("no_image" if image else "no_model")
    return names[0]


def gemini_text(prompt, key):
    reply = gemini_call(f"models/{pick_model(key, False)}:generateContent", key, {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}})
    try:
        return "".join(p.get("text", "") for p in reply["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        raise RuntimeError("empty")


def gemini_image(prompt, key):
    reply = gemini_call(f"models/{pick_model(key, True)}:generateContent", key, {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseModalities": ["IMAGE"]}})
    for part in ((reply.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []:
        data = part.get("inlineData") or part.get("inline_data")
        if data:
            return base64.b64decode(data["data"])
    raise RuntimeError("empty")


def _json_text(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON")
    return m.group(0)


def ask_gemini(setup, title, text):
    data = json.loads(_json_text(gemini_text(build_prompt(setup, title, text, True), gemini_key())))
    test = []
    for row in data.get("test") or []:
        name = str(row.get("title") or "").strip()
        if name:
            test.append({"title": name, "why": str(row.get("why") or ""), "notes": tmf.title_lines(tmf.check_title(name))[1:3],
                         "thumbs": [dict(pair_facts(name, str(t.get("text") or "")), image_prompt=str(t.get("image_prompt") or "")) for t in row.get("thumbs") or [] if isinstance(t, dict)]})
    return {"test": test, "titles": [str(t) for t in data.get("titles") or []]}


# ---------- thumbnails

def pair_facts(title, text):
    """Which thumbnail words the title already says. Facts only."""
    in_title = set(tmf.words_of(title)); said = list(dict.fromkeys(tmf.words_of(text)))
    return {"text": text, "repeats": [w for w in said if w in in_title], "new": [w for w in said if w not in in_title]}


def next_file(title, ext):
    MADE.mkdir(exist_ok=True); base = safe_name(title)
    for n in range(1, 1000):
        path = MADE / f"{base} {n}{ext}"
        if not path.exists():
            return path
    raise RuntimeError("too many files")


def font_for(size):
    from PIL import ImageFont
    for name in ("C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/arialbd.ttf"):
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default(size)


def draw_thumb(title, text, palette):
    """Big plain letters, drawn on this PC. Always free. Devanagari needs an image tool, because plain Pillow breaks its letters."""
    from PIL import Image, ImageDraw, ImageFilter
    if tmf.DEVANAGARI.search(text):
        raise ValueError("devanagari")
    words = text.upper().split()
    if not words:
        raise ValueError("empty")
    page, glow, ink, hot = {"red": ("#0f0f0f", "#6b1414", "#ffffff", "#ffd21f"), "blue": ("#071226", "#123a7a", "#ffffff", "#ffe14d"), "green": ("#06140d", "#0e5a33", "#ffffff", "#fff04d"), "purple": ("#120a22", "#47207f", "#ffffff", "#5ff2d0")}.get(palette, ("#0f0f0f", "#6b1414", "#ffffff", "#ffd21f"))
    W, H = 1280, 720
    back = Image.new("RGB", (W, H), page); layer = Image.new("RGB", (W, H), page)
    ImageDraw.Draw(layer).ellipse((180, 130, 1100, 700), fill=glow)
    im = Image.blend(back, layer.filter(ImageFilter.GaussianBlur(120)), .9); d = ImageDraw.Draw(im)
    rows = min(3, max(1, (len(words) + 1) // 2)); lines = [words[i * len(words) // rows:(i + 1) * len(words) // rows] for i in range(rows)]
    size = int(H * .8 / rows)
    while size > 40 and max(d.textlength(" ".join(l), font=font_for(size)) for l in lines) > W - 120:
        size -= 6
    font = font_for(size); gap = H * .86 / rows
    for i, line in enumerate(lines):
        y = H * .07 + gap * (i + .5); x = (W - d.textlength(" ".join(line), font=font)) / 2
        for j, w in enumerate(line):
            d.text((x, y), w, font=font, fill=hot if i == rows - 1 else ink, stroke_width=max(6, size // 18), stroke_fill="#000", anchor="lm")
            x += d.textlength(w + " ", font=font)
    path = next_file(title, ".jpg"); im.save(path, "JPEG", quality=92)
    return path


# ---------- the local server

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def allowed(self, q):
        """Only this page. Another website open in the browser can't reach it: wrong Host, or no token."""
        host = self.headers.get("Host", "").split(":")[0]
        return host in ("127.0.0.1", "localhost") and secrets.compare_digest((q.get("t") or [""])[0] or self.headers.get("X-Token", ""), TOKEN)

    def send(self, data, kind="application/json; charset=utf-8", code=200):
        body = data if isinstance(data, bytes) else data.encode("utf-8") if isinstance(data, str) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", kind); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query)
        if not self.allowed(q):
            return self.send({"error": "forbidden"}, code=403)
        if u.path == "/":
            return self.send(PAGE.replace("__TOKEN__", TOKEN), "text/html; charset=utf-8")
        if u.path == "/api/state":
            return self.send({"setup": load_setup(), "has_key": bool(gemini_key()), "job": JOB})
        if u.path == "/file":
            path = MADE / Path((q.get("name") or [""])[0]).name
            if path.is_file():
                return self.send(path.read_bytes(), "image/png" if path.suffix == ".png" else "image/jpeg")
            return self.send({"error": "missing"}, code=404)
        self.send({"error": "not found"}, code=404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if not self.allowed({}):
            return self.send({"error": "forbidden"}, code=403)
        size = int(self.headers.get("Content-Length") or 0)
        if size > 4_000_000:
            return self.send({"error": "too big"}, code=413)
        try:
            a = json.loads(self.rfile.read(size) or b"{}")
            self.send(self.route(u.path, a))
        except (ValueError, RuntimeError, OSError) as e:
            self.send({"error": str(e)[:300]}, code=400)

    def route(self, path, a):
        lang = "hi" if a.get("lang") == "hi" else "en"
        if path == "/api/find":
            niche = " ".join(str(a.get("niche") or "").split())
            if not niche:
                raise ValueError("niche")
            CANDIDATES.clear()
            return {"ok": start_job(lambda: find_channels(niche, lang))}
        if path == "/api/save":
            add = [tmf.videos_url(str(x)) for x in a.get("add") or [] if str(x).strip()]
            dream, model = list(a.get("dream") or [])[:PER_LIST], list(a.get("model") or [])[:PER_LIST]
            model = [m for m in model if m not in dream] + [x for x in add if x][:PER_LIST - len(model)] if add else [m for m in model if m not in dream]
            if not dream and not model:
                raise ValueError("pick")
            return {"ok": start_job(lambda: save_lists(lang, a.get("niche") or "", dream, model))}
        if path == "/api/refresh":
            s = load_setup(); CANDIDATES.clear()
            return {"ok": start_job(lambda: save_lists(s["lang"], s["niche"], [c["url"] for c in s["dream"]], [c["url"] for c in s["model"]]))}
        if path == "/api/forget":
            SETUP.unlink(missing_ok=True); return {"ok": True}
        if path == "/api/key":
            key = str(a.get("key") or "").strip()
            if key:
                KEY_FILE.write_text(key, encoding="utf-8")
            else:
                KEY_FILE.unlink(missing_ok=True)
            return {"has_key": bool(key)}
        s = load_setup(); title = " ".join(str(a.get("title") or "").split())
        if path in ("/api/prompt", "/api/ask") and not s:
            raise ValueError("setup")
        if path == "/api/prompt":
            if not title:
                raise ValueError("title")
            text = build_prompt(s, title, a.get("text")); PROMPTS.mkdir(exist_ok=True)
            file = PROMPTS / f"{safe_name(title)}.txt"; file.write_text(text, encoding="utf-8")
            return {"prompt": text, "file": str(file), "name": file.name}
        if path == "/api/ask":
            if not title:
                raise ValueError("title")
            return ask_gemini(s, title, a.get("text"))
        if path == "/api/check":
            pictures = tmf.thumb_files([str(HERE / "thumbnails")])[:tmf.MAX_THUMBS]
            if not pictures:
                raise ValueError("no_thumbs")
            try:
                reader = tmf.text_reader()
            except SystemExit:
                raise ValueError("no_reader")
            out = []
            for path_ in pictures:
                thumb = tmf.read_thumb(path_, reader)
                if not thumb.get("error"):
                    c = tmf.check_combo(title, thumb); out.append({"picture": c["picture"], "lines": tmf.combo_lines(c)})
            return {"checks": out}
        if path == "/api/draw":
            f = draw_thumb(title or "thumbnail", str(a.get("text") or ""), a.get("palette"))
            return {"file": f.name, "facts": pair_facts(title, str(a.get("text") or ""))}
        if path == "/api/image":
            key = gemini_key()
            if not key:
                raise ValueError("nokey")
            f = next_file(title or "thumbnail", ".png"); f.write_bytes(gemini_image(str(a.get("prompt") or ""), key))
            return {"file": f.name}
        raise ValueError("unknown")


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Title Model Finder</title><style>
:root{--bg:#f6f6f4;--card:#fff;--ink:#17181a;--soft:#62666d;--line:#dddcd6;--hot:#c8321e;--go:#1e5fd8;--tag:#eeeee9}
@media(prefers-color-scheme:dark){:root{--bg:#141517;--card:#1e2023;--ink:#f0f0ee;--soft:#a2a6ad;--line:#33363a;--hot:#ff7a5e;--go:#5c93ff;--tag:#2b2d31}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,'Segoe UI','Nirmala UI',sans-serif}
main{max-width:1020px;margin:0 auto;padding:24px 16px 70px}h1{font-size:28px;margin:0}h2{font-size:20px;margin:0 0 10px}.top{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin:14px 0}label{display:block;font-weight:600;margin:12px 0 4px}.soft{color:var(--soft)}.small{font-size:14px}.err{color:var(--hot)}
input[type=text],input[type=password],textarea,select{width:100%;padding:10px;border:1px solid var(--line);border-radius:7px;background:var(--bg);color:var(--ink);font:inherit}textarea{min-height:130px}
button{font:inherit;font-weight:600;padding:9px 15px;border-radius:7px;border:1px solid var(--go);background:var(--go);color:#fff;cursor:pointer}button.ghost{background:none;color:var(--go)}button:disabled{opacity:.5;cursor:default}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:12px}.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}.bar{height:8px;background:var(--tag);border-radius:9px;overflow:hidden;margin-top:8px}.bar i{display:block;height:100%;background:var(--go)}
.ch{display:block;border:1px solid var(--line);border-radius:8px;padding:10px;margin:8px 0;font-weight:400;cursor:pointer}.ch b{font-weight:650}.ch input{margin-right:6px}.ch ul{margin:5px 0 0;padding-left:18px;font-size:13px}.ch li a{color:inherit}.ch.off{opacity:.62}
pre{white-space:pre-wrap;background:var(--bg);border:1px solid var(--line);border-radius:7px;padding:10px;max-height:320px;overflow:auto;font:13.5px/1.5 Consolas,'Nirmala UI',monospace}
.pair{border:1px solid var(--line);border-radius:8px;padding:12px;margin:10px 0}.th{border-top:1px solid var(--line);margin-top:9px;padding-top:9px}img.made{display:block;width:320px;max-width:100%;border-radius:7px;margin-top:8px}
@media(max-width:720px){.cols{grid-template-columns:1fr}}
</style></head><body><main><div class="top"><h1>Title Model Finder</h1><select id="ui" style="width:auto" onchange="setUi(this.value)"><option value="en">English</option><option value="hi">Hinglish</option></select></div><div id="app"></div></main>
<script>
const T="__TOKEN__",$=i=>document.getElementById(i),esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const W={en:{s1:"1. Your channels",niche:"Your channel's niche",nicheHint:"For example: home cooking, YouTube growth, stock market basics. The tool searches YouTube itself.",lang:"Language of your videos",find:"Find channels",dream:"Dream 10: your own topic",model:"Model 10: other topics, patterns to borrow",pick:"Ticked",add:"Add your own (one link or @handle per line, goes to Model 10)",save:"Save my lists",few:"Fewer than 10 usable channels in a list. Add your own below, or try a wider niche.",back:"Keep my old lists",checkFolder:"Already have thumbnails?",checkHint:"Put them in the thumbnails folder next to this tool. The first time, close this window and run RUN.bat again so it can install the text reader.",checkBtn:"Check them with this title",normal:"normal video",ontopic:"titles on your topic",search:"Searching YouTube",check:"Checking channels",ds:"Reading Dream channels",ms:"Reading Model channels",start:"Starting",saved:"Your lists",change:"Change channels",refresh:"Refresh views",dataFrom:"Data from",s2:"2. Your video",title:"Video title",text:"Description or SRT (what the video really says)",textHint:"The prompt is written for this. Paste it here, or choose a file.",make:"Make my prompt",copy:"Copy prompt",copied:"Copied",savedAs:"Saved as",paste:"Paste this into any AI chat. It gives you titles, thumbnail text and image prompts.",s3:"3. Thumbnail, free",draw:"Draw thumbnail text on this PC",thumbText:"Thumbnail text",colour:"Colour",repeat:"Repeats the title",noRepeat:"Doesn't repeat the title",newWords:"New words",s4:"Optional: free Gemini key",keyHint:"Only if you want the page itself to write titles. Get it free at aistudio.google.com/apikey. It stays in gemini_key.txt on this PC.",keySave:"Save key",keyDel:"Remove key",keyOn:"Key saved",ask:"Get titles from Gemini",image:"Try Gemini image",imgWarn:"Google's free tier often allows no images. If your Google project has billing on, this can cost money.",noWin:"The tool never says which will win. Put your best 2-3 in YouTube's Test & Compare.",why:"Why",more:"More titles",use:"Use text",e:{niche:"Type your niche first.",title:"Type the video title first.",pick:"Tick at least one channel.",setup:"Save your channels first.",nokey:"Save a Gemini key first.",key:"Google refused this key. Check it at aistudio.google.com/apikey.",limit:"Google's free limit said no. Your key may have no image quota, or you have to wait. Use Draw thumbnail, or paste the image prompt into gemini.google.com.",net:"Could not reach Google. Check your internet.",no_image:"This key has no Gemini image model. Use Draw thumbnail, or paste the image prompt into gemini.google.com.",no_model:"This key has no text model.",empty:"Gemini returned nothing. Try again.",yt:"YouTube did not answer. Check your internet, or update the reader: python -m pip install -U yt-dlp",none:"No channel could be read. Check your internet, or update the reader: python -m pip install -U yt-dlp",devanagari:"Drawing here can't write Devanagari correctly. Use roman letters, or an image tool.",empty_text:"Type the thumbnail text first.",no_thumbs:"The thumbnails folder has no pictures.",no_reader:"The text reader isn't installed. Close this window and run RUN.bat again."}},
hi:{s1:"1. Aapke channels",niche:"Aapke channel ka niche",nicheHint:"Jaise: home cooking, YouTube growth, share market basics. Tool khud YouTube par dhoondhega.",lang:"Videos ki bhasha",find:"Channels dhoondho",dream:"Dream 10: aapka hi topic",model:"Model 10: doosre topic, pattern seekhne ke liye",pick:"Tick kiye",add:"Apne channel jodo (har line mein ek link ya @handle, Model 10 mein jayenge)",save:"Meri list save karo",few:"Ek list mein 10 se kam kaam ke channels mile. Neeche apne jodo, ya niche thoda chauda likho.",back:"Purani list hi rakho",checkFolder:"Thumbnail pehle se hai?",checkHint:"Tool ke paas wale thumbnails folder mein daalo. Pehli baar ye window band karke RUN.bat dobara chalao, taaki text reader install ho jaye.",checkBtn:"Is title ke saath check karo",normal:"normal video",ontopic:"titles aapke topic par",search:"YouTube par dhoondh rahe hain",check:"Channels check ho rahe hain",ds:"Dream channels padh rahe hain",ms:"Model channels padh rahe hain",start:"Shuru",saved:"Aapki list",change:"Channels badlo",refresh:"Views refresh karo",dataFrom:"Data ki date",s2:"2. Aapka video",title:"Video ka title",text:"Description ya SRT (video mein asal mein kya hai)",textHint:"Prompt isi ke hisaab se banega. Yahan paste karo, ya file chuno.",make:"Mera prompt banao",copy:"Prompt copy karo",copied:"Copy ho gaya",savedAs:"Save hua",paste:"Ise kisi bhi AI chat mein paste karo. Title, thumbnail text aur image prompt mil jayenge.",s3:"3. Thumbnail, free",draw:"Thumbnail text isi PC par banao",thumbText:"Thumbnail ka text",colour:"Rang",repeat:"Title repeat karta hai",noRepeat:"Title repeat nahi karta",newWords:"Naye shabd",s4:"Agar chaho: free Gemini key",keyHint:"Sirf tab jab page khud title likhe. Free key aistudio.google.com/apikey par milegi. Ye isi PC par gemini_key.txt mein rehti hai.",keySave:"Key save karo",keyDel:"Key hatao",keyOn:"Key save hai",ask:"Gemini se titles lo",image:"Gemini image try karo",imgWarn:"Google ka free tier aksar image nahi deta. Aapke Google project par billing on hai to paise lag sakte hain.",noWin:"Tool kabhi nahi batata kaun jeetega. Best 2-3 ko YouTube ke Test & Compare mein daalo.",why:"Kyun",more:"Aur titles",use:"Text lo",e:{niche:"Pehle apna niche likho.",title:"Pehle video ka title likho.",pick:"Kam se kam ek channel tick karo.",setup:"Pehle channels save karo.",nokey:"Pehle Gemini key save karo.",key:"Google ne ye key nahi maani. aistudio.google.com/apikey par check karo.",limit:"Google ki free limit ne mana kiya. Key par image quota nahi hoga, ya thoda rukna padega. Draw thumbnail use karo, ya image prompt gemini.google.com par paste karo.",net:"Google tak nahi pahunch paye. Internet check karo.",no_image:"Is key par Gemini image model nahi hai. Draw thumbnail use karo, ya image prompt gemini.google.com par paste karo.",no_model:"Is key par text model nahi hai.",empty:"Gemini ne kuch nahi diya. Dobara try karo.",yt:"YouTube ne jawab nahi diya. Internet check karo, ya reader update karo: python -m pip install -U yt-dlp",none:"Koi channel nahi padha gaya. Internet check karo, ya reader update karo: python -m pip install -U yt-dlp",devanagari:"Yahan Devanagari sahi nahi banti. Roman likho, ya image tool use karo.",empty_text:"Pehle thumbnail ka text likho.",no_thumbs:"Thumbnails folder mein koi picture nahi hai.",no_reader:"Text reader install nahi hai. Ye window band karke RUN.bat dobara chalao."}}};
let S={},ui="en",view="",found=null,last={},ideas=null,made=[],checks=[];try{ui=localStorage.getItem("tmf_ui")||"en"}catch(e){}
const t=k=>W[ui][k]??k,er=k=>W[ui].e[k]||k;
async function call(p,b){const r=await fetch(p,{method:"POST",headers:{"Content-Type":"application/json","X-Token":T},body:JSON.stringify(b||{})});return r.json()}
async function load(){const r=await fetch("/api/state?t="+T);S=await r.json();render()}
function setUi(v){ui=v;try{localStorage.setItem("tmf_ui",v)}catch(e){}render()}
function keep(){for(const k of["title","text","niche","thumbText"]){const e=$(k);if(e)last[k]=e.value}}
function render(){keep();$("ui").value=ui;const J=S.job||{};let h="";if(J.running)h+=progress(J);
 else if(!S.setup||view==="setup")h+=setupCard();else h+=listCard()+videoCard();
 h+=keyCard();$("app").innerHTML=h;for(const k in last){const e=$(k);if(e&&last[k]!==undefined)e.value=last[k]}}
function progress(J){const n={search:t("search"),check:t("check"),dream:t("ds"),model:t("ms"),start:t("start")}[J.step]||"";return `<div class="card"><b>${esc(n)}…</b> ${J.pct}%<div class="bar"><i style="width:${J.pct}%"></i></div></div>`}
function setupCard(){const l=(S.setup&&S.setup.lang)||last.lang||(ui==="hi"?"hi":"en");let h=`<div class="card"><h2>${t("s1")}</h2><label>${t("niche")}</label><input type="text" id="niche" value="${esc(last.niche??(S.setup?S.setup.niche:""))}"><div class="soft small">${t("nicheHint")}</div>
 <label>${t("lang")}</label><select id="vlang" style="width:auto"><option value="en" ${l==="en"?"selected":""}>English</option><option value="hi" ${l==="hi"?"selected":""}>Hindi / Hinglish</option></select>
 <div class="row"><button onclick="find()">${t("find")}</button>${S.setup?`<button class="ghost" onclick="view='';found=null;render()">${t("back")}</button>`:""}<span class="err" id="msg">${esc(S.msg||"")}</span></div></div>`;
 if(found){const box=(k,name)=>`<div><h2>${name} <span class="soft small" id="n_${k}"></span></h2>${found[k].map((c,i)=>`<label class="ch"><input type="checkbox" class="c_${k}" value="${esc(c.url)}" ${i<10?"checked":""} onchange="count()"><b>${esc(c.name)}</b> <span class="soft small">· ${t("normal")} ${esc(c.median)} · ${k==="dream"?c.on_topic+"% "+t("ontopic"):c.best_x+"x"}</span><ul>${c.best.map(v=>`<li>${v.times}x <a target="_blank" rel="noopener" href="https://www.youtube.com/watch?v=${esc(v.id)}">${esc(v.title)}</a></li>`).join("")}</ul></label>`).join("")}</div>`;
  const few=found.dream.length<10||found.model.length<10;
  h+=`<div class="card">${few?`<p class="err">${t("few")}</p>`:""}<div class="cols">${box("dream",t("dream"))}${box("model",t("model"))}</div><label>${t("add")}</label><textarea id="addc" style="min-height:70px"></textarea><div class="row"><button onclick="saveLists()">${t("save")}</button><span class="err" id="msg2"></span></div></div>`}
 return h}
function count(){for(const k of["dream","model"]){const b=[...document.querySelectorAll(".c_"+k)],n=b.filter(x=>x.checked).length;const e=$("n_"+k);if(e)e.textContent=`${t("pick")}: ${n}/10`;b.forEach(x=>{x.disabled=!x.checked&&n>=10;x.closest(".ch").classList.toggle("off",!x.checked)})}}
function listCard(){const s=S.setup,names=l=>l.map(c=>esc(c.name)).join(", ");return `<div class="card"><h2>${t("saved")}: ${esc(s.niche)}</h2><div class="small"><b>Dream ${s.dream.length}:</b> ${names(s.dream)}</div><div class="small" style="margin-top:4px"><b>Model ${s.model.length}:</b> ${names(s.model)}</div><div class="soft small" style="margin-top:6px">${t("dataFrom")}: ${esc(s.scanned)}</div>
 <div class="row"><button class="ghost" onclick="view='setup';found=null;render()">${t("change")}</button><button class="ghost" onclick="refreshData()">${t("refresh")}</button></div></div>`}
function videoCard(){const p=S.promptDone;let h=`<div class="card"><h2>${t("s2")}</h2><label>${t("title")}</label><input type="text" id="title"><label>${t("text")}</label><textarea id="text"></textarea><div class="soft small">${t("textHint")}</div><input type="file" accept=".srt,.vtt,.txt" onchange="readFile(this)" style="margin-top:6px">
 <div class="row"><button onclick="makePrompt()">${t("make")}</button>${S.has_key?`<button class="ghost" onclick="askAi()">${t("ask")}</button>`:""}<span class="err" id="msg3"></span></div>
 ${p?`<p class="small">${t("savedAs")}: <b>${esc(p.file)}</b><br><span class="soft">${t("paste")}</span></p><div class="row" style="margin:6px 0"><button onclick="copyP()" id="cp">${t("copy")}</button></div><pre id="ptxt">${esc(p.prompt)}</pre>`:""}${ideasHtml()}</div>
 <div class="card"><h2>${t("s3")}</h2><label>${t("thumbText")}</label><input type="text" id="thumbText"><label>${t("colour")}</label><select id="pal" style="width:auto"><option>red</option><option>blue</option><option>green</option><option>purple</option></select>
 <div class="row"><button class="ghost" onclick="draw()">${t("draw")}</button><span class="err" id="msg4"></span></div>${made.map(m=>`<div class="pair small"><img class="made" src="/file?t=${T}&name=${encodeURIComponent(m.file)}"><div class="soft" style="margin-top:6px">${esc(m.file)}</div>${m.facts?`<div>${m.facts.repeats.length?`${t("repeat")}: ${esc(m.facts.repeats.join(", "))}.`:t("noRepeat")+"."} ${m.facts.new.length?`${t("newWords")}: ${esc(m.facts.new.join(", "))}`:""}</div>`:""}</div>`).join("")}<label>${t("checkFolder")}</label><div class="soft small">${t("checkHint")}</div><div class="row"><button class="ghost" onclick="checkThumbs()">${t("checkBtn")}</button><span class="err" id="msg5"></span></div>${checks.map(c=>`<div class="pair small"><img class="made" src="data:image/jpeg;base64,${c.picture}"><div>${c.lines.map(esc).join("<br>")}</div></div>`).join("")}<p class="soft small">${t("noWin")}</p></div>`;return h}
function ideasHtml(){if(!ideas)return"";return `<div style="margin-top:12px">${ideas.test.map((r,i)=>`<div class="pair"><b>${esc(r.title)}</b><div class="soft small">${esc(r.notes.join(" · "))}</div><div class="small">${t("why")}: ${esc(r.why)}</div>${r.thumbs.map((x,k)=>`<div class="th small"><b>${esc(x.text)}</b> · ${x.repeats.length?`${t("repeat")}: ${esc(x.repeats.join(", "))}`:t("noRepeat")}<div class="soft">${esc(x.image_prompt)}</div><div class="row" style="margin-top:6px"><button class="ghost" onclick="useText(${i},${k})">${t("use")}</button><button class="ghost" onclick="geminiImg(${i},${k})">${t("image")}</button></div></div>`).join("")}</div>`).join("")}<p class="soft small">${t("imgWarn")}</p>${ideas.titles.length?`<details><summary>${t("more")}</summary><ul class="small">${ideas.titles.map(x=>`<li>${esc(x)}</li>`).join("")}</ul></details>`:""}</div>`}
function keyCard(){return `<div class="card"><details ${S.has_key?"open":""}><summary><b>${t("s4")}</b></summary><p class="soft small">${t("keyHint")}</p><div class="row"><input type="password" id="gkey" style="flex:1;min-width:240px" autocomplete="off" placeholder="${S.has_key?esc(t("keyOn")):""}"><button class="ghost" onclick="saveKey()">${t("keySave")}</button>${S.has_key?`<button class="ghost" onclick="delKey()">${t("keyDel")}</button>`:""}</div></details></div>`}
function poll(after){const go=async()=>{await load();if(S.job.running){setTimeout(go,900);return}after&&after(S.job)};go()}
async function find(){keep();last.lang=$("vlang").value;const d=await call("/api/find",{niche:$("niche").value,lang:$("vlang").value});if(d.error){$("msg").textContent=er(d.error);return}found=null;poll(j=>{if(j.error){S.msg=er(j.error)}else{S.msg="";found=j.result;view="setup"}render();count()})}
async function saveLists(){const pick=k=>[...document.querySelectorAll(".c_"+k)].filter(x=>x.checked).map(x=>x.value);const add=$("addc").value.split("\n").map(s=>s.trim()).filter(Boolean);const d=await call("/api/save",{lang:found.lang,niche:found.niche,dream:pick("dream"),model:pick("model"),add});if(d.error){$("msg2").textContent=er(d.error);return}poll(j=>{if(j.error){S.msg=er(j.error);render();count()}else{view="";found=null;render()}})}
function refreshData(){call("/api/refresh").then(()=>poll())}
function readFile(i){const f=i.files[0];if(!f)return;const r=new FileReader();r.onload=()=>{$("text").value=r.result};r.readAsText(f)}
async function makePrompt(){keep();const d=await call("/api/prompt",{title:$("title").value,text:$("text").value});if(d.error){$("msg3").textContent=er(d.error);return}S.promptDone=d;render()}
async function copyP(){try{await navigator.clipboard.writeText(S.promptDone.prompt)}catch(e){const r=document.createRange();r.selectNodeContents($("ptxt"));const s=getSelection();s.removeAllRanges();s.addRange(r);document.execCommand("copy")}$("cp").textContent=t("copied")}
async function askAi(){keep();$("msg3").textContent="…";const d=await call("/api/ask",{title:$("title").value,text:$("text").value});if(d.error){$("msg3").textContent=er(d.error);return}ideas=d;render()}
function useText(i,k){last.thumbText=ideas.test[i].thumbs[k].text;last.title=ideas.test[i].title;render()}
async function draw(){keep();const x=$("thumbText").value.trim();if(!x){$("msg4").textContent=er("empty_text");return}const d=await call("/api/draw",{title:$("title").value,text:x,palette:$("pal").value});if(d.error){$("msg4").textContent=er(d.error);return}made.unshift(d);render()}
async function geminiImg(i,k){keep();$("msg3").textContent="…";const r=ideas.test[i];const d=await call("/api/image",{title:r.title,prompt:r.thumbs[k].image_prompt});if(d.error){$("msg3").textContent=er(d.error);return}$("msg3").textContent="";made.unshift(d);render()}
async function checkThumbs(){keep();$("msg5").textContent="…";const d=await call("/api/check",{title:$("title").value});if(d.error){$("msg5").textContent=er(d.error);return}checks=d.checks;render()}
async function saveKey(){keep();const k=$("gkey").value.trim();if(!k)return;await call("/api/key",{key:k});load()}
async function delKey(){keep();await call("/api/key",{});ideas=null;load()}
load();
</script></body></html>"""


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    port = int(os.environ.get("TMF_PORT") or 0)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{server.server_port}/?t={TOKEN}"
    print(f"Title Model Finder is open in your browser.\nIf it didn't open, copy this link: {url}\nKeep this window open. Close it when you are done.", flush=True)
    if not os.environ.get("TMF_NO_BROWSER"):
        webbrowser.open(url)
    server.serve_forever()


if __name__ == "__main__":
    main()
