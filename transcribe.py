#!/usr/bin/env python3
"""clip.mp4 -> words.json (word timestamps, CPU). Optional Hinglish romanisation via Gemini CLI.
pip install faster-whisper
Usage: transcribe.py clip.mp4 words.json --lang hi [--model small|medium] [--romanize]
"""
import argparse, json, re, subprocess
import av
_orig_av_open = av.open
def _compat_av_open(*args, **kwargs):
    kwargs.pop("metadata_errors", None)
    return _orig_av_open(*args, **kwargs)
av.open = _compat_av_open

from faster_whisper import WhisperModel

p = argparse.ArgumentParser()
p.add_argument("clip"); p.add_argument("out")
p.add_argument("--lang", default="hi")
p.add_argument("--model", default="small")
p.add_argument("--romanize", action="store_true", help="Devanagari -> Roman Hinglish, same word count (uses `gemini -p`)")
a = p.parse_args()

model = WhisperModel(a.model, device="cpu", compute_type="int8")
segs, _ = model.transcribe(a.clip, language=a.lang, word_timestamps=True, vad_filter=True)
words = [{"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)}
         for s in segs for w in s.words if w.word.strip()]

if a.romanize and words:
    prompt = ("Transliterate each item of this JSON array into natural Roman-script Hinglish "
              "(how an Indian would type it on WhatsApp, e.g. 'निकालना' -> 'nikalna'). Keep English words as they are. "
              "Return ONLY a JSON array of strings with EXACTLY the same number of items, same order.\n"
              + json.dumps([x["w"] for x in words], ensure_ascii=False))
    r = subprocess.run(["gemini", "-p", prompt], capture_output=True, text=True)
    m = re.search(r"\[.*\]", r.stdout, re.S)
    try:
        rom = json.loads(m.group(0))
        if len(rom) == len(words):
            for x, t in zip(words, rom): x["w"] = str(t)
        else: print(f"! romanise length mismatch ({len(rom)} vs {len(words)}), keeping original")
    except Exception as e:
        print("! romanise failed, keeping original:", e)

json.dump(words, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"{len(words)} words -> {a.out}")
