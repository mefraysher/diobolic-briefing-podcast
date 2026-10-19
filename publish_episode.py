#!/usr/bin/env python3
"""Publish one Dio-Bolic studio briefing episode.

Usage:
  .venv/bin/python publish_episode.py SCRIPT.txt YYYY-MM-DD [--voice en-US-AndrewNeural] [--no-push]

Steps: text-to-speech (edge-tts, piper fallback) -> episodes/YYYY-MM-DD.mp3,
regenerate feed.xml (newest first, last 30 episodes, older mp3s deleted),
then git commit and push. Rerunning for the same date replaces that episode.
"""
import argparse, datetime as dt, email.utils, os, re, shutil, subprocess, sys, tempfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
EP_DIR = os.path.join(ROOT, "episodes")
FEED = os.path.join(ROOT, "feed.xml")
BASE = "https://mefraysher.github.io/diobolic-briefing-podcast"
TZ = ZoneInfo("America/New_York")
KEEP = 30
SHOW_TITLE = "Dio-Bolic Daily Briefing"
SHOW_DESC = ("Weekday studio news briefing for Dio-Bolic Studios: Tesla, SpaceX and xAI, "
             "AI tools, manufacturing, collectibles, crowdfunding and e-commerce.")
AUTHOR = "Dio-Bolic Studios"
ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
VENV_BIN = os.path.join(ROOT, ".venv", "bin")


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def tts(text_file, out_mp3, voice):
    """Render speech to a normalized 44.1 kHz mono 64 kbps MP3. Returns engine used."""
    tmp = tempfile.mkdtemp()
    raw = None
    engine = None
    edge = os.path.join(VENV_BIN, "edge-tts")
    if not os.path.exists(edge):
        edge = shutil.which("edge-tts")
    for attempt in range(4):
        try:
            raw = os.path.join(tmp, "raw.mp3")
            run([edge, "--voice", voice, "--file", text_file, "--write-media", raw],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if os.path.getsize(raw) > 1000:
                engine = f"edge-tts:{voice}"
                break
        except Exception as e:  # noqa
            err = getattr(e, "stderr", b"") or b""
            print(f"edge-tts attempt {attempt + 1} failed: {err.decode(errors='replace')[-300:] or e}",
                  file=sys.stderr)
            import time; time.sleep(5)
        raw = None
    if raw is None:
        piper = os.path.join(VENV_BIN, "piper")
        model = os.environ.get("PIPER_MODEL", os.path.join(ROOT, ".piper", "en_US-ryan-high.onnx"))
        if not (os.path.exists(piper) and os.path.exists(model)):
            sys.exit("edge-tts failed and piper fallback is not installed "
                     "(pip install piper-tts into .venv and set PIPER_MODEL).")
        raw = os.path.join(tmp, "raw.wav")
        with open(text_file, "rb") as fh:
            run([piper, "--model", model, "--output_file", raw], stdin=fh)
        engine = f"piper:{os.path.basename(model)}"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-ac", "1", "-ar", "44100",
         "-b:a", "64k", "-map_metadata", "-1", "-id3v2_version", "3", out_mp3])
    shutil.rmtree(tmp, ignore_errors=True)
    return engine


