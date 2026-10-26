
cat > /mnt/d/tts-test-wsl/start_nova.sh <<'BASH'
#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="/mnt/d/tts-test-wsl"
VOICE_DIR="$ROOT/profile"
SERVER_DIR="$ROOT/src/omnivoice.cpp"
PORT=8080
API="http://127.0.0.1:$PORT"

# Pastikan profil tersedia
for file in "$VOICE_DIR/ref.txt" "$VOICE_DIR/ref.rvq"; do
    if [[ ! -f "$file" ]]; then
        echo "ERROR: File profil tidak ditemukan: $file"
        exit 1
    fi
done

# Cegah menjalankan server kedua pada port yang sama
if curl -fsS "$API/health" >/dev/null 2>&1; then
    echo "ERROR: Server sudah berjalan pada port $PORT."
    echo "Hentikan server lama terlebih dahulu dengan Ctrl+C."
    exit 1
fi

cd "$SERVER_DIR"

echo "=== Menjalankan OmniVoice ==="
./build/tts-server \
    --model models/omnivoice-base-Q8_0.gguf \
    --codec models/omnivoice-tokenizer-F32.gguf \
    --device CUDA0 \
    --lang Indonesian \
    --port "$PORT" &
SERVER_PID=$!

cleanup() {
    echo
    echo "Menghentikan OmniVoice..."
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Menunggu API OmniVoice siap..."
READY=0
for i in $(seq 1 180); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "ERROR: Proses server berhenti."
        exit 1
    fi

    if curl -fsS "$API/health" >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 1
done

if [[ "$READY" != 1 ]]; then
    echo "ERROR: Server tidak siap dalam 180 detik."
    exit 1
fi


echo "=== Mendaftarkan voice nova ==="

REF_TEXT=$(python3 -c \
    'import json,sys; print(json.dumps(open(sys.argv[1]).read().strip()))' \
    "$VOICE_DIR/ref.txt")
RVQ_B64=$(base64 -w0 "$VOICE_DIR/ref.rvq")

PAYLOAD=$(python3 -c \
    'import json,sys; print(json.dumps({"name":"nova","ref_text":json.loads(sys.argv[1]),"rvq_b64":sys.argv[2]}))' \
    "$REF_TEXT" "$RVQ_B64")

HTTP_CODE=$(curl -sS -o /tmp/nova-register-response.json \
    -w '%{http_code}' \
    -X POST "$API/v1/audio/voices" \
    -H 'Content-Type: application/json' \
    -d "$PAYLOAD") || {
        echo "ERROR: Gagal menghubungi endpoint registrasi."
        exit 1
    }

echo "HTTP registrasi: $HTTP_CODE"
cat /tmp/nova-register-response.json
echo

echo "=== Memverifikasi daftar voice ==="
VOICES=$(curl -fsS "$API/v1/audio/voices") || {
    echo "ERROR: Tidak dapat membaca daftar voice."
    exit 1
}

echo "$VOICES"

if ! printf '%s' "$VOICES" | python3 -c \
    'import json,sys; d=json.load(sys.stdin); sys.exit(0 if any(v.get("name")=="nova" for v in d.get("voices",[])) else 1)'
then
    echo "ERROR: Registrasi belum menghasilkan voice nova."
    echo "Periksa respons registrasi dan implementasi endpoint server."
    exit 1
fi

echo "Voice nova berhasil terdaftar dan terverifikasi."

echo "Nova siap digunakan. Tekan Ctrl+C untuk menghentikan server."
wait "$SERVER_PID"
BASH

chmod +x /mnt/d/tts-test-wsl/start_nova.sh
/mnt/d/tts-test-wsl/start_nova.sh
