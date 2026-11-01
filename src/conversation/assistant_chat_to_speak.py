import urllib.error
from pathlib import Path
from datetime import datetime

from nova_stream import SentenceChunker, SpeechPipeline, stream_ollama, warm_up

# =========================
# KONFIGURASI
# =========================
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "gemma3:1b"

TTS_URL = "http://localhost:8080/v1/audio/speech"
TTS_VOICE = "nova"
TTS_LANGUAGE = "Indonesian"
TTS_SEED = 42  # seed tetap supaya karakter suara konsisten antar kalimat

# Batas panjang jawaban. Jawaban pendek = suara lebih cepat selesai.
MAX_REPLY_TOKENS = 150

# Folder output Linux
OUTPUT_DIR = Path.home() / "nova" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SYSTEM_PROMPT = """
Kamu adalah Nova, asisten AI pribadi yang ramah, ceria, dan santai.
Kamu berbicara dalam bahasa Indonesia secara alami.
Jawab pertanyaan tanpa menggunakan emotikon atau simbol atau stiker.
Jangan menggunakan Markdown, daftar bernomor, atau poin-poin karena jawabanmu akan dibacakan.
Jawab singkat, cukup satu sampai tiga kalimat, kecuali pengguna meminta penjelasan panjang.
"""

# Riwayat percakapan selama program berjalan
messages = [{"role": "system", "content": SYSTEM_PROMPT}]


def reply_and_speak(user_text):
    """
    Minta jawaban Ollama secara streaming dan bacakan per kalimat.
    Kalimat pertama sudah diputar sementara sisa jawaban masih dibuat.
    """
    messages.append({"role": "user", "content": user_text})

    filename = f"nova_{datetime.now():%Y%m%d_%H%M%S_%f}.wav"
    pipeline = SpeechPipeline(
        TTS_URL,
        TTS_VOICE,
        language=TTS_LANGUAGE,
        seed=TTS_SEED,
        save_path=OUTPUT_DIR / filename,
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
        ):
            parts.append(delta)
            print(delta, end="", flush=True)
            for sentence in chunker.feed(delta):
                pipeline.say(sentence)
        for sentence in chunker.flush():
            pipeline.say(sentence)
        print("\n")
    except Exception:
        # Hapus pesan user jika gagal diproses
        if messages and messages[-1].get("role") == "user":
            messages.pop()
        pipeline.finish()
        raise

    answer = "".join(parts).strip()
    if not answer:
        messages.pop()
        pipeline.finish()
        raise RuntimeError(
            "Ollama menghasilkan jawaban kosong. "
            "Coba model lain atau periksa konfigurasi thinking."
        )
    messages.append({"role": "assistant", "content": answer})

    # Tunggu sampai semua kalimat selesai diputar.
    audio_path = pipeline.finish()
    for error in pipeline.errors:
        print(f"Peringatan: {error}")
    return audio_path


def main():
    print("=" * 52)
    print(" NOVA - LOCAL AI VOICE ASSISTANT")
    print("=" * 52)
    print(f"LLM  : {OLLAMA_MODEL}")
    print(f"Voice: {TTS_VOICE}")
    print(f"Output: {OUTPUT_DIR}")
    print("Ketik 'keluar' untuk berhenti.")
    print("Ketik 'reset' untuk menghapus riwayat percakapan.\n")

    print("Memanaskan model...", end=" ", flush=True)
    try:
        warm_up(OLLAMA_URL, OLLAMA_MODEL, TTS_URL, TTS_VOICE, TTS_LANGUAGE)
        print("siap.\n")
    except Exception as exc:  # noqa: BLE001
        print(f"gagal ({exc}). Lanjut tanpa pemanasan.\n")

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
            audio_path = reply_and_speak(user_text)
            if audio_path:
                print(f"Audio: {audio_path}\n")

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
