import pexpect
import time
import os


def main():
    os.environ["OPENCLAW_RUNTIME_DIR"] = os.path.abspath(".")
    os.environ["OPENCLAW_STATE_DIR"] = os.path.abspath("state")
    os.environ["DIRECT_CHAT_HTTP_PORT"] = "8000"

    print("--- EMULADOR DE USUARIO (PEXPECT) ---")
    child = pexpect.spawn("python3 scripts/reader_cli.py diego_audio", encoding="utf-8", timeout=25)

    # Wait for the first chunk to be read
    child.expect("SISTEMA LEE", timeout=10)
    print("---", child.after, child.readline().strip())

    # Wait for the auto-advance second chunk
    child.expect("SISTEMA LEE", timeout=10)
    print("---", child.after, child.readline().strip())

    # Wait just a bit while it reads chunk 2, then interrupt
    time.sleep(1)
    print("\\n[Usuario interrumpe y pregunta]")
    child.sendline("¿qué quiso decir acá?")

    # Expect system response
    child.expect("SISTEMA RESPONDE", timeout=10)
    print("---", child.after, child.readline().strip())

    time.sleep(1)
    print("\\n[Usuario pide resumen]")
    child.sendline("resumime este bloque")

    child.expect("SISTEMA RESPONDE", timeout=10)
    print("---", child.after, child.readline().strip())

    time.sleep(1)
    print("\\n[Usuario pide seguir]")
    child.sendline("seguí")

    # Expect it to resume
    child.expect("SISTEMA RESPONDE", timeout=10)  # 'seguí' -> Entendido, reanudo
    print("---", child.after, child.readline().strip())

    # Expect it to read next chunk
    # Wait, 'seguí' resumes chunk 2 because it was interrupted!
    child.expect("SISTEMA LEE", timeout=10)
    print("---", child.after, child.readline().strip())

    time.sleep(1)
    print("\\n[Usuario pide saltar al párrafo 4]")
    child.sendline("andá al párrafo 4")

    child.expect("SISTEMA LEE", timeout=10)
    print("---", child.after, child.readline().strip())

    child.sendline("salir")
    child.close()
    print("\\n--- FIN TEST ---")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Error en pexpect:", e)
