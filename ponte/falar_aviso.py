"""Toca um som de notificação e fala o texto pelo alto-falante padrão do Windows
(ex.: um Echo Dot pareado via Bluetooth).

Voz: Microsoft neural via edge-tts (precisa de internet; o texto vai para a Microsoft).
Sem internet: cai para pyttsx3 (voz do Windows).
O áudio gerado sai baixo, então é reforçado com ffmpeg (compressor + ganho + limitador)
quando o ffmpeg estiver no PATH.

Uso: python falar_aviso.py "texto" [1|0 = som antes] [reforço em dB, padrão 14]

Variáveis opcionais (no .env da raiz):
  PONTE_VOZ            voz do edge-tts (padrão pt-BR-AntonioNeural)
  PONTE_FALANDO_LOCK   arquivo criado enquanto fala (útil para outro assistente
                       de voz não "ouvir" o aviso)
"""
import asyncio
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile

SOM = r"C:\Windows\Media\Windows Notify Messaging.wav"
SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def ler_env():
    env = {}
    caminho = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        with open(caminho, encoding="utf-8") as f:
            for linha in f:
                if "=" in linha and not linha.lstrip().startswith("#"):
                    k, v = linha.strip().split("=", 1)
                    env[k] = v
    except OSError:
        pass
    return env


ENV = ler_env()
VOZ = ENV.get("PONTE_VOZ") or "pt-BR-AntonioNeural"
LOCK = ENV.get("PONTE_FALANDO_LOCK") or ""


def tocar(caminho):
    """Toca mp3/wav de forma síncrona no dispositivo padrão (Windows MCI)."""
    mci = ctypes.windll.winmm.mciSendStringW
    alias = "aviso%d" % os.getpid()
    mci(f'open "{caminho}" type mpegvideo alias {alias}', None, 0, None)
    mci(f"play {alias} wait", None, 0, None)
    mci(f"close {alias}", None, 0, None)


def gerar_edge(texto, destino):
    import edge_tts
    asyncio.run(edge_tts.Communicate(texto, VOZ, rate="+8%").save(destino))


def falar_offline(texto):
    import pyttsx3
    engine = pyttsx3.init()
    for v in engine.getProperty("voices"):
        if "Maria" in v.name or "Portuguese" in v.name:
            engine.setProperty("voice", v.id)
            break
    engine.say(texto)
    engine.runAndWait()


def reforcar(origem, destino, ganho):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or ganho <= 0:
        return origem
    filtro = f"acompressor=threshold=-30dB:ratio=6:attack=5:release=120:makeup={ganho},alimiter=limit=0.97"
    r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", origem, "-af", filtro, destino],
                       capture_output=True, timeout=30, creationflags=SEM_JANELA)
    return destino if r.returncode == 0 and os.path.exists(destino) else origem


def main():
    texto = sys.argv[1] if len(sys.argv) > 1 else "Teste"
    com_som = len(sys.argv) < 3 or sys.argv[2] == "1"
    ganho = float(sys.argv[3]) if len(sys.argv) > 3 else 14.0
    tmp = tempfile.mkdtemp(prefix="aviso_")
    try:
        if LOCK:
            with open(LOCK, "w") as f:
                f.write(str(os.getpid()))
        try:
            mp3 = os.path.join(tmp, "fala.mp3")
            gerar_edge(texto, mp3)
            fala = reforcar(mp3, os.path.join(tmp, "fala.wav"), ganho)
        except Exception:
            fala = None
        if com_som and os.path.exists(SOM):
            try:
                tocar(reforcar(SOM, os.path.join(tmp, "som.wav"), min(ganho, 10)))
            except Exception:
                pass
        if fala:
            tocar(fala)
        else:
            falar_offline(texto)
    finally:
        if LOCK:
            try:
                os.remove(LOCK)
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
