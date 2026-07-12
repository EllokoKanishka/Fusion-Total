#!/usr/bin/env python3
import os
import sys
import json
from pathlib import Path
from typing import List, Optional, Dict

# Try to import mcp SDK
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types
except ImportError:
    # If not available, we define minimal types for internal logic to stay testable
    Server = None
    stdio_server = None
    types = None

REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_DIR = REPO_ROOT / "runtime" / "fusion_reader_v2" / "memory"

ALLOWED_FILENAMES = {
    "README.md",
    "project_state.md",
    "decisions.md",
    "boundaries.md",
    "runtime_commands.md",
    "next_steps.md",
}


def get_base_dir() -> Path:
    return MEMORY_DIR


def allowed_memory_files() -> List[str]:
    """Returns the list of allowed memory files present in the directory."""
    if not MEMORY_DIR.exists():
        return []
    return sorted([f for f in ALLOWED_FILENAMES if (MEMORY_DIR / f).is_file()])


def safe_memory_path(name: str) -> Optional[Path]:
    """Resolves and validates a memory path to ensure it is within the allowed bounds."""
    if not name or name not in ALLOWED_FILENAMES:
        return None

    # Basic filename validation
    if os.path.sep in name or (os.path.altsep and os.path.altsep in name):
        return None

    try:
        base = MEMORY_DIR.resolve()
        target = (MEMORY_DIR / name).resolve()

        # Check if target is inside base
        if not str(target).startswith(str(base)):
            return None

        # Only .md allowed
        if not name.endswith(".md"):
            return None

        if target.is_file():
            return target
    except Exception:
        return None
    return None


def read_memory_file(name: str, limit: int = 20000) -> str:
    """Reads a memory file with safety checks and character limit."""
    path = safe_memory_path(name)
    if not path:
        return f"Error: Archivo '{name}' no permitido o no encontrado."

    try:
        content = path.read_text(encoding="utf-8")
        if len(content) > limit:
            return content[:limit] + "\n\n[... Truncado por límite de tamaño ...]"
        return content
    except Exception as e:
        return f"Error al leer archivo: {str(e)}"


def search_memory(query: str, limit: int = 20) -> List[Dict]:
    """Searches for a query in all allowed memory files."""
    results = []
    if not query:
        return results

    query_lower = query.lower()
    files = allowed_memory_files()

    for filename in files:
        path = MEMORY_DIR / filename
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines):
                if query_lower in line.lower():
                    results.append({"file": filename, "line": i + 1, "content": line.strip()})
                    if len(results) >= limit:
                        return results
        except Exception:
            continue
    return results


# MCP Server Implementation
if Server:
    server = Server("fusion-memory-mcp")

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="memory.list",
                description="Lista los archivos de memoria curada disponibles.",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="memory.read",
                description="Lee el contenido de un archivo de memoria específico.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Nombre del archivo (ej: project_state.md)"}
                    },
                    "required": ["name"],
                },
            ),
            types.Tool(
                name="memory.search",
                description="Busca texto simple en todos los archivos de memoria.",
                inputSchema={
                    "type": "object",
                    "properties": {"query": {"type": "string", "description": "Término de búsqueda"}},
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="memory.state",
                description="Muestra el estado actual del proyecto (project_state.md).",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="memory.boundaries",
                description="Muestra las reglas de frontera del sistema (boundaries.md).",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="memory.next_steps",
                description="Muestra los próximos pasos recomendados (next_steps.md).",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
        if name == "memory.list":
            files = allowed_memory_files()
            return [types.TextContent(type="text", text=json.dumps(files, indent=2))]

        elif name == "memory.read":
            fname = arguments.get("name") if arguments else None
            if not fname:
                return [types.TextContent(type="text", text="Error: Falta el argumento 'name'.")]
            return [types.TextContent(type="text", text=read_memory_file(fname))]

        elif name == "memory.search":
            query = arguments.get("query") if arguments else None
            if not query:
                return [types.TextContent(type="text", text="Error: Falta el argumento 'query'.")]
            res = search_memory(query)
            return [types.TextContent(type="text", text=json.dumps(res, indent=2, ensure_ascii=False))]

        elif name == "memory.state":
            return [types.TextContent(type="text", text=read_memory_file("project_state.md"))]

        elif name == "memory.boundaries":
            return [types.TextContent(type="text", text=read_memory_file("boundaries.md"))]

        elif name == "memory.next_steps":
            return [types.TextContent(type="text", text=read_memory_file("next_steps.md"))]

        else:
            raise ValueError(f"Herramienta desconocida: {name}")


async def main():
    if not Server:
        print("Error: SDK mcp no instalado.", file=sys.stderr)
        sys.exit(1)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
