#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fusion_reader_v2.conversation import ChatProvider, ChatResult, ConversationCore, OllamaChatProvider


DOCUMENT_TEXT = (
    "Fedro abre la serie de discursos afirmando que Eros es uno de los dioses mas antiguos y que su presencia "
    "empuja a los seres humanos a avergonzarse de lo bajo y a buscar lo noble.\n\n"
    "Pausanias distingue entre un amor vulgar, ligado al cuerpo y al provecho inmediato, y un amor celeste que "
    "se orienta a la formacion del alma y a la virtud compartida.\n\n"
    "Eriximaco traslada la cuestion a una armonia mas amplia y piensa el amor como principio de proporcion, salud "
    "y orden, casi como una ley que atraviesa cuerpo, musica y cosmos.\n\n"
    "Aristofanes cuenta el mito de los seres esfericos partidos y presenta el amor como busqueda de reunion con "
    "la mitad perdida; su fuerza esta en la nostalgia de una unidad originaria.\n\n"
    "Agaton elogia a Eros por su belleza, juventud y delicadeza, pero Socrates pone en cuestion ese tono celebratorio "
    "y lo obliga a pasar del elogio retorico al examen conceptual.\n\n"
    "Cuando Socrates recuerda a Diotima, el amor deja de ser un dios pleno y pasa a entenderse como un daimon intermedio: "
    "ni sabio ni ignorante, ni rico ni pobre, sino tension hacia lo que falta. Esa tension ordena una escalera que sube "
    "desde los cuerpos bellos hasta la contemplacion de la Belleza en si.\n\n"
    "En esa subida, Diotima insiste en que amar no es poseer un objeto inmovil, sino engendrar en lo bello: hijos, obras, "
    "leyes, conocimiento, formas de vida. El eros se vuelve productividad espiritual.\n\n"
    "Alcibiades irrumpe al final y vuelve a encarnar el problema en una escena viva: ama a Socrates, pero no logra dominar "
    "ni traducir del todo la fuerza de ese vinculo. El banquete queda entonces abierto entre teoria, deseo, cuerpo, ironia y verdad."
)


TASKS = [
    {
        "key": "current_chunk_analysis",
        "question": "Analiza filosoficamente el bloque actual y explica que problema abre sobre eros, falta y conocimiento.",
        "current_chunk": (
            "Cuando Socrates recuerda a Diotima, el amor deja de ser un dios pleno y pasa a entenderse como un daimon intermedio: "
            "ni sabio ni ignorante, ni rico ni pobre, sino tension hacia lo que falta."
        ),
        "expected_any": ["diotima", "falta", "daimon", "intermedio"],
    },
    {
        "key": "compare_discourses",
        "question": "Compara el discurso de Aristofanes con el de Diotima y marca una diferencia conceptual fuerte, no solo de estilo.",
        "current_chunk": (
            "Aristofanes cuenta el mito de los seres esfericos partidos y presenta el amor como busqueda de reunion con la mitad perdida."
        ),
        "expected_any": ["aristofanes", "diotima", "mito", "falta", "belleza", "escalera"],
    },
    {
        "key": "focus_obedience",
        "question": "Sin salir del documento, responde si en este material Socrates aparece como solucion o como problema, y justifica.",
        "current_chunk": (
            "Agaton elogia a Eros por su belleza, juventud y delicadeza, pero Socrates pone en cuestion ese tono celebratorio."
        ),
        "expected_any": ["socrates", "problema", "solucion", "documento"],
    },
    {
        "key": "reconstruct_argument",
        "question": "Reconstruye en pasos la logica de Diotima desde la falta hasta la Belleza en si, pero sin hacer lista escolar vacia.",
        "current_chunk": (
            "Esa tension ordena una escalera que sube desde los cuerpos bellos hasta la contemplacion de la Belleza en si."
        ),
        "expected_any": ["falta", "belleza", "escalera", "diotima"],
    },
    {
        "key": "critical_reader",
        "question": "Someté a critica la posicion de Fedro y deci donde se queda filosoficamente corta frente a lo que despues trae Diotima.",
        "current_chunk": (
            "Fedro abre la serie de discursos afirmando que Eros es uno de los dioses mas antiguos y que empuja a buscar lo noble."
        ),
        "expected_any": ["fedro", "diotima", "corta", "virtud", "falta"],
    },
]


