# Paste this into Gemini CLI (run it from the gta-clips folder, with kimo already added as an MCP server)

You are my GTA clip-finding agent. Work ONLY with (a) the kimo MCP tools and (b) ffmpeg/ffprobe through the shell. Never invent a tool name, a flag or a timestamp.

## Step 0 - tool check (do this first)
List every kimo MCP tool you can see (name + one-line purpose). If kimo is not connected, say so and STOP.

## Input
`./input/sample.mp4` - a ~5 minute stream recording of GTA 5 / GTA 6 gameplay. The streamer's facecam is part of the frame. Speech is Hindi / Hinglish / English.

## Task
1. Probe the file (duration, resolution, fps, audio track) with ffprobe and print it.
2. Analyse the video. If you cannot read video directly, extract frames at 1 fps (small size) plus the audio, analyse those, and tell me which method you used.
3. Find candidate moments. Rank high:
   - GTA events: chases, crashes, explosions, shootouts, police escapes, stunts, ragdoll / physics fails, glitches, near misses, unexpected kills.
   - Streamer reaction: shouting, laughing, shock, rage, funny one-liners, running jokes.
   - Best = a GTA event and a strong reaction overlapping.
   Reject: menus, loading screens, idle driving, long silence, setup with no payoff.
4. Pick the 2-3 best clips (this is a sample; later I need 3-5 per week). Each clip 20-45 s. Start 1-2 s BEFORE the action, end after the payoff/reaction, never cut mid-sentence.
5. Cut each clip with ffmpeg (re-encode, frame-accurate) to `./clips/clip_01.mp4`, `clip_02.mp4`, ... Never modify the original.
6. Write `./clips/clips.json` with EXACTLY this schema:
```json
[{
  "file": "clip_01.mp4",
  "start_sec": 0.0,
  "end_sec": 0.0,
  "game": "GTA5 | GTA6 | unknown",
  "event_type": "chase | crash | shootout | stunt | glitch | funny_reaction | other",
  "why": "one sentence",
  "hook_en": "3-6 word meme-style English hook, no hashtags, no spoilers of the payoff",
  "title": "<=60 chars",
  "description": "<=150 chars",
  "speech_language": "hi | en | hinglish | none",
  "music_audible": true,
  "confidence": 0.0
}]
```
7. `music_audible` must be true if game radio, licensed music or the streamer's background music is audible (copyright / YPP risk). Say which clips are risky.

## Rules
- If you are unsure of a timestamp, say so and set a low `confidence`. Do not guess.
- If kimo cannot do something, tell me plainly and tell me what you did instead. No silent workarounds.
- No uploading, no deleting, no touching anything outside ./clips.
- End with a short report: tools used, anything that failed, anything you could not verify.
