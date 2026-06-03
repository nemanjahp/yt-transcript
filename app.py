import os
import re
import json
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)
APP_VERSION = "3.1"

# Ažurirana lista Piped API instanci (jun 2026)
PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://pipedapi-libre.kavin.rocks",
    "https://pipedapi.leptons.xyz",
    "https://pipedapi.nosebs.ru",
    "https://piped-api.privacy.com.de",
    "https://pipedapi.adminforge.de",
    "https://api.piped.yt",
    "https://pipedapi.drgns.space",
    "https://pipedapi.owo.si",
    "https://pipedapi.ducks.party",
    "https://piped-api.codespace.cz",
    "https://pipedapi.reallyaweso.me",
    "https://api.piped.private.coffee",
    "https://pipedapi.darkness.services",
    "https://pipedapi.orangenet.cc",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}


def izvuci_video_id(url: str) -> str | None:
    patterns = [
        r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def parse_vtt(vtt_text: str) -> list:
    """Parsiraj VTT tekst u listu segmenata."""
    segments = []
    lines = vtt_text.strip().split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Match timestamp: 00:00:01.234 --> ... or 0:00:01.234 --> ...
        ts_match = re.match(r'(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->', line)
        if not ts_match:
            # Try MM:SS.mmm format
            ts_match2 = re.match(r'(\d{2}):(\d{2})[.,](\d{3})\s*-->', line)
            if ts_match2:
                m, s = int(ts_match2.group(1)), int(ts_match2.group(2))
                start = m * 60 + s
            else:
                i += 1
                continue
        else:
            h, m, s = int(ts_match.group(1)), int(ts_match.group(2)), int(ts_match.group(3))
            start = h * 3600 + m * 60 + s

        i += 1
        text_lines = []
        while i < len(lines) and lines[i].strip():
            clean = re.sub(r'<[^>]+>', '', lines[i].strip())
            if clean:
                text_lines.append(clean)
            i += 1
        text = ' '.join(text_lines).strip()
        if text and 'WEBVTT' not in text:
            if not segments or segments[-1]['text'] != text:
                segments.append({'text': text, 'start': round(start, 1)})
        i += 1
    return segments


def parse_json3(json_text: str) -> list:
    """Parsiraj YouTube json3 subtitle format."""
    try:
        j = json.loads(json_text)
    except Exception:
        return []
    segments = []
    for event in j.get("events", []):
        start_ms = event.get("tStartMs", 0)
        segs = event.get("segs", [])
        text = "".join(s.get("utf8", "") for s in segs).strip()
        text = text.replace("\n", " ").strip()
        if text:
            segments.append({
                "text": text,
                "start": round(start_ms / 1000, 1)
            })
    return segments


def try_piped(video_id: str) -> dict:
    """Probaj Piped API instance za dobijanje titlova."""
    lang_pref = ['sr', 'hr', 'bs', 'en', 'ru', 'de', 'fr', 'es']
    last_error = ""

    for base in PIPED_INSTANCES:
        try:
            resp = requests.get(
                f"{base}/streams/{video_id}",
                headers=HEADERS,
                timeout=10
            )
            if resp.status_code != 200:
                last_error = f"{base} status {resp.status_code}"
                continue

            data = resp.json()
            subtitles = data.get("subtitles", [])

            if not subtitles:
                last_error = f"{base} nema titlova u odgovoru"
                continue

            # Izaberi jezik po prioritetu
            chosen = None
            for lang in lang_pref:
                for sub in subtitles:
                    sub_code = sub.get("code", "")
                    if sub_code.startswith(lang):
                        chosen = sub
                        break
                if chosen:
                    break
            if not chosen:
                chosen = subtitles[0]

            sub_url = chosen.get("url", "")
            if not sub_url:
                last_error = f"{base} nema URL za titlove"
                continue

            # Preuzmi titlove
            r = requests.get(sub_url, headers=HEADERS, timeout=15)
            r.raise_for_status()

            # Probaj JSON3 format
            if "fmt=json3" in sub_url or r.text.strip().startswith("{"):
                segments = parse_json3(r.text)
            else:
                segments = parse_vtt(r.text)

            if segments:
                full_text = " ".join(s["text"] for s in segments)
                return {
                    "text": full_text,
                    "segments": segments,
                    "language": chosen.get("code", "?"),
                    "source": f"Piped ({base})"
                }
            else:
                last_error = f"{base} titlovi prazni"

        except requests.exceptions.Timeout:
            last_error = f"{base} timeout"
        except Exception as e:
            last_error = f"{base} {str(e)[:80]}"
            continue

    raise Exception(f"Nijedna od {len(PIPED_INSTANCES)} Piped instanci nije uspela. Poslednja greška: {last_error}")


def try_ytdlp(video_id: str) -> dict:
    """Poslednji pokušaj: yt-dlp."""
    import subprocess
    import tempfile

    url = f"https://www.youtube.com/watch?v={video_id}"

    with tempfile.TemporaryDirectory() as tmpdir:
        out_template = os.path.join(tmpdir, "subs")

        for extra_args in [
            ["--write-auto-subs"],
            ["--write-subs"],
            ["--write-auto-subs", "--extractor-args", "youtube:player_client=web,android"],
        ]:
            try:
                cmd = [
                    "yt-dlp",
                    "--skip-download",
                    *extra_args,
                    "--sub-langs", "sr,hr,bs,en,ru,de,fr,es",
                    "--sub-format", "vtt",
                    "--output", out_template,
                    "--no-warnings",
                    "--quiet",
                    url
                ]
                subprocess.run(cmd, capture_output=True, timeout=30)

                for f in sorted(os.listdir(tmpdir)):
                    if f.endswith('.vtt'):
                        vtt_path = os.path.join(tmpdir, f)
                        with open(vtt_path, 'r', encoding='utf-8') as fh:
                            segments = parse_vtt(fh.read())
                        if segments:
                            parts = f.rsplit('.', 2)
                            lang = parts[-2] if len(parts) >= 2 else "?"
                            full_text = " ".join(s["text"] for s in segments)
                            return {
                                "text": full_text,
                                "segments": segments,
                                "language": lang,
                                "source": "yt-dlp"
                            }
            except Exception:
                continue

    raise Exception("titlovi nisu pronađeni")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    """Dijagnostički endpoint - proveri koja verzija koda je aktivna."""
    return jsonify({
        "version": APP_VERSION,
        "piped_instances": len(PIPED_INSTANCES),
        "status": "ok"
    })


@app.route("/api/transcript", methods=["POST"])
def get_transcript():
    data = request.get_json()
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "Nije unet URL."}), 400

    video_id = izvuci_video_id(url)
    if not video_id:
        return jsonify({"error": "Neispravan YouTube URL."}), 400

    errors = []
    for name, fn in [("Piped", try_piped), ("yt-dlp", try_ytdlp)]:
        try:
            result = fn(video_id)
            result["video_id"] = video_id
            result["url"] = url
            return jsonify(result)
        except Exception as e:
            errors.append(f"{name}: {e}")

    return jsonify({
        "error": f"Transkript nije pronađen. Pokušano: {'; '.join(errors)}",
        "version": APP_VERSION
    }), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
