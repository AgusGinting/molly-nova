# Nova — Local AI Voice Assistant

Nova adalah asisten AI berbasis lokal yang menerima input teks, menghasilkan jawaban menggunakan **Gemma 3 1B melalui Ollama**, lalu membacakan jawaban tersebut menggunakan **OmniVoice** dengan profil suara `nova`.

README ini ditujukan untuk **Linux Ubuntu**. Program menggunakan Python standard library sehingga tidak memerlukan instalasi paket Python tambahan.

## Fitur

- Percakapan teks melalui terminal.
- Jawaban LLM menggunakan `gemma3:1b`.
- Sintesis suara bahasa Indonesia melalui OmniVoice.
- Menggunakan profil suara `nova` yang sudah tersedia di server OmniVoice.
- Menyimpan file audio WAV ke folder output.
- Memutar audio otomatis di Linux jika pemutar yang didukung tersedia.
- Menyimpan riwayat percakapan selama program berjalan.
- Perintah `reset` untuk menghapus riwayat dan `keluar` untuk berhenti.
- Komponen LLM dan TTS berjalan melalui server lokal; setelah model diunduh, penggunaan model tidak memerlukan layanan cloud.

## Arsitektur

```text
Input pengguna di terminal
          |
          v
   Python assistant.py
          |
          v
 Ollama API (localhost:11434)
          |
          v
      gemma3:1b
          |
          v
    Teks jawaban Nova
          |
          v
OmniVoice API (localhost:8080)
          |
          v
 File WAV -> Pemutar audio Linux
```

## Persyaratan

- Linux Ubuntu atau distribusi Linux yang kompatibel.
- Python 3.10 atau lebih baru disarankan.
- Ollama sudah terpasang.
- Model Ollama `gemma3:1b` sudah diunduh.
- OmniVoice C++ sudah dibangun dan model OmniVoice sudah tersedia.
- Server OmniVoice sudah dikonfigurasi agar menerima permintaan suara `nova` dan bahasa `Indonesian`.
- Perangkat audio Linux berfungsi jika ingin memutar hasil suara secara otomatis.

> **Penting:** Script ini tidak menginstal atau menjalankan Ollama dan OmniVoice server secara otomatis. Keduanya harus sudah berjalan dan dapat diakses melalui URL lokal yang dikonfigurasi.

## Struktur direktori yang disarankan

```text
~/nova/
├── assistant.py
└── output/
    ├── nova_20261026_120000_123456.wav
    └── ...
```

Folder `output/` dibuat otomatis saat script dijalankan. File WAV menggunakan timestamp pada namanya agar hasil percakapan tidak saling menimpa.

Direktori model OmniVoice berada di lokasi instalasi OmniVoice milik Anda, terpisah dari folder `~/nova/`.

## 1. Instal Python dan pemutar audio

Perbarui daftar paket Ubuntu dan instal Python serta `paplay`:

```bash
sudo apt update
sudo apt install -y python3 pulseaudio-utils
```

Script tidak membutuhkan library Python pihak ketiga karena menggunakan standard library, termasuk `urllib`, `json`, `subprocess`, dan `pathlib`.

Script mencoba pemutar audio dengan urutan berikut:

1. `paplay`
2. `aplay`
3. `ffplay`

Jika ingin menggunakan fallback lainnya, instal paket yang sesuai:

```bash
sudo apt install -y alsa-utils
```

Atau:

```bash
sudo apt install -y ffmpeg
```

Jika tidak ada pemutar yang berfungsi, file WAV tetap disimpan dan lokasi file ditampilkan di terminal.

## 2. Instal dan jalankan Ollama

