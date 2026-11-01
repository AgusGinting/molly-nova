import subprocess
import urllib.request
import urllib.error
import wave
from pathlib import Path
from datetime import datetime

from nova_stream import SentenceChunker, SpeechPipeline, stream_ollama, warm_up

# ============================================================
# KONFIGURASI NOVA
# ============================================================

BASE_DIR = Path(__file__).resolve().parents[2]
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Whisper.cpp
WHISPER_BIN = BASE_DIR / "src" / "whisper.cpp" / "build" / "bin" / "whisper-cli"
WHISPER_MODEL = BASE_DIR / "src" / "whisper.cpp" / "models" / "ggml-base.bin"

# Mikrofon PulseAudio di WSL
MIC_SOURCE = "RDPSource"

# Ollama
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "gemma3:1b"

# OmniVoice
TTS_URL = "http://localhost:8080/v1/audio/speech"
TTS_VOICE = "nova"

# Batas panjang jawaban. Jawaban pendek = suara lebih cepat selesai.
MAX_REPLY_TOKENS = 150

# Riwayat percakapan
conversation_history = []
MAX_HISTORY_MESSAGES = 12


# ============================================================
# UTILITAS
# ============================================================


def check_configuration():
    if not WHISPER_BIN.exists():
        raise FileNotFoundError(f"Whisper tidak ditemukan: {WHISPER_BIN}")

    if not WHISPER_MODEL.exists():
        raise FileNotFoundError(f"Model Whisper tidak ditemukan: {WHISPER_MODEL}")

    for command in ("ffmpeg",):
        try:
            subprocess.run(
                ["which", command],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            raise RuntimeError(f"{command} belum terpasang.")


# ============================================================
# REKAM SUARA
# Enter pertama = mulai
# Enter kedua   = berhenti
# ============================================================


def record_audio():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_path = OUTPUT_DIR / f"nova_input_{timestamp}.wav"

    print("\nTekan ENTER untuk mulai merekam...")
    input()

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "pulse",
        "-i",
        MIC_SOURCE,
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(audio_path),
    ]

    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # Beri FFmpeg waktu untuk membuka sumber audio.
        import time

        time.sleep(0.5)

        if process.poll() is not None:
            error = process.stderr.read() if process.stderr else ""
            raise RuntimeError(f"Gagal memulai rekaman: {error}")

        print("🔴 MEREKAM... Bicara sekarang.")
        print("Tekan ENTER sekali lagi untuk berhenti.")

        input()

    except (KeyboardInterrupt, EOFError):
        print("\nMenghentikan rekaman...")

    finally:
        if process.poll() is None:
            process.terminate()

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

        if process.stderr:
            error = process.stderr.read()
            process.stderr.close()
        else:
            error = ""

    if process.returncode not in (0, -15, 255):
        # FFmpeg dapat keluar dengan kode berbeda ketika dihentikan.
        if not audio_path.exists() or audio_path.stat().st_size < 1000:
            raise RuntimeError(f"Rekaman gagal: {error}")

    if not audio_path.exists() or audio_path.stat().st_size < 1000:
        raise RuntimeError(
            "Audio kosong atau terlalu pendek. " "Periksa sumber mikrofon PulseAudio."
        )

    print(f"Rekaman tersimpan: {audio_path}")
    return audio_path


# ============================================================
# SPEECH-TO-TEXT: WHISPER.CPP
# ============================================================


def listen(audio_path):
    print("\n🎧 Memproses suara dengan Whisper...")

    command = [
        str(WHISPER_BIN),
        "-m",
        str(WHISPER_MODEL),
        "-f",
        str(audio_path),
        "-l",
        "id",
        "-nt",
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Whisper gagal:\n{result.stderr.strip()}")

    # Whisper biasanya menulis hasil transkripsi ke stdout.
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]

    # Buang baris log atau timestamp jika ada.
    transcript_lines = [
        line
        for line in lines
        if not line.startswith("[")
        and not line.startswith("whisper_")
        and "-->" not in line
    ]

    user_text = " ".join(transcript_lines).strip()

    if not user_text:
        print("Tidak ada ucapan yang berhasil dikenali.")
        if result.stderr.strip():
            print(result.stderr.strip()[-1000:])
        return ""

    print(f"Kamu: {user_text}")
    return user_text


