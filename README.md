# GTA 6 Clipper — Shorts Creator & Rendering Pipeline

Automated AI clipping, transcription, subtitle styling, and vertical 9:16 video rendering pipeline for GTA 5 & GTA 6 gameplay stream recordings.

---

## ✨ Features

- **Shorts Wizard Web UI**: Intuitive 4-step wizard workflow running on `http://127.0.0.1:5000` (`clip-desk.html`).
- **Interactive 9:16 Shorts Framing**: Draggable green crop frame with left/right arrows across the 16:9 source monitor to position the gameplay crop.
- **Draggable Live Divider**: Real-time adjustable divider directly on the Live 9:16 Phone Preview to manage the ratio of facecam vs gameplay (`B_TOP_PCT`).
- **Flexible Facecam Options**: Support for cropping facecam from the stream corner OR uploading/selecting separate dedicated webcam video recordings (global or per-clip).
- **Single & Batch Export**: Render or edit any specific clip individually in Step 4 with inline preview & download, or batch render all selected shorts.
- **Word-Level Subtitles & Meme Headlines**: Faster-Whisper speech-to-text with karaoke highlight effects (`make_ass.py`) and bold Anton meme titles.

---

## 🚀 Quick Start

### 1. Requirements
- Python 3.10+
- FFmpeg & FFprobe (installed and available in `PATH`)
- Git Bash (for Windows) or standard bash (macOS/Linux)

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Start the Web UI
```bash
# Windows
run.bat

# Linux / macOS
chmod +x run.sh
./run.sh

# Or directly with Python
python server.py
```
Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your web browser.

---

## 🛠️ CLI Pipeline Workflow

```bash
# 1. Transcribe audio from clip
python transcribe.py clips/clip_01.mp4 out/clip_01.words.json --lang hi --romanize

# 2. Generate styled ASS subtitles
# Layout A (blurred background)
python make_ass.py out/clip_01.words.json out/clip_01_A.ass --layout A

# Layout B (split screen with headline)
python make_ass.py out/clip_01.words.json out/clip_01_B.ass --layout B --upper

# 3. Render 1080x1920 MP4 short
./render.sh -i clips/clip_01.mp4 -s 0 -e 30 -l B -o out/clip_01_B.mp4 -a out/clip_01_B.ass -t "BRO EJECTED AT LAST SECOND"
```

To render with a separate webcam video:
```bash
./render.sh -i clips/clip_01.mp4 -s 0 -e 30 -l B -o out/clip_01_B.mp4 -a out/clip_01_B.ass -t "BRO EJECTED AT LAST SECOND" -f input/facecam.mp4
```

---

## ⚙️ Configuration

Tune parameters in `config.env` or visually via the **⚙️ Advanced Settings** modal inside the web interface:
- `SRC_W`, `SRC_H`: Stream video dimensions.
- `FACE_X`, `FACE_Y`, `FACE_W`, `FACE_H`: Stream corner crop bounding box.
- `B_TOP_PCT`: Height share percentage of facecam (default: 35–45%).
- `B_DIV_COLOR`: Divider line hex color.
- `B_MAIN_X`: Horizontal crop offset for gameplay (`auto` for center).
- `ENCODER`: `libx264` (CPU) or `h264_qsv` (Intel QuickSync hardware acceleration).
