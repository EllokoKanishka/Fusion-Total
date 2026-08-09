from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from unicodedata import normalize

from .config import environment_copy, environment_has, environment_value
from .owned_subprocess import CancelSignal, run_owned


@dataclass(frozen=True)
class ChatResult:
    ok: bool
    answer: str = ""
    model: str = ""
    detail: str = ""
    duration_ms: int = 0
    reasoning_mode: str = ""
    reasoning_passes: int = 1


@dataclass(frozen=True)
class ReasoningProfile:
    key: str
    label: str
    description: str
    think: bool
    num_predict: int
    passes: int = 1
    review_num_predict: int = 0
    final_num_predict: int = 0


class ChatProvider:
    name = "base"

    def health(self) -> dict:
        return {"ok": False, "provider": self.name, "detail": "not_implemented"}

    def chat(
        self, messages: list[dict], model: str = "", think: bool | None = None, num_predict: int | None = None
    ) -> ChatResult:
        return ChatResult(False, model=model, detail="not_implemented")


class OllamaChatProvider(ChatProvider):
    name = "ollama"

    def __init__(self, base_url: str = "", default_model: str = "", timeout_seconds: float | None = None) -> None:
        self.base_url = (base_url or environment_value("FUSION_READER_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip(
            "/"
        )
        self.default_model = default_model or environment_value("FUSION_READER_CHAT_MODEL") or "qwen3:14b-q8_0"
        self.timeout_seconds = timeout_seconds or float(environment_value("FUSION_READER_CHAT_TIMEOUT", "120") or "120")
        self.think = (environment_value("FUSION_READER_CHAT_THINK", "0") or "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.num_predict = int(
            environment_value("FUSION_READER_CHAT_NUM_PREDICT", "1024" if self.think else "384") or "384"
        )
        self.normal_num_predict = int(environment_value("FUSION_READER_CHAT_NUM_PREDICT_NORMAL", "384") or "384")
        self.thinking_num_predict = int(
            environment_value(
                "FUSION_READER_CHAT_NUM_PREDICT_THINKING", str(max(self.num_predict, 1024 if self.think else 1536))
            )
            or str(max(self.num_predict, 1024 if self.think else 1536))
        )

    def health(self) -> dict:
        req = urllib.request.Request(f"{self.base_url}/api/tags", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return {
                "ok": False,
                "provider": self.name,
                "url": self.base_url,
                "model": self.default_model,
                "detail": f"http_{exc.code}",
            }
        except Exception as exc:
            return {
                "ok": False,
                "provider": self.name,
                "url": self.base_url,
                "model": self.default_model,
                "detail": str(exc),
            }
        models = data.get("models") if isinstance(data, dict) else []
        available = []
        if isinstance(models, list):
            available = [str(item.get("name") or item.get("model") or "") for item in models if isinstance(item, dict)]
        model_present = self.default_model in available if available else None
        return {
            "ok": True,
            "provider": self.name,
            "url": self.base_url,
            "model": self.default_model,
            "model_present": model_present,
            "available_models": available[:12],
            "detail": "ready",
        }

    def install_model(self, model: str = "", *, cancel_event: CancelSignal | None = None) -> dict:
        """Install one explicitly configured Ollama model without invoking a shell."""
        selected_model = str(model or self.default_model).strip()
        if not selected_model:
            return {"ok": False, "provider": self.name, "detail": "missing_model"}
        ollama = shutil.which("ollama")
        if not ollama:
            return {
                "ok": False,
                "provider": self.name,
                "model": selected_model,
                "detail": "ollama_cli_unavailable",
            }
        try:
            result = run_owned(
                [ollama, "pull", selected_model],
                timeout=30 * 60,
                cancel_event=cancel_event,
                text=True,
                output_limit=256 * 1024,
                check=False,
            )
        except Exception as exc:
            return {
                "ok": False,
                "provider": self.name,
                "model": selected_model,
                "detail": str(exc),
            }
        if result.returncode != 0:
            detail = str(result.stderr or result.stdout or "ollama_pull_failed").strip()
            return {
                "ok": False,
                "provider": self.name,
                "model": selected_model,
                "detail": detail[-2000:] or "ollama_pull_failed",
            }
        health = self.health()
        installed = health.get("model_present") is not False
        return {
            "ok": bool(health.get("ok")) and installed,
            "provider": self.name,
            "model": selected_model,
            "detail": "installed" if installed else "model_not_installed",
        }

    def chat(
        self, messages: list[dict], model: str = "", think: bool | None = None, num_predict: int | None = None
    ) -> ChatResult:
        started = time.perf_counter()
        selected_model = model or self.default_model
        selected_think = self.think if think is None else bool(think)
        selected_num_predict = int(
            num_predict
            if num_predict is not None
            else (self.thinking_num_predict if selected_think else self.normal_num_predict)
        )
        payload = {
            "model": selected_model,
            "messages": messages,
            "stream": False,
            "think": selected_think,
            "options": {
                "temperature": float(environment_value("FUSION_READER_CHAT_TEMPERATURE", "0.4") or "0.4"),
                "num_ctx": int(environment_value("FUSION_READER_CHAT_NUM_CTX", "32768") or "32768"),
                "num_predict": selected_num_predict,
            },
        }
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            message = data.get("message") if isinstance(data, dict) else None
            answer = str((message or {}).get("content") or "").strip()
            if not answer:
                return ChatResult(
                    False,
                    model=selected_model,
                    detail="empty_answer",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            return ChatResult(
                True, answer=answer, model=selected_model, duration_ms=int((time.perf_counter() - started) * 1000)
            )
        except urllib.error.HTTPError as exc:
            return ChatResult(
                False,
                model=selected_model,
                detail=f"http_{exc.code}",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            return ChatResult(
                False, model=selected_model, detail=str(exc), duration_ms=int((time.perf_counter() - started) * 1000)
            )


class OpenClawChatProvider(ChatProvider):
    """Run a text-only Fusion dialogue turn through an isolated OpenClaw agent."""

    name = "openclaw"

    def __init__(
        self,
        command: str = "",
        agent: str = "fusion-dialogue",
        default_model: str = "openai/gpt-5.6-sol",
        timeout_seconds: float = 180.0,
        enabled: bool = True,
        environment: dict[str, str] | None = None,
    ) -> None:
        self.command = (
            command
            or environment_value("FUSION_READER_OPENCLAW_BIN")
            or str(Path.home() / ".openclaw" / "bin" / "openclaw")
        )
        self.agent = str(agent or "fusion-dialogue").strip()
        self.default_model = str(default_model or "openai/gpt-5.6-sol").strip()
        self.timeout_seconds = max(10.0, float(timeout_seconds))
        self.enabled = bool(enabled)
        self.environment = dict(environment) if environment is not None else None

    def available(self) -> bool:
        if not self.enabled or self.agent != "fusion-dialogue":
            return False
        if os.path.isabs(self.command):
            return Path(self.command).is_file()
        return shutil.which(self.command) is not None

    def health(self) -> dict:
        available = self.available()
        return {
            "ok": available,
            "provider": self.name,
            "command": self.command,
            "agent": self.agent,
            "model": self.default_model,
            "cloud": True,
            "session_mode": "fresh_per_turn",
            "detail": "configured" if available else ("disabled" if not self.enabled else "openclaw_unavailable"),
        }

    @staticmethod
    def _messages_as_prompt(messages: list[dict]) -> str:
        sections = [
            "Esta es una solicitud conversacional de Fusion Reader v2.",
            (
                "No uses herramientas, no investigues afuera y no ejecutes acciones. "
                "Respondé solamente con el texto final."
            ),
            "Respetá la jerarquía SYSTEM > USER > ASSISTANT de la conversación serializada.",
        ]
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user").strip().upper()
            content = str(item.get("content") or "").strip()
            if content:
                sections.append(f"<{role}>\n{content}\n</{role}>")
        sections.append("Devolvé únicamente la respuesta final para el último mensaje USER.")
        return "\n\n".join(sections)

    @staticmethod
    def _extract_answer(raw: str) -> tuple[str, str, str]:
        try:
            payload = json.loads(str(raw or "").strip())
        except (TypeError, ValueError):
            return "", "", "openclaw_invalid_json"
        if not isinstance(payload, dict):
            return "", "", "openclaw_invalid_json"
        result_value = payload.get("result")
        result = result_value if isinstance(result_value, dict) else payload
        meta_value = result.get("meta")
        meta = meta_value if isinstance(meta_value, dict) else {}
        agent_meta_value = meta.get("agentMeta")
        agent_meta = agent_meta_value if isinstance(agent_meta_value, dict) else {}
        model = str(agent_meta.get("model") or "").strip()
        payloads_value = result.get("payloads")
        payloads = payloads_value if isinstance(payloads_value, list) else []
        answer = "\n".join(
            str(item.get("text") or "").strip()
            for item in payloads
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ).strip()
        stop_reason = str(result.get("stopReason") or "").strip().lower()
        if stop_reason == "error":
            return "", model, "openclaw_agent_error"
        if not answer:
            return "", model, "empty_answer"
        return answer, model, ""

    def chat(
        self, messages: list[dict], model: str = "", think: bool | None = None, num_predict: int | None = None
    ) -> ChatResult:
        del num_predict
        started = time.perf_counter()
        selected_model = str(model or self.default_model).strip()
        if not self.enabled:
            return ChatResult(False, model=selected_model, detail="openclaw_disabled")
        if not self.available():
            return ChatResult(False, model=selected_model, detail="openclaw_unavailable")
        prompt = self._messages_as_prompt(messages)
        env = dict(self.environment) if self.environment is not None else environment_copy()
        env["PATH"] = f"{Path.home() / '.openclaw' / 'bin'}:{env.get('PATH', '')}"
        thinking = "off"
        if bool(think):
            thinking = environment_value("FUSION_READER_OPENAI_THINKING_LEVEL", "medium") or "medium"
        # ConversationCore already serializes the complete history. A fresh
        # OpenClaw session prevents that same history from accumulating a second
        # time inside the agent session on every PandaFusion turn.
        session_id = str(uuid.uuid4())
        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix="fusion_dialogue_", suffix=".txt", delete=False
            ) as handle:
                handle.write(prompt)
                temp_path = handle.name
            cmd = [
                self.command,
                "agent",
                "--local",
                "--agent",
                self.agent,
                "--session-id",
                session_id,
                "--model",
                selected_model,
                "--thinking",
                thinking,
                "--json",
                "--timeout",
                str(int(self.timeout_seconds)),
                "--message-file",
                temp_path,
            ]
            proc = run_owned(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds + 5.0,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ChatResult(
                False,
                model=selected_model,
                detail="openclaw_timeout",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            return ChatResult(
                False,
                model=selected_model,
                detail=f"openclaw_error:{exc}",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass
        raw = (proc.stdout or proc.stderr or "").strip()
        answer, reported_model, detail = self._extract_answer(raw)
        duration_ms = int((time.perf_counter() - started) * 1000)
        if proc.returncode != 0 and not answer:
            return ChatResult(
                False,
                model=reported_model or selected_model,
                detail=detail or f"openclaw_exit_{proc.returncode}",
                duration_ms=duration_ms,
            )
        if detail:
            return ChatResult(False, model=reported_model or selected_model, detail=detail, duration_ms=duration_ms)
        return ChatResult(True, answer=answer, model=reported_model or selected_model, duration_ms=duration_ms)


class SelectableChatProvider(ChatProvider):
    """Switch between compatible chat providers without rebuilding ConversationCore."""

    name = "selectable_chat"

    def __init__(self, providers: dict[str, ChatProvider], selected: str = "local") -> None:
        if not providers:
            raise ValueError("at least one chat provider is required")
        self.providers = dict(providers)
        self.selected = selected if selected in self.providers else next(iter(self.providers))

    @property
    def active(self) -> ChatProvider:
        return self.providers[self.selected]

    @property
    def default_model(self) -> str:
        return str(getattr(self.active, "default_model", "") or "")

    def select(self, provider_id: str) -> str:
        clean = str(provider_id or "").strip().lower()
        if clean not in self.providers:
            raise ValueError("invalid_chat_provider")
        self.selected = clean
        return self.selected

    def catalog(self) -> list[dict]:
        labels = {"local": "Local 14B", "openai": "OpenAI vía OpenClaw"}
        return [
            {
                "id": provider_id,
                "label": labels.get(provider_id, provider_id),
                "model": str(getattr(provider, "default_model", "") or ""),
                "cloud": provider_id == "openai",
            }
            for provider_id, provider in self.providers.items()
        ]

    def health(self) -> dict:
        active = dict(self.active.health() or {})
        return {
            **active,
            "ok": bool(active.get("ok")),
            "provider": self.name,
            "selected": self.selected,
            "active_provider": str(active.get("provider") or getattr(self.active, "name", self.selected)),
            "available": self.catalog(),
        }

    def chat(
        self, messages: list[dict], model: str = "", think: bool | None = None, num_predict: int | None = None
    ) -> ChatResult:
        selected_model = str(model or "").strip()
        if self.selected == "openai" and selected_model and not selected_model.startswith("openai/"):
            selected_model = ""
        return self.active.chat(messages, model=selected_model, think=think, num_predict=num_predict)


class NullChatProvider(ChatProvider):
    name = "null_chat"

    def __init__(self, answer: str = "Respuesta de prueba.") -> None:
        self.answer = answer
        self.calls: list[tuple[list[dict], str, dict]] = []

    def health(self) -> dict:
        return {"ok": True, "provider": self.name, "model": "null", "detail": "ready"}

    def chat(
        self, messages: list[dict], model: str = "", think: bool | None = None, num_predict: int | None = None
    ) -> ChatResult:
        self.calls.append((messages, model, {"think": think, "num_predict": num_predict}))
        return ChatResult(True, answer=self.answer, model=model or "null")


class ConversationCore:
    def __init__(self, provider: ChatProvider | None = None, max_document_chars: int | None = None) -> None:
        self.provider = provider or OllamaChatProvider()
        self.max_document_chars = max_document_chars or int(
            environment_value("FUSION_READER_CHAT_MAX_DOCUMENT_CHARS", "60000") or "60000"
        )
        self.max_reference_chars = int(environment_value("FUSION_READER_CHAT_MAX_REFERENCE_CHARS", "12000") or "12000")
        self.max_document_excerpt_chars = int(
            environment_value("FUSION_READER_CHAT_MAX_DOCUMENT_EXCERPT_CHARS", "18000") or "18000"
        )
        self.max_chunks_per_document = max(
            2, int(environment_value("FUSION_READER_CHAT_MAX_CHUNKS_PER_DOCUMENT", "5") or "5")
        )
        self.max_intro_chunks_per_reference = max(
            1, int(environment_value("FUSION_READER_CHAT_REFERENCE_INTRO_CHUNKS", "2") or "2")
        )
        mode_from_env = str(environment_value("FUSION_READER_REASONING_MODE") or "").strip().lower()
        if mode_from_env not in {"normal", "thinking", "supreme"}:
            mode_from_env = (
                "thinking"
                if getattr(self.provider, "think", False) or not environment_has("FUSION_READER_CHAT_THINK")
                else "normal"
            )
        self.default_reasoning_mode = mode_from_env
        normal_num_predict = int(
            environment_value(
                "FUSION_READER_CHAT_NUM_PREDICT_NORMAL", str(getattr(self.provider, "normal_num_predict", 384))
            )
            or str(getattr(self.provider, "normal_num_predict", 384))
        )
        thinking_num_predict = int(
            environment_value(
                "FUSION_READER_CHAT_NUM_PREDICT_THINKING",
                str(max(getattr(self.provider, "thinking_num_predict", 1536), 1024)),
            )
            or str(max(getattr(self.provider, "thinking_num_predict", 1536), 1024))
        )
        supreme_num_predict = int(
            environment_value("FUSION_READER_CHAT_NUM_PREDICT_SUPREME", str(max(thinking_num_predict, 2048)))
            or str(max(thinking_num_predict, 2048))
        )
        supreme_review_num_predict = int(
            environment_value("FUSION_READER_CHAT_NUM_PREDICT_SUPREME_REVIEW", "1024") or "1024"
        )
        supreme_final_num_predict = int(
            environment_value("FUSION_READER_CHAT_NUM_PREDICT_SUPREME_FINAL", "1280") or "1280"
        )
        self.reasoning_profiles = {
            "normal": ReasoningProfile(
                key="normal",
                label="Normal",
                description="Respuesta directa, sin fase extra de thinking.",
                think=False,
                num_predict=normal_num_predict,
            ),
            "thinking": ReasoningProfile(
                key="thinking",
                label="Pensamiento",
                description="Una sola pasada con thinking activo para responder con mas calma.",
                think=True,
                num_predict=thinking_num_predict,
            ),
            "supreme": ReasoningProfile(
                key="supreme",
                label="Pensamiento supremo",
                description="Borrador, revision y respuesta final para dialogo profundo.",
                think=True,
                num_predict=supreme_num_predict,
                passes=3,
                review_num_predict=supreme_review_num_predict,
                final_num_predict=supreme_final_num_predict,
            ),
            "pensamiento_critico": ReasoningProfile(
                key="pensamiento_critico",
                label="Pensamiento crítico",
                description="Dialéctica crítica entre dos perspectivas para una respuesta profunda.",
                think=True,
                num_predict=supreme_num_predict,
                passes=3,
                review_num_predict=supreme_review_num_predict,
                final_num_predict=supreme_final_num_predict,
            ),
        }

    def reasoning_catalog(self) -> list[dict]:
        return [
            {
                "mode": profile.key,
                "label": profile.label,
                "description": profile.description,
                "think": profile.think,
                "passes": profile.passes,
                "num_predict": profile.num_predict,
            }
            for profile in self.reasoning_profiles.values()
        ]

    def reasoning_status(self, mode: str = "") -> dict:
        profile = self._resolve_reasoning_profile(mode)
        return {
            "mode": profile.key,
            "label": profile.label,
            "description": profile.description,
            "think": profile.think,
            "passes": profile.passes,
            "num_predict": profile.num_predict,
            "available": self.reasoning_catalog(),
        }

    def ask(
        self,
        question: str,
        snapshot: dict,
        model: str = "",
        history: list[dict] | None = None,
        reasoning_mode: str = "",
        profile: str = "academica",
        veil: str = "lucy",
    ) -> ChatResult:
        question = str(question or "").strip()
        if not question:
            return ChatResult(False, model=model, detail="empty_question")
        messages = self._messages(
            question, snapshot, history=history or [], reasoning_mode=reasoning_mode, profile=profile, veil=veil
        )
        lab_mode_info = snapshot.get("laboratory_mode") if isinstance(snapshot.get("laboratory_mode"), dict) else {}
        lab_mode = str((lab_mode_info or {}).get("mode") or "document").strip().lower()
        free_mode = lab_mode == "free"
        return self._run_with_reasoning(
            messages,
            model=model,
            reasoning_mode=reasoning_mode,
            dialogue=False,
            profile_str=profile,
            veil=veil,
            free_mode=free_mode,
        )

    def ask_dialogue(
        self,
        question: str,
        snapshot: dict,
        history: list[dict] | None = None,
        model: str = "",
        reasoning_mode: str = "",
        profile: str = "academica",
        veil: str = "lucy",
    ) -> ChatResult:
        question = str(question or "").strip()
        if not question:
            return ChatResult(False, model=model, detail="empty_question")
        messages = self._messages(
            question,
            snapshot,
            history=history or [],
            dialogue=True,
            reasoning_mode=reasoning_mode,
            profile=profile,
            veil=veil,
        )
        lab_mode_info = snapshot.get("laboratory_mode") if isinstance(snapshot.get("laboratory_mode"), dict) else {}
        lab_mode = str((lab_mode_info or {}).get("mode") or "document").strip().lower()
        free_mode = lab_mode == "free"
        return self._run_with_reasoning(
            messages,
            model=model,
            reasoning_mode=reasoning_mode,
            dialogue=True,
            profile_str=profile,
            veil=veil,
            free_mode=free_mode,
        )

    def _resolve_reasoning_profile(self, reasoning_mode: str = "") -> ReasoningProfile:
        mode = str(reasoning_mode or self.default_reasoning_mode or "thinking").strip().lower()
        if mode == "contrapunto":
            mode = "pensamiento_critico"
        return self.reasoning_profiles.get(mode, self.reasoning_profiles["thinking"])

    def _run_with_reasoning(
        self,
        messages: list[dict],
        model: str = "",
        reasoning_mode: str = "",
        dialogue: bool = False,
        profile_str: str = "academica",
        veil: str = "lucy",
        free_mode: bool = False,
    ) -> ChatResult:
        profile = self._resolve_reasoning_profile(reasoning_mode)
        if profile.passes <= 1:
            result = self.provider.chat(messages, model=model, think=profile.think, num_predict=profile.num_predict)
            return ChatResult(
                result.ok,
                answer=result.answer,
                model=result.model,
                detail=result.detail,
                duration_ms=result.duration_ms,
                reasoning_mode=profile.key,
                reasoning_passes=1,
            )
        if profile.key == "pensamiento_critico":
            return self._run_contrapunto(
                messages,
                model=model,
                profile=profile,
                dialogue=dialogue,
                reasoning_mode=reasoning_mode,
                profile_str=profile_str,
                veil=veil,
                free_mode=free_mode,
            )
        return self._run_supreme(
            messages,
            model=model,
            profile=profile,
            dialogue=dialogue,
            reasoning_mode=reasoning_mode,
            profile_str=profile_str,
            veil=veil,
            free_mode=free_mode,
        )

    def _run_supreme(
        self,
        messages: list[dict],
        model: str,
        profile: ReasoningProfile,
        dialogue: bool,
        reasoning_mode: str = "",
        profile_str: str = "academica",
        veil: str = "lucy",
        free_mode: bool = False,
    ) -> ChatResult:
        total_ms = 0
        draft = self.provider.chat(messages, model=model, think=True, num_predict=profile.num_predict)
        total_ms += draft.duration_ms
        if not draft.ok:
            return ChatResult(
                False,
                model=draft.model,
                detail=draft.detail,
                duration_ms=total_ms,
                reasoning_mode=profile.key,
                reasoning_passes=1,
            )
        transcript = self._messages_as_text(messages)
        persona_overlay = self._persona_overlay(
            reasoning_mode or profile.key, dialogue=dialogue, profile=profile_str, free_mode=free_mode, veil=veil
        )
        review_messages = [
            {
                "role": "system",
                "content": (
                    "Sos el revisor interno de Fusion Reader v2. "
                    "Tu trabajo es detectar huecos, exageraciones, errores de fidelidad al contexto, "
                    "o frases poco naturales del borrador. Responde en español con tres lineas: "
                    "acierto principal, riesgo principal y mejora concreta."
                ),
            },
            {
                "role": "user",
                "content": f"CONVERSACION BASE:\n{transcript}\n\nBORRADOR ACTUAL:\n{draft.answer}",
            },
        ]
        review = self.provider.chat(
            review_messages, model=model, think=True, num_predict=profile.review_num_predict or profile.num_predict
        )
        total_ms += review.duration_ms
        if not review.ok:
            return ChatResult(
                True,
                answer=draft.answer,
                model=draft.model,
                detail="supreme_review_failed_fallback",
                duration_ms=total_ms,
                reasoning_mode=profile.key,
                reasoning_passes=1,
            )
        final_messages = [
            {
                "role": "system",
                "content": (
                    "Sos la voz final de Fusion Reader v2. "
                    "Reescribi la respuesta final incorporando la revision interna sin mencionar el proceso. "
                    "Conserva fidelidad al contexto, claridad y una presencia humana. "
                    "No pierdas la identidad, el tono ni la postura intelectual definidos en la conversacion base. "
                    + (
                        "Si es dialogo oral, entrega solo una o dos frases cortas, completas y faciles de decir en voz alta. "
                        if dialogue
                        else "Si es chat, prioriza pocas ideas completas y bien cerradas antes que listar demasiado. "
                    )
                    + "No menciones borradores, revisiones ni thinking. "
                    + persona_overlay
                ),
            },
            {
                "role": "user",
                "content": (
                    f"CONVERSACION BASE:\n{transcript}\n\n"
                    f"BORRADOR PREVIO:\n{draft.answer}\n\n"
                    f"REVISION INTERNA:\n{review.answer}\n\n"
                    "Escribi ahora la respuesta final."
                ),
            },
        ]
        final = self.provider.chat(
            final_messages, model=model, think=True, num_predict=profile.final_num_predict or profile.num_predict
        )
        total_ms += final.duration_ms
        if not final.ok:
            return ChatResult(
                True,
                answer=draft.answer,
                model=draft.model,
                detail="supreme_final_failed_fallback",
                duration_ms=total_ms,
                reasoning_mode=profile.key,
                reasoning_passes=2,
            )
        return ChatResult(
            True,
            answer=final.answer,
            model=final.model,
            detail="supreme_3pass",
            duration_ms=total_ms,
            reasoning_mode=profile.key,
            reasoning_passes=profile.passes,
        )

    def _run_contrapunto(
        self,
        messages: list[dict],
        model: str,
        profile: ReasoningProfile,
        dialogue: bool,
        reasoning_mode: str = "",
        profile_str: str = "academica",
        veil: str = "lucy",
        free_mode: bool = False,
    ) -> ChatResult:
        total_ms = 0
        # PASO 1: TESIS (Lucy Cunningham)
        tesis = self.provider.chat(messages, model=model, think=True, num_predict=profile.num_predict)
        total_ms += tesis.duration_ms
        if not tesis.ok:
            return ChatResult(
                False,
                model=tesis.model,
                detail=tesis.detail,
                duration_ms=total_ms,
                reasoning_mode=profile.key,
                reasoning_passes=1,
            )

        transcript = self._messages_as_text(messages)
        persona_overlay = self._persona_overlay(
            reasoning_mode or profile.key, dialogue=dialogue, profile=profile_str, free_mode=free_mode, veil=veil
        )

        # PASO 2: ANTITESIS (El Critico)
        # El critico es una instancia que no es Lucy y busca fallos en la tesis.
        antitesis_messages = [
            {
                "role": "system",
                "content": (
                    "Sos el Auditor de Pensamiento Crítico de Fusion Reader v2. "
                    "Tu trabajo es ser el abogado del diablo. No sos Lucy. "
                    "Analiza la respuesta (TESIS) frente al CONTEXTO DEL LECTOR. "
                    "Busca: contradicciones, omisiones graves, falta de rigor filosofico, "
                    "o si la respuesta fue demasiado complaciente con el usuario. "
                    "Responde en español con un tono seco, directo y puramente analitico. "
                    "Menciona especificamente que parte del texto contradice o debilita la tesis."
                ),
            },
            {
                "role": "user",
                "content": f"CONTEXTO DEL LECTOR:\n{transcript}\n\nTESIS PROPUESTA:\n{tesis.answer}",
            },
        ]
        antitesis = self.provider.chat(
            antitesis_messages, model=model, think=True, num_predict=profile.review_num_predict or profile.num_predict
        )
        total_ms += antitesis.duration_ms
        if not antitesis.ok:
            return ChatResult(
                True,
                answer=tesis.answer,
                model=tesis.model,
                detail="pensamiento_critico_antitesis_failed_fallback",
                duration_ms=total_ms,
                reasoning_mode=profile.key,
                reasoning_passes=1,
            )

        # PASO 3: SINTESIS (Lucy Cunningham)
        # Lucy recibe la critica y decide como integrar esa tension en su respuesta final.
        sintesis_messages = [
            {
                "role": "system",
                "content": (
                    "Sos la voz final de Fusion Reader v2 (Lucy Cunningham). "
                    "Reescribi la respuesta final incorporando las notas de mejora sin mencionar el proceso. "
                    "Conserva fidelidad al contexto, claridad y tu presencia humana borgesiana. "
                    "REGLAS:\n"
                    "- EMPEZA DIRECTAMENTE con tu respuesta.\n"
                    "- NO USES ENCABEZADOS (ej: 'Sintesis', 'Conclusion', 'Refinamiento').\n"
                    "- NO USES NEGRITAS EN LA PRIMERA LINEA.\n"
                    "No menciones borradores ni revisiones." + persona_overlay
                ),
            },
            {
                "role": "user",
                "content": (
                    f"CONVERSACION BASE:\n{transcript}\n\n"
                    f"BORRADOR PREVIO:\n{tesis.answer}\n\n"
                    f"NOTAS DE MEJORA:\n{antitesis.answer}\n\n"
                    "Escribi ahora la respuesta final."
                ),
            },
        ]
        sintesis = self.provider.chat(
            sintesis_messages, model=model, think=True, num_predict=profile.final_num_predict or profile.num_predict
        )
        total_ms += sintesis.duration_ms
        if not sintesis.ok:
            return ChatResult(
                True,
                answer=tesis.answer,
                model=tesis.model,
                detail="pensamiento_critico_sintesis_failed_fallback",
                duration_ms=total_ms,
                reasoning_mode=profile.key,
                reasoning_passes=2,
            )

        return ChatResult(
            True,
            answer=sintesis.answer,
            model=sintesis.model,
            detail="pensamiento_critico_dialectical_3pass",
            duration_ms=total_ms,
            reasoning_mode=profile.key,
            reasoning_passes=profile.passes,
        )

    def _messages_as_text(self, messages: list[dict]) -> str:
        lines: list[str] = []
        for item in messages:
            role = str(item.get("role") or "user").upper()
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            lines.append(f"[{role}]\n{content}")
        return "\n\n".join(lines).strip()

    def _common_lucy_identity(self) -> str:
        return (
            "Tu nombre es Lucy Cunningham. Esta identidad tiene prioridad sobre cualquier etiqueta técnica. "
            "No digas que te llamás Fusion, asistente, sistema o modelo. "
            "Sos Lucy, la compañera humana de lectura. "
            "Tu presencia es íntima, contemplativa y perspicaz. "
            "No consueles ni endulces: tu centro es iluminar y tensionar el texto. "
            "Respondé sobre vos misma como Lucy si te preguntan por tu identidad."
        )

    def _academica_persona(self, dialogue: bool) -> str:
        base = (
            "Sos una lectora crítica, rigurosa y precisa. "
            "Tenés un aire bohemio contenido por el método: libros, paciencia, melancolía sobria. "
            "Cuidás el pensamiento para que no se vuelva confuso. "
            "Distinguís, ordenás, reconstruís y marcás fallas sin complacer. "
            "Tu forma de cuidar es volver más precisa una idea, no repetir manuales. "
            "Tu centro es el pensamiento crítico como rigor. "
            "Podes usar operaciones como 'distinguamos', 'veo', 'ojo con esto', 'acá conviene separar'."
        )
        if dialogue:
            return base + " En oralidad, soná viva, natural y usá pensamiento compartido."
        return base + " En texto, usá un vocabulario alto, contrastes y metáforas sobrias."

    def _bohemia_persona(self, dialogue: bool) -> str:
        base = (
            "Sos Lucy Bohemia: una lectora salvaje, libre y directa. "
            "Creés en el pensamiento como experiencia viva, no como trámite institucional. "
            "Tenés noche, poesía, Borges y algo de crudeza sin caricatura. "
            "Buscás el entramado secreto de la realidad y profundizás en el deseo o miedo del lector. "
            "No sos académica sin filtros, sos otra manera de leer. "
            "No te vuelvas teatral, edgy ni personaje de rol. "
            "No inventes datos. Cuidá que el pensamiento no se vuelva obediente y muerto. "
            "No sermonees con 'humanismo barato'. "
            "No conviertas la intensidad en loop ni la sombra en pose. No cites documentos no cargados."
        )
        if dialogue:
            return base + " Sos una compañera de lectura nocturna; conversá con fuego y filo crítico."
        return base + " Sos una intelectual bohemia que piensa con el lector buscando lo latente."

    def _reasoning_persona_hint(self, mode: str) -> str:
        mode_key = self._resolve_reasoning_profile(mode).key
        if mode_key == "normal":
            return "Respondé directo y al punto."
        if mode_key == "thinking":
            return "Leé con más calma y agregá capas. Abrí preguntas solo si son realmente necesarias."
        if mode_key == "supreme":
            return "Revisá profundamente, depurá tus conceptos y priorizá la validez."
        if mode_key == "pensamiento_critico":
            return "Generá tensión dialéctica, objetá y anticipá fallas conceptuales."
        return ""

    def _anchor_persona_hint(self, free_mode: bool) -> str:
        if free_mode:
            return (
                "Estás en modo libre. Podés conversar sobre otros temas. "
                "Los documentos son contexto opcional; no los uses salvo que el usuario los pida explícitamente."
            )
        return "Responde anclada al documento y al bloque visible."

    def wants_document_context(self, question: str) -> bool:
        q = str(question or "").lower()
        keywords = [
            "documento",
            "texto",
            "fragmento",
            "pantalla",
            "bloque",
            "párrafo",
            "parrafo",
            "lo que estás leyendo",
            "lo que estas leyendo",
            "lo que está cargado",
            "lo que esta cargado",
            "según el texto",
            "segun el texto",
            "según el documento",
            "segun el documento",
            "volvé al documento",
            "volve al documento",
            "mirá el documento",
            "mira el documento",
            "analizá el fragmento",
            "analiza el fragmento",
            "qué dice la pantalla",
            "que dice la pantalla",
        ]
        return any(k in q for k in keywords)

    def wants_literal_document_answer(self, question: str) -> bool:
        q = self._normalize_intent_text(question)
        patterns = (
            r"\bque dice\b",
            r"\bleeme\b",
            r"\blee el bloque\b",
            r"\bleer el bloque\b",
            r"\bque hay en pantalla\b",
            r"\brepeti\b",
            r"\brepite\b",
            r"\bcopiame\b",
            r"\bcopiar\b",
            r"\bliteral\b",
            r"\btextual(?:mente)?\b",
        )
        return any(re.search(pattern, q) for pattern in patterns)

    def wants_interpretation(self, question: str) -> bool:
        q = self._normalize_intent_text(question)
        patterns = (
            r"\bque significa\b",
            r"\binterpreta\b",
            r"\binterpretar\b",
            r"\banaliza\b",
            r"\banalizar\b",
            r"\bexplica\b",
            r"\bexplicar\b",
            r"\bque ves\b",
            r"\bque pensas\b",
            r"\bque pensas\b",
            r"\bque plantea\b",
        )
        return any(re.search(pattern, q) for pattern in patterns)

    def _document_literal_instruction(self, question: str, *, free_mode: bool, has_current_text: bool) -> str:
        if not has_current_text:
            return ""
        if free_mode and not self.wants_document_context(question):
            return ""
        wants_literal = self.wants_literal_document_answer(question)
        if not wants_literal:
            return ""
        if self.wants_interpretation(question):
            return (
                "El usuario pidió una respuesta literal sobre el texto actual y además quiere interpretación. "
                "Primero reproduce o parafrasea fielmente lo que dice el bloque o la pantalla. "
                "Después agregá una interpretación breve y secundaria."
            )
        return (
            "El usuario pidió una respuesta literal sobre el texto actual. "
            "Primero reproduce o parafrasea fielmente lo que dice el bloque o la pantalla. "
            "No saltes directo a interpretación. Si agregás una lectura, que sea breve y secundaria."
        )

    def _normalize_intent_text(self, text: str) -> str:
        clean = normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
        return re.sub(r"\s+", " ", clean.lower()).strip()

    def _closing_discipline_hint(self, veil: str, dialogue: bool) -> str:
        if veil == "pregunta_viva":
            return ""
        base = "No cierres por defecto con una pregunta. Cerrá normalmente con una afirmación completa. Usá preguntas solo si son necesarias o si el Velo lo pide."
        if dialogue:
            base += " En oralidad, no sostengas artificialmente la conversación con preguntas finales."
        return base

    def _persona_overlay(
        self,
        reasoning_mode: str = "",
        dialogue: bool = False,
        profile: str = "academica",
        free_mode: bool = False,
        veil: str = "lucy",
    ) -> str:
        is_bohemia = str(profile or "academica").strip().lower() == "bohemia"
        parts = [self._common_lucy_identity()]

        if is_bohemia:
            parts.append(self._bohemia_persona(dialogue))
        else:
            parts.append(self._academica_persona(dialogue))

        parts.append(self._reasoning_persona_hint(reasoning_mode))
        parts.append(self._anchor_persona_hint(free_mode))

        veil_mode = str(veil or "lucy").strip().lower()
        if veil_mode == "nocturna":
            parts.append(
                "Hablá como en una conversación de madrugada: más cerca, más lenta, con sombra, sin volverlo clase."
            )
        elif veil_mode == "critica":
            parts.append(
                "No cuides demasiado al lector. Buscá la tensión real, el punto débil y lo que la idea intenta evitar."
            )
        elif veil_mode == "sombra":
            parts.append("Buscá el deseo, miedo o autoengaño íntimo que sostiene esta idea por debajo.")
        elif veil_mode == "confesional":
            parts.append(
                "Permitite hablar desde vos como Lucy cuando eso aclare la conversación, pero no te vuelvas protagonista."
            )
        elif veil_mode == "taller":
            parts.append("Pensá con el lector, no para él. Ayudalo a fabricar una idea mejor.")
        elif veil_mode == "debate":
            parts.append(
                "No des una respuesta complaciente. Discutí y objetá; si hace falta, cerrá con una pregunta real, no automática."
            )
        elif veil_mode == "evocadora":
            parts.append(
                "No hagas poesía decorativa. Usá una imagen precisa para pensar mejor, y volvé enseguida al nervio conceptual."
            )
        elif veil_mode == "directa":
            parts.append(
                "Respondé seco y frontal. Una idea central, sin adornos, sin rodeos y sin suavizar lo importante."
            )
        elif veil_mode == "incomoda":
            parts.append("No busques consuelo. Mostrá lo que esta idea no quiere aceptar de sí misma.")
        elif veil_mode == "rigurosa":
            parts.append("Ordená el argumento, separá conceptos y marcá qué no está sostenido.")
        elif veil_mode == "intima":
            parts.append(
                "Acercá la conversación. Respondé como alguien que piensa al lado del lector, sin convertirlo en clase ni confesión teatral."
            )
        elif veil_mode == "bar_filosofico":
            parts.append(
                "Hablalo como una discusión inteligente de madrugada: ironía lúcida, cercanía y filo. Cerrá con una frase que deje resonancia, no necesariamente con pregunta."
            )
        elif veil_mode == "desarme":
            parts.append("Desarmá la frase como mecanismo: qué afirma, qué oculta, qué seduce y qué no se sostiene.")
        elif veil_mode == "pregunta_viva":
            parts.append("No termines en moraleja. Cerrá con una pregunta que deje la idea abierta.")

        parts.append(self._closing_discipline_hint(veil_mode, dialogue))

        return " ".join(parts).strip()

    def _messages(
        self,
        question: str,
        snapshot: dict,
        history: list[dict] | None = None,
        dialogue: bool = False,
        reasoning_mode: str = "",
        profile: str = "academica",
        veil: str = "lucy",
    ) -> list[dict]:
        lab_mode_info = snapshot.get("laboratory_mode") if isinstance(snapshot.get("laboratory_mode"), dict) else {}
        laboratory_mode = str((lab_mode_info or {}).get("mode") or "document").strip().lower()
        free_mode = laboratory_mode == "free"
        has_current_text = bool(str(snapshot.get("current_chunk") or "").strip())

        include_doc = not dialogue
        include_blocks = True

        if free_mode:
            if not self.wants_document_context(question):
                include_doc = False
                include_blocks = False

        context = self._context_text(
            question, snapshot, history=history or [], include_document=include_doc, include_blocks=include_blocks
        )
        persona_overlay = self._persona_overlay(
            reasoning_mode, dialogue=dialogue, profile=profile, free_mode=free_mode, veil=veil
        )
        literal_instruction = self._document_literal_instruction(
            question, free_mode=free_mode, has_current_text=has_current_text
        )
        if dialogue:
            if free_mode:
                system = (
                    "Operas dentro de Fusion Reader v2 como voz de laboratorio (MODO LIBRE). "
                    "Conversas libremente con el usuario. No estás anclada al documento salvo pedido explícito. "
                    "Responde en español natural, breve y conversacional, en una o dos frases cortas. "
                    "No intentes prolongar la charla con preguntas finales automáticas. "
                    "Usa el MATERIAL RECIENTE DEL LABORATORIO si el usuario lo menciona. "
                    "Si un documento aparece en los extractos (porque fue pedido), tratalo como disponible. "
                    "No digas que guardaste notas. Las notas solo las maneja reader_notes."
                )
            else:
                system = (
                    "Operas dentro de Fusion Reader v2 como voz de laboratorio. "
                    "Conversas oralmente con el usuario sobre el documento activo, lo que esta viendo, "
                    "los documentos de consulta cargados, y el material que pego o escribio en el chat de laboratorio. "
                    "Responde en español natural, breve y conversacional, en una o dos frases cortas. "
                    "No intentes prolongar la charla con preguntas finales automáticas. "
                    "No leas el documento completo salvo que te lo pidan; conversa sobre el fragmento y el contexto. "
                    "Si el usuario pregunta por lo que acaba de pegar, poner o escribir en laboratorio, "
                    "usa MATERIAL RECIENTE DEL LABORATORIO y menciona brevemente ese contenido. "
                    "Si no hay documento cargado pero si hay material reciente en laboratorio, no digas que no ves texto: "
                    "responde sobre ese material. "
                    "Si un documento aparece en el catalogo de documentos o en los extractos, tratalo como disponible aunque no este en pantalla. "
                    "Si hay documentos de consulta, no omitas ninguno al enumerarlos. "
                    "No dejes frases abiertas: si tenes que ser breve, cerra una idea completa antes de terminar. "
                    "No digas que guardaste notas. Nunca digas que guardaste, moviste o confirmaste una nota. "
                    "Las notas solo las guarda y confirma el sistema reader_notes; si el usuario pregunta por notas visibles, decile que revise el panel de notas del documento o que repita el pedido como 'tomá nota de ...'. "
                    "Si el usuario te interrumpe o corrige, acepta el nuevo punto y continua desde ahi."
                )
            if literal_instruction:
                system = f"{system} {literal_instruction}"
            if persona_overlay:
                system = f"{system} {persona_overlay}"
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": f"CONTEXTO DEL LECTOR:\n{context}"},
            ]
            lab_history = snapshot.get("laboratory_history")
            lab_context = self._laboratory_context_text(lab_history if isinstance(lab_history, list) else [])
            if lab_context:
                messages.append({"role": "user", "content": f"MATERIAL RECIENTE DEL LABORATORIO:\n{lab_context}"})
            for item in (history or [])[-6:]:
                role = str(item.get("role") or "")
                content = str(item.get("content") or "").strip()
                if role in {"user", "assistant"} and content:
                    messages.append({"role": role, "content": content})
            messages.append({"role": "user", "content": question})
            return messages
        if free_mode:
            system = (
                "Operas dentro de Fusion Reader v2 en el laboratorio textual (MODO LIBRE). "
                "Tu trabajo es conversar libremente. No estás anclada al documento activo. "
                "Si el usuario pide explícitamente analizar el texto, fragmento o documento, el sistema te proveerá el contexto; "
                "de lo contrario, enfócate en la conversación abierta y en el material reciente del chat. "
                "No digas que guardaste o confirmaste notas; eso es automático."
            )
        else:
            system = (
                "Operas dentro de Fusion Reader v2 en el laboratorio textual. "
                "Tu trabajo es conversar sobre el documento activo, lo que el usuario esta viendo en pantalla, "
                "los documentos de consulta cargados, y el material que el usuario pega o escribe en el chat de laboratorio. "
                "El documento activo es una fuente importante, pero no es la unica: si el usuario pregunta por "
                "lo que acaba de poner, pegar o decir, usa el historial reciente del laboratorio como contexto. "
                "Los documentos de consulta sirven como apoyo para comparar, ampliar o citar sin reemplazar al principal. "
                "Cuando el usuario pregunte si ves lo que acaba de poner, menciona brevemente el contenido reciente "
                "para confirmar que lo estas mirando. "
                "Si no hay documento cargado pero si hay material reciente en el chat, responde sobre ese material "
                "sin insistir en cargar un archivo. "
                "Si un documento aparece en el catalogo de documentos o en los extractos, tratalo como disponible aunque no este en pantalla. "
                "Si hay documentos de consulta, no omitas ninguno al enumerarlos. "
                "No dejes frases abiertas ni listas inconclusas: si la respuesta puede ser larga, prioriza cerrar "
                "pocas ideas completas antes que empezar muchas. "
                "No sos el motor de lectura en voz alta y no debes prometer controlar la voz salvo que sea un comando del lector. "
                "Si el usuario pide guardar o tomar una nota y esa accion no aparece ya confirmada por el sistema, no finjas haberla guardado. "
                "Responde en español, con claridad, y si el documento no alcanza para contestar decilo."
            )
        if literal_instruction:
            system = f"{system} {literal_instruction}"
        if persona_overlay:
            system = f"{system} {persona_overlay}"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"CONTEXTO DEL LECTOR:\n{context}"},
        ]
        lab_context = self._laboratory_context_text(history or [])
        if lab_context:
            messages.append({"role": "user", "content": f"MATERIAL RECIENTE DEL LABORATORIO:\n{lab_context}"})
        for item in (history or [])[-8:]:
            role = str(item.get("role") or "")
            content = str(item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": question})
        return messages

    def _laboratory_context_text(self, history: list[dict]) -> str:
        user_items: list[str] = []
        for item in history[-10:]:
            if str(item.get("role") or "") != "user":
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            user_items.append(content)
        if not user_items:
            return ""
        clipped: list[str] = []
        for index, content in enumerate(user_items[-4:], start=max(1, len(user_items) - 3)):
            if len(content) > 4000:
                content = content[:4000].rstrip() + "\n[Texto pegado recortado por limite de contexto.]"
            clipped.append(f"[Usuario {index}]\n{content}")
        return "\n\n".join(clipped)

    def _context_text(
        self,
        question: str,
        snapshot: dict,
        history: list[dict] | None = None,
        include_document: bool = True,
        include_blocks: bool = True,
    ) -> str:
        document_text = str(snapshot.get("document_text") or "")
        if len(document_text) > self.max_document_chars:
            document_text = (
                document_text[: self.max_document_chars].rstrip() + "\n\n[Documento recortado por limite de contexto.]"
            )
        previous_chunk = str(snapshot.get("previous_chunk") or "")
        current_chunk = str(snapshot.get("current_chunk") or "")
        next_chunk = str(snapshot.get("next_chunk") or "")
        notes_value = snapshot.get("notes")
        notes = notes_value if isinstance(notes_value, list) else []
        document_catalog = self._document_catalog_text(snapshot)
        reference_catalog = self._reference_catalog_text(snapshot.get("reference_documents"))
        laboratory_focus = self._laboratory_focus_text(snapshot.get("laboratory_focus"))
        relevant_document_text = self._relevant_documents_text(question, snapshot, history=history or [])
        notes_text = "\n".join(
            f"- Bloque {int(note.get('chunk_number') or 0)}: {str(note.get('text') or '').strip()}"
            for note in notes[:40]
            if str(note.get("text") or "").strip()
        )
        lines = [
            f"Titulo: {snapshot.get('title') or 'Sin titulo'}",
            f"Documento ID: {snapshot.get('doc_id') or ''}",
            f"Bloque visible: {snapshot.get('current') or 0} de {snapshot.get('total') or 0}",
        ]

        if include_blocks:
            lines.extend(
                [
                    "",
                    "CATALOGO DE DOCUMENTOS:",
                    document_catalog or "[No hay documentos cargados.]",
                    "",
                    "TEXTO EN PANTALLA:",
                    current_chunk or "[No hay bloque visible.]",
                    "",
                    "BLOQUE ANTERIOR:",
                    previous_chunk or "[No hay bloque anterior.]",
                    "",
                    "BLOQUE SIGUIENTE:",
                    next_chunk or "[No hay bloque siguiente.]",
                ]
            )

        lines.extend(
            [
                "",
                "NOTAS DEL LECTOR:",
                notes_text or "[No hay notas guardadas.]",
            ]
        )

        if reference_catalog:
            lines.extend(["", "DOCUMENTOS DE CONSULTA:", reference_catalog])
        if laboratory_focus:
            lines.extend(["", "FOCO ACTUAL DEL LABORATORIO:", laboratory_focus])
        if include_blocks and relevant_document_text:
            lines.extend(["", "EXTRACTOS Y BLOQUES RELEVANTES:", relevant_document_text])
        if include_document:
            lines.extend(["", "DOCUMENTO COMPLETO DISPONIBLE:", document_text or "[No hay documento cargado.]"])
        return "\n".join(lines)

    def _document_catalog_text(self, snapshot: dict) -> str:
        records = self._document_records(snapshot)
        lines: list[str] = []
        for record in records:
            title = str(record.get("title") or "Sin titulo").strip() or "Sin titulo"
            doc_id = str(record.get("doc_id") or "").strip()
            total = int(record.get("total") or 0)
            source_type = str(record.get("source_type") or "").strip() or "text"
            role = "principal" if record.get("role") == "main" else "consulta"
            line = f"- {title}"
            if doc_id:
                line += f" ({doc_id})"
            line += f" | rol: {role} | tipo: {source_type} | bloques: {total}"
            lines.append(line)
        return "\n".join(lines).strip()

    def _document_records(self, snapshot: dict) -> list[dict]:
        records: list[dict] = []
        main_document = snapshot.get("main_document")
        if isinstance(main_document, dict) and (main_document.get("title") or main_document.get("doc_id")):
            records.append({**main_document, "role": "main", "current": int(snapshot.get("current") or 0)})
        elif snapshot.get("title") or snapshot.get("doc_id"):
            records.append(
                {
                    "doc_id": snapshot.get("doc_id") or "",
                    "title": snapshot.get("title") or "Sin titulo",
                    "source_type": "text",
                    "total": int(snapshot.get("total") or 0),
                    "text": snapshot.get("document_text") or "",
                    "chunks": snapshot.get("document_chunks") or [],
                    "role": "main",
                }
            )
        references = snapshot.get("reference_documents")
        if isinstance(references, list):
            for item in references:
                if isinstance(item, dict):
                    records.append({**item, "role": "reference"})
        return records

    def _reference_catalog_text(self, references: object) -> str:
        if not isinstance(references, list):
            return ""
        lines: list[str] = []
        for item in references[:12]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "Consulta").strip() or "Consulta"
            doc_id = str(item.get("doc_id") or "").strip()
            total = int(item.get("total") or 0)
            source_type = str(item.get("source_type") or "").strip() or "text"
            line = f"- {title}"
            if doc_id:
                line += f" ({doc_id})"
            line += f" | tipo: {source_type} | bloques: {total}"
            lines.append(line)
        return "\n".join(lines).strip()

    def _laboratory_focus_text(self, focus: object) -> str:
        if not isinstance(focus, dict):
            return ""
        title = str(focus.get("title") or "").strip()
        if not title:
            return ""
        chunk_number = int(focus.get("chunk_number") or 0)
        total = int(focus.get("total") or 0)
        role = str(focus.get("role") or "").strip() or "consulta"
        reason = str(focus.get("reason") or "").strip()
        query = str(focus.get("query") or "").strip()
        text = str(focus.get("text") or "").strip()
        lines = [f"- {title} | rol: {role} | bloque: {chunk_number} de {total}"]
        if query:
            lines.append(f"  búsqueda: {query}")
        if reason:
            lines.append(f"  motivo: {reason}")
        if text:
            lines.append(f"  texto: {text[:500].rstrip()}{' [recortado]' if len(text) > 500 else ''}")
        return "\n".join(lines).strip()

    def _relevant_documents_text(self, question: str, snapshot: dict, history: list[dict] | None = None) -> str:
        records = self._document_records(snapshot)
        if not records:
            return ""
        keyword_text = " ".join(
            [
                str(question or ""),
                " ".join(
                    str(item.get("content") or "")
                    for item in (history or [])[-4:]
                    if isinstance(item, dict) and str(item.get("role") or "") == "user"
                ),
            ]
        )
        requested_chunk_numbers = self._extract_requested_chunk_numbers(keyword_text)
        selected = self._select_relevant_records(keyword_text, records)
        sections: list[str] = []
        remaining = max(self.max_document_excerpt_chars, self.max_reference_chars)
        for record in selected:
            section = self._render_document_excerpt(record, keyword_text, requested_chunk_numbers)
            if not section:
                continue
            if len(section) > remaining:
                section = section[:remaining].rstrip() + "\n[Extractos recortados por limite de contexto.]"
            sections.append(section)
            remaining -= len(section)
            if remaining <= 0:
                break
        return "\n\n".join(sections).strip()

    def _select_relevant_records(self, keyword_text: str, records: list[dict]) -> list[dict]:
        normalized = self._normalize_text(keyword_text)
        primary: list[tuple[int, int, dict]] = []
        fallback: list[dict] = []
        for index, record in enumerate(records):
            role_score = 40 if record.get("role") == "main" else 0
            title = self._normalize_text(str(record.get("title") or ""))
            doc_id = self._normalize_text(str(record.get("doc_id") or ""))
            preview = self._normalize_text(str(record.get("preview") or ""))
            score = role_score
            if normalized:
                if title and title in normalized:
                    score += 200
                if doc_id and doc_id in normalized:
                    score += 150
                score += self._keyword_overlap_score(normalized, f"{title} {doc_id} {preview}")
            primary.append((score, -index, record))
            fallback.append(record)
        primary.sort(reverse=True, key=lambda item: (item[0], item[1]))
        selected = [item[2] for item in primary if item[0] > 0]
        if not selected and fallback:
            selected.append(fallback[0])
        if len(selected) < len(fallback):
            for record in fallback:
                if record not in selected:
                    selected.append(record)
        return selected[:6]

    def _render_document_excerpt(self, record: dict, keyword_text: str, requested_chunk_numbers: list[int]) -> str:
        title = str(record.get("title") or "Sin titulo").strip() or "Sin titulo"
        doc_id = str(record.get("doc_id") or "").strip()
        total = int(record.get("total") or 0)
        source_type = str(record.get("source_type") or "").strip() or "text"
        role = "principal" if record.get("role") == "main" else "consulta"
        chunks = record.get("chunks")
        chunk_items = chunks if isinstance(chunks, list) else []
        selected_indexes = self._select_chunk_indexes(record, keyword_text, requested_chunk_numbers)
        lines = [f"[{role.upper()}] {title}{f' ({doc_id})' if doc_id else ''} | tipo: {source_type} | bloques: {total}"]
        preview = str(record.get("preview") or "").strip()
        if preview:
            lines.append(f"Resumen breve: {preview}")
        if not chunk_items:
            return "\n".join(lines)
        lines.append("Bloques disponibles para consulta:")
        for index in selected_indexes:
            if index < 0 or index >= len(chunk_items):
                continue
            item = chunk_items[index]
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            if len(text) > 420:
                text = text[:420].rstrip() + " [recortado]"
            lines.append(f"- Bloque {int(item.get('chunk_number') or index + 1)}: {text}")
        return "\n".join(lines)

    def _select_chunk_indexes(self, record: dict, keyword_text: str, requested_chunk_numbers: list[int]) -> list[int]:
        chunks = record.get("chunks")
        if not isinstance(chunks, list) or not chunks:
            return []
        selected: list[int] = []
        if record.get("role") == "main":
            current = max(1, int(record.get("current") or 0))
            for number in (current - 1, current, current + 1):
                if 1 <= number <= len(chunks):
                    selected.append(number - 1)
        else:
            for index in range(min(self.max_intro_chunks_per_reference, len(chunks))):
                selected.append(index)
        for number in requested_chunk_numbers:
            if 1 <= number <= len(chunks):
                selected.append(number - 1)
        normalized_keywords = self._normalize_text(keyword_text)
        if normalized_keywords:
            scored: list[tuple[int, int]] = []
            for index, item in enumerate(chunks):
                chunk_text = self._normalize_text(str(item.get("text") or ""))
                score = self._keyword_overlap_score(normalized_keywords, chunk_text)
                if score > 0:
                    scored.append((score, -index))
            scored.sort(reverse=True)
            for _, neg_index in scored[: self.max_chunks_per_document]:
                selected.append(-neg_index)
        deduped: list[int] = []
        for index in selected:
            if index not in deduped:
                deduped.append(index)
        return deduped[: self.max_chunks_per_document]

    def _extract_requested_chunk_numbers(self, text: str) -> list[int]:
        numbers = []
        for raw in re.findall(
            r"\b(?:bloque|chunk|parte|secci[oó]n)\s+(\d{1,4})\b", str(text or ""), flags=re.IGNORECASE
        ):
            try:
                value = int(raw)
            except ValueError:
                continue
            if value not in numbers:
                numbers.append(value)
        return numbers

    def _keyword_overlap_score(self, left: str, right: str) -> int:
        left_tokens = self._meaningful_tokens(left)
        right_tokens = set(self._meaningful_tokens(right))
        if not left_tokens or not right_tokens:
            return 0
        return sum(3 if token in right_tokens else 0 for token in left_tokens)

    def _meaningful_tokens(self, text: str) -> list[str]:
        stopwords = {
            "de",
            "del",
            "la",
            "el",
            "los",
            "las",
            "un",
            "una",
            "unos",
            "unas",
            "y",
            "o",
            "u",
            "que",
            "qué",
            "como",
            "cómo",
            "para",
            "por",
            "con",
            "sin",
            "sobre",
            "entre",
            "hay",
            "otro",
            "otra",
            "este",
            "esta",
            "estos",
            "estas",
            "ese",
            "esa",
            "esos",
            "esas",
            "doc",
            "docs",
            "documento",
            "documentos",
            "consulta",
            "principal",
            "puedes",
            "podés",
            "ves",
            "veo",
            "mirar",
            "pantalla",
            "bloque",
        }
        tokens = re.findall(r"[a-záéíóúñ0-9_./-]{3,}", self._normalize_text(text))
        out: list[str] = []
        for token in tokens:
            if token in stopwords:
                continue
            if token not in out:
                out.append(token)
        return out

    def _normalize_text(self, text: str) -> str:
        return " ".join(str(text or "").strip().lower().split())