Jika Ollama belum terpasang, ikuti petunjuk instalasi resmi di [ollama.com](https://ollama.com/).

Unduh model yang digunakan Nova:

```bash
ollama pull gemma3:1b
```

Pastikan model tersedia:

```bash
ollama list
```

Uji model secara langsung:

```bash
ollama run gemma3:1b
```

Ketik contoh:

```text
Jawab dalam bahasa Indonesia. Perkenalkan dirimu sebagai Nova dalam dua kalimat.
```

Keluar dari sesi pengujian sesuai instruksi CLI Ollama.

### Pastikan API Ollama aktif

Script mengirim permintaan ke:

```text
http://localhost:11434/api/chat
```

Periksa API dengan:

```bash
curl -sS http://localhost:11434/api/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gemma3:1b",
    "messages": [
      {
        "role": "user",
        "content": "Halo Nova, jawab dalam bahasa Indonesia."
      }
    ],
    "stream": false,
    "options": {
      "num_predict": 128
    }
  }'
```

Jika menerima respons JSON yang berisi `message.content`, API dapat diakses.

Jika koneksi ditolak, pastikan layanan Ollama aktif. Pada sistem yang menggunakan systemd:

```bash
systemctl status ollama
```

Untuk memulai layanan jika diperlukan:

```bash
sudo systemctl start ollama
```

Nama layanan dan cara menjalankannya bisa berbeda bergantung pada metode instalasi.

## 3. Siapkan OmniVoice

Nova menggunakan OmniVoice sebagai server text-to-speech (TTS). Script mengirim teks jawaban ke endpoint:

```text
http://localhost:8080/v1/audio/speech
```

Konfigurasi default:

- Voice: `nova`
- Language: `Indonesian`
- Format respons: `wav`

Pastikan OmniVoice C++ sudah dibangun, file model tersedia, dan server berjalan. Contoh berikut mengikuti susunan direktori instalasi OmniVoice yang digunakan pada proyek ini. Sesuaikan path jika direktori Anda berbeda.

```bash
cd /path/ke/omnivoice.cpp

./build/tts-server \
  --model models/omnivoice-base-Q8_0.gguf \
  --codec models/omnivoice-tokenizer-F32.gguf \
  --device CUDA0 \
  --lang Indonesian \
  --port 8080
```

Jika perangkat tidak mendukung CUDA atau build Anda menggunakan backend lain, sesuaikan opsi `--device` dengan opsi yang didukung oleh build OmniVoice Anda.

Biarkan terminal server tetap terbuka selama Nova digunakan.

> Script mengasumsikan server sudah mengenali profil suara `nova`. Jika profil harus didaftarkan atau dimuat secara khusus oleh server Anda, lakukan langkah tersebut sesuai konfigurasi OmniVoice yang dipakai.

### Periksa endpoint OmniVoice

Endpoint TTS menerima `POST`, jadi pemeriksaan paling langsung adalah mencoba membuat WAV:

```bash
curl -fS http://localhost:8080/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{
    "input": "Halo, saya Nova. Apa kabar?",
    "voice": "nova",
    "language": "Indonesian",
    "response_format": "wav"
  }' \
  --output /tmp/nova-test.wav
```

Periksa file:

```bash
file /tmp/nova-test.wav
```

Jika perintah `curl` gagal, periksa log server OmniVoice dan pastikan nama parameter API sesuai dengan versi server yang digunakan.

## 4. Simpan script

Buat folder proyek:

```bash
mkdir -p ~/nova/output
cd ~/nova
```

Buat file:

```bash
nano assistant.py
```

Salin kode `assistant.py` yang menyertai proyek ini ke file tersebut, lalu simpan. Di Nano, gunakan `Ctrl+O`, tekan `Enter`, lalu `Ctrl+X`.

Script mengatur model dan endpoint melalui konstanta di bagian atas:

```python
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "gemma3:1b"

TTS_URL = "http://localhost:8080/v1/audio/speech"
TTS_VOICE = "nova"
TTS_LANGUAGE = "Indonesian"

OUTPUT_DIR = Path.home() / "nova" / "output"
```

Pastikan `OLLAMA_MODEL` tetap `gemma3:1b` jika ingin mengikuti konfigurasi README ini.

## 5. Jalankan Nova

Pastikan kedua server aktif:

- Ollama di `localhost:11434`
- OmniVoice di `localhost:8080`

Kemudian jalankan:

```bash
python3 ~/nova/assistant.py
```

Tampilan awal yang diharapkan:

```text
====================================================
 NOVA - LOCAL AI VOICE ASSISTANT
====================================================
LLM  : gemma3:1b
Voice: nova
Output: /home/<user>/nova/output
Ketik 'keluar' untuk berhenti.
Ketik 'reset' untuk menghapus riwayat percakapan.
```

Contoh percakapan:

```text
Kamu: Halo Nova, siapa kamu?
Nova sedang berpikir...

Nova: Halo! Saya Nova, asisten AI pribadi kamu. Senang bisa menemani kamu hari ini.

Nova sedang berbicara...
Audio: /home/<user>/nova/output/nova_20261026_120000_123456.wav
```

Teks jawaban aktual akan bergantung pada keluaran model. Contoh di atas hanya ilustrasi.

## Perintah yang tersedia

| Input | Fungsi |
|---|---|
| `reset` | Menghapus riwayat percakapan yang tersimpan di memori program. |
| `keluar` | Menghentikan program. |
| `exit` atau `quit` | Alternatif untuk menghentikan program. |

Riwayat percakapan hanya disimpan selama proses Python berjalan. Script ini tidak menyimpan riwayat chat ke database atau file.

## Konfigurasi dan perilaku

### Batas panjang jawaban

Script menggunakan:

```python
"options": {
    "num_predict": 256
}
```

Ini membatasi jumlah token keluaran yang diminta dari Ollama. Jika jawaban terpotong, naikkan nilainya, misalnya menjadi `384` atau `512`. Jawaban lebih panjang dapat meningkatkan waktu inferensi.

### Mengganti folder audio

Ubah `OUTPUT_DIR`, misalnya:

```python
OUTPUT_DIR = Path.home() / "Music" / "Nova"
```

Pastikan proses memiliki izin menulis ke folder tersebut.

### Mengubah instruksi karakter Nova

Ubah `SYSTEM_PROMPT`. Contoh instruksi saat ini meminta Nova menjawab secara ramah, santai, jelas, dan ringkas dalam bahasa Indonesia tanpa emotikon atau Markdown rumit.

### Mengubah model

Untuk konfigurasi proyek ini, model yang digunakan adalah `gemma3:1b`. Jika model diubah, unduh model terlebih dahulu dengan `ollama pull <nama-model>`, lalu ubah `OLLAMA_MODEL` di script.

## Troubleshooting

### 1. `Tidak bisa terhubung ke server`

Periksa endpoint Ollama:

```bash
curl -sS http://localhost:11434/api/tags
```

Periksa port OmniVoice:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  http://localhost:8080/
```

Endpoint root OmniVoice mungkin tidak menyediakan halaman atau status, jadi respons non-200 pada `/` tidak selalu berarti endpoint TTS mati. Periksa log server dan uji endpoint TTS menggunakan contoh `curl` di atas.

### 2. Ollama menghasilkan jawaban kosong

Uji API Ollama secara terpisah menggunakan contoh di bagian Ollama. Pastikan respons memiliki `message.content` yang tidak kosong.

Jika terjadi berulang, coba uji model langsung:

```bash
ollama run gemma3:1b
```

Periksa juga log Ollama, penggunaan RAM/VRAM, serta apakah model selesai menghasilkan jawaban sebelum batas token tercapai.

### 3. HTTP 400 dari OmniVoice

Baca isi respons error yang dicetak program. Pastikan:

- `answer` tidak kosong.
- Nama voice `nova` dikenali server.
- Bahasa `Indonesian` didukung.
- Nama field request cocok dengan versi API OmniVoice yang terpasang.

### 4. Audio tidak terdengar

Periksa file yang dibuat:

```bash
ls -lh ~/nova/output/
file ~/nova/output/*.wav
```

Uji pemutar secara langsung:

```bash
paplay ~/nova/output/NAMA_FILE.wav
```

Ganti `NAMA_FILE.wav` dengan nama file yang benar. Pastikan perangkat audio Linux aktif dan proses memiliki akses ke audio server.

### 5. Model terasa lambat

Kecepatan dipengaruhi CPU/GPU, RAM/VRAM, panjang riwayat percakapan, panjang jawaban, dan waktu sintesis OmniVoice. `gemma3:1b` relatif kecil, tetapi bukan jaminan respons instan pada semua perangkat.

### 6. File WAV tidak valid

Script memeriksa header `RIFF` untuk memastikan respons tampak seperti WAV. Jika pemeriksaan gagal, periksa respons server dan log OmniVoice. Jangan mengganti ekstensi file secara manual jika data yang diterima bukan WAV.

## Privasi dan mode offline

Setelah model tersedia secara lokal, permintaan chat dikirim ke Ollama lokal dan permintaan sintesis dikirim ke OmniVoice lokal. Script tidak mengirim teks percakapan ke API cloud secara eksplisit.

Namun, status offline sepenuhnya bergantung pada konfigurasi Ollama, OmniVoice, sistem operasi, serta layanan lain yang berjalan. Mengunduh model dan paket saat instalasi tetap membutuhkan internet. Untuk penggunaan offline, unduh semua model dan dependensi yang dibutuhkan terlebih dahulu, lalu putuskan koneksi internet dan uji seluruh alur.

## Batasan saat ini

- Input masih berupa teks yang diketik; belum ada speech-to-text atau deteksi wake word.
- Output suara dihasilkan setelah jawaban teks selesai; belum streaming audio.
- Riwayat percakapan hilang ketika program dihentikan.
- Tidak ada GUI.
- Script tidak otomatis memulai ulang server jika server berhenti.
- Profil suara `nova` harus sudah tersedia di server OmniVoice.

## Checklist sebelum digunakan

- [ ] Python 3 tersedia.
- [ ] Ollama berjalan dan `gemma3:1b` sudah diunduh.
- [ ] API Ollama dapat diakses di `localhost:11434`.
- [ ] OmniVoice server berjalan di `localhost:8080`.
- [ ] Profil suara `nova` tersedia.
- [ ] Uji endpoint TTS berhasil menghasilkan WAV.
- [ ] Pemutar audio Linux berfungsi.
- [ ] `assistant.py` dijalankan dengan `python3 ~/nova/assistant.py`.

---

**Ringkasan:** Nova menggunakan `gemma3:1b` untuk menghasilkan jawaban berbahasa Indonesia dan OmniVoice untuk membacakannya dengan suara `nova`. Jalankan Ollama dan OmniVoice server terlebih dahulu, lalu mulai program dengan `python3 ~/nova/assistant.py`.
