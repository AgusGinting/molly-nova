"""
Pipeline suara Nova berbasis kalimat.

Alur:
  Ollama (stream=True) -> potong per kalimat -> antrean TTS
  -> thread sintesis (OmniVoice, PCM) -> antrean audio
  -> satu proses pemutar raw PCM yang terus terbuka.

Kalimat pertama sudah diputar sementara kalimat berikutnya masih
disintesis, jadi pengguna tidak perlu menunggu seluruh jawaban selesai.
Hanya memakai Python standard library.
"""

import json
import queue
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
import wave

SAMPLE_RATE = 24000

# Potongan pertama boleh pendek supaya suara cepat terdengar.
FIRST_MIN_CHUNK_CHARS = 12
# Potongan berikutnya digabung sampai minimal sepanjang ini. Setiap panggilan
# TTS punya biaya tetap, jadi kalimat pendek yang berdiri sendiri disintesis
# lebih lama dari durasi audionya dan menimbulkan jeda hening.
MIN_CHUNK_CHARS = 60
# Teks tanpa tanda baca dipaksa dipotong di koma/spasi setelah panjang ini.
MAX_CHUNK_CHARS = 220

# Jumlah langkah MaskGIT untuk potongan pertama. Potongan berikutnya memakai
# default server (--steps), karena disintesis sambil potongan pertama diputar.
FIRST_CHUNK_STEPS = 8

_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0000FE0F\U0000200D]+"
)
# Titik setelah angka ("1." pada daftar, "3.5") tidak dianggap akhir kalimat.
_SENTENCE_END = re.compile(r"(?<![0-9])[.!?…]+[\"')\]]*(?=\s)|\n+")


def clean_for_speech(text):
    """Buang format Markdown dan simbol yang tidak perlu dibacakan."""
    text = _EMOJI.sub("", text)
    text = re.sub(r"(?m)^\s*#+\s*", "", text)
    text = re.sub(r"(?m)^\s*(?:[-*•]|\d+[.)])(?:\s+|$)", "", text)
    text = re.sub(r"[*_`#~|>]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class SentenceChunker:
    """Menerima potongan teks streaming, mengeluarkan kalimat utuh."""

    def __init__(self):
        self.buffer = ""
        self.emitted = 0

    def _min_chars(self):
        return FIRST_MIN_CHUNK_CHARS if self.emitted == 0 else MIN_CHUNK_CHARS

    def feed(self, delta):
        self.buffer += delta
        chunks = []
        while True:
            match = _SENTENCE_END.search(self.buffer)
            if match:
                # Gabungkan potongan yang terlalu pendek dengan kalimat
                # berikutnya; tunggu teks lagi jika batas berikutnya belum ada.
                end = match.end()
                while len(clean_for_speech(self.buffer[:end])) < self._min_chars():
                    nxt = _SENTENCE_END.search(self.buffer, end)
                    if not nxt:
                        end = None
                        break
                    end = nxt.end()
                if end is None:
                    break
                candidate = self.buffer[:end]
                self.buffer = self.buffer[len(candidate):]
                if clean_for_speech(candidate):
                    chunks.append(candidate)
                    self.emitted += 1
                continue
            if len(self.buffer) > MAX_CHUNK_CHARS:
                cut = max(self.buffer.rfind(",", 0, MAX_CHUNK_CHARS),
                          self.buffer.rfind(" ", 0, MAX_CHUNK_CHARS))
                if cut <= 0:
                    cut = MAX_CHUNK_CHARS
                chunks.append(self.buffer[: cut + 1])
                self.buffer = self.buffer[cut + 1:]
                self.emitted += 1
                continue
            break
        return [c for c in (clean_for_speech(c) for c in chunks) if c]

    def flush(self):
        rest = clean_for_speech(self.buffer)
        self.buffer = ""
        return [rest] if rest else []


# Biarkan model tetap di VRAM. Tanpa ini Ollama melepas model setelah
# 5 menit diam, dan memuat ulang bisa memakan waktu lebih dari satu menit.
OLLAMA_KEEP_ALIVE = -1


def stream_ollama(url, model, messages, options=None, timeout=300):
    """Generator potongan teks dari Ollama /api/chat dengan stream=True."""
    payload = {"model": model, "messages": messages, "stream": True,
               "keep_alive": OLLAMA_KEEP_ALIVE}
    if options:
        payload["options"] = options
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for line in response:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line.decode("utf-8"))
            if "error" in data:
                raise RuntimeError(f"Ollama: {data['error']}")
            delta = data.get("message", {}).get("content", "")
            if delta:
                yield delta
            if data.get("done"):
                break