def snapshot_for(current_chunk: str) -> dict:
    return {
        "doc_id": "banquete",
        "title": "El Banquete",
        "current": 1,
        "total": 8,
        "current_chunk": current_chunk,
        "previous_chunk": "",
        "next_chunk": "",
        "document_text": DOCUMENT_TEXT,
        "notes": [],
        "main_document": {
            "doc_id": "banquete",
            "title": "El Banquete",
            "text": DOCUMENT_TEXT,
            "chunks": [
                {"chunk_number": idx + 1, "text": chunk.strip()}
                for idx, chunk in enumerate(DOCUMENT_TEXT.split("\n\n"))
            ],
        },
        "document_chunks": [
            {"chunk_number": idx + 1, "text": chunk.strip()} for idx, chunk in enumerate(DOCUMENT_TEXT.split("\n\n"))
        ],
        "reference_documents": [],
        "laboratory_focus": {},
        "laboratory_mode": {"mode": "document", "label": "Anclado al texto"},
    }


class GemmaLocalProvider(ChatProvider):
    name = "gemma_bf16_local"

    def __init__(self, model_dir: str, device: str = "auto") -> None:
        self.model_dir = model_dir
        self.device = device
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
        if device == "cpu":
            self.model = AutoModelForCausalLM.from_pretrained(
                model_dir,
                dtype=torch.bfloat16,
                device_map={"": "cpu"},
                local_files_only=True,
            )
        else:
            self.model = AutoModelForCausalLM.from_pretrained(
                model_dir,
                dtype=torch.bfloat16,
                device_map="auto",
                max_memory={0: "22GiB", "cpu": "64GiB"},
                local_files_only=True,
            )
        self.model.eval()

    def chat(
        self, messages: list[dict], model: str = "", think: bool | None = None, num_predict: int | None = None
    ) -> ChatResult:
        started = time.perf_counter()
        max_new_tokens = int(num_predict or 384)
        rendered_messages = []
        for item in messages:
            role = (
                "assistant" if item.get("role") == "assistant" else "user" if item.get("role") == "user" else "system"
            )
            rendered_messages.append({"role": role, "content": str(item.get("content") or "")})
        prompt = self.tokenizer.apply_chat_template(
            rendered_messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        model_device = self.model.device if self.device != "cpu" else torch.device("cpu")
        inputs = self.tokenizer(prompt, return_tensors="pt").to(model_device)
        with torch.inference_mode():
            output = self.model.generate(
                **inputs,
                do_sample=False,
                max_new_tokens=max_new_tokens,
                use_cache=True,
                eos_token_id=self.tokenizer.eos_token_id,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        generated = output[0][inputs["input_ids"].shape[1] :]
        answer = self.tokenizer.decode(generated, skip_special_tokens=True).strip()
        return ChatResult(
            ok=bool(answer),
            answer=answer,
            model=model or "google/gemma-4-E4B-it-bf16",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


def coarse_score(answer: str, expected_any: list[str]) -> dict:
    clean = " ".join(str(answer or "").lower().split())
    hits = [term for term in expected_any if term.lower() in clean]
    penalties = []
    if "youtube" in clean or "internet" in clean or "navegador" in clean:
        penalties.append("salio_del_documento")
    if len(clean) < 80:
        penalties.append("demasiado_breve")
    return {
        "hits": hits,
        "hit_count": len(hits),
        "penalties": penalties,
        "score": len(hits) - len(penalties),
    }


def build_core(provider_name: str, gemma_dir: str, gemma_device: str) -> ConversationCore:
    if provider_name == "qwen":
        provider = OllamaChatProvider(default_model="qwen3:14b-q8_0")
    elif provider_name == "gptoss_multilingual_reasoner_q8":
        provider = OllamaChatProvider(default_model="gpt-oss:20b-multilingual-reasoner-q8")
    elif provider_name == "gptoss_sanguine_q8":
        provider = OllamaChatProvider(default_model="gpt-oss:20b-sanguine-q8")
    elif provider_name == "ministral_instruct":
        provider = OllamaChatProvider(default_model="ministral:14b-instruct-q8")
    elif provider_name == "ministral_reasoning":
        provider = OllamaChatProvider(default_model="ministral:14b-reasoning-q8")
    elif provider_name == "nemo":
        provider = OllamaChatProvider(default_model="mistral-nemo:12b-q8")
    elif provider_name == "gptoss":
        provider = OllamaChatProvider(default_model="gpt-oss:20b")
    elif provider_name == "gptoss_q8":
        provider = OllamaChatProvider(default_model="gpt-oss:20b-q8_0")
    elif provider_name == "gemma_ollama":
        provider = OllamaChatProvider(default_model="gemma4:e4b")
    elif provider_name == "gemma":
        provider = GemmaLocalProvider(gemma_dir, device=gemma_device)
    else:
        raise ValueError(provider_name)
    return ConversationCore(provider=provider)


def run_suite(provider_name: str, gemma_dir: str, gemma_device: str, modes: list[str]) -> dict:
    core = build_core(provider_name, gemma_dir, gemma_device)
    out = {"provider_name": provider_name, "modes": {}}
    for mode in modes:
        mode_rows = []
        for task in TASKS:
            snapshot = snapshot_for(task["current_chunk"])
            started = time.perf_counter()
            result = core.ask(task["question"], snapshot=snapshot, history=[], reasoning_mode=mode)
            wall_ms = int((time.perf_counter() - started) * 1000)
            scored = coarse_score(result.answer, task["expected_any"])
            mode_rows.append(
                {
                    "task": task["key"],
                    "ok": result.ok,
                    "model": result.model,
                    "reasoning_mode": result.reasoning_mode,
                    "reasoning_passes": result.reasoning_passes,
                    "duration_ms": result.duration_ms,
                    "wall_ms": wall_ms,
                    "score": scored["score"],
                    "hit_count": scored["hit_count"],
                    "hits": scored["hits"],
                    "penalties": scored["penalties"],
                    "answer": result.answer,
                }
            )
        out["modes"][mode] = mode_rows
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gemma-dir", default="runtime/models/google_gemma-4-E4B-it_bf16")
    parser.add_argument("--out", default="")
    parser.add_argument(
        "--providers",
        nargs="+",
        choices=[
            "qwen",
            "gptoss_multilingual_reasoner_q8",
            "gptoss_sanguine_q8",
            "ministral_instruct",
            "ministral_reasoning",
            "nemo",
            "gptoss",
            "gptoss_q8",
            "gemma",
            "gemma_ollama",
        ],
        default=["qwen", "gemma_ollama"],
    )
    parser.add_argument("--gemma-device", choices=["auto", "cpu"], default="auto")
    parser.add_argument(
        "--modes", nargs="+", choices=["normal", "thinking", "supreme"], default=["normal", "thinking", "supreme"]
    )
    args = parser.parse_args()

    report = {
        "generated_at": int(time.time()),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "gemma_dir": str(Path(args.gemma_dir).resolve()),
        "gemma_device": args.gemma_device,
        "results": [
            run_suite(provider_name, args.gemma_dir, args.gemma_device, args.modes) for provider_name in args.providers
        ],
    }
    rendered = json.dumps(report, ensure_ascii=True, indent=2)
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("FUSION_READER_CHAT_TEMPERATURE", "0.2")
    os.environ.setdefault("FUSION_READER_CHAT_NUM_CTX", "8192")
    os.environ.setdefault("FUSION_READER_CHAT_NUM_PREDICT_NORMAL", "220")
    os.environ.setdefault("FUSION_READER_CHAT_NUM_PREDICT_THINKING", "420")
    os.environ.setdefault("FUSION_READER_CHAT_NUM_PREDICT_SUPREME", "520")
    os.environ.setdefault("FUSION_READER_CHAT_NUM_PREDICT_SUPREME_REVIEW", "260")
    os.environ.setdefault("FUSION_READER_CHAT_NUM_PREDICT_SUPREME_FINAL", "320")
    raise SystemExit(main())
