import os
import sys
import re
import json
import uuid
import shutil
import threading
import subprocess
import io
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

BASE_DIR = Path(__file__).parent.resolve()
CONFIG_ENV_PATH = BASE_DIR / "config.env"
UI_SETTINGS_PATH = BASE_DIR / "ui_settings.json"
CLIPS_DIR = BASE_DIR / "clips"
OUT_DIR = BASE_DIR / "out"
INPUTS_DIR = BASE_DIR / "input"
PROJECTS_DIR = BASE_DIR / "projects"
PROJECTS_INDEX_PATH = PROJECTS_DIR / "projects.json"

CLIPS_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)
INPUTS_DIR.mkdir(parents=True, exist_ok=True)
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

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


def get_project_dir(project_id: str) -> Path:
    safe_id = re.sub(r'[^a-zA-Z0-9_\-]', '', project_id)
    if not safe_id:
        safe_id = "default"
    p = PROJECTS_DIR / safe_id
    p.mkdir(parents=True, exist_ok=True)
    (p / "out").mkdir(parents=True, exist_ok=True)
    return p


def load_projects_index() -> List[Dict[str, Any]]:
    if not PROJECTS_INDEX_PATH.exists():
        default_proj = {
            "id": "default",
            "name": "GTA 6 - Fighter Jet (Default)",
            "source": "input/sample.webm",
            "created_at": time.time(),
            "updated_at": time.time(),
            "step": 4
        }
        # Migrate existing root clips.json if present
        p_dir = get_project_dir("default")
        root_clips = CLIPS_DIR / "clips.json"
        p_clips = p_dir / "clips.json"
        if root_clips.exists() and not p_clips.exists():
            try:
                shutil.copy2(root_clips, p_clips)
            except Exception:
                pass

        # Migrate existing out/ files if present
        p_out = p_dir / "out"
        if OUT_DIR.exists():
            for f in OUT_DIR.glob("*"):
                if f.is_file() and not (p_out / f.name).exists():
                    try:
                        shutil.copy2(f, p_out / f.name)
                    except Exception:
                        pass

        index = [default_proj]
        save_projects_index(index)
        return index

    try:
        data = json.loads(PROJECTS_INDEX_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def save_projects_index(index: List[Dict[str, Any]]) -> None:
    PROJECTS_INDEX_PATH.write_text(json.dumps(index, indent=2), encoding="utf-8")


def update_project_meta(project_id: str, **kwargs) -> Optional[Dict[str, Any]]:
    index = load_projects_index()
    for p in index:
        if p["id"] == project_id:
            for k, v in kwargs.items():
                p[k] = v
            p["updated_at"] = time.time()
            save_projects_index(index)
            return p
    return None


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

    project_id = (payload.get("project") or "").strip()
    if project_id:
        p_dir = get_project_dir(project_id)
        proj_out_dir = p_dir / "out"
        proj_out_dir.mkdir(parents=True, exist_ok=True)
    else:
        proj_out_dir = OUT_DIR

    try:
        if job_type == "find_clips":
            source_raw = payload.get("source", "input/sample.mp4")
            source_val = validate_source_path(source_raw)
            gemini_prompt_path = BASE_DIR / "GEMINI_PROMPT.md"
            
            prompt_text = ""
            if gemini_prompt_path.exists():
                prompt_text = gemini_prompt_path.read_text(encoding="utf-8")
                prompt_text = prompt_text.replace("./input/sample.mp4", source_val)
                prompt_text += "\nkimo is unavailable; use ffmpeg/ffprobe and local Python only.\n"

            # Check if Gemini CLI is installed and whether an API key is available
            gemini_cmd = shutil.which("gemini")
            cfg = read_config_env()
            if cfg.get("GEMINI_API_KEY") and "GEMINI_API_KEY" not in os.environ:
                os.environ["GEMINI_API_KEY"] = cfg["GEMINI_API_KEY"]

            ret = 1
            has_api_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") or os.environ.get("GOOGLE_GENAI_USE_GCA"))

            if gemini_cmd and prompt_text and has_api_key:
                with jobs_lock:
                    job["log"].append(f"Running gemini search on {source_val}...")
                ret = run_command_logged([gemini_cmd, "-p", prompt_text], job)

            clips_json_path = CLIPS_DIR / "clips.json"

            # Fallback to local action scanner if Gemini CLI is missing, unauthenticated, or failed
            if ret != 0 or not clips_json_path.exists():
                with jobs_lock:
                    if gemini_cmd and not has_api_key:
                        job["log"].append("ℹ️ Gemini CLI requires auth (GEMINI_API_KEY). Running Local Video Action Scanner (ffmpeg/ffprobe)...")
                    else:
                        job["log"].append("⚠️ Gemini CLI exited or failed. Running Local Video Action Scanner (ffmpeg/ffprobe)...")

                local_scanner_script = BASE_DIR / "find_clips_local.py"
                local_args = [sys.executable, str(local_scanner_script), source_val]
                if project_id:
                    local_args.append(project_id)

                ret_local = run_command_logged(local_args, job)
                if ret_local == 0 and clips_json_path.exists():
                    ret = 0

            if project_id and ret == 0 and clips_json_path.exists():
                p_dir = get_project_dir(project_id)
                shutil.copy2(clips_json_path, p_dir / "clips.json")
                clips_json_path = p_dir / "clips.json"

            with jobs_lock:
                if ret == 0 and clips_json_path.exists():
                    out_rel = f"projects/{project_id}/clips.json" if project_id else "clips/clips.json"
                    job["outputs"].append(out_rel)
                    job["status"] = "done"
                else:
                    if ret != 0:
                        job["log"].append(f"! Clip scanner exited with status code {ret}.")
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
                words_file = f"projects/{project_id}/out/{stem}.words.json" if project_id else f"out/{stem}.words.json"
                (BASE_DIR / words_file).parent.mkdir(parents=True, exist_ok=True)
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

                words_file = f"projects/{project_id}/out/{stem}.words.json" if project_id else f"out/{stem}.words.json"
                words_path = BASE_DIR / words_file
                (BASE_DIR / words_file).parent.mkdir(parents=True, exist_ok=True)
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

                ass_file = f"projects/{project_id}/out/{stem}.ass" if project_id else f"out/{stem}.ass"
                ass_path = BASE_DIR / ass_file
                (BASE_DIR / ass_file).parent.mkdir(parents=True, exist_ok=True)
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

                out_mp4 = f"projects/{project_id}/out/{stem}.mp4" if project_id else f"out/{stem}.mp4"
                (BASE_DIR / out_mp4).parent.mkdir(parents=True, exist_ok=True)
                # Clean up legacy style-suffixed files if present
                for old_suffix in ("_B.mp4", "_A.mp4"):
                    old_f = (BASE_DIR / out_mp4).parent / f"{stem}{old_suffix}"
                    if old_f.exists():
                        try:
                            old_f.unlink()
                        except Exception:
                            pass

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

                # Optional custom gameplay crop X (for Shorts Framing)
                gameplay_x = item.get("gameplay_x") or payload.get("gameplay_x")
                if gameplay_x is not None and str(gameplay_x).strip() != "":
                    render_cmd.extend(["-x", str(gameplay_x).strip()])
                    with jobs_lock:
                        job["log"].append(f"Applying custom gameplay framing: {gameplay_x}")

                # Optional custom hook font style, color, size
                text_font = (item.get("text_font") or payload.get("text_font") or "").strip()
                if text_font:
                    render_cmd.extend(["-F", text_font])
                text_color = (item.get("text_color") or payload.get("text_color") or "").strip()
                if text_color:
                    render_cmd.extend(["-C", text_color])
                text_size = item.get("text_size") or payload.get("text_size")
                if text_size:
                    render_cmd.extend(["-S", str(text_size)])

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
app.mount("/projects", StaticFiles(directory=str(PROJECTS_DIR)), name="projects")


@app.get("/api/health")
def get_health():
    return {"ok": True}


@app.get("/api/projects")
def list_projects():
    projects = load_projects_index()
    res = []
    for p in projects:
        p_id = p["id"]
        p_dir = PROJECTS_DIR / p_id

        # Count clips
        clips_cnt = 0
        p_clips = p_dir / "clips.json"
        if p_clips.exists():
            try:
                c_data = json.loads(p_clips.read_text(encoding="utf-8"))
                if isinstance(c_data, list):
                    clips_cnt = len(c_data)
            except Exception:
                pass
        elif p_id == "default" and (CLIPS_DIR / "clips.json").exists():
            try:
                c_data = json.loads((CLIPS_DIR / "clips.json").read_text(encoding="utf-8"))
                if isinstance(c_data, list):
                    clips_cnt = len(c_data)
            except Exception:
                pass

        # Count outputs
        out_cnt = 0
        total_mb = 0.0
        p_out = p_dir / "out"
        seen_out = set()
        check_dirs = [p_out]
        if p_id == "default" and OUT_DIR.exists():
            check_dirs.append(OUT_DIR)
        for od in check_dirs:
            if od.exists():
                for f in od.glob("*.mp4"):
                    if f.name not in seen_out:
                        seen_out.add(f.name)
                        out_cnt += 1
                        try:
                            total_mb += f.stat().st_size / (1024 * 1024)
                        except Exception:
                            pass

        item = dict(p)
        item["clips_count"] = clips_cnt
        item["outputs_count"] = out_cnt
        item["total_size_mb"] = round(total_mb, 1)
        res.append(item)
    return res


@app.post("/api/projects")
async def create_project(request: Request):
    data = await request.json()
    name = (data.get("name") or "").strip()
    source = (data.get("source") or "input/sample.webm").strip()
    if not name:
        name = Path(source).stem.replace("_", " ").title() if source else "New Video Project"

    proj_id = f"proj_{int(time.time())}_{uuid.uuid4().hex[:4]}"
    p_dir = get_project_dir(proj_id)
    (p_dir / "clips.json").write_text("[]", encoding="utf-8")

    new_proj = {
        "id": proj_id,
        "name": name,
        "source": source,
        "created_at": time.time(),
        "updated_at": time.time(),
        "step": 1
    }

    projects = load_projects_index()
    projects.append(new_proj)
    save_projects_index(projects)
    return {"ok": True, "project": new_proj}


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    projects = load_projects_index()
    proj = next((p for p in projects if p["id"] == project_id), None)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    p_dir = get_project_dir(project_id)
    clips = []
    p_clips = p_dir / "clips.json"
    if p_clips.exists():
        try:
            clips = json.loads(p_clips.read_text(encoding="utf-8"))
        except Exception:
            pass
    elif project_id == "default" and (CLIPS_DIR / "clips.json").exists():
        try:
            clips = json.loads((CLIPS_DIR / "clips.json").read_text(encoding="utf-8"))
        except Exception:
            pass

    return {
        "ok": True,
        "project": proj,
        "clips": clips
    }


@app.put("/api/projects/{project_id}")
async def update_project(project_id: str, request: Request):
    data = await request.json()
    projects = load_projects_index()
    proj = next((p for p in projects if p["id"] == project_id), None)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    if "name" in data and str(data["name"]).strip():
        proj["name"] = str(data["name"]).strip()
    if "source" in data and str(data["source"]).strip():
        proj["source"] = str(data["source"]).strip()
    if "step" in data:
        try:
            proj["step"] = int(data["step"])
        except Exception:
            pass
    proj["updated_at"] = time.time()
    save_projects_index(projects)
    return {"ok": True, "project": proj}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str):
    projects = load_projects_index()
    existing = [p for p in projects if p["id"] == project_id]
    if not existing:
        raise HTTPException(status_code=404, detail="Project not found")

    p_dir = PROJECTS_DIR / project_id
    if p_dir.exists():
        try:
            shutil.rmtree(p_dir, ignore_errors=True)
        except Exception:
            pass

    updated = [p for p in projects if p["id"] != project_id]
    if not updated:
        updated = [{
            "id": f"proj_{int(time.time())}",
            "name": "New Project 1",
            "source": "input/sample.webm",
            "created_at": time.time(),
            "updated_at": time.time(),
            "step": 1
        }]

    save_projects_index(updated)
    return {"ok": True, "remaining": len(updated), "projects": updated}