def duration_seconds(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def hms(sec):
    s = int(round(sec))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def ep_title(d):
    return f"Studio Briefing - {d.strftime('%A')}, {d.strftime('%b')} {d.day}"


def summarize(text, limit=600):
    """Short plain description from the spoken script (skip the greeting line)."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines and lines[0].lower().startswith("good morning"):
        lines = lines[1:]
    body = " ".join(lines)
    if len(body) > limit:
        body = body[:limit].rsplit(" ", 1)[0] + "..."
    return body


def existing_descriptions():
    descs = {}
    if not os.path.exists(FEED):
        return descs
    try:
        tree = ET.parse(FEED)
        for item in tree.getroot().iter("item"):
            guid = (item.findtext("guid") or "").strip()
            m = re.search(r"(\d{4}-\d{2}-\d{2})", guid)
            if m:
                descs[m.group(1)] = item.findtext("description") or ""
    except ET.ParseError:
        pass
    return descs


def build_feed(new_date=None, new_desc=None):
    descs = existing_descriptions()
    if new_date:
        descs[new_date] = new_desc
    dates = sorted((f[:-4] for f in os.listdir(EP_DIR)
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.mp3", f)), reverse=True)
    for old in dates[KEEP:]:
        os.remove(os.path.join(EP_DIR, old + ".mp3"))
        print(f"pruned episodes/{old}.mp3")
    dates = dates[:KEEP]
    items = []
    for ds in dates:
        d = dt.date.fromisoformat(ds)
        path = os.path.join(EP_DIR, ds + ".mp3")
        pub = dt.datetime(d.year, d.month, d.day, 7, 0, tzinfo=TZ)
        desc = descs.get(ds) or f"Dio-Bolic studio news briefing for {d.strftime('%A, %B')} {d.day}, {d.year}."
        url = f"{BASE}/episodes/{ds}.mp3"
        title = ep_title(d)
        items.append(f"""    <item>
      <title>{escape(title)}</title>
      <itunes:title>{escape(title)}</itunes:title>
      <description>{escape(desc)}</description>
      <itunes:summary>{escape(desc)}</itunes:summary>
      <enclosure url="{url}" length="{os.path.getsize(path)}" type="audio/mpeg"/>
      <guid isPermaLink="false">diobolic-briefing-{ds}</guid>
      <pubDate>{email.utils.format_datetime(pub)}</pubDate>
      <itunes:duration>{hms(duration_seconds(path))}</itunes:duration>
      <itunes:episodeType>full</itunes:episodeType>
      <itunes:explicit>false</itunes:explicit>
    </item>""")
    now = email.utils.format_datetime(dt.datetime.now(TZ).replace(microsecond=0))
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="{ITUNES}" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{SHOW_TITLE}</title>
    <link>{BASE}/</link>
    <atom:link href="{BASE}/feed.xml" rel="self" type="application/rss+xml"/>
    <description>{escape(SHOW_DESC)}</description>
    <language>en-us</language>
    <copyright>{dt.date.today().year} {AUTHOR}</copyright>
    <lastBuildDate>{now}</lastBuildDate>
    <itunes:author>{AUTHOR}</itunes:author>
    <itunes:summary>{escape(SHOW_DESC)}</itunes:summary>
    <itunes:type>episodic</itunes:type>
    <itunes:image href="{BASE}/cover.jpg"/>
    <image>
      <url>{BASE}/cover.jpg</url>
      <title>{SHOW_TITLE}</title>
      <link>{BASE}/</link>
    </image>
    <itunes:category text="News">
      <itunes:category text="Tech News"/>
    </itunes:category>
    <itunes:category text="Business"/>
    <itunes:explicit>false</itunes:explicit>
    <itunes:block>Yes</itunes:block>
{chr(10).join(items)}
  </channel>
</rss>
"""
    with open(FEED, "w", encoding="utf-8") as fh:
        fh.write(feed)
    ET.parse(FEED)  # sanity: must be well-formed
    return dates


def git_publish(ds, push=True):
    os.chdir(ROOT)
    run(["git", "add", "-A", "feed.xml", "episodes"])
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        print("nothing to commit")
    else:
        run(["git", "commit", "-q", "-m", f"Publish briefing {ds}"])
    if push:
        run(["git", "push", "-q", "origin", "main"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("script", help="plain-text spoken script")
    ap.add_argument("date", help="YYYY-MM-DD")
    ap.add_argument("--voice", default="en-US-AndrewNeural")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    d = dt.date.fromisoformat(a.date)
    ds = d.isoformat()
    text = open(a.script, encoding="utf-8").read()
    if not text.strip():
        sys.exit("script is empty")
    os.makedirs(EP_DIR, exist_ok=True)
    out = os.path.join(EP_DIR, ds + ".mp3")
    tmp_out = out + ".tmp.mp3"
    engine = tts(a.script, tmp_out, a.voice)
    os.replace(tmp_out, out)  # idempotent: same date replaces the file
    dates = build_feed(ds, summarize(text))
    dur = hms(duration_seconds(out))
    git_publish(ds, push=not a.no_push)
    print(f"published: {ep_title(d)} | duration {dur} | {engine} | {os.path.getsize(out)} bytes")
    print(f"episodes in feed: {len(dates)}")
    print(f"feed: {BASE}/feed.xml")


if __name__ == "__main__":
    main()
