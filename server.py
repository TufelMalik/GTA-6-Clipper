import os
import sys
import re
import json
import uuid
import shutil
import threading
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

BASE_DIR = Path(__file__).parent.resolve()
CONFIG_ENV_PATH = BASE_DIR / "config.env"
UI_SETTINGS_PATH = BASE_DIR / "ui_settings.json"
CLIPS_DIR = BASE_DIR / "clips"
OUT_DIR = BASE_DIR / "out"
INPUTS_DIR = BASE_DIR / "input"

CLIPS_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)
INPUTS_DIR.mkdir(parents=True, exist_ok=True)

UI_KEYS = {"LAYOUT", "CAP_STYLE", "CAP_CHUNK", "CAP_HL", "CAP_UPPER"}
DEFAULT_UI_SETTINGS: Dict[str, Any] = {
    "LAYOUT": "B",
    "CAP_STYLE": "auto",
    "CAP_CHUNK": 3,
    "CAP_HL": "#ffe600",
    "CAP_UPPER": "no"
}

# In-memory jobs tracking
jobs_lock = threading.Lock()
jobs: Dict[str, Dict[str, Any]] = {}

app = FastAPI(title="GTA Clip Desk Server")


def hex_to_bbggrr(color_str: str) -> str:
    color_str = str(color_str).strip().lstrip("#")
    if color_str.lower().startswith("0x"):
        color_str = color_str[2:]
    if len(color_str) == 6:
        r = color_str[0:2]
        g = color_str[2:4]
        b = color_str[4:6]
        return f"{b}{g}{r}".upper()
    if color_str.lower() == "yellow":
        return "00FFFF"
    return color_str.upper()


def get_bash_executable() -> str:
    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
            shutil.which("bash"),
        ]
        for c in candidates:
            if c and os.path.isfile(c):
                try:
                    res = subprocess.run([c, "--version"], capture_output=True, text=True, timeout=2)
                    if res.returncode == 0 and "GNU bash" in res.stdout:
                        return c
                except Exception:
                    continue
        try:
            where_out = subprocess.check_output(["where.exe", "bash"], text=True).splitlines()
            for line in where_out:
                p = line.strip()
                if "Git" in p and os.path.isfile(p):
                    return p
        except Exception:
            pass

    bash = shutil.which("bash")
    if bash:
        return bash
    raise RuntimeError("Bash executable not found. Please install Git Bash.")


def validate_clip_filename(filename: str) -> str:
    if not filename or not isinstance(filename, str):
        raise ValueError("Invalid clip filename")
    clean = filename.strip()
    if ".." in clean or clean.startswith("/") or clean.startswith("\\") or ":" in clean:
        raise ValueError(f"Path traversal detected in filename: {filename}")
    if not re.match(r'^[a-zA-Z0-9_\-\.]+$', clean):
        raise ValueError(f"Invalid characters in filename: {filename}")
    clips_dir = CLIPS_DIR.resolve()
    target = (clips_dir / clean).resolve()
    if not target.is_relative_to(clips_dir):
        raise ValueError(f"Path traversal detected in filename: {filename}")
    return clean


def validate_source_path(source: str) -> str:
    if not source or not isinstance(source, str):
        raise ValueError("Invalid source path")
    clean = source.strip().replace("\\", "/")
    if ".." in clean or clean.startswith("/") or ":" in clean:
        raise ValueError(f"Path traversal detected in source path: {source}")
    target = (BASE_DIR / clean).resolve()
    if not target.is_relative_to(BASE_DIR):
        raise ValueError(f"Path traversal detected in source path: {source}")
    return clean


def read_config_env() -> Dict[str, Any]:
    conf: Dict[str, Any] = {}
    if not CONFIG_ENV_PATH.exists():
        return conf
    content = CONFIG_ENV_PATH.read_text(encoding="utf-8")
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, rest = line.split("=", 1)
            key = key.strip()
            parts = re.split(r'\s+#', rest, maxsplit=1)
            val = parts[0].strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            if key == "B_DIV_COLOR":
                if val.lower().startswith("0x"):
                    val = "#" + val[2:]
                elif val.lower() == "yellow":
                    val = "#ffd23f"
            else:
                if val.isdigit() or (val.startswith("-") and val[1:].isdigit()):
                    val = int(val)
                else:
                    try:
                        val = float(val)
                    except ValueError:
                        pass
            conf[key] = val
    return conf


