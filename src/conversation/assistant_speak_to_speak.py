import json
import subprocess
import urllib.request
import urllib.error
import wave
from pathlib import Path
from datetime import datetime

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


def ask_ollama(user_text):
    global conversation_history

    conversation_history.append(
        {
            "role": "user",
            "content": user_text,
        }
    )

    messages = [
        {
            "role": "system",
            "content": (
                "Namamu Nova. Kamu adalah asisten suara yang ramah, "
                "ceria, dan membantu. Jawab dalam Bahasa Indonesia tanpa menggunakan emoticon "
                "yang alami dan ringkas. Jawaban akan dibacakan "
                "dengan suara, jadi hindari format yang rumit."
            ),
        }
    ] + conversation_history[-MAX_HISTORY_MESSAGES:]

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
    }

    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama tidak dapat dihubungi: {exc}") from exc

    answer = data.get("message", {}).get("content", "").strip()

    if not answer:
        raise RuntimeError("Ollama mengembalikan jawaban kosong.")

    conversation_history.append(
        {
            "role": "assistant",
            "content": answer,
        }
    )

    return answer


# ============================================================
# TEXT-TO-SPEECH: OMNIVOICE
# ============================================================


def speak(text):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_path = OUTPUT_DIR / f"nova_reply_{timestamp}.wav"

    payload = {
        "input": text,
        "voice": "nova",
        "language": "Indonesian",
        "response_format": "wav",
        "seed": 42,
    }

    request = urllib.request.Request(
        TTS_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            audio_data = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OmniVoice HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OmniVoice tidak dapat dihubungi: {exc}") from exc

    if not audio_data:
        raise RuntimeError("OmniVoice menghasilkan audio kosong.")

    audio_path.write_bytes(audio_data)
    return audio_path


# ============================================================
# PUTAR AUDIO
# ============================================================


def play_audio(audio_path):
    # Coba paplay terlebih dahulu, lalu ffplay.
    try:
        subprocess.run(
            ["paplay", str(audio_path)],
            check=True,
            timeout=180,
        )
        return
    except FileNotFoundError:
        pass
    except subprocess.CalledProcessError:
        pass

    subprocess.run(
        [
            "ffplay",
            "-nodisp",
            "-autoexit",
            "-loglevel",
            "error",
            str(audio_path),
        ],
        check=True,
        timeout=180,
    )


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
            answer = ask_ollama(user_text)
            print(f"\nNova: {answer}")

            print("\n🔊 Membuat suara...")
            reply_audio = speak(answer)
            play_audio(reply_audio)

        except (KeyboardInterrupt, EOFError):
            print("\nNova: Sampai jumpa!")
            break
        except Exception as exc:
            print(f"\nTerjadi kesalahan: {exc}")
            print("Nova akan mencoba lagi.\n")


if __name__ == "__main__":
    main()
