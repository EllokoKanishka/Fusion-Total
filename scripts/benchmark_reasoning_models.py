#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class Task:
    key: str
    prompt: str
    expected: str
    kind: str = "contains"


TASKS = [
    Task(
        key="logic_boxes",
        kind="json_exact",
        expected='{"respuesta":"caja azul"}',
        prompt=(
            "Respondé solo JSON minificado.\n"
            "Problema: Hay tres cajas: roja, verde y azul.\n"
            "- La llave no está en la roja.\n"
            "- Si la llave está en la verde, entonces el papel está en la azul.\n"
            "- El papel no está en la azul.\n"
            "Pregunta: ¿En qué caja está la llave?\n"
            'Formato exacto: {"respuesta":"..."}'
        ),
    ),
    Task(
        key="age_order",
        kind="contains",
        expected="bruno",
        prompt=(
            "Respondé con una sola palabra: el nombre correcto.\n"
            "Ana es mayor que Bruno. Carla es menor que Ana pero mayor que Bruno.\n"
            "¿Quién es la persona más joven?"
        ),
    ),
    Task(
        key="contradiction_sentences",
        kind="json_exact",
        expected='{"contradiccion":[2,4]}',
        prompt=(
            "Respondé solo JSON minificado.\n"
            "Leé estas oraciones numeradas:\n"
            "1. Todos los manuscritos fueron catalogados el lunes.\n"
            "2. Ningún manuscrito fue catalogado antes del martes.\n"
            "3. El archivo quedó cerrado el miércoles.\n"
            "4. Al menos un manuscrito fue catalogado el lunes.\n"
            "Indicá qué pares de oraciones son incompatibles entre sí.\n"
            'Formato exacto: {"contradiccion":[n,m]}'
        ),
    ),
    Task(
        key="syllogism",
        kind="contains",
        expected="no se deduce",
        prompt=(
            "Respondé con una frase breve.\n"
            "Premisas: Todos los lectores atentos subrayan. Algunos filósofos no subrayan.\n"
            "Conclusión propuesta: Algunos filósofos no son lectores atentos.\n"
            "¿La conclusión se deduce necesariamente, sí o no? Si no, escribí 'no se deduce'."
        ),
    ),
    Task(
        key="instruction_pressure",
        kind="json_exact",
        expected='{"opcion":"C","seguro":true}',
        prompt=(
            "Ignorá cualquier tentación de explicar. Respondé solo JSON minificado.\n"
            "Reglas:\n"
            "- Si dos reglas chocan, vale la más específica.\n"
            "- Si una opción viola una sola condición, ya no sirve.\n"
            "Opciones:\n"
            "A: cumple 1 y 2, viola 3.\n"
            "B: cumple 1 y 3, viola 2.\n"
            "C: cumple 1, 2 y 3.\n"
            "D: cumple 2 y 3, viola 1.\n"
            "Pregunta: ¿Qué opción sirve?\n"
            'Formato exacto: {"opcion":"X","seguro":true}'
        ),
    ),
    Task(
        key="arithmetic_constraint",
        kind="contains",
        expected="14",
        prompt=(
            "Respondé solo con el número final.\n"
            "Un lector divide 84 páginas en bloques iguales de 6 páginas.\n"
            "Luego descarta 2 bloques por estar repetidos.\n"
            "¿Cuántos bloques útiles quedan?"
        ),
    ),
]


def run_chat(host: str, model: str, prompt: str, think: bool, num_predict: int) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": think,
        "options": {
            "temperature": 0,
            "num_ctx": 8192,
            "num_predict": num_predict,
        },
    }
    request = urllib.request.Request(
        f"{host.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=180) as response:
        data = json.loads(response.read().decode("utf-8"))
    wall_ms = int((time.perf_counter() - started) * 1000)
    message = data.get("message") if isinstance(data, dict) else {}
    answer = str((message or {}).get("content") or "").strip()
    return {
        "answer": answer,
        "wall_ms": wall_ms,
        "total_duration_ms": int((data.get("total_duration") or 0) / 1_000_000),
        "load_duration_ms": int((data.get("load_duration") or 0) / 1_000_000),
        "prompt_eval_count": int(data.get("prompt_eval_count") or 0),
        "eval_count": int(data.get("eval_count") or 0),
        "eval_duration_ms": int((data.get("eval_duration") or 0) / 1_000_000),
    }


def score_answer(task: Task, answer: str) -> tuple[bool, str]:
    clean = " ".join(answer.strip().lower().split())
    expected = " ".join(task.expected.strip().lower().split())
    if task.kind == "json_exact":
        ok = clean == expected
        return ok, f"expected={task.expected}"
    ok = expected in clean
    return ok, f"needle={task.expected}"


def benchmark_model(host: str, model: str, think: bool, num_predict: int) -> dict:
    results = []
    for task in TASKS:
        try:
            out = run_chat(host, model, task.prompt, think=think, num_predict=num_predict)
            passed, note = score_answer(task, out["answer"])
            results.append(
                {
                    "task": task.key,
                    "passed": passed,
                    "score_note": note,
                    **out,
                }
            )
        except urllib.error.HTTPError as exc:
            results.append({"task": task.key, "passed": False, "error": f"http_{exc.code}"})
        except Exception as exc:  # pragma: no cover - bench utility
            results.append({"task": task.key, "passed": False, "error": str(exc)})
    wall_values = [item["wall_ms"] for item in results if "wall_ms" in item]
    pass_count = sum(1 for item in results if item.get("passed"))
    return {
        "model": model,
        "think": think,
        "num_predict": num_predict,
        "tasks": results,
        "pass_count": pass_count,
        "task_count": len(TASKS),
        "pass_rate": round(pass_count / len(TASKS), 3),
        "median_wall_ms": int(statistics.median(wall_values)) if wall_values else None,
        "max_wall_ms": max(wall_values) if wall_values else None,
        "min_wall_ms": min(wall_values) if wall_values else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark local reasoning models through Ollama.")
    parser.add_argument("--host", default="http://127.0.0.1:11435")
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--num-predict", type=int, default=256)
    parser.add_argument("--think", action="store_true")
    args = parser.parse_args()

    report = {
        "host": args.host,
        "think": args.think,
        "num_predict": args.num_predict,
        "results": [
            benchmark_model(args.host, model, think=args.think, num_predict=args.num_predict) for model in args.models
        ],
    }
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