@app.get("/api/projects/{project_id}/zip")
def download_project_zip(project_id: str):
    p_dir = get_project_dir(project_id)
    projects = load_projects_index()
    proj = next((p for p in projects if p["id"] == project_id), None)
    proj_name = proj["name"] if proj else project_id
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', proj_name)

    import tempfile
    temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    temp_zip_path = Path(temp_zip.name)
    temp_zip.close()

    with zipfile.ZipFile(temp_zip_path, mode="w", compression=zipfile.ZIP_STORED) as zf:
        if proj:
            zf.writestr("project.json", json.dumps(proj, indent=2))

        p_clips = p_dir / "clips.json"
        if p_clips.exists():
            zf.write(p_clips, arcname="clips.json")
        elif (CLIPS_DIR / "clips.json").exists() and project_id == "default":
            zf.write(CLIPS_DIR / "clips.json", arcname="clips.json")

        out_dirs = [p_dir / "out"]
        if project_id == "default" and OUT_DIR.exists():
            out_dirs.append(OUT_DIR)

        seen_files = set()
        for od in out_dirs:
            if od.exists():
                for f in od.glob("*"):
                    if f.is_file() and f.name not in seen_files:
                        seen_files.add(f.name)
                        zf.write(f, arcname=f"shorts/{f.name}")

        p_clip_dir = p_dir / "clips"
        if p_clip_dir.exists():
            for f in p_clip_dir.glob("*.mp4"):
                zf.write(f, arcname=f"clips/{f.name}")
        elif project_id == "default" and CLIPS_DIR.exists():
            for f in CLIPS_DIR.glob("*.mp4"):
                zf.write(f, arcname=f"clips/{f.name}")

    return FileResponse(
        str(temp_zip_path),
        media_type="application/zip",
        filename=f"{safe_name}_export.zip"
    )


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


