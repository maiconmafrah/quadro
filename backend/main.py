"""
Quadro — backend pessoal para baixar vídeos do YouTube e do Facebook.

Endpoint único: POST /api/download
Body: {"url": "...", "access_key": "..."}
Resposta: o arquivo de vídeo (mp4) direto no corpo da resposta, ou um JSON de erro.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from yt_dlp.utils import sanitize_filename

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# --- configuração via variáveis de ambiente ---------------------------------

ACCESS_KEYS = {k.strip() for k in os.environ.get("ACCESS_KEY", "").split(",") if k.strip()}
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "")
MAX_DURATION_SECONDS = int(os.environ.get("MAX_DURATION_SECONDS", "3600"))
MAX_FILESIZE_MB = int(os.environ.get("MAX_FILESIZE_MB", "500"))
EXTRACT_TIMEOUT_SECONDS = int(os.environ.get("EXTRACT_TIMEOUT_SECONDS", "30"))
DOWNLOAD_TIMEOUT_SECONDS = int(os.environ.get("DOWNLOAD_TIMEOUT_SECONDS", "600"))
TEMP_ROOT = Path(os.environ.get("TEMP_DIR", "/tmp/quadro-downloads"))

if not ACCESS_KEYS:
    raise RuntimeError(
        "A variável de ambiente ACCESS_KEY precisa estar definida antes de iniciar o servidor."
    )

TEMP_ROOT.mkdir(parents=True, exist_ok=True)

WORKER_SCRIPT = Path(__file__).parent / "ytdlp_worker.py"

ALLOWED_HOST_SUFFIXES = {
    "youtube": ("youtube.com", "youtu.be"),
    "facebook": ("facebook.com", "fb.watch"),
}

# --- app ---------------------------------------------------------------------

app = FastAPI(title="Quadro API", description="Backend pessoal de download de vídeos.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN] if FRONTEND_ORIGIN else [],
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    expose_headers=["Content-Disposition", "X-Video-Title", "X-Video-Duration"],
)


class DownloadRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2048)
    access_key: str = Field(..., min_length=1, max_length=256)


def detect_platform(url: str) -> str | None:
    """Retorna 'youtube', 'facebook' ou None se o link não for suportado."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None

    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None

    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]

    for platform, suffixes in ALLOWED_HOST_SUFFIXES.items():
        if any(host == suffix or host.endswith("." + suffix) for suffix in suffixes):
            return platform
    return None


