"""
Executa uma chamada do yt-dlp (extração de metadados ou download) num
processo separado do backend.

Por quê: chamar a API do yt-dlp diretamente dentro do processo do FastAPI
roda numa thread, e uma thread travada numa operação de rede (algo comum
quando o YouTube/Facebook ficam lentos ou instáveis) não tem como ser
"matada" de verdade em Python — ela fica presa pra sempre, mesmo depois do
backend desistir de esperar. Um processo, ao contrário de uma thread, pode
ser morto de fato (SIGKILL) se estourar o tempo limite.

Uso: python ytdlp_worker.py <extract|download> <url> <opts_json>
Saída: uma linha de JSON em stdout com o resultado.
Erros: prefixo DOWNLOAD_ERROR ou INTERNAL_ERROR em stderr + exit code != 0.
"""

import json
import sys

import yt_dlp


def main() -> None:
    mode, url, opts_json = sys.argv[1], sys.argv[2], sys.argv[3]
    opts = json.loads(opts_json)

    try:
        if mode == "extract":
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            print(
                json.dumps(
                    {
                        "title": info.get("title"),
                        "duration": info.get("duration"),
                        "filesize": info.get("filesize"),
                        "filesize_approx": info.get("filesize_approx"),
                    }
                )
            )
        elif mode == "download":
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            print(json.dumps({"ok": True}))
        else:
            print(f"INTERNAL_ERROR: modo desconhecido {mode!r}", file=sys.stderr)
            sys.exit(1)
    except yt_dlp.utils.DownloadError as e:
        print(f"DOWNLOAD_ERROR: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"INTERNAL_ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