@app.get("/api/outputs")
def get_outputs(project: Optional[str] = None):
    results = []
    dirs_to_check = []
    if project:
        p_dir = get_project_dir(project)
        dirs_to_check.append((p_dir / "out", f"projects/{project}/out"))
        if project == "default":
            dirs_to_check.append((OUT_DIR, "out"))
    else:
        dirs_to_check.append((OUT_DIR, "out"))
        p_dir = PROJECTS_DIR / "default" / "out"
        if p_dir.exists():
            dirs_to_check.append((p_dir, "projects/default/out"))

    seen_names = set()
    for d, prefix in dirs_to_check:
        if d.exists():
            for p in d.glob("*.mp4"):
                if p.name in seen_names:
                    continue
                seen_names.add(p.name)
                try:
                    st = p.stat()
                    results.append({
                        "name": p.name,
                        "path": f"{prefix}/{p.name}",
                        "size_mb": round(st.st_size / (1024 * 1024), 1),
                        "mtime": st.st_mtime
                    })
                except Exception:
                    pass
    results.sort(key=lambda x: x["mtime"], reverse=True)
    return results


@app.get("/api/clips")
def get_clips(project: Optional[str] = None):
    if project:
        p_dir = get_project_dir(project)
        p_clips = p_dir / "clips.json"
        if p_clips.exists():
            try:
                return json.loads(p_clips.read_text(encoding="utf-8"))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to parse clips.json: {e}")
        if project == "default" and (CLIPS_DIR / "clips.json").exists():
            try:
                return json.loads((CLIPS_DIR / "clips.json").read_text(encoding="utf-8"))
            except Exception:
                pass
        return []

    clips_json_path = CLIPS_DIR / "clips.json"
    if clips_json_path.exists():
        try:
            return json.loads(clips_json_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to parse clips.json: {e}")
    return []


@app.post("/api/clips")
async def post_clips(request: Request, project: Optional[str] = None):
    data = await request.json()
    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="Expected list of clips")

    if project:
        p_dir = get_project_dir(project)
        p_clips = p_dir / "clips.json"
        p_clips.write_text(json.dumps(data, indent=2), encoding="utf-8")
        update_project_meta(project, clips_count=len(data))
    else:
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
