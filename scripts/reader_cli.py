import sys
import os
import time
import requests
import subprocess
import threading
import argparse

API_BASE = "http://localhost:8000/api"

# Global state
session_id = None
current_tts_process = None
barge_in_event = threading.Event()


def play_tts(text):
    global current_tts_process
    barge_in_event.clear()

    if os.environ.get("TTS_DRY_RUN") == "1":
        # Simulate quick reading
        time.sleep(min(2, len(text) / 200))  # Faster for test
        return not barge_in_event.is_set()

    # We use spd-say with -w (wait) and -l es (spanish language)
    try:
        proc = subprocess.Popen(["spd-say", "-w", "-l", "es", text])
        current_tts_process = proc
        proc.wait()
        ret = proc.returncode
    except Exception as e:
        print(f"\n[TTS Fallback - No se pudo reproducir audio]: {e}\n> ", end="")
        time.sleep(2)  # simulated fallback
        ret = 0

    current_tts_process = None
    return ret == 0 and not barge_in_event.is_set()


def reader_thread():
    while True:
        try:
            resp = requests.get(f"{API_BASE}/reader/session", params={"session_id": session_id})
            if resp.status_code == 200:
                data = resp.json()
                if data.get("reader_state") == "reading" and not data.get("pending"):
                    # Needs next chunk
                    n_resp = requests.get(f"{API_BASE}/reader/session/next", params={"session_id": session_id})
                    if n_resp.status_code == 200:
                        n_data = n_resp.json()
                        chunk = n_data.get("chunk")
                        if chunk:
                            print(f"\n[SISTEMA LEE]: {chunk['text']}\n> ", end="", flush=True)
                            completed = play_tts(chunk["text"])
                            if completed:
                                requests.post(
                                    f"{API_BASE}/reader/session/commit",
                                    json={"session_id": session_id, "chunk_index": chunk["chunk_index"]},
                                )
                elif data.get("done"):
                    print("\n[FIN DEL LIBRO]")
                    break
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(0.5)


def main():
    global session_id, current_tts_process
    parser = argparse.ArgumentParser(description="Cliente CLI del Lector Conversacional")
    parser.add_argument("book_id", help="El ID del texto a leer (ej. mi_libro)")
    args = parser.parse_args()

    session_id = f"cli_{int(time.time())}"

    print(f"--- Arrancando cliente conversacional para: {args.book_id} ---")
    print("Verificando servidor backend...")

    try:
        requests.post(f"{API_BASE}/reader/rescan")
    except requests.exceptions.ConnectionError:
        print("ERROR: Servidor backend no disponible. Asegúrese de correr openclaw_direct_chat.py")
        sys.exit(1)

    resp = requests.post(f"{API_BASE}/reader/session/start", json={"session_id": session_id, "book_id": args.book_id})
    if not resp.json().get("ok"):
        print("Error al iniciar sesión:", resp.json())
        sys.exit(1)

    t = threading.Thread(target=reader_thread, daemon=True)
    t.start()

    print("\nComandos útiles: 'pará', '¿qué quiso decir?', 'seguí', 'andá al párrafo 3'")
    print("El audio comenzará en breve. Presione Enter o escriba para interrumpir.\n")

    while True:
        try:
            cmd = input("> ")
            if not cmd.strip():
                # Just a pause / barge_in if empty enter
                cmd = "pará"

            # Interrumpir audio / barge-in
            barge_in_event.set()
            if current_tts_process:
                subprocess.run(["spd-say", "-C"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                current_tts_process = None

            requests.post(f"{API_BASE}/reader/session/barge_in", json={"session_id": session_id})

            if cmd.lower() in ("pará", "pausá"):
                print("[Lectura pausada. Escribe 'seguí' para retomar]")
                continue

            # Chat context
            print(f"[Procesando tu mensaje... '{cmd}']")
            chat_resp = requests.post(f"{API_BASE}/chat/message", json={"session_id": session_id, "message": cmd})

            if chat_resp.status_code == 200:
                ans = chat_resp.json().get("response")
                print(f"[SISTEMA RESPONDE]: {ans}")

            time.sleep(0.5)  # debounce

        except (EOFError, KeyboardInterrupt):
            print("\nSaliendo...")
            # Cancel current audio if any
            if current_tts_process:
                subprocess.run(["spd-say", "-C"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            break


if __name__ == "__main__":
    main()
