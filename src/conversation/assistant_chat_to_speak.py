import json
import urllib.request
import urllib.error
import subprocess
import shutil
from pathlib import Path
from datetime import datetime

# =========================
# KONFIGURASI
# =========================
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "gemma3:1b"

TTS_URL = "http://localhost:8080/v1/audio/speech"
TTS_VOICE = "nova"
TTS_LANGUAGE = "Indonesian"

# Folder output Linux
OUTPUT_DIR = Path.home() / "nova" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SYSTEM_PROMPT = """
Kamu adalah Nova, asisten AI pribadi yang ramah, ceria, dan santai.
Kamu berbicara dalam bahasa Indonesia secara alami.
Jawab pertanyaan dengan jelas dan ringkas.
Jangan gunakan emotikon atau stiker.
Jangan menggunakan Markdown yang rumit karena jawabanmu akan dibacakan.
"""

# Riwayat percakapan selama program berjalan
messages = [{"role": "system", "content": SYSTEM_PROMPT}]


def post_json(url, payload, timeout=300):
    """Mengirim JSON melalui HTTP dan membaca responsnya."""
    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def ask_ollama(user_text):
    """Menghasilkan jawaban menggunakan Ollama."""
    messages.append({"role": "user", "content": user_text})

    try:
        raw = post_json(
            OLLAMA_URL,
            {
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "options": {"num_predict": 256},
            },
            timeout=300,
        )

        result = json.loads(raw.decode("utf-8"))
        answer = (result.get("message", {}).get("content") or "").strip()

        if not answer:
            raise RuntimeError(
                "Ollama menghasilkan jawaban kosong. "
                "Coba model lain atau periksa konfigurasi thinking."
            )

        messages.append({"role": "assistant", "content": answer})

        return answer

    except Exception:
        # Hapus pesan user jika gagal diproses
        if messages and messages[-1].get("role") == "user":
            messages.pop()
        raise


def speak(text):
    """Mengubah teks menjadi file WAV menggunakan OmniVoice."""
    filename = f"nova_{datetime.now():%Y%m%d_%H%M%S_%f}.wav"
    output_path = OUTPUT_DIR / filename

    audio = post_json(
        TTS_URL,
        {
            "input": text,
            "voice": TTS_VOICE,
            "language": TTS_LANGUAGE,
            "response_format": "wav",
        },
        timeout=300,
    )

    if not audio or not audio.startswith(b"RIFF"):
        raise RuntimeError(
            "Respons TTS bukan file WAV yang valid. " "Periksa log server OmniVoice."
        )

    output_path.write_bytes(audio)
    return output_path


def play_audio(path):
    """Memutar audio menggunakan pemutar Linux."""
    players = [
        ["paplay", str(path)],
        ["aplay", str(path)],
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(path)],
    ]

    for command in players:
        if shutil.which(command[0]):
            try:
                subprocess.run(command, check=True)
                return
            except subprocess.CalledProcessError:
                continue

    print(f"Audio tersimpan di: {path}")
    print("Pemutar audio tidak ditemukan atau gagal memutar.")
    print("Instal salah satu: pulseaudio-utils, alsa-utils, atau ffmpeg.")


def main():
    print("=" * 52)
    print(" NOVA - LOCAL AI VOICE ASSISTANT")
    print("=" * 52)
    print(f"LLM  : {OLLAMA_MODEL}")
    print(f"Voice: {TTS_VOICE}")
    print(f"Output: {OUTPUT_DIR}")
    print("Ketik 'keluar' untuk berhenti.")
    print("Ketik 'reset' untuk menghapus riwayat percakapan.\n")

    while True:
        try:
            user_text = input("Kamu: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nNova: Sampai jumpa!")
            break

        if not user_text:
            continue

        if user_text.lower() in ("keluar", "exit", "quit"):
            print("Nova: Sampai jumpa!")
            break

        if user_text.lower() == "reset":
            messages[:] = [{"role": "system", "content": SYSTEM_PROMPT}]
            print("Riwayat percakapan direset.\n")
            continue

        try:
            print("Nova sedang berpikir...")
            answer = ask_ollama(user_text)
            print(f"\nNova: {answer}\n")

            print("Nova sedang berbicara...")
            audio_path = speak(answer)
            print(f"Audio: {audio_path}")

            play_audio(audio_path)
            print()

        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            print(f"\nHTTP error {exc.code}: {detail}\n")

        except urllib.error.URLError as exc:
            print(f"\nTidak bisa terhubung ke server: {exc.reason}")
            print("Pastikan Ollama dan OmniVoice server berjalan.\n")

        except Exception as exc:
            print(f"\nTerjadi kesalahan: {exc}\n")


if __name__ == "__main__":
    main()
