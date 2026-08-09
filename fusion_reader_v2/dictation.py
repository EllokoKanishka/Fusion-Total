from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DictationInstruction:
    """A bounded editor operation derived from one dictated utterance."""

    kind: str
    text: str = ""
    target: str = ""
    scope: str = ""
    number: int = 0
    all_matches: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


_LEADING_FILLER = re.compile(
    r"^(?:(?:no|bueno|a ver|l[uú]c(?:y|[ií])|che)\s*[,.:;-]?\s*)+",
    flags=re.IGNORECASE,
)
_WAKE_WORD = re.compile(
    r"^l[uú]c(?:y|[ií])(?:\b|(?=[,.:;!?_-]))\s*[,.:;!?_-]*\s*(.*)$",
    flags=re.IGNORECASE,
)
_JOINED_COMMAND_SEPARATOR = re.compile(r"(?<=\w)[.:;]+(?=\w)")
_SPANISH_CARDINALS = {
    "una": 1,
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "dieciséis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
    "cien": 100,
}


def _clean_utterance(value: str) -> str:
    return " ".join(str(value or "").strip().replace("¿", "").replace("¡", "").split())


def _command_text(value: str) -> str:
    clean = _clean_utterance(value)
    clean = _JOINED_COMMAND_SEPARATOR.sub(" ", clean)
    return _LEADING_FILLER.sub("", clean).strip()


def _positive_count(value: str) -> int:
    clean = str(value or "").strip().lower()
    try:
        number = int(clean)
    except ValueError:
        number = _SPANISH_CARDINALS.get(clean, 0)
    return max(0, min(number, 10_000))


def _read_instruction(command: str) -> DictationInstruction | None:
    read_match = re.match(
        r"^(?:l[eé]eme|lee(?:me)?|leeme|reproduc[ií]|reproduce)\b\s*(.*)$",
        command,
        flags=re.IGNORECASE,
    )
    if not read_match:
        return None
    request = str(read_match.group(1) or "").strip(" .,:;!?")
    lowered = request.lower()
    if not request or lowered in {"todo", "el texto", "el documento", "completo", "todo el texto"}:
        return DictationInstruction("read", scope="all")
    if re.search(r"\b(?:la\s+)?selecci[oó]n\b", request, flags=re.IGNORECASE):
        return DictationInstruction("read", scope="selection")
    if re.search(r"\b(?:la\s+)?(?:[uú]ltima|final)\s+(?:hoja|p[aá]gina)\b", request, flags=re.IGNORECASE):
        return DictationInstruction("read", scope="last_page")
    if re.search(r"\b(?:el\s+)?[uú]ltimo\s+p[aá]rrafo\b", request, flags=re.IGNORECASE):
        return DictationInstruction("read", scope="last_paragraph")
    if re.search(r"\b(?:el\s+)?p[aá]rrafo\s+actual\b", request, flags=re.IGNORECASE):
        return DictationInstruction("read", scope="current_paragraph")
    if re.search(r"\b(?:el\s+)?p[aá]rrafo\s+anterior\b", request, flags=re.IGNORECASE):
        return DictationInstruction("read", scope="previous_paragraph")
    paragraph_number = re.search(r"\bp[aá]rrafo\s+(?:n[uú]mero\s+)?(\d{1,4})\b", request, flags=re.IGNORECASE)
    if paragraph_number:
        return DictationInstruction("read", scope="paragraph_number", number=int(paragraph_number.group(1)))
    paragraph_match = re.search(
        r"\bp[aá]rrafo\s+(?:que\s+(?:empieza|comienza)\s+(?:con\s+)?|donde\s+dice\s+|con\s+)?(.+)$",
        request,
        flags=re.IGNORECASE,
    )
    if paragraph_match:
        target = str(paragraph_match.group(1) or "").strip(" .,:;!?")
        if target:
            return DictationInstruction("read", target=target, scope="paragraph_matching")
    from_match = re.search(r"\b(?:a\s+partir\s+de|desde)\s+(.+)$", request, flags=re.IGNORECASE)
    if from_match:
        target = str(from_match.group(1) or "").strip(" .,:;!?")
        if target in {"acá", "aquí", "el cursor", "cursor"}:
            return DictationInstruction("read", scope="from_cursor")
        return DictationInstruction("read", target=target, scope="from_text")
    return DictationInstruction("read", target=request, scope="paragraph_matching")


