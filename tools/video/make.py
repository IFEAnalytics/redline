"""데모 영상 만들기: 해설 음성(Liner 음성 합성) → 화면 녹화(Playwright) → 자막·합성(ffmpeg).

    LINER_API_KEY=... python tools/video/make.py <work_dir> [--only s08,...]
    (Redline 서버가 http://localhost:8765 에 떠 있어야 한다. 한글 글꼴 NanumSquare 필요)

실행 구간은 실제 Liner API 호출을 그대로 찍는다. 해설보다 오래 걸린 구간만 빠르게 재생하고 화면에 표시한다.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
work = Path(sys.argv[1]).resolve(); (work / "raw").mkdir(parents=True, exist_ok=True); (work / "clips").mkdir(exist_ok=True)
only = set(sys.argv[3].split(",")) if len(sys.argv) > 3 and sys.argv[2] == "--only" else None
spec = json.loads((HERE / "script.json").read_text(encoding="utf-8"))
dur = json.loads((work / "audio" / "durations.json").read_text())
PAD, URL, FONT = 0.6, "http://localhost:8765/", "NanumSquare"
T = {k: round(v + PAD, 2) for k, v in dur.items()}
T["s11"] += 1.5


def sh(*a):
    r = subprocess.run(a, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"실패: {' '.join(map(str, a))[:300]}\n{r.stderr[-1500:]}")
    return r.stdout


def record(name, s):
    out = work / "raw" / f"{name}.webm"
    if out.exists() and not (only and name in only):
        return json.loads(Path(str(out) + ".json").read_text())
    s.update(dir=str(work / "raw" / "tmp"), out=str(out))
    return json.loads(sh("node", str(HERE / "record.js"), json.dumps(s)).strip().splitlines()[-1])


ENC = ["-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-an"]


def fit(name, total, meta=None, W=None):
    """원본 녹화를 정확히 total 초짜리 30fps 클립으로. 실행이 길어진 구간만 빠르게 재생한다."""
    src, dst = work / "raw" / f"{name}.webm", work / "clips" / f"{name}.mp4"
    if meta and meta.get("tDone") and meta["base"] > meta["tClick"] + W + 0.05:
        a, b = meta["tClick"], meta["base"]; k = (b - a) / W
        badge = (f"drawtext=font={FONT}:text='실행 구간 {k:.1f}배속':fontsize=30:fontcolor=white:box=1:boxcolor=0x102b4c@0.85:boxborderw=14:x=w-tw-50:y=44")
        fc = (f"[0:v]fps=30,split=3[a][b][c];[a]trim=0:{a},setpts=PTS-STARTPTS[va];"
              f"[b]trim={a}:{b},setpts=(PTS-STARTPTS)/{k},{badge}[vb];[c]trim={b},setpts=PTS-STARTPTS[vc];"
              f"[va][vb][vc]concat=n=3:v=1[v0];[v0]tpad=stop_mode=clone:stop_duration=3,trim=0:{total},setpts=PTS-STARTPTS[v]")
    else:
        fc = f"[0:v]fps=30,tpad=stop_mode=clone:stop_duration=3,trim=0:{total},setpts=PTS-STARTPTS[v]"
    sh("ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex", fc, "-map", "[v]", *ENC, str(dst))
    return dst


plan = []  # (클립 이름, 길이, 장면 id 들)
card = lambda sid, scene: (record(sid, {"kind": "card", "scene": scene, "total": T[sid] + 0.5}), fit(sid, T[sid]))
card("s01", "title"); plan.append(("s01", T["s01"], ["s01"]))
record("s02", {"kind": "about", "total": T["s02"] + 0.5}); fit("s02", T["s02"]); plan.append(("s02", T["s02"], ["s02"]))
card("s03", "rules"); plan.append(("s03", T["s03"], ["s03"]))

us_total = T["s04"] + T["s05"] + T["s06"] + T["s07"]; Wus = T["s04"] - 5 + 12; r5 = T["s05"] - 12
m = record("us", {"kind": "live", "url": URL, "sample": "us-inflation-oct", "tSelect": 1.5, "tClick": 5.0, "W": Wus, "tail": r5 + T["s06"] + T["s07"],
                  "posts": [{"at": 0.3, "text": "2.6%"}, {"at": r5 * 0.6, "text": "4.2%"}, {"at": r5 + 0.5, "text": "trade services"},
                            {"at": r5 + T["s06"] + 0.5, "text": "증거다"}, {"at": r5 + T["s06"] + T["s07"] * 0.5, "text": "유리하다"}]})
fit("us", us_total, m, Wus); plan.append(("us", us_total, ["s04", "s05", "s06", "s07"])); print("us", m)
m = record("kr", {"kind": "live", "url": URL, "sample": "kr-macro-oct", "tSelect": 0.6, "tClick": 1.8, "W": 4.0, "tail": T["s08"] - 5.8,
                  "posts": [{"at": 0.2, "text": "2.2%"}, {"at": 2.4, "text": "1.4%"}]})
fit("kr", T["s08"], m, 4.0); plan.append(("kr", T["s08"], ["s08"])); print("kr", m)
m = record("proposal", {"kind": "live", "url": URL, "sample": "proposal-mock", "tSelect": 1.5, "tClick": 5.0, "W": 5.5, "tail": T["s09"] - 10.5,
                        "posts": [{"at": 0.3, "text": "12주"}]})
fit("proposal", T["s09"], m, 5.5); plan.append(("proposal", T["s09"], ["s09"])); print("proposal", m)
card("s10", "results"); plan.append(("s10", T["s10"], ["s10"]))
card("s11", "close"); plan.append(("s11", T["s11"], ["s11"]))

# ── 음성: 장면마다 길이를 맞춰 이어 붙인다 ──
ids = [s["id"] for s in spec["scenes"]]
inputs, parts = [], []
for i, sid in enumerate(ids):
    inputs += ["-i", str(work / "audio" / f"{sid}.wav")]
    parts.append(f"[{i}:a]aresample=48000,apad,atrim=0:{T[sid]},asetpts=PTS-STARTPTS[a{i}]")
sh("ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(parts) + ";" + "".join(f"[a{i}]" for i in range(len(ids))) + f"concat=n={len(ids)}:v=0:a=1[a]",
   "-map", "[a]", "-c:a", "aac", "-b:a", "160k", str(work / "audio.m4a"))

# ── 자막(ASS): 해설을 문장 단위로 나눠 글자 수에 비례해 시간을 준다 ──
def ts(x):
    h, r = divmod(x, 3600); mnt, s = divmod(r, 60)
    return f"{int(h)}:{int(mnt):02d}:{s:05.2f}"

lines, t = [], 0.0
for sc in spec["scenes"]:
    text = sc["narration"]
    for a, b in spec["caption_replace"].items():
        text = text.replace(a, b)
    chunks = []
    for sent in re.split(r"(?<=[.?!])\s+", text):
        if len(sent) > 40 and ", " in sent:
            half = min((i for i, ch in enumerate(sent) if ch == ","), key=lambda i: abs(i - len(sent) / 2))
            chunks += [sent[:half + 1], sent[half + 2:]]
        else:
            chunks.append(sent)
    total_chars = sum(len(c) for c in chunks); s0 = t + 0.15
    for c in chunks:
        d = dur[sc["id"]] * len(c) / total_chars
        lines.append(f"Dialogue: 0,{ts(s0)},{ts(s0 + d - 0.05)},Cap,,0,0,0,,{c}")
        s0 += d
    t += T[sc["id"]]
(work / "captions.ass").write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{FONT},46,&H00FFFFFF,&H00FFFFFF,&H1F3A200B,&H1F3A200B,1,0,0,0,100,100,0,0,3,14,0,2,160,160,54,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(lines) + "\n", encoding="utf-8")

# ── 이어 붙이고 자막을 입힌다 ──
(work / "list.txt").write_text("".join(f"file '{work / 'clips' / (n + '.mp4')}'\n" for n, _, _ in plan))
sh("ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(work / "list.txt"), "-c", "copy", str(work / "video.mp4"))
final = work / "Redline-demo.mp4"
sh("ffmpeg", "-v", "error", "-y", "-i", str(work / "video.mp4"), "-i", str(work / "audio.m4a"), "-vf", f"ass={work / 'captions.ass'}",
   "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", "-shortest", str(final))
print("총 길이", round(t, 1), "초 →", final)
