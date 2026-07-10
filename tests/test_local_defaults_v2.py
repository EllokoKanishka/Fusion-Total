import unittest
from pathlib import Path


class LocalDefaultsV2Tests(unittest.TestCase):
    def test_local_defaults_doc_exists_and_mentions_override_variables(self):
        text = Path("docs/LOCAL_DEFAULTS_V2.md").read_text(encoding="utf-8")
        for token in (
            "FUSION_READER_GPU_ENV",
            "DIRECT_CHAT_ALLTALK_DIR",
            "DIRECT_CHAT_ALLTALK_PYTHON",
            "FUSION_READER_DOCLING_GPU_ENV",
            "DOCTORA_LUCY_ROOT",
            "FUSION_READER_STT_COMMAND",
            "/home/linuxbrew/.linuxbrew/bin/whisper",
            "no deben interpretarse como rotos solo porque falte un",
        ):
            self.assertIn(token, text)

    def test_primary_docs_link_local_defaults_audit(self):
        for relative in ("README.md", "docs/DEPENDENCIES_V2.md", "docs/OPERATIONS.md", "FUSION_READER_V2_STATE.md"):
            text = Path(relative).read_text(encoding="utf-8")
            self.assertIn("LOCAL_DEFAULTS_V2.md", text, relative)

    def test_runtime_scripts_keep_external_defaults_overrideable(self):
        checks = {
            "scripts/start_reader_neural_tts.sh": (
                'DIRECT_CHAT_ALLTALK_DIR:-/home/lucy-ubuntu/Archivo_proyectos/Taverna/Taverna-legacy/alltalk_tts',
                'DIRECT_CHAT_ALLTALK_PYTHON:-/home/lucy-ubuntu/ebook2audiobook/python_env/bin/python',
            ),
            "scripts/start_reader_neural_tts_gpu_5090.sh": (
                'DIRECT_CHAT_ALLTALK_DIR:-/home/lucy-ubuntu/Archivo_proyectos/Taverna/Taverna-legacy/alltalk_tts',
                'FUSION_READER_GPU_ENV:-/home/lucy-ubuntu/fusion_reader_envs/alltalk_gpu_5090_py311',
            ),
            "scripts/bootstrap_alltalk_gpu_5090.sh": (
                'DIRECT_CHAT_ALLTALK_DIR:-/home/lucy-ubuntu/Archivo_proyectos/Taverna/Taverna-legacy/alltalk_tts',
                'FUSION_READER_GPU_ENV:-/home/lucy-ubuntu/fusion_reader_envs/alltalk_gpu_5090_py311',
            ),
            "scripts/start_fusion_reader_v2_stt.sh": (
                'FUSION_READER_GPU_ENV:-/home/lucy-ubuntu/fusion_reader_envs/alltalk_gpu_5090_py311',
            ),
            "scripts/verify_voice_port_isolation.sh": (
                'DOCTORA_LUCY_ROOT:-/home/lucy-ubuntu/Escritorio/doctora-lucy',
                'DIRECT_CHAT_ALLTALK_DIR:-/home/lucy-ubuntu/Archivo_proyectos/Taverna/Taverna-legacy/alltalk_tts',
            ),
        }
        for relative, tokens in checks.items():
            text = Path(relative).read_text(encoding="utf-8")
            for token in tokens:
                self.assertIn(token, text, f"{relative} missing {token}")

    def test_doctora_absolute_path_only_lives_in_audited_files(self):
        allowed = {
            Path("docs/LOCAL_DEFAULTS_V2.md"),
            Path("docs/AUDIT_CONSOLIDATION_2026-07.md"),
            Path("scripts/verify_voice_port_isolation.sh"),
            Path("tests/test_local_defaults_v2.py"),
        }
        needle = "/home/lucy-ubuntu/Escritorio/doctora-lucy"
        hits = []
        for path in Path(".").rglob("*"):
            if not path.is_file():
                continue
            if ".git" in path.parts or "runtime" in path.parts or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError, OSError):
                continue
            if needle in text:
                hits.append(path)
        self.assertEqual(set(hits), allowed)