def interpret_dictation_transcript(
    value: str,
    *,
    commands_enabled: bool = True,
    require_wake_word: bool = False,
) -> DictationInstruction:
    """Interpret Spanish editor commands and otherwise preserve the utterance as dictation.

    Commands are deliberately bounded. The caller remains responsible for applying
    the operation to an editor and can therefore keep undo/redo ownership local.
    """

    original = _clean_utterance(value)
    if not original:
        return DictationInstruction("noop")
    if not commands_enabled:
        return DictationInstruction("dictate", text=original)
    if require_wake_word:
        wake_match = _WAKE_WORD.match(original)
        if not wake_match:
            return DictationInstruction("dictate", text=original)
        command = _command_text(str(wake_match.group(1) or ""))
        if not command:
            return DictationInstruction("noop")
    else:
        command = _command_text(original)

    pause_match = re.match(
        r"^(?:par[aá]|para|paramos)\s+(?:ac[aá]|aqu[ií])\b\s*[.,:;-]?\s*(.*)$",
        command,
        flags=re.IGNORECASE,
    )
    if pause_match:
        resumed_command = str(pause_match.group(1) or "").strip()
        if not resumed_command:
            return DictationInstruction("stop_listening")
        command = resumed_command

    if re.fullmatch(
        r"(?:det[eé]n|detener|par[aá]|para|paramos|termin[aá]|terminar|cerr[aá]|cerrar)"
        r"(?:\s+(?:el\s+)?dictado|\s+(?:ac[aá]|aqu[ií]))?",
        command,
        flags=re.IGNORECASE,
    ):
        return DictationInstruction("stop_listening")
    if re.fullmatch(r"(?:deshac[eé]|deshacer|atr[aá]s)", command, flags=re.IGNORECASE):
        return DictationInstruction("undo")
    if re.fullmatch(r"(?:rehac[eé]|rehacer)", command, flags=re.IGNORECASE):
        return DictationInstruction("redo")
    if re.fullmatch(
        r"(?:borr[aá]|borra|elimin[aá]|elimina|limpi[aá]|limpia)\s+(?:todo|el\s+texto|el\s+documento)",
        command,
        flags=re.IGNORECASE,
    ):
        return DictationInstruction("clear")

    anchor_first_delete = re.fullmatch(
        r"(?:despu[eé]s|luego|a\s+partir)\s+de\s+(.+?)\s*[,;:]?\s+"
        r"(?:borr[aá]|borra|borrar|elimin[aá]|elimina|eliminar|quit[aá]|quita|quitar)"
        r"(?:\s+(?:todo|el\s+resto|hasta\s+el\s+final))?\s*[.,;:!?]*",
        command,
        flags=re.IGNORECASE,
    )
    if anchor_first_delete:
        target = str(anchor_first_delete.group(1) or "").strip(" .,:;!?\"'")
        if target:
            return DictationInstruction("delete_from", target=target)

    object_first_replace = re.fullmatch(
        r"(.+?)\s*[,;:]?\s+"
        r"(?:c[aá]mbialo|cambiarlo|reempl[aá]zalo|reemplazarlo|sustit[uú]yelo|sustituirlo)\s+"
        r"(?:por|con)\s+(.+)",
        command,
        flags=re.IGNORECASE,
    )
    if object_first_replace:
        target = str(object_first_replace.group(1) or "").strip(" .,:;!?\"'")
        replacement = str(object_first_replace.group(2) or "").strip()
        if target and replacement:
            return DictationInstruction("replace", target=target, text=replacement)

    replace_last_words = re.fullmatch(
        r"(?:reemplaz[aá]|reemplaza|reemplazar|cambi[aá]|cambia|cambiar|sustitu[ií]|sustituye|sustituir)\s+"
        r"(?:las?\s+)?(?:[uú]ltimas?|finales?)\s+([\wáéíóúüñ]+)\s+palabras?\s+"
        r"(?:por|con)\s+(.+)",
        command,
        flags=re.IGNORECASE,
    )
    if replace_last_words:
        number = _positive_count(replace_last_words.group(1))
        replacement = str(replace_last_words.group(2) or "").strip()
        if number and replacement:
            return DictationInstruction("replace_last_words", text=replacement, number=number)

    delete_last_words = re.fullmatch(
        r"(?:borr[aá]|borra|borrar|elimin[aá]|elimina|eliminar|quit[aá]|quita|quitar)\s+"
        r"(?:(?:las?\s+)?(?:[uú]ltimas?|finales?)\s+)?([\wáéíóúüñ]+)\s+palabras?\s*[.,;:!?]*",
        command,
        flags=re.IGNORECASE,
    )
    if delete_last_words:
        number = _positive_count(delete_last_words.group(1))
        if number:
            return DictationInstruction("delete_last_words", number=number)

    read = _read_instruction(command)
    if read is not None:
        return read

    replace_match = re.match(
        r"^(?:reemplaz[aá]|reemplaza|cambi[aá]|cambia|sustitu[ií]|sustituye)\s+(.+?)\s+(?:por|con)\s+(.+)$",
        command,
        flags=re.IGNORECASE,
    )
    if replace_match:
        return DictationInstruction(
            "replace",
            target=str(replace_match.group(1)).strip(" .,:;!?\"'"),
            text=str(replace_match.group(2)).strip(),
        )

    delete_from_match = re.match(
        r"^(?:borr[aá]|borra|elimin[aá]|elimina|quit[aá]|quita)\s+"
        r"(?:(?:todo\s+)?(?:desde|a\s+partir\s+de|de)\s+)?(.+?)\s+"
        r"(?:(?:(?:para|hacia|en)\s+adelante)|hasta\s+el\s+final)\s*[.,;:!?]*$",
        command,
        flags=re.IGNORECASE,
    )
    if delete_from_match:
        target = str(delete_from_match.group(1) or "").strip(" .,:;!?\"'")
        if target:
            return DictationInstruction("delete_from", target=target)

    delete_and_write = re.match(
        r"^(?:borr[aá]|borra|elimin[aá]|elimina|quit[aá]|quita)\s+(.+?)\s+y\s+(?:escrib[ií]|escribe|pon[eé]|pone|pon|agreg[aá]|agrega)\s+(.+)$",
        command,
        flags=re.IGNORECASE,
    )
    if delete_and_write:
        return DictationInstruction(
            "replace",
            target=str(delete_and_write.group(1)).strip(" .,:;!?\"'"),
            text=str(delete_and_write.group(2)).strip(),
        )

    delete_match = re.match(
        r"^(?:borr[aá]|borra|elimin[aá]|elimina|quit[aá]|quita)\s+(?:(todas?)\s+(?:las\s+)?(?:veces\s+)?(?:que\s+aparece\s+)?)?(.+)$",
        command,
        flags=re.IGNORECASE,
    )
    if delete_match:
        target = str(delete_match.group(2) or "").strip(" .,:;!?\"'")
        if target:
            return DictationInstruction(
                "delete",
                target=target,
                all_matches=bool(delete_match.group(1)),
            )

    paragraph_match = re.fullmatch(
        r"(?:nuevo\s+p[aá]rrafo|punto\s+y\s+aparte)",
        command,
        flags=re.IGNORECASE,
    )
    if paragraph_match:
        return DictationInstruction("insert", text="\n\n")

    insert_match = re.match(
        r"^(?:escrib[ií]|escribe|pon[eé]|pone|pon|agreg[aá]|agrega|a[nñ]ad[ií]|a[nñ]ade)\s+(.+)$",
        command,
        flags=re.IGNORECASE,
    )
    if insert_match:
        return DictationInstruction("insert", text=str(insert_match.group(1)).strip())

    if require_wake_word:
        return DictationInstruction("noop")
    return DictationInstruction("dictate", text=original)


__all__ = ["DictationInstruction", "interpret_dictation_transcript"]