def write_config_env(updates: Dict[str, Any]):
    lines: List[str] = []
    if CONFIG_ENV_PATH.exists():
        lines = CONFIG_ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True)

    updated_lines: List[str] = []
    written_keys = set()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            updated_lines.append(line)
            continue

        k, rest = line.split("=", 1)
        k = k.strip()
        if k in updates:
            written_keys.add(k)
            new_val = str(updates[k])
            if k == "B_DIV_COLOR":
                if new_val.startswith("#"):
                    new_val = "0x" + new_val[1:]

            match = re.search(r'(\s+#.*)$', rest.rstrip('\r\n'))
            comment_part = match.group(1) if match else ""
            newline = "\r\n" if line.endswith("\r\n") else "\n"
            updated_lines.append(f"{k}={new_val}{comment_part}{newline}")
        else:
            updated_lines.append(line)

    for k, v in updates.items():
        if k not in written_keys and k not in UI_KEYS:
            new_val = str(v)
            if k == "B_DIV_COLOR" and new_val.startswith("#"):
                new_val = "0x" + new_val[1:]
            updated_lines.append(f"{k}={new_val}\n")

    CONFIG_ENV_PATH.write_text("".join(updated_lines), encoding="utf-8")


def load_ui_settings() -> Dict[str, Any]:
    if UI_SETTINGS_PATH.exists():
        try:
            data = json.loads(UI_SETTINGS_PATH.read_text(encoding="utf-8"))
            merged = dict(DEFAULT_UI_SETTINGS)
            merged.update(data)
            return merged
        except Exception:
            pass
    return dict(DEFAULT_UI_SETTINGS)


def save_ui_settings(updates: Dict[str, Any]):
    current = load_ui_settings()
    for k in UI_KEYS:
        if k in updates:
            current[k] = updates[k]
    UI_SETTINGS_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")


