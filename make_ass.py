#!/usr/bin/env python3
"""words.json -> word-by-word ASS captions.
words.json: [{"w":"nikalna","s":1.23,"e":1.60}, ...]  (seconds, relative to CLIP start)
Styles: karaoke = current word highlighted (Layout A) | meme = big chunky text, thick outline (Layout B)
Usage: make_ass.py words.json out.ass --layout A|B [--style karaoke|meme] [--chunk 3] [--upper] [--font Anton]
"""
import argparse, json

p = argparse.ArgumentParser()
p.add_argument("words"); p.add_argument("out")
p.add_argument("--layout", default="A", choices=["A", "B"])
p.add_argument("--style", choices=["karaoke", "meme"])
p.add_argument("--chunk", type=int, default=0)
p.add_argument("--upper", action="store_true")
p.add_argument("--font", default="Anton")
p.add_argument("--top-pct", type=int, default=30)
p.add_argument("--hl", default="00FFFF", help="highlight colour BBGGRR (default yellow)")
a = p.parse_args()

W, H = 1080, 1920
style = a.style or ("karaoke" if a.layout == "A" else "meme")
chunk = a.chunk or (3 if style == "karaoke" else 2)
if style == "karaoke":
    size, outline = 84, 7
else:
    size, outline = 120, 10
if a.layout == "A":
    align, mv = 2, 560                      # bottom-center, 560px up from bottom (inside the center panel)
else:
    align, mv = 8, int(H * a.top_pct / 100) + 70   # top-center, just below the divider

def ts(t):
    t = max(t, 0); h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"

words = json.load(open(a.words, encoding="utf-8"))
for x in words:
    x["w"] = x["w"].strip().replace("{", "(").replace("}", ")")
    if a.upper: x["w"] = x["w"].upper()
words = [x for x in words if x["w"]]

head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Main,{a.font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{outline},0,{align},60,60,{mv},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
ev = []
for i in range(0, len(words), chunk):
    grp = words[i:i + chunk]
    nxt = words[i + chunk]["s"] if i + chunk < len(words) else None
    for j, cur in enumerate(grp):
        start = cur["s"]
        if j + 1 < len(grp): end = grp[j + 1]["s"]
        else:
            end = cur["e"] + 0.20
            if nxt is not None: end = min(end, nxt)
        if end <= start: end = start + 0.12
        parts = []
        for k, wd in enumerate(grp):
            if style == "karaoke" and k == j:
                parts.append(f"{{\\c&H{a.hl}&\\fscx112\\fscy112}}{wd['w']}{{\\r}}")
            else:
                parts.append(wd["w"])
        ev.append(f"Dialogue: 0,{ts(start)},{ts(end)},Main,,0,0,0,,{' '.join(parts)}")
open(a.out, "w", encoding="utf-8").write(head + "\n".join(ev) + "\n")
print(f"{len(ev)} caption events -> {a.out}")