def warm_up(ollama_url, model, tts_url=None, voice=None, language="Indonesian"):
    """
    Muat model Ollama ke memori dan pancing TTS sekali saat program mulai,
    supaya pertanyaan pertama tidak menunggu model dimuat.
    """
    payload = {"model": model, "messages": [], "keep_alive": OLLAMA_KEEP_ALIVE}
    request = urllib.request.Request(
        ollama_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        response.read()
    if tts_url and voice:
        pipeline = SpeechPipeline(tts_url, voice, language=language, play=False)
        pipeline.say("Halo.")
        pipeline.finish()
        if pipeline.errors:
            raise RuntimeError(pipeline.errors[0])


def _player_command():
    """Pemutar raw PCM s16le 24 kHz mono yang membaca dari stdin."""
    if shutil.which("paplay"):
        return ["paplay", "--raw", "--format=s16le",
                f"--rate={SAMPLE_RATE}", "--channels=1"]
    if shutil.which("aplay"):
        return ["aplay", "-q", "-t", "raw", "-f", "S16_LE",
                "-r", str(SAMPLE_RATE), "-c", "1"]
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error",
                "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "-"]
    return None


class SpeechPipeline:
    """
    Satu jawaban = satu SpeechPipeline.

    say(teks)  : masukkan kalimat ke antrean sintesis (tidak blocking).
    finish()   : tunggu semua kalimat selesai disintesis dan diputar,
                 lalu simpan seluruh audio ke WAV jika save_path diisi.
    """

    def __init__(self, tts_url, voice, language="Indonesian", seed=42,
                 save_path=None, play=True, timeout=300, on_first_audio=None,
                 first_steps=FIRST_CHUNK_STEPS, steps=None):
        self.tts_url = tts_url
        self.voice = voice
        self.language = language
        self.seed = seed
        self.save_path = save_path
        self.timeout = timeout
        self.on_first_audio = on_first_audio
        # None = pakai default server. first_steps hanya untuk potongan pertama.
        self.first_steps = first_steps
        self.steps = steps
        self._chunks_sent = 0
        self.errors = []
        self.pcm_parts = []

        self._text_q = queue.Queue()
        self._audio_q = queue.Queue()

        command = _player_command() if play else None
        self._player = None
        if command:
            self._player = subprocess.Popen(
                command, stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif play:
            print("Pemutar audio tidak ditemukan. "
                  "Instal pulseaudio-utils, alsa-utils, atau ffmpeg.")

        self._tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
        self._play_thread = threading.Thread(target=self._play_worker, daemon=True)
        self._tts_thread.start()
        self._play_thread.start()

    def say(self, text):
        if text:
            self._text_q.put(text)

    def _synthesize(self, text, steps=None):
        payload = {
            "input": text,
            "voice": self.voice,
            "language": self.language,
            "response_format": "pcm",
            "seed": self.seed,
        }
        if steps:
            payload["steps"] = steps
        request = urllib.request.Request(
            self.tts_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            # Server mengirim PCM secara chunked; teruskan begitu tiba.
            while True:
                block = response.read(SAMPLE_RATE)  # ~0.5 detik audio
                if not block:
                    break
                yield block

    def _tts_worker(self):
        while True:
            text = self._text_q.get()
            if text is None:
                self._audio_q.put(None)
                return
            steps = self.first_steps if self._chunks_sent == 0 else self.steps
            self._chunks_sent += 1
            try:
                for block in self._synthesize(text, steps):
                    self._audio_q.put(block)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                self.errors.append(f"OmniVoice HTTP {exc.code}: {detail}")
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"TTS gagal untuk '{text[:40]}...': {exc}")

    def _play_worker(self):
        first = True
        carry = b""
        while True:
            block = self._audio_q.get()
            if block is None:
                break
            block = carry + block
            # Jaga agar sampel 16-bit tidak terpotong di tengah.
            if len(block) % 2:
                carry, block = block[-1:], block[:-1]
            else:
                carry = b""
            self.pcm_parts.append(block)
            if first and self.on_first_audio:
                self.on_first_audio()
            first = False
            if self._player and self._player.stdin:
                try:
                    self._player.stdin.write(block)
                    self._player.stdin.flush()
                except BrokenPipeError:
                    self._player = None

    def finish(self):
        self._text_q.put(None)
        self._tts_thread.join()
        self._play_thread.join()
        if self._player:
            try:
                self._player.stdin.close()
            except BrokenPipeError:
                pass
            self._player.wait()
        if self.save_path and self.pcm_parts:
            with wave.open(str(self.save_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(b"".join(self.pcm_parts))
        return self.save_path if self.pcm_parts else None