# ============================================================
# LLM: OLLAMA
# ============================================================


def reply_and_speak(user_text):
    """
    Minta jawaban Ollama secara streaming dan bacakan per kalimat.
    Kalimat pertama sudah diputar sementara sisa jawaban masih dibuat.
    """
    conversation_history.append({"role": "user", "content": user_text})

    messages = [
        {
            "role": "system",
            "content": (
                "Namamu Nova. Kamu adalah asisten suara yang ramah, "
                "ceria, dan membantu. Jawab dalam Bahasa Indonesia yang alami "
                "tanpa emoticon. Jawaban akan dibacakan dengan suara, jadi "
                "jangan memakai Markdown, daftar bernomor, atau poin-poin. "
                "Jawab singkat, cukup satu sampai tiga kalimat, kecuali "
                "pengguna meminta penjelasan panjang."
            ),
        }
    ] + conversation_history[-MAX_HISTORY_MESSAGES:]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pipeline = SpeechPipeline(
        TTS_URL,
        TTS_VOICE,
        language="Indonesian",
        seed=42,
        save_path=OUTPUT_DIR / f"nova_reply_{timestamp}.wav",
        on_first_audio=lambda: print("\n🔊 Nova mulai berbicara...", flush=True),
    )
    chunker = SentenceChunker()
    parts = []

    try:
        print("\nNova: ", end="", flush=True)
        for delta in stream_ollama(
            OLLAMA_URL,
            OLLAMA_MODEL,
            messages,
            options={"num_predict": MAX_REPLY_TOKENS},
            timeout=180,
        ):
            parts.append(delta)
            print(delta, end="", flush=True)
            for sentence in chunker.feed(delta):
                pipeline.say(sentence)
        for sentence in chunker.flush():
            pipeline.say(sentence)
        print()
    except urllib.error.URLError as exc:
        conversation_history.pop()
        pipeline.finish()
        raise RuntimeError(f"Ollama tidak dapat dihubungi: {exc}") from exc
    except Exception:
        conversation_history.pop()
        pipeline.finish()
        raise

    answer = "".join(parts).strip()
    if not answer:
        conversation_history.pop()
        pipeline.finish()
        raise RuntimeError("Ollama mengembalikan jawaban kosong.")

    conversation_history.append({"role": "assistant", "content": answer})

    # Tunggu sampai semua kalimat selesai diputar.
    pipeline.finish()
    for error in pipeline.errors:
        print(f"Peringatan: {error}")
    return answer


# ============================================================
# LOOP UTAMA
# ============================================================


def main():
    print("=" * 52)
    print(" NOVA - LOCAL AI VOICE ASSISTANT")
    print("=" * 52)
    print(f"LLM    : {OLLAMA_MODEL}")
    print(f"Voice  : {TTS_VOICE}")
    print("STT    : Whisper.cpp - Bahasa Indonesia")
    print(f"Mic    : {MIC_SOURCE}")
    print(f"Output : {OUTPUT_DIR}")
    print("\nPerintah suara:")
    print("- 'reset'  : hapus riwayat percakapan")
    print("- 'keluar' : hentikan Nova")
    print("- Tekan Ctrl+C kapan saja untuk berhenti")

    check_configuration()

    print("Memanaskan model...", end=" ", flush=True)
    try:
        warm_up(OLLAMA_URL, OLLAMA_MODEL, TTS_URL, TTS_VOICE, "Indonesian")
        print("siap.\n")
    except Exception as exc:  # noqa: BLE001
        print(f"gagal ({exc}). Lanjut tanpa pemanasan.\n")

    while True:
        try:
            audio_path = record_audio()
            user_text = listen(audio_path)

            if not user_text:
                continue

            command = user_text.lower().strip(" .!?,")
            if command in ("keluar", "exit", "quit", "berhenti"):
                print("Nova: Sampai jumpa!")
                break

            if command == "reset":
                conversation_history.clear()
                print("Nova: Riwayat percakapan sudah dihapus.")
                continue

            print("\n🤖 Nova sedang berpikir...")
            reply_and_speak(user_text)

        except (KeyboardInterrupt, EOFError):
            print("\nNova: Sampai jumpa!")
            break
        except Exception as exc:
            print(f"\nTerjadi kesalahan: {exc}")
            print("Nova akan mencoba lagi.\n")


if __name__ == "__main__":
    main()