def error_response(status_code: int, error: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": error, "message": message})


class WorkerError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # "download_error" ou "internal_error"
        self.message = message


def run_ytdlp_worker(mode: str, url: str, opts: dict, timeout_seconds: int) -> dict:
    """
    Roda o yt-dlp num processo separado (não numa thread) para que um
    travamento de rede possa ser encerrado de verdade com SIGKILL — uma
    thread Python presa numa chamada de rede não tem como ser interrompida
    à força, e ficaria consumindo memória indefinidamente no servidor.
    """
    proc = subprocess.Popen(
        [sys.executable, str(WORKER_SCRIPT), mode, url, json.dumps(opts)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,  # próprio grupo de processos, pra matar o yt-dlp e o ffmpeg que ele chama junto
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise HTTPException(
            status_code=504,
            detail={
                "error": "timeout",
                "message": f"A operação excedeu o tempo limite de {timeout_seconds} segundos.",
            },
        )

    if proc.returncode != 0:
        stderr = (stderr or "").strip()
        if stderr.startswith("DOWNLOAD_ERROR:"):
            raise WorkerError("download_error", stderr[len("DOWNLOAD_ERROR:") :].strip())
        raise WorkerError("internal_error", stderr or "erro desconhecido no yt-dlp")

    last_line = stdout.strip().splitlines()[-1] if stdout.strip() else "{}"
    return json.loads(last_line)


def needs_transcode(path: Path) -> bool:
    """
    iPhone/WhatsApp só reproduzem de forma garantida vídeo H.264 8-bit
    (yuv420p) com áudio AAC. Um mp4 com VP9/AV1, HEVC ou cor de 10 bits
    costuma "abrir" (o arquivo existe, o player nem sempre acusa erro) mas
    mostra tela preta nesses apps. Se não der pra confirmar que já está
    nesse formato, transcodifica por segurança.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        info = json.loads(out.stdout)
    except Exception:
        return True

    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is None:
        return True
    if video.get("codec_name") != "h264":
        return True
    if not str(video.get("pix_fmt", "")).startswith("yuv420p"):
        return True
    if audio is not None and audio.get("codec_name") != "aac":
        return True
    return False


def transcode_to_h264(src_path: Path, dst_path: Path, timeout_seconds: int) -> None:
    # "veryfast" + "main" pesam bem menos em CPU/memória que os padrões do
    # ffmpeg ("medium"/"high") — importante numa VPS pequena, onde um preset
    # mais lento pode travar por minutos ou até estourar a RAM.
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src_path),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-profile:v",
            "main",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(dst_path),
        ],
        capture_output=True,
        timeout=timeout_seconds,
        check=True,
    )


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "quadro"}


@app.post("/api/download")
def download_video(payload: DownloadRequest):
    if payload.access_key not in ACCESS_KEYS:
        raise error_response(401, "unauthorized", "Chave de acesso inválida.")

    platform = detect_platform(payload.url)
    if platform is None:
        raise error_response(
            400,
            "invalid_url",
            "O link precisa ser uma URL válida do YouTube ou do Facebook.",
        )

    job_id = uuid.uuid4().hex
    job_dir = TEMP_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts = {
        # Força H.264 (avc1) + AAC (mp4a) sempre que possível: são os únicos
        # codecs que tocam de forma garantida no iPhone/iOS. Um mp4 com vídeo
        # em VP9/AV1 (comum no Facebook em qualidades mais altas) abre no PC
        # mas não reproduz no iOS.
        "format": (
            "bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]/"
            "best[vcodec^=avc1][ext=mp4]/"
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
            "best[ext=mp4]/best"
        ),
        "merge_output_format": "mp4",
        "outtmpl": str(job_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 3,
        "restrictfilenames": True,
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "web"]}},
    }

    # 1) extrai metadados sem baixar, para validar duração/tamanho antes de gastar banda
    try:
        info = run_ytdlp_worker("extract", payload.url, ydl_opts, EXTRACT_TIMEOUT_SECONDS)
    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    except WorkerError as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        if platform == "youtube" and "sign in" in e.message.lower():
            raise error_response(
                422,
                "youtube_blocked",
                "O YouTube está bloqueando downloads feitos a partir deste servidor no momento "
                "(proteção anti-robô deles, não um problema do link). O Facebook continua "
                "funcionando normalmente.",
            )
        raise error_response(
            422,
            "extraction_failed",
            "Não foi possível acessar este vídeo. Ele pode ser privado, ter sido removido ou o link está incorreto.",
        )
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise error_response(500, "internal_error", "Erro inesperado ao ler as informações do vídeo.")

    duration = info.get("duration") or 0
    if MAX_DURATION_SECONDS and duration > MAX_DURATION_SECONDS:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise error_response(
            413,
            "video_too_long",
            f"O vídeo dura mais que o limite configurado de {MAX_DURATION_SECONDS // 60} minutos.",
        )

    approx_size = info.get("filesize") or info.get("filesize_approx") or 0
    if MAX_FILESIZE_MB and approx_size and approx_size > MAX_FILESIZE_MB * 1024 * 1024:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise error_response(
            413,
            "file_too_large",
            f"O arquivo estimado excede o limite configurado de {MAX_FILESIZE_MB} MB.",
        )

    # 2) baixa de fato
    try:
        run_ytdlp_worker("download", payload.url, ydl_opts, DOWNLOAD_TIMEOUT_SECONDS)
    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    except WorkerError:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise error_response(502, "download_failed", "Falha ao baixar o vídeo. Tente novamente em instantes.")
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise error_response(500, "internal_error", "Erro inesperado ao baixar o vídeo.")

    files = [f for f in job_dir.iterdir() if f.is_file()]
    mp4_files = [f for f in files if f.suffix.lower() == ".mp4"]
    result_file = mp4_files[0] if mp4_files else (files[0] if files else None)

    if result_file is None or not result_file.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
        raise error_response(500, "file_missing", "O arquivo baixado não foi encontrado no servidor.")

    # 3) garante H.264/AAC de verdade (não só a extensão .mp4) — sem isso o
    # vídeo pode abrir tela preta no iPhone/WhatsApp mesmo parecendo válido.
    if needs_transcode(result_file):
        transcoded_file = result_file.with_name(result_file.stem + "_h264.mp4")
        try:
            transcode_to_h264(result_file, transcoded_file, DOWNLOAD_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise error_response(
                504,
                "timeout",
                f"A conversão do vídeo excedeu o tempo limite de {DOWNLOAD_TIMEOUT_SECONDS} segundos.",
            )
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise error_response(
                500,
                "transcode_failed",
                "Não foi possível preparar o vídeo para reprodução no celular.",
            )
        result_file.unlink(missing_ok=True)
        result_file = transcoded_file

    if MAX_FILESIZE_MB and result_file.stat().st_size > MAX_FILESIZE_MB * 1024 * 1024:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise error_response(
            413,
            "file_too_large",
            f"O arquivo baixado excede o limite configurado de {MAX_FILESIZE_MB} MB.",
        )

    title = info.get("title") or "video"
    safe_title = sanitize_filename(title, restricted=True) or "video"
    download_filename = f"{safe_title}.mp4"

    return FileResponse(
        path=result_file,
        media_type="video/mp4",
        filename=download_filename,
        headers={
            "X-Video-Title": safe_title,
            "X-Video-Duration": str(int(duration)),
        },
        background=BackgroundTask(shutil.rmtree, job_dir, ignore_errors=True),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(status_code=exc.status_code, content={"error": "error", "message": str(detail)})