def get_clip_duration(clip_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(clip_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not res.stdout.strip():
        raise RuntimeError(f"ffprobe failed for {clip_path}: {res.stderr}")
    return float(res.stdout.strip())


def run_command_logged(cmd: List[str], job: Dict[str, Any], cwd: Path = BASE_DIR) -> int:
    with jobs_lock:
        job["log"].append(f"$ {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(cwd),
            encoding="utf-8",
            errors="replace"
        )
        if proc.stdout:
            for line in proc.stdout:
                line_str = line.rstrip("\r\n")
                if line_str:
                    with jobs_lock:
                        job["log"].append(line_str)
        proc.wait()
        return proc.returncode
    except Exception as e:
        with jobs_lock:
            job["log"].append(f"! Subprocess execution error: {e}")
        return 1


def run_job_thread(job_id: str, job_type: str, payload: Dict[str, Any]):
    with jobs_lock:
        job = jobs[job_id]

    try:
        if job_type == "find_clips":
            source_raw = payload.get("source", "input/sample.mp4")
            source_val = validate_source_path(source_raw)
            gemini_prompt_path = BASE_DIR / "GEMINI_PROMPT.md"
            if not gemini_prompt_path.exists():
                with jobs_lock:
                    job["log"].append("! GEMINI_PROMPT.md not found")
                    job["status"] = "error"
                return

            prompt_text = gemini_prompt_path.read_text(encoding="utf-8")
            prompt_text = prompt_text.replace("./input/sample.mp4", source_val)
            prompt_text += "\nkimo is unavailable; use ffmpeg/ffprobe and local Python only.\n"

            gemini_cmd = shutil.which("gemini") or "gemini"
            with jobs_lock:
                job["log"].append(f"Running gemini search on {source_val}...")

            ret = run_command_logged([gemini_cmd, "-p", prompt_text], job)
            clips_json_path = CLIPS_DIR / "clips.json"
            with jobs_lock:
                if ret == 0 and clips_json_path.exists():
                    job["outputs"].append("clips/clips.json")
                    job["status"] = "done"
                else:
                    if ret != 0:
                        job["log"].append(f"! Gemini CLI exited with status code {ret}.")
                    if not clips_json_path.exists():
                        job["log"].append("! clips.json was not generated.")
                    job["status"] = "error"

        elif job_type == "transcribe":
            clips = payload.get("clips", [])
            if not clips:
                with jobs_lock:
                    job["log"].append("! No clips provided to transcribe")
                    job["status"] = "error"
                return

            OUT_DIR.mkdir(parents=True, exist_ok=True)
            for item in clips:
                filename = validate_clip_filename(item.get("file", ""))
                clip_path = CLIPS_DIR / filename
                if not clip_path.exists():
                    with jobs_lock:
                        job["log"].append(f"! Clip not found: clips/{filename}")
                        job["status"] = "error"
                    return

                stem = clip_path.stem
                words_file = f"out/{stem}.words.json"
                with jobs_lock:
                    job["log"].append(f"Transcribing clips/{filename} -> {words_file}...")

                cmd = [
                    sys.executable,
                    "transcribe.py",
                    f"clips/{filename}",
                    words_file,
                    "--lang", "hi",
                    "--romanize"
                ]
                ret = run_command_logged(cmd, job)
                if ret != 0 or not (BASE_DIR / words_file).exists():
                    with jobs_lock:
                        job["log"].append(f"! Transcription failed for {filename}")
                        job["status"] = "error"
                    return
                with jobs_lock:
                    job["outputs"].append(words_file)

            with jobs_lock:
                job["status"] = "done"

        elif job_type == "render":
            clips = payload.get("clips", [])
            if not clips:
                single = payload.get("clip")
                if single and isinstance(single, dict):
                    clips = [single]
            if not clips:
                with jobs_lock:
                    job["log"].append("! No clips provided to render")
                    job["status"] = "error"
                return

            OUT_DIR.mkdir(parents=True, exist_ok=True)
            ui_settings = load_ui_settings()
            cap_style = ui_settings.get("CAP_STYLE", "auto")
            cap_chunk = ui_settings.get("CAP_CHUNK", 3)
            cap_hl = ui_settings.get("CAP_HL", "#ffe600")
            cap_upper = ui_settings.get("CAP_UPPER", "no")
            hl_bbggrr = hex_to_bbggrr(cap_hl)
            bash_exe = get_bash_executable()

            for item in clips:
                filename = validate_clip_filename(item.get("file", ""))
                clip_path = CLIPS_DIR / filename
                if not clip_path.exists():
                    with jobs_lock:
                        job["log"].append(f"! Clip not found: clips/{filename}")
                        job["status"] = "error"
                    return

                stem = clip_path.stem
                layout = item.get("layout") or ui_settings.get("LAYOUT") or "B"
                if layout not in ("A", "B"):
                    layout = "B"

                words_file = f"out/{stem}.words.json"
                words_path = BASE_DIR / words_file
                if not words_path.exists():
                    with jobs_lock:
                        job["log"].append(f"words.json missing for {filename}, running transcribe first...")
                    transcribe_cmd = [
                        sys.executable,
                        "transcribe.py",
                        f"clips/{filename}",
                        words_file,
                        "--lang", "hi",
                        "--romanize"
                    ]
                    ret = run_command_logged(transcribe_cmd, job)
                    if ret != 0 or not words_path.exists():
                        with jobs_lock:
                            job["log"].append(f"! Transcription failed for {filename}")
                            job["status"] = "error"
                        return

                ass_file = f"out/{stem}_{layout}.ass"
                ass_path = BASE_DIR / ass_file
                with jobs_lock:
                    job["log"].append(f"Generating ASS subtitles for {filename} -> {ass_file}...")

                make_ass_cmd = [
                    sys.executable,
                    "make_ass.py",
                    words_file,
                    ass_file,
                    "--layout", layout,
                    "--chunk", str(cap_chunk),
                    "--hl", hl_bbggrr
                ]
                if cap_style and cap_style != "auto" and cap_style in ("karaoke", "meme"):
                    make_ass_cmd.extend(["--style", cap_style])
                if str(cap_upper).lower() in ("yes", "true", "1"):
                    make_ass_cmd.append("--upper")

                ret = run_command_logged(make_ass_cmd, job)
                if ret != 0 or not ass_path.exists():
                    with jobs_lock:
                        job["log"].append(f"! Subtitle generation failed for {filename}")
                        job["status"] = "error"
                    return

                try:
                    duration = get_clip_duration(clip_path)
                except Exception as e:
                    with jobs_lock:
                        job["log"].append(f"! Failed to get duration from ffprobe: {e}")
                        job["status"] = "error"
                    return
                duration_str = str(round(duration, 2))

                out_mp4 = f"out/{stem}_{layout}.mp4"
                hook = (item.get("hook") or "").strip()

                # Optional separate facecam video file
                facecam_raw = (item.get("facecam") or payload.get("facecam") or "").strip()
                facecam_rel = None
                if facecam_raw and facecam_raw != "__crop__":
                    clean_fc = facecam_raw.replace("\\", "/")
                    if ".." not in clean_fc and not clean_fc.startswith("/"):
                        candidates = [
                            BASE_DIR / clean_fc,
                            INPUTS_DIR / clean_fc,
                            CLIPS_DIR / clean_fc,
                            INPUTS_DIR / Path(clean_fc).name
                        ]
                        for cand in candidates:
                            if cand.exists() and cand.is_file():
                                facecam_rel = str(cand.resolve().relative_to(BASE_DIR)).replace("\\", "/")
                                break
                    if facecam_rel:
                        with jobs_lock:
                            job["log"].append(f"Using separate facecam: {facecam_rel}")
                    else:
                        with jobs_lock:
                            job["log"].append(f"! Note: Separate facecam not found: {facecam_raw}, falling back to stream crop.")

                with jobs_lock:
                    job["log"].append(f"Rendering clips/{filename} -> {out_mp4} (layout={layout}, duration={duration_str}s)...")

                render_cmd = [
                    bash_exe,
                    "./render.sh",
                    "-i", f"clips/{filename}",
                    "-s", "0",
                    "-e", duration_str,
                    "-l", layout,
                    "-o", out_mp4,
                    "-a", ass_file
                ]
                if hook:
                    render_cmd.extend(["-t", hook])
                if facecam_rel:
                    render_cmd.extend(["-f", facecam_rel])
                
                # Optional facecam framing crop
                facecam_crop = (item.get("facecam_crop") or payload.get("facecam_crop") or "").strip()
                if facecam_crop:
                    clean_crop = facecam_crop.replace("crop=", "").strip()
                    if re.match(r'^\d+:\d+:\d+:\d+$', clean_crop):
                        render_cmd.extend(["-c", clean_crop])
                        with jobs_lock:
                            job["log"].append(f"Applying facecam framing crop: {clean_crop}")

                # Optional facecam scaling fit mode (stretch, fit/contain, cover/fill)
                facecam_fit = (item.get("facecam_fit") or payload.get("facecam_fit") or "").strip().lower()
                if facecam_fit in ("cover", "fit", "stretch", "contain", "fill"):
                    render_cmd.extend(["-m", facecam_fit])
                    with jobs_lock:
                        job["log"].append(f"Applying facecam scaling mode: {facecam_fit}")

                # Optional custom facecam height percentage (for Layout B)
                b_top_pct = item.get("b_top_pct") or payload.get("b_top_pct")
                if b_top_pct:
                    try:
                        top_pct_val = int(b_top_pct)
                        if 10 <= top_pct_val <= 85:
                            render_cmd.extend(["-p", str(top_pct_val)])
                            with jobs_lock:
                                job["log"].append(f"Applying custom facecam height: {top_pct_val}%")
                    except (ValueError, TypeError):
                        pass

                ret = run_command_logged(render_cmd, job)
                if ret != 0 or not (BASE_DIR / out_mp4).exists():
                    with jobs_lock:
                        job["log"].append(f"! Render failed for {filename}")
                        job["status"] = "error"
                    return
                with jobs_lock:
                    job["outputs"].append(out_mp4)

            with jobs_lock:
                job["status"] = "done"

        else:
            with jobs_lock:
                job["log"].append(f"! Unknown job type: {job_type}")
                job["status"] = "error"

    except Exception as e:
        with jobs_lock:
            job["log"].append(f"! Unexpected error: {e}")
            job["status"] = "error"


@app.get("/")
def get_index():
    index_file = BASE_DIR / "clip-desk.html"
    if not index_file.exists():
        return PlainTextResponse("clip-desk.html not found", status_code=404)
    return FileResponse(str(index_file), media_type="text/html")


@app.api_route("/favicon.ico", methods=["GET", "HEAD"])
def get_favicon_ico():
    ico = BASE_DIR / "favicon.ico"
    if ico.exists():
        return FileResponse(str(ico), media_type="image/x-icon")
    svg = BASE_DIR / "favicon.svg"
    if svg.exists():
        return FileResponse(str(svg), media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="Favicon not found")


@app.api_route("/favicon.svg", methods=["GET", "HEAD"])
def get_favicon_svg():
    svg = BASE_DIR / "favicon.svg"
    if svg.exists():
        return FileResponse(str(svg), media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="Favicon not found")


@app.get("/api/inputs")
def get_inputs():
    res = []
    if INPUTS_DIR.exists():
        for p in sorted(INPUTS_DIR.iterdir()):
            if p.is_file() and p.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov", ".avi"):
                res.append({
                    "path": f"input/{p.name}",
                    "name": p.name,
                    "size_mb": round(p.stat().st_size / (1024 * 1024), 1)
                })
    return res


@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    filename = Path(file.filename).name
    clean_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', filename)
    target_path = INPUTS_DIR / clean_name
    with open(target_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {
        "ok": True,
        "path": f"input/{clean_name}",
        "name": clean_name,
        "size_mb": round(target_path.stat().st_size / (1024 * 1024), 1)
    }


@app.api_route("/api/frame", methods=["GET", "HEAD"])
def get_frame(source: str = "input/sample.mp4"):
    try:
        source_val = validate_source_path(source)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    source_path = BASE_DIR / source_val
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="Source video not found")

    thumb_name = f"preview_{re.sub(r'[^a-zA-Z0-9_]', '_', source_val)}.jpg"
    thumb_path = OUT_DIR / thumb_name

    if not thumb_path.exists() or thumb_path.stat().st_mtime < source_path.stat().st_mtime:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", "3", "-i", str(source_path),
            "-vframes", "1", "-q:v", "2", str(thumb_path)
        ]
        try:
            subprocess.run(cmd, check=True, timeout=10)
        except Exception:
            cmd[6] = "0"
            subprocess.run(cmd, check=True, timeout=10)

    return FileResponse(str(thumb_path), media_type="image/jpeg")


app.mount("/out", StaticFiles(directory=str(OUT_DIR)), name="out")
app.mount("/clips", StaticFiles(directory=str(CLIPS_DIR)), name="clips")


@app.get("/api/health")
def get_health():
    return {"ok": True}


@app.get("/api/config")
def get_config():
    conf = read_config_env()
    ui_conf = load_ui_settings()
    conf.update(ui_conf)
    return conf


@app.post("/api/config")
async def post_config(request: Request):
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid config payload")

    save_ui_settings(data)
    env_updates = {k: v for k, v in data.items() if k not in UI_KEYS}
    write_config_env(env_updates)
    return {"ok": True}


@app.get("/api/clips")
def get_clips():
    clips_json_path = CLIPS_DIR / "clips.json"
    if clips_json_path.exists():
        try:
            return json.loads(clips_json_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to parse clips.json: {e}")
    return []


@app.post("/api/clips")
async def post_clips(request: Request):
    data = await request.json()
    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="Expected list of clips")
    clips_json_path = CLIPS_DIR / "clips.json"
    clips_json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"ok": True, "count": len(data)}


@app.post("/api/jobs")
async def create_job(request: Request):
    data = await request.json()
    job_type = data.get("type")
    if job_type not in ("find_clips", "transcribe", "render"):
        raise HTTPException(status_code=400, detail=f"Invalid job type: {job_type}")

    job_id = str(uuid.uuid4())[:8]
    with jobs_lock:
        jobs[job_id] = {
            "status": "running",
            "log": [],
            "outputs": []
        }

    t = threading.Thread(target=run_job_thread, args=(job_id, job_type, data), daemon=True)
    t.start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return {
            "status": job["status"],
            "log": list(job["log"]),
            "outputs": list(job["outputs"])
        }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5000, log_level="info")
