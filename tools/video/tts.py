"""해설 음성 만들기 — Liner Model API의 음성 합성(/audio/speech). 장면마다 wav 한 개와 길이를 남긴다.

    python tools/video/tts.py <out_dir>
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
spec = json.loads((HERE / "script.json").read_text(encoding="utf-8"))
key = os.environ["LINER_API_KEY"]
durs = {}
for sc in spec["scenes"]:
    wav = out / f"{sc['id']}.wav"
    if not wav.exists():
        for attempt in range(4):
            r = requests.post("https://platform.liner.com/api/v1/audio/speech", timeout=300,
                              headers={"X-API-KEY": key, "Content-Type": "application/json"},
                              data=json.dumps({"model": spec["voice"]["model"], "voice": spec["voice"]["voice"], "input": sc["narration"]}))
            if r.status_code == 200 and len(r.content) > 20000:
                break
            time.sleep(3 * (attempt + 1))
        else:
            raise SystemExit(f"{sc['id']} 음성 생성 실패: {r.status_code} {r.text[:200]}")
        pcm = out / f"{sc['id']}.pcm"; pcm.write_bytes(r.content)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", str(pcm), str(wav)], check=True)
        pcm.unlink()
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(wav)],
                             capture_output=True, text=True).stdout.strip())
    durs[sc["id"]] = round(d, 2)
    print(sc["id"], durs[sc["id"]], file=sys.stderr)
(out / "durations.json").write_text(json.dumps(durs, indent=1))
print("total", round(sum(durs.values()), 1))
