from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Union
from pypdf import PdfReader

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
import gradio as gr
import jinja2
import pandas as pd
import anthropic
from dotenv import load_dotenv
import os
import sys
import json
import re
import hashlib
import threading
from datetime import datetime

load_dotenv()

# Sonnet is the workhorse here: a batch issues one call per characteristic, so the
# cost/latency profile matters. Set ANTHROPIC_MODEL=claude-opus-5 for the strongest model.
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
# A hung request used to stall an entire batch; bound it and retry transient failures.
REQUEST_TIMEOUT = float(os.getenv("ANTHROPIC_TIMEOUT", "180"))
# Batch classification fans out one request per characteristic.
MAX_PARALLEL_CLASSIFICATIONS = int(os.getenv("MAX_PARALLEL_CLASSIFICATIONS", "8"))
# Run the first characteristic alone so it populates the prompt cache for the rest.
WARM_PROMPT_CACHE = os.getenv("WARM_PROMPT_CACHE", "1") not in ("0", "false", "False")

client = anthropic.Anthropic(timeout=REQUEST_TIMEOUT, max_retries=3)

# Custom UI theme & styling (light, clean, with a subtle warm accent)
APP_CSS = """
.gradio-container {
  background: #f9fafb;
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

#app-title {
  font-size: 2.1rem;
  font-weight: 700;
  margin-bottom: 0.2rem;
  color: #111827;
}

#app-subtitle {
  color: #6b7280;
  margin-bottom: 1.3rem;
}

.app-panel {
  border-radius: 0.9rem;
  border: 1px solid #e5e7eb;
  padding: 1.1rem 1.25rem;
  background: #ffffff;
  box-shadow: 0 8px 18px rgba(15,23,42,0.04);
  box-sizing: border-box;
  flex: 1;
}

.app-section-label {
  font-weight: 600;
  margin-bottom: 0.4rem;
  color: #111827;
}

.app-row-equal {
  display: flex !important;
  align-items: stretch !important;
}

.app-row-equal > div {
  display: flex;
}

#app-footer {
  margin-top: 1.75rem;
  font-size: 0.8rem;
  color: #9ca3af;
  text-align: center;
}

/* Tabs */
.gradio-container .tabs > div[role="tablist"] > button[role="tab"] {
  font-weight: 500;
}

.gradio-container .tabs > div[role="tablist"] > button[role="tab"][aria-selected="true"] {
  color: #ea580c;
  border-color: #ea580c;
}

/* Primary buttons */
button.primary, button[class*="primary"] {
  background: linear-gradient(135deg, #f97316, #ea580c);
  border-color: #ea580c;
}

button.primary:hover, button[class*="primary"]:hover {
  background: linear-gradient(135deg, #fb923c, #ea580c);
  border-color: #ea580c;
}

/* Secondary buttons */
button.secondary, button[class*="secondary"] {
  border-radius: 999px;
}

/* File components and text areas */
.gradio-container .file, .gradio-container textarea, .gradio-container input, .gradio-container .dataframe {
  border-radius: 0.6rem;
}

/* Result pill */
.result-wrap {
  display: flex;
  align-items: flex-start;
  justify-content: flex-start;
  padding: 0.4rem 0;
}

.primary-cta {
  align-self: flex-start;
  width: fit-content;
  min-width: 150px;
  padding: 0.55rem 1.2rem !important;
  border-radius: 10px !important;
  box-shadow: 0 6px 12px rgba(234, 88, 12, 0.08);
}

.cta-row {
  display: flex;
  justify-content: flex-start;
}

.result-title {
  font-weight: 600;
  font-size: 1.15rem;
  color: #111827;
}

.result-sub {
  margin-top: 0.15rem;
  color: #6b7280;
  font-size: 1.05rem;
  line-height: 1.35;
}

.result-main {
  margin-top: 0.4rem;
  font-size: 0.95rem;
  font-weight: 300;
  color: #111827;
}

.result-card {
  background: #f9fafb;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  padding: 0.75rem 0.9rem;
}

/* Spreadsheet summary strip */
.sheet-summary {
  background: #fff7ed;
  border: 1px solid #fed7aa;
  border-left: 3px solid #ea580c;
  border-radius: 8px;
  padding: 0.6rem 0.85rem;
  margin-bottom: 0.6rem;
  font-size: 0.9rem;
}

.sheet-summary code {
  background: #ffedd5;
  padding: 0.05rem 0.3rem;
  border-radius: 4px;
}

.hint-text {
  color: #6b7280;
  font-size: 0.85rem;
  margin-top: 0.4rem;
}

/* Destructive actions are visually quarantined from everything else */
.danger-zone {
  border: 1px solid #fecaca !important;
  border-radius: 0.7rem !important;
  background: #fef2f2 !important;
  margin-top: 1rem;
}

.danger-zone button.stop, .danger-zone button[class*="stop"] {
  border-radius: 8px !important;
}
"""

# User progress storage
PROGRESS_DIR = Path(__file__).resolve().parent / "user_progress"
PROGRESS_DIR.mkdir(exist_ok=True)

def get_user_progress_file(username: str) -> Path:
    """Get the progress file path for a user."""
    # Hash username for filename
    username_hash = hashlib.md5(username.encode()).hexdigest()
    return PROGRESS_DIR / f"{username_hash}_progress.json"

def load_user_progress(username: str) -> dict:
    """Load user's progress from file."""
    progress_file = get_user_progress_file(username)
    if progress_file.exists():
        try:
            with open(progress_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_user_progress(username: str, progress: dict):
    """Save user's progress to file."""
    progress_file = get_user_progress_file(username)
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)

def get_document_id(document_file) -> str:
    """Generate a unique ID for a document based on its content."""
    if document_file is None:
        return None
    # Use filename and size as document identifier
    file_path = Path(resolve_file_path(document_file))
    file_size = file_path.stat().st_size if file_path.exists() else 0
    # Create a hash of filename + size
    doc_id = hashlib.md5(f"{file_path.name}_{file_size}".encode()).hexdigest()
    return doc_id

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    trim_blocks=True,
    lstrip_blocks=True
)

SYSTEM_PROMPT = """You are an expert legal document assistant who helps generate professional legal documents.
Analyze the user's requirements and enhance them with proper legal language and structure."""

CharacteristicClass = Literal[
    "a1_1",
    "a1_2",
    "a1_3_alineacion",
    "a1_3_ambito",
    "a2_1",
    "a2_2",
    "a2_3",
    "a3_1",
    "a3_2_competencias",
    "a3_2_cooperacion",
    "a3_3",
    "b1",
    "b2",
    "b3",
    "c1",
    "c2_1",
    "c2_2",
    "c3_1",
    "c3_2",
    "c4_1",
    "c4_2",
    "genero",
    # Project identification metadata (only core fields)
    "meta_pais",
    "meta_titulo",
    "meta_anio",
    "meta_autores",
    "meta_partido",
]

@dataclass(frozen=True)
class CharacteristicDefinition:
    name: CharacteristicClass
    template_filename: str
    info: str


CHARACTERISTIC_DEFINITIONS: dict[CharacteristicClass, CharacteristicDefinition] = {
    "a1_1": CharacteristicDefinition(
        name="a1_1",
        template_filename="a1_1_prompt.jinja",
        info="A1.1 - Alcance (General/Específico)"
    ),
    "a1_2": CharacteristicDefinition(
        name="a1_2",
        template_filename="a1_2_prompt.jinja",
        info="A1.2 - Definición de IA (Incluye/No incluye)"
    ),
    "a1_3_alineacion": CharacteristicDefinition(
        name="a1_3_alineacion",
        template_filename="a1_3_alineacion_prompt.jinja",
        info="A.1.3 - Alineación con definiciones internacionales"
    ),
    "a1_3_ambito": CharacteristicDefinition(
        name="a1_3_ambito",
        template_filename="a1_3_ambito_prompt.jinja",
        info="A1.3 - Ámbito de aplicación: sujetos alcanzados y excepciones"
    ),
    "a2_1": CharacteristicDefinition(
        name="a2_1",
        template_filename="a2_1_prompt.jinja",
        info="A.2.1 - Ley nueva o modificatoria"
    ),
    "a2_2": CharacteristicDefinition(
        name="a2_2",
        template_filename="a2_2_prompt.jinja",
        info="A2.2 - Enfoque Normativo (Punitivo/Orientativo)"
    ),
    "a2_3": CharacteristicDefinition(
        name="a2_3",
        template_filename="a2_3_prompt.jinja",
        info="A2.3 - Procedimientos específicos de regulación"
    ),
    "a3_1": CharacteristicDefinition(
        name="a3_1",
        template_filename="a3_1_prompt.jinja",
        info="A3.1 - Autoridad encargada de la regulación y/o supervisión"
    ),
    "a3_2_competencias": CharacteristicDefinition(
        name="a3_2_competencias",
        template_filename="a3_2_competencias_prompt.jinja",
        info="A.3.2 - Competencias de la autoridad"
    ),
    "a3_2_cooperacion": CharacteristicDefinition(
        name="a3_2_cooperacion",
        template_filename="a3_2_cooperacion_prompt.jinja",
        info="A3.2 - Cooperación internacional"
    ),
    "a3_3": CharacteristicDefinition(
        name="a3_3",
        template_filename="a3_3_prompt.jinja",
        info="A3.3 - Participación de partes interesadas"
    ),
    "b1": CharacteristicDefinition(
        name="b1",
        template_filename="b1_prompt.jinja",
        info="B1 - Desarrollo de Talentos"
    ),
    "b2": CharacteristicDefinition(
        name="b2",
        template_filename="b2_prompt.jinja",
        info="B2 - Datos"
    ),
    "b3": CharacteristicDefinition(
        name="b3",
        template_filename="b3_prompt.jinja",
        info="B3 - Desarrollo de Infraestructura"
    ),
    "c1": CharacteristicDefinition(
        name="c1",
        template_filename="c1_prompt.jinja",
        info="C1 - Derechos Humanos y Libertades Fundamentales"
    ),
    "c2_1": CharacteristicDefinition(
        name="c2_1",
        template_filename="c2_1_prompt.jinja",
        info="C2.1 - Uso para la gestión interna y modernización del Estado"
    ),
    "c2_2": CharacteristicDefinition(
        name="c2_2",
        template_filename="c2_2_prompt.jinja",
        info="C2.2 - Ciberseguridad y ciberdefensa"
    ),
    "c3_1": CharacteristicDefinition(
        name="c3_1",
        template_filename="c3_1_prompt.jinja",
        info="C3.1 - Desarrollo Productivo"
    ),
    "c3_2": CharacteristicDefinition(
        name="c3_2",
        template_filename="c3_2_prompt.jinja",
        info="C.3.2 - Impulso al sistema de I+D relacionado a la IA"
    ),
    "c4_1": CharacteristicDefinition(
        name="c4_1",
        template_filename="c4_1_prompt.jinja",
        info="C4.1 - Uso de la IA para la sostenibilidad y la lucha contra el cambio climático"
    ),
    "c4_2": CharacteristicDefinition(
        name="c4_2",
        template_filename="c4_2_prompt.jinja",
        info="C4.2 - Evaluaciones de impacto ambiental"
    ),
    "genero": CharacteristicDefinition(
        name="genero",
        template_filename="genero_prompt.jinja",
        info="Género - Consideraciones de género"
    ),
    # Project identification metadata (Identificación del proyecto)
    "meta_pais": CharacteristicDefinition(
        name="meta_pais",
        template_filename="meta_pais_prompt.jinja",
        info="País - País responsable del proyecto o regulación"
    ),
    "meta_titulo": CharacteristicDefinition(
        name="meta_titulo",
        template_filename="meta_titulo_prompt.jinja",
        info="Título del Proyecto - Título oficial o inferido"
    ),
    "meta_anio": CharacteristicDefinition(
        name="meta_anio",
        template_filename="meta_anio_prompt.jinja",
        info="Año - Año principal del proyecto o iniciativa"
    ),
    "meta_autores": CharacteristicDefinition(
        name="meta_autores",
        template_filename="meta_autores_prompt.jinja",
        info="Autor/a/es - Personas o instituciones proponentes"
    ),
    "meta_partido": CharacteristicDefinition(
        name="meta_partido",
        template_filename="meta_partido_prompt.jinja",
        info="Partido Político - Afiliación política principal"
    ),
}

PROMPT_TEMPLATES = {
    characteristic: jinja_env.get_template(config.template_filename)
    for characteristic, config in CHARACTERISTIC_DEFINITIONS.items()
}


DEFAULT_CHARACTERISTIC: CharacteristicClass = "a1_1"

def sort_characteristics(characteristics: list[CharacteristicClass]) -> list[CharacteristicClass]:
    """Sort characteristics by dimension (A, B, C) then by number."""
    def sort_key(char: CharacteristicClass) -> tuple:
        # Extract dimension letter and numbers
        if char.startswith('a'):
            dim = 'A'
            rest = char[1:]
        elif char.startswith('b'):
            dim = 'B'
            rest = char[1:]
        elif char.startswith('c'):
            dim = 'C'
            rest = char[1:]
        elif char == 'genero':
            return ('Z', 0, 0, 0)  # Put genero at the end
        else:
            return ('Z', 0, 0, 0)
        
        # Parse numbers from rest (e.g., "1_1" -> (1, 1), "2_3" -> (2, 3), "3_2_competencias" -> (3, 2, 0))
        parts = rest.split('_')
        nums = []
        for part in parts:
            if part.isdigit():
                nums.append(int(part))
            else:
                nums.append(0)  # Non-numeric parts get 0
        
        # Pad to 3 numbers for consistent sorting
        while len(nums) < 3:
            nums.append(0)
        
        return (dim, nums[0], nums[1], nums[2])
    
    return sorted(characteristics, key=sort_key)

CHARACTERISTIC_CHOICES: tuple[CharacteristicClass, ...] = tuple(CHARACTERISTIC_DEFINITIONS.keys())
SORTED_CHARACTERISTICS: list[CharacteristicClass] = sort_characteristics(list(CHARACTERISTIC_CHOICES))

CHARACTERISTIC_INFORMATION_BLOCK = "\n".join(
    f"- {characteristic}: {config.info}" 
    for characteristic, config in CHARACTERISTIC_DEFINITIONS.items()
)





def strip_label_decoration(line: str) -> str:
    """Remove markdown/list decoration the model sometimes wraps the label in.

    The label column is the categorical value the whole spreadsheet is filtered and
    pivoted on, so "# Se alinea con la definición de la OCDE" has to land as the same
    value as "Se alinea con la definición de la OCDE" rather than a second category.
    """
    cleaned = line.strip()
    # Leading heading hashes, blockquote markers, bullets, or "1." / "1)" numbering.
    cleaned = re.sub(r'^\s*(?:#{1,6}\s*|>\s*|[-*+]\s+|\d+[.)]\s+)', '', cleaned).strip()
    # Paired bold/italic or backtick wrappers around the whole label.
    cleaned = re.sub(r'^(\*{1,3}|_{1,3}|`+)(.*?)\1$', r'\2', cleaned).strip()
    # Surrounding quotes (straight or curly), which the templates use in their examples.
    cleaned = re.sub(r'^["\'“‘](.*)["\'”’]$', r'\1', cleaned).strip()
    # A trailing colon from "Answer:"-style phrasing leaves an empty tail; drop the label prefix.
    cleaned = re.sub(r'^(?:respuesta|answer|clasificaci[oó]n|classification)\s*:\s*', '', cleaned,
                     flags=re.IGNORECASE).strip()
    return cleaned or line.strip()


def extract_clean_answer(full_response: str) -> str:
    """Extract the clean answer (first line before '_____') from the full response."""
    if not full_response:
        return ""

    # Split by "_____" and take the first part
    parts = full_response.split("_____")
    if parts:
        # Get first line and strip whitespace
        first_line = parts[0].strip().split('\n')[0].strip()
        return strip_label_decoration(first_line)
    return strip_label_decoration(full_response.strip().split('\n')[0])


# Parsing a PDF costs seconds; a batch used to re-parse it once per characteristic.
# Keyed on (path, size, mtime) so a re-uploaded/edited file never serves stale text.
_PDF_CACHE: dict[tuple, list[dict]] = {}
_PDF_CACHE_LOCK = threading.Lock()
_PDF_CACHE_MAX_ENTRIES = 8


def resolve_file_path(document_file) -> str | None:
    """Return the on-disk path for a Gradio file value (object or plain path)."""
    if document_file is None:
        return None
    return getattr(document_file, "name", document_file)


def extract_rationale(full_response: str) -> str:
    """Return the explanation after the '_____' separator.

    The label already has its own column, so repeating it at the head of every summary
    cell just pushes the actual reasoning out of view in Excel.
    """
    if not full_response:
        return ""
    parts = full_response.split("_____", 1)
    if len(parts) == 2:
        return parts[1].strip()
    return full_response.strip()


def extract_pdf_pages(document_file) -> list[dict]:
    """Extract text per page for evidence highlighting (cached per file version)."""
    path = resolve_file_path(document_file)
    if not path:
        return []

    try:
        stat = os.stat(path)
        cache_key = (str(path), stat.st_size, stat.st_mtime_ns)
    except OSError:
        cache_key = None

    if cache_key is not None:
        with _PDF_CACHE_LOCK:
            cached = _PDF_CACHE.get(cache_key)
        if cached is not None:
            return cached

    reader = PdfReader(path)
    pages: list[dict] = []
    for idx, page in enumerate(reader.pages):
        pages.append({
            "page": idx + 1,
            "text": page.extract_text() or ""
        })

    if cache_key is not None:
        with _PDF_CACHE_LOCK:
            _PDF_CACHE[cache_key] = pages
            while len(_PDF_CACHE) > _PDF_CACHE_MAX_ENTRIES:
                _PDF_CACHE.pop(next(iter(_PDF_CACHE)))

    return pages


def extract_pdf_text(document_file) -> str:
    """Extract full text from a PDF."""
    return "\n".join(page["text"] for page in extract_pdf_pages(document_file))


def summary_column_name(characteristic: str) -> str:
    """Return the summary column name for a given characteristic."""
    return f"{characteristic}_summary"


def reorder_columns_with_summaries(df: pd.DataFrame) -> pd.DataFrame:
    """Reorder columns to interleave characteristics with their summary columns for editor/download."""
    if "Document Name" not in df.columns:
        return df
    
    other_cols = [col for col in df.columns if col != "Document Name"]
    
    # Metadata columns we want at the front, in a fixed order if present
    meta_cols = [col for col in other_cols if col.startswith("meta_")]
    preferred_meta_order = [
        "meta_pais",
        "meta_titulo",
        "meta_anio",
        "meta_autores",
        "meta_partido",
    ]
    ordered_meta_cols = [col for col in preferred_meta_order if col in meta_cols] + [
        col for col in meta_cols if col not in preferred_meta_order
    ]
    
    # Classification characteristics (excluding metadata)
    char_cols = [
        col for col in other_cols
        if col in SORTED_CHARACTERISTICS and not col.startswith("meta_")
    ]
    sorted_char_cols = sort_characteristics(char_cols)
    
    # Ensure summary columns exist
    for c in sorted_char_cols:
        s_col = summary_column_name(c)
        if s_col not in df.columns:
            df[s_col] = None
    
    # Interleave characteristic and summary columns
    interleaved_cols: list[str] = []
    for c in sorted_char_cols:
        interleaved_cols.append(c)
        interleaved_cols.append(summary_column_name(c))
    
    # Any remaining non-characteristic, non-metadata columns
    remaining_cols = [
        col for col in other_cols
        if col not in meta_cols and col not in interleaved_cols
    ]
    
    return df[["Document Name"] + ordered_meta_cols + interleaved_cols + remaining_cols]


# Opaque token -> path, so the preview route can never be pointed at an arbitrary file.
# Gradio's own /gradio_api/file= route sends Content-Disposition: attachment, which makes
# browsers download rather than render, so the preview needs its own inline route.
_PDF_TOKENS: dict[str, str] = {}
_PDF_TOKENS_LOCK = threading.Lock()


def register_pdf(pdf_path: str) -> str:
    """Register a PDF for previewing and return its opaque token."""
    token = hashlib.sha256(str(pdf_path).encode()).hexdigest()[:32]
    with _PDF_TOKENS_LOCK:
        _PDF_TOKENS[token] = str(pdf_path)
    return token


def render_pdf_embed(pdf_path: str | None, page: int = 1) -> str:
    """Return HTML embedding the PDF at a given page.

    The file is streamed on demand instead of inlined as a base64 data URL, so jumping
    between pages costs a few hundred bytes instead of re-sending the whole document
    (~1.33x its size) through the page on every update.
    """
    if not pdf_path:
        return "<p>No PDF uploaded.</p>"
    safe_page = page if page and page > 0 else 1
    token = register_pdf(pdf_path)
    return (
        f'<iframe src="/pdf/{token}#page={safe_page}" '
        'style="width:100%;height:640px;border:1px solid #e5e7eb;border-radius:10px;"></iframe>'
    )


def get_supporting_evidence(
    document_file,
    characteristic: CharacteristicClass,
    pages: list[dict] | None = None,
) -> list[dict]:
    """Use the model to find supporting snippets with page references."""
    if pages is None:
        pages = extract_pdf_pages(document_file)
    if not pages:
        return []

    pages_prompt = "\n\n".join(
        f"Page {p['page']}:\n{p['text']}" for p in pages if p.get("text")
    )
    if not pages_prompt:
        return []
    
    characteristic_info = CHARACTERISTIC_DEFINITIONS[characteristic].info
    evidence_instructions = (
        "You are helping cite evidence for a classification decision.\n"
        "Given the pages of a legal document above and the selected taxonomy characteristic, "
        "return up to 3 supporting snippets that justify the classification. "
        "Each snippet must include the page number, an exact quote from that page, "
        "and a one-sentence reason. Respond ONLY with a JSON array of objects in this form:\n"
        '[{"page": 2, "quote": "…", "reason": "…"}]\n\n'
        f"Characteristic: {characteristic} - {characteristic_info}"
    )

    try:
        response = client.messages.create(
            model=MODEL,
            # Three verbatim legal quotes plus reasons truncate well under 600.
            max_tokens=1500,
            system="Return only JSON. Do not add commentary.",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Document pages:\n{pages_prompt}",
                            "cache_control": {"type": "ephemeral"},
                        },
                        {"type": "text", "text": evidence_instructions},
                    ],
                },
            ],
        )
        raw = "".join(b.text for b in response.content if b.type == "text")
        # Tolerate code fences or a sentence wrapped around the array.
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return []
        evidence = json.loads(match.group(0))
        if not isinstance(evidence, list):
            return []
        cleaned = []
        for item in evidence:
            if not isinstance(item, dict):
                continue
            page = item.get("page")
            quote = (item.get("quote") or "").strip()
            reason = (item.get("reason") or "").strip()
            if page and quote:
                cleaned.append({"page": page, "quote": quote, "reason": reason})
        return cleaned
    except Exception as error:
        # Best-effort; if the call or parsing fails, fall back to no evidence — but say
        # so on the console rather than failing silently.
        print(f"[evidence] {characteristic}: {type(error).__name__}: {error}", file=sys.stderr)
        return []


_DOCUMENT_SENTINEL = "<<<AI_TAXONOMY_DOCUMENT_TEXT>>>"
_DOCUMENT_BACKREFERENCE = "(the full document text is provided above)"
_INSTRUCTION_CACHE: dict[CharacteristicClass, str] = {}


def build_instructions(characteristic: CharacteristicClass) -> str:
    """Render a characteristic's template with the document replaced by a back-reference.

    The document travels as its own cacheable content block ahead of these instructions,
    identical across every characteristic, instead of being embedded in the middle of 28
    different prompts where nothing could be cached.
    """
    cached = _INSTRUCTION_CACHE.get(characteristic)
    if cached is not None:
        return cached

    try:
        template = PROMPT_TEMPLATES[characteristic]
    except KeyError as error:
        raise ValueError(f"Unsupported characteristic: {characteristic}") from error

    rendered = template.render(document_text=_DOCUMENT_SENTINEL).replace(
        _DOCUMENT_SENTINEL, _DOCUMENT_BACKREFERENCE
    )
    _INSTRUCTION_CACHE[characteristic] = rendered
    return rendered


def document_block(document_text: str) -> dict:
    """The document as a cacheable content block.

    Every characteristic in a batch sends this identical block, so Claude's prompt cache
    serves it after the first call instead of re-reading the whole document each time.
    """
    return {
        "type": "text",
        "text": f"Document Text:\n{document_text}",
        "cache_control": {"type": "ephemeral"},
    }


def process_prompt(
    document_file,
    characteristic: CharacteristicClass,
    return_clean_answer: bool = False,
    document_text: str | None = None,
) -> Union[str, tuple[str, str]]:
    """Process and generate a classification prompt using the selected template."""
    if document_text is None:
        document_text = extract_pdf_text(document_file)

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    document_block(document_text),
                    {"type": "text", "text": build_instructions(characteristic)},
                ],
            }
        ],
    )

    full_response = "".join(block.text for block in response.content if block.type == "text")

    if return_clean_answer:
        clean_answer = extract_clean_answer(full_response)
        return full_response, clean_answer

    return full_response


def generate_prompt_handler(
    document_file,
    current_characteristic: str | None,
    pdf_path_state: str | None,
):
    """Main handler for prompt generation with supporting evidence."""
    
    if document_file is None:
        return (
            "<div class='result-wrap'><div class='result-pill'>upload a pdf</div></div>",
            "Upload a PDF to view supporting snippets.",
            gr.update(value="<p>No PDF uploaded.</p>"),
            gr.update(choices=[], value=None),
            pdf_path_state,
        )
    
    # Parse the PDF once, then run the classification and the evidence lookup
    # concurrently — they are independent calls that used to run back to back.
    pages = extract_pdf_pages(document_file)
    document_text = "\n".join(page["text"] for page in pages)

    with ThreadPoolExecutor(max_workers=2) as executor:
        classification_future = executor.submit(
            process_prompt,
            document_file=document_file,
            characteristic=current_characteristic,
            document_text=document_text,
        )
        evidence_future = (
            executor.submit(
                get_supporting_evidence,
                document_file,
                current_characteristic,
                pages=pages,
            )
            if current_characteristic
            else None
        )
        full_prompt = classification_future.result()
        evidence = evidence_future.result() if evidence_future else []

    clean_answer = extract_clean_answer(full_prompt) if isinstance(full_prompt, str) else ""

    jump_choices = []
    for item in evidence:
        page = item.get("page")
        quote = item.get("quote")
        if page and quote:
            label = f"Page {page}: {quote[:80]}{'…' if len(quote) > 80 else ''}"
            jump_choices.append((label, page))
    
    evidence_md = ""
    if evidence:
        lines = []
        for idx, item in enumerate(evidence, start=1):
            page = item.get("page")
            quote = item.get("quote")
            reason = item.get("reason")
            lines.append(f"**{idx}. Page {page}** – {reason or 'Supporting excerpt'}\n> {quote}")
        evidence_md = "\n\n".join(lines)
    else:
        evidence_md = "No explicit supporting snippets were extracted for this run."
    
    # The viewer only needs the file path; Gradio serves the bytes itself.
    if not pdf_path_state:
        pdf_path_state = resolve_file_path(document_file)
    viewer_html = render_pdf_embed(pdf_path_state, page=jump_choices[0][1] if jump_choices else 1)
    
    characteristic_summary = CHARACTERISTIC_DEFINITIONS.get(current_characteristic, None)
    summary_text = characteristic_summary.info if characteristic_summary else ""
    title_text = f"{current_characteristic} - {summary_text}" if summary_text else current_characteristic or "Classification"
    display_text = (
        "<div class='result-card'>"
        f"<div class='result-title'>{title_text}</div>"
        f"<div class='result-main'>{clean_answer or 'n/a'}</div>"
        "</div>"
    )
    return (
        display_text,
        evidence_md,
        viewer_html,
        jump_choices,
        pdf_path_state,
    )


def process_selected_classifications(
    document_file,
    selected_characteristics: list[CharacteristicClass],
    on_progress=None,
):
    """Process selected classifications for a document and return results as a dictionary.

    Each characteristic is an independent, network-bound model call, so they are issued
    concurrently instead of one after another.
    """
    if document_file is None:
        return None, "Error: Please upload a document."

    if not selected_characteristics:
        return None, "Error: Please select at least one characteristic to process."

    # Parse the PDF exactly once and share the text with every worker.
    document_text = extract_pdf_text(document_file)

    def classify(characteristic: CharacteristicClass) -> dict:
        try:
            full_response, clean_answer = process_prompt(
                document_file=document_file,
                characteristic=characteristic,
                return_clean_answer=True,
                document_text=document_text,
            )
            return {
                "clean_answer": clean_answer,
                "full_response": full_response,
                "summary": extract_rationale(full_response),
                "info": CHARACTERISTIC_DEFINITIONS[characteristic].info
            }
        except Exception as e:
            # Flagged rather than string-sniffed downstream: a failed call must never be
            # written to the spreadsheet, where it would overwrite a good prior value.
            return {
                "failed": True,
                "clean_answer": f"Error: {str(e)}",
                "full_response": f"Error: {str(e)}",
                "summary": f"Error: {str(e)}",
                "info": CHARACTERISTIC_DEFINITIONS[characteristic].info
            }

    results = {}
    total = len(selected_characteristics)
    done = 0
    remaining = list(selected_characteristics)

    # The prompt cache is written by the first call that sees the document. Running one
    # characteristic on its own first means the other 27 read from cache instead of all
    # missing simultaneously — costs one call's latency, saves ~90% on their input tokens.
    if WARM_PROMPT_CACHE and total > 1:
        first = remaining.pop(0)
        results[first] = classify(first)
        done += 1
        if on_progress:
            on_progress(done, total, first)

    if remaining:
        workers = max(1, min(len(remaining), MAX_PARALLEL_CLASSIFICATIONS))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(classify, characteristic): characteristic
                for characteristic in remaining
            }
            for future in as_completed(futures):
                characteristic = futures[future]
                results[characteristic] = future.result()
                done += 1
                if on_progress:
                    on_progress(done, total, characteristic)

    # Preserve the caller's ordering regardless of completion order.
    ordered = {c: results[c] for c in selected_characteristics if c in results}
    return ordered, None


def save_to_spreadsheet(results: dict, document_name: str, filename: str = None, append_mode: bool = False) -> str:
    """Save classification results to an Excel spreadsheet with document name column.
    
    If document name already exists, updates that row with new characteristics.
    If document name is new, creates a new row.
    """
    if not results:
        return None

    # Never persist a failed call. An API outage mid-batch used to write
    # "Error: ..." into every selected column, destroying classifications that a
    # previous successful run had already stored for this document.
    results = {c: r for c, r in results.items() if not r.get("failed")}
    if not results:
        return None

    # Sort results by characteristic code to maintain consistent column order
    sorted_chars = sort_characteristics(list(results.keys()))

    # Generate filename if not provided
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"classification_results_{timestamp}.xlsx"
    
    # Ensure output directory exists
    output_dir = Path(__file__).resolve().parent / "outputs"
    output_dir.mkdir(exist_ok=True)
    
    filepath = output_dir / filename
    
    # If append mode and file exists, merge with existing data
    if append_mode and filepath.exists():
        try:
            existing_df = pd.read_excel(filepath, engine='openpyxl')
            
            # Ensure "Document Name" column exists
            if "Document Name" not in existing_df.columns:
                # If no Document Name column, add it as first column
                existing_df.insert(0, "Document Name", None)
            
            # Find row with matching document name (case-insensitive comparison)
            matching_row_idx = None
            if "Document Name" in existing_df.columns:
                # Convert to string and strip whitespace for comparison
                existing_df["Document Name"] = existing_df["Document Name"].astype(str).str.strip()
                matching_rows = existing_df[existing_df["Document Name"].str.lower() == document_name.lower().strip()]
                if len(matching_rows) > 0:
                    matching_row_idx = matching_rows.index[0]
            
            if matching_row_idx is not None:
                # Document name exists - UPDATE existing row (don't create new row)
                # Add any new characteristic columns (and their summary columns) that don't exist yet
                for char in sorted_chars:
                    if char in results and char not in existing_df.columns:
                        existing_df[char] = None
                    summary_name = summary_column_name(char)
                    if char in results and summary_name not in existing_df.columns:
                        existing_df[summary_name] = None
                
                # Update the existing row with new characteristics and summaries.
                # Labels are always text, but pandas may have inferred a numeric dtype for a
                # column whose values happened to look numeric (meta_anio -> int64). Writing a
                # string back into it is deprecated and raises on newer pandas, so widen the
                # column to object first.
                for char in sorted_chars:
                    if char in results:
                        for col in (char, summary_column_name(char)):
                            if existing_df[col].dtype != object:
                                existing_df[col] = existing_df[col].astype(object)
                        existing_df.loc[matching_row_idx, char] = results[char]["clean_answer"]
                        existing_df.loc[matching_row_idx, summary_column_name(char)] = results[char].get("summary")
                
                df = existing_df
            else:
                # Document name NOT found - CREATE new row
                # First, ensure all characteristic and summary columns exist in existing_df
                for char in sorted_chars:
                    if char not in existing_df.columns:
                        existing_df[char] = None
                    summary_name = summary_column_name(char)
                    if summary_name not in existing_df.columns:
                        existing_df[summary_name] = None
                
                # Create new row with Document Name and all characteristics
                new_row = {"Document Name": document_name}
                # Add all existing columns (preserve existing characteristics)
                for col in existing_df.columns:
                    if col == "Document Name":
                        continue
                    elif col in results:
                        new_row[col] = results[col]["clean_answer"]
                    elif any(col == summary_column_name(c) and c in results for c in sorted_chars):
                        # Summary columns
                        base_char = col.replace("_summary", "")
                        if base_char in results:
                            new_row[col] = results[base_char].get("summary")
                    else:
                        new_row[col] = None
                
                # Add new row to dataframe
                new_row_df = pd.DataFrame([new_row])
                df = pd.concat([existing_df, new_row_df], ignore_index=True)
        except Exception as e:
            # If reading fails, create new dataframe
            data = {"Document Name": [document_name]}
            for char in sorted_chars:
                if char in results:
                    data[char] = [results[char]["clean_answer"]]
                    data[summary_column_name(char)] = [results[char].get("summary")]
            df = pd.DataFrame(data)
    else:
        # Create new dataframe
        data = {"Document Name": [document_name]}
        for char in sorted_chars:
            if char in results:
                data[char] = [results[char]["clean_answer"]]
                data[summary_column_name(char)] = [results[char].get("summary")]
        df = pd.DataFrame(data)
    
    # Document Name first, then metadata, then characteristics interleaved with summaries
    df = reorder_columns_with_summaries(df)

    # Save to Excel
    df.to_excel(filepath, index=False, engine='openpyxl')
    
    return str(filepath)


def results_table(results: dict | None) -> pd.DataFrame:
    """Render a finished run as a readable table so results are visible without Excel."""
    if not results:
        return pd.DataFrame(columns=["Characteristic", "Result"])
    return pd.DataFrame(
        [
            {
                "Characteristic": f"{char} — {info['info']}",
                "Result": info["clean_answer"],
            }
            for char, info in results.items()
        ]
    )


def process_selected_and_export_handler(
    document_file,
    selected_characteristics: list[CharacteristicClass],
    request: gr.Request,
    progress_tracker: gr.Progress = gr.Progress(),
):
    """Process selected classifications and export to spreadsheet."""
    empty = pd.DataFrame(columns=["Characteristic", "Result"])

    if document_file is None:
        return None, "Error: Please upload a document.", empty

    if not selected_characteristics:
        return None, "Error: Please select at least one characteristic to process.", empty
    
    # Get username from request (Gradio auth)
    username = getattr(request, 'username', None) or "guest"
    
    # Get document name and ID
    document_name = Path(resolve_file_path(document_file)).stem if document_file else "document"
    doc_id = get_document_id(document_file)
    if not doc_id:
        return None, "Error: Could not identify document.", empty
    
    # Load user progress
    progress = load_user_progress(username)
    if doc_id not in progress:
        progress[doc_id] = {
            "filename": document_name,
            "completed": {},
            "spreadsheet": None
        }
    
    # Process selected classifications
    def report(done: int, total: int, characteristic: str):
        if progress_tracker is not None:
            progress_tracker(
                done / total,
                desc=f"{done}/{total} classified (latest: {characteristic})",
            )

    results, _ = process_selected_classifications(
        document_file, selected_characteristics, on_progress=report
    )

    if results is None:
        return None, "Error: Failed to process classifications.", empty
    
    # Update progress (successes only, so a failed run does not mark work as done
    # and does not grey the characteristic out on the next visit)
    for char in selected_characteristics:
        if char in results and not results[char].get("failed"):
            progress[doc_id]["completed"][char] = {
                "clean_answer": results[char]["clean_answer"],
                "timestamp": datetime.now().isoformat()
            }
    
    # Use a single spreadsheet per user (all documents go to same file)
    # Filename based on username
    username_safe = "".join(c for c in username if c.isalnum() or c in (' ', '-', '_')).strip()
    output_filename = f"{username_safe}_classifications.xlsx" if username_safe else "classifications.xlsx"
    append_mode = True  # Always append mode since we're using one file per user
    
    # Save to spreadsheet (append/update based on document name)
    try:
        filepath = save_to_spreadsheet(results, document_name, output_filename, append_mode=append_mode)
        progress[doc_id]["spreadsheet"] = filepath
        save_user_progress(username, progress)
        
        completed_count = len(progress[doc_id]["completed"])
        failed = [c for c in results if results[c].get("failed")]
        succeeded = len(selected_characteristics) - len(failed)
        if succeeded:
            headline = f"✓ {succeeded} of {len(selected_characteristics)} classification(s) completed"
        else:
            headline = f"✗ all {len(selected_characteristics)} classification(s) failed — nothing was written"
        if failed:
            headline += f" — {len(failed)} failed: {', '.join(failed)}"
            # Surface the underlying cause once (an expired key or spent balance shows
            # up as 28 identical errors, and the reason is what the user has to act on).
            reason = str(results[failed[0]]["clean_answer"]).removeprefix("Error: ")
            headline += f"\n  reason: {reason[:300]}"
        location = f"Saved to: {filepath}" if filepath else (
            "Nothing saved — existing values for this document were left untouched."
        )
        success_message = (
            f"{headline}\n\n"
            f"Document: {document_name}\n"
            f"Total completed for this document: {completed_count}\n\n"
            f"{location}"
        )
        return filepath, success_message, results_table(results)
    except Exception as e:
        return None, f"Error saving spreadsheet: {str(e)}", results_table(results)


def get_completed_characteristics(username: str, document_file) -> list[CharacteristicClass]:
    """Get list of already completed characteristics for a document."""
    if document_file is None:
        return []
    
    doc_id = get_document_id(document_file)
    if not doc_id:
        return []
    
    progress = load_user_progress(username)
    if doc_id in progress:
        return list(progress[doc_id]["completed"].keys())
    return []


def reset_spreadsheet(request: gr.Request) -> tuple:
    """Reset/clear the Excel spreadsheet for the current user."""
    # Get username from request
    username = "guest"
    if request:
        try:
            username = getattr(request, 'username', None) or "guest"
        except Exception:
            username = "guest"
    
    # Generate the same filename that would be used for saving
    username_safe = "".join(c for c in username if c.isalnum() or c in (' ', '-', '_')).strip()
    output_filename = f"{username_safe}_classifications.xlsx" if username_safe else "classifications.xlsx"
    
    # Get the filepath
    output_dir = Path(__file__).resolve().parent / "outputs"
    filepath = output_dir / output_filename
    
    # Delete the Excel file if it exists
    if filepath.exists():
        try:
            filepath.unlink()
            # Also clear user progress
            progress_file = get_user_progress_file(username)
            if progress_file.exists():
                progress_file.unlink()
            return None, f"✓ Spreadsheet reset successfully! File '{output_filename}' has been deleted.\n\nAll progress has been cleared."
        except Exception as e:
            return None, f"Error resetting spreadsheet: {str(e)}"
    else:
        return None, f"Spreadsheet '{output_filename}' does not exist. Nothing to reset."


def load_spreadsheet_for_editor(request: gr.Request):
    """Load the current user's spreadsheet into an editable DataFrame."""
    username = "guest"
    if request:
        try:
            username = getattr(request, 'username', None) or "guest"
        except Exception:
            username = "guest"
    
    username_safe = "".join(c for c in username if c.isalnum() or c in (' ', '-', '_')).strip()
    output_filename = f"{username_safe}_classifications.xlsx" if username_safe else "classifications.xlsx"
    output_dir = Path(__file__).resolve().parent / "outputs"
    filepath = output_dir / output_filename
    
    if filepath.exists():
        try:
            df = pd.read_excel(filepath, engine='openpyxl')
            df = reorder_columns_with_summaries(df)
            status = f"Loaded existing spreadsheet: {filepath}"
            return df, str(filepath), status
        except Exception as e:
            # If something goes wrong, start with an empty frame
            df = pd.DataFrame({"Document Name": []})
            status = f"Error loading existing spreadsheet: {e}. Started with an empty sheet."
            return df, None, status
    else:
        df = pd.DataFrame({"Document Name": []})
        status = "No existing spreadsheet found for this user. Started with an empty sheet."
        return df, None, status


def save_spreadsheet_from_editor(df, request: gr.Request):
    """Save the edited DataFrame back to the user's spreadsheet file."""
    # Normalize input to DataFrame in case Gradio passes list-of-lists
    if df is None:
        return pd.DataFrame({"Document Name": []}), None, "No data to save."
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    
    username = "guest"
    if request:
        try:
            username = getattr(request, 'username', None) or "guest"
        except Exception:
            username = "guest"
    
    username_safe = "".join(c for c in username if c.isalnum() or c in (' ', '-', '_')).strip()
    output_filename = f"{username_safe}_classifications.xlsx" if username_safe else "classifications.xlsx"
    output_dir = Path(__file__).resolve().parent / "outputs"
    output_dir.mkdir(exist_ok=True)
    filepath = output_dir / output_filename
    
    try:
        df = reorder_columns_with_summaries(df)
        df.to_excel(filepath, index=False, engine='openpyxl')
        status = f"✓ Spreadsheet saved successfully to: {filepath}"
        return df, str(filepath), status
    except Exception as e:
        status = f"Error saving spreadsheet: {e}"
        return df, None, status

def update_checkbox_states(document_file, request: gr.Request):
    """Update checkbox states based on completed characteristics."""
    username = "guest"
    if request:
        try:
            username = getattr(request, 'username', None) or "guest"
        except Exception:
            username = "guest"
    
    completed = get_completed_characteristics(username, document_file)
    
    # Return the completed list for each checkbox group
    return (
        [char for char in completed if char.startswith('a1')],
        [char for char in completed if char.startswith('a2')],
        [char for char in completed if char.startswith('a3')],
        [char for char in completed if char.startswith('b')],
        [char for char in completed if char.startswith('c')],
        [char for char in completed if char.startswith('meta_')],
        [char for char in completed if char == 'genero'],
    )


def sheet_summary(df) -> str:
    """A one-line read on what the spreadsheet actually contains.

    The grid alone does not answer "did my batch land?" without scrolling sideways
    past 50-odd columns, so state the document count and the fill rate up front.
    """
    if df is None or len(df) == 0:
        return "**Empty sheet** — run a batch classification to populate it."
    char_cols = [c for c in df.columns if c in CHARACTERISTIC_DEFINITIONS]
    filled = int(df[char_cols].notna().sum().sum()) if char_cols else 0
    total = len(df) * len(char_cols)
    pct = f"{filled / total * 100:.0f}%" if total else "0%"
    docs = ""
    if "Document Name" in df.columns:
        names = [str(d) for d in df["Document Name"].tolist()]
        docs = "\n\nDocuments: " + ", ".join(f"`{n}`" for n in names[:8])
        if len(names) > 8:
            docs += f" +{len(names) - 8} more"
    return (
        f"**{len(df)} document(s)** · {len(char_cols)} characteristic columns · "
        f"{filled}/{total} classified ({pct}){docs}"
    )


# Gradio Interface
# Simple authentication - users can create accounts by just logging in
# For production, you'd want to use a proper auth system
with gr.Blocks(
    title="AI Taxonomy",
) as demo:
    pdf_path_state = gr.State("")
    evidence_md_state = gr.State("Run a classification to see supporting quotes.")
    viewer_html_state = gr.State("<p>No PDF uploaded yet.</p>")
    evidence_choices_state = gr.State([])
    # Inject custom CSS for styling
    gr.HTML(f"<style>{APP_CSS}</style>")
    gr.Markdown("## AI Taxonomy", elem_id="app-title")
    
    current_doc_banner = gr.Markdown(
        "**No document loaded** — upload a PDF above to begin.",
        elem_id="current-doc",
    )
    
    with gr.Row(elem_classes=["app-row-equal"]):
        with gr.Column(scale=2, elem_classes=["app-panel"]):
            gr.Markdown("### 1. Upload legal document (PDF)", elem_classes=["app-section-label"])
            document_input = gr.File(
                label="Upload Legal Document (PDF)",
                file_types=[".pdf"]
            )
            gr.Markdown(
                "Upload the full text of the bill, law or proposal in **PDF** format. "
                "All classifications (single and batch) will use this same document.",
            )
        with gr.Column(scale=1, elem_classes=["app-panel"]):
            gr.Markdown("### Tips", elem_classes=["app-section-label"])
            gr.Markdown(
                "- Use batch mode to fill many taxonomy fields in one go.\n"
                "- You can re-run classifications on the same document; results will be merged in the spreadsheet.\n"
                "- The *Project Identification* group helps you auto-fill metadata (country, title, year, etc.)."
            )
    
    with gr.Tabs():
        with gr.Tab("Single Classification"):
            with gr.Column(elem_classes=["app-panel"]):
                gr.Markdown("### 2. Choose a single characteristic", elem_classes=["app-section-label"])
                with gr.Row():
                    with gr.Column(scale=2):
                        characteristic_dropdown = gr.Dropdown(
                            choices=SORTED_CHARACTERISTICS,
                            value=DEFAULT_CHARACTERISTIC,
                            label="Classification Characteristic",
                            info="Select one taxonomy characteristic to classify.",
                            interactive=True,
                        )
                    with gr.Column(scale=1):
                        prompt_output = gr.HTML(
                            value=(
                                "<div class='result-card'>"
                                "<div class='result-title'>Waiting</div>"
                                "<div class='result-sub'>Run a classification to see a summary.</div>"
                                "<div class='result-main'>—</div>"
                                "</div>"
                            ),
                            show_label=False,
                        )
                with gr.Row(elem_classes=["cta-row"]):
                    generate_button = gr.Button("Generate Classification", variant="primary", elem_classes=["primary-cta"])
            
            with gr.Column(elem_classes=["app-panel"]):
                gr.Markdown("### Evidence & source pages", elem_classes=["app-section-label"])
                evidence_notes = gr.Markdown("Run a classification to see supporting quotes.")
                evidence_jump = gr.Dropdown(
                    label="Jump to supporting page",
                    choices=[],
                    interactive=True,
                )
                pdf_viewer = gr.HTML(
                    label="Document Preview",
                    value="<p>No PDF uploaded yet.</p>",
                )
        with gr.Tab("Batch Classification & Export"):
            # Section 2 full width
            with gr.Column(elem_classes=["app-panel"]):
                gr.Markdown("### 2. Select characteristics to process", elem_classes=["app-section-label"])
                progress_info = gr.Markdown("", visible=True)
                
                # Create checkboxes organized by dimension
                with gr.Accordion("Dimension A: Characteristics of Regulation", open=True):
                    a1_group = gr.CheckboxGroup(
                        choices=[(f"{char} - {CHARACTERISTIC_DEFINITIONS[char].info}", char) 
                                for char in SORTED_CHARACTERISTICS if char.startswith('a1')],
                        label="A1 - Scope",
                        value=[],
                    )
                    a2_group = gr.CheckboxGroup(
                        choices=[(f"{char} - {CHARACTERISTIC_DEFINITIONS[char].info}", char) 
                                for char in SORTED_CHARACTERISTICS if char.startswith('a2')],
                        label="A2 - Regulatory Approach",
                        value=[],
                    )
                    a3_group = gr.CheckboxGroup(
                        choices=[(f"{char} - {CHARACTERISTIC_DEFINITIONS[char].info}", char) 
                                for char in SORTED_CHARACTERISTICS if char.startswith('a3')],
                        label="A3 - Actors",
                        value=[],
                    )
                
                with gr.Accordion("Dimension B: Enabling Factors", open=False):
                    b_group = gr.CheckboxGroup(
                        choices=[(f"{char} - {CHARACTERISTIC_DEFINITIONS[char].info}", char) 
                                for char in SORTED_CHARACTERISTICS if char.startswith('b')],
                        label="B - Enabling Factors",
                        value=[],
                    )
                
                with gr.Accordion("Dimension C: Impacts", open=False):
                    c_group = gr.CheckboxGroup(
                        choices=[(f"{char} - {CHARACTERISTIC_DEFINITIONS[char].info}", char) 
                                for char in SORTED_CHARACTERISTICS if char.startswith('c')],
                        label="C - Impacts",
                        value=[],
                    )
                
                with gr.Accordion("Project Identification (metadata)", open=False):
                    meta_group = gr.CheckboxGroup(
                        choices=[(f"{char} - {CHARACTERISTIC_DEFINITIONS[char].info}", char)
                                 for char in SORTED_CHARACTERISTICS if char.startswith('meta_')],
                        label="Identificación del proyecto",
                        value=[],
                    )
                
                with gr.Accordion("Additional", open=False):
                    genero_group = gr.CheckboxGroup(
                        choices=[(f"{char} - {CHARACTERISTIC_DEFINITIONS[char].info}", char) 
                                for char in SORTED_CHARACTERISTICS if char == 'genero'],
                        label="Gender",
                        value=[],
                    )
                
                with gr.Row():
                    select_all_btn = gr.Button("Select All", variant="secondary", size="sm")
                    deselect_all_btn = gr.Button("Deselect All", variant="secondary", size="sm")
                
                process_selected_button = gr.Button("Process Selected & Export to Spreadsheet", variant="primary")

            # Section 3 below
            with gr.Column(elem_classes=["app-panel"]):
                gr.Markdown("### 3. Review status & download results", elem_classes=["app-section-label"])
                export_status = gr.Textbox(
                    label="Batch Processing Status",
                    lines=6,
                )
                batch_results_df = gr.Dataframe(
                    headers=["Characteristic", "Result"],
                    label="Results from this run",
                    interactive=False,
                    wrap=True,
                    max_height=340,
                )
                spreadsheet_output = gr.File(
                    label="Download Spreadsheet",
                    visible=True,
                )
                gr.Markdown(
                    "_To view, edit or clear the whole spreadsheet, use the "
                    "**Spreadsheet Editor** tab._",
                    elem_classes=["hint-text"],
                )
        with gr.Tab("Spreadsheet Editor") as editor_tab:
            with gr.Column(scale=1, elem_classes=["app-panel"]):
                gr.Markdown("### Full-screen spreadsheet view & edit", elem_classes=["app-section-label"])
                gr.Markdown(
                    "One row per document. Each characteristic has a `<code>` column with the "
                    "label and a `<code>_summary` column with the reasoning."
                )
                editor_summary = gr.Markdown(
                    "**Empty sheet** — run a batch classification to populate it.",
                    elem_classes=["sheet-summary"],
                )
                spreadsheet_full_df = gr.Dataframe(
                    label="Editable classifications spreadsheet (full width)",
                    interactive=True,
                    wrap=True,
                    max_height=460,
                )
                with gr.Row():
                    load_sheet_full_btn = gr.Button("Reload from disk", variant="secondary")
                    save_sheet_full_btn = gr.Button("Save changes", variant="primary", elem_classes=["primary-cta"])
                editor_status = gr.Markdown("")
                editor_full_download = gr.File(
                    label="Download edited spreadsheet",
                    visible=True,
                )
                with gr.Accordion("What do the column codes mean?", open=False):
                    gr.Dataframe(
                        value=pd.DataFrame(
                            [
                                {"Column": c, "Meaning": CHARACTERISTIC_DEFINITIONS[c].info}
                                for c in SORTED_CHARACTERISTICS
                            ]
                        ),
                        interactive=False,
                        wrap=True,
                        max_height=320,
                    )

                # Reset lives here rather than beside the batch Download button: it
                # deletes the spreadsheet and every recorded classification, so it
                # belongs with the sheet it destroys, behind a deliberate confirmation.
                with gr.Accordion("Danger zone", open=False, elem_classes=["danger-zone"]):
                    gr.Markdown(
                        "**Reset deletes the entire spreadsheet and all recorded progress "
                        "for your user.** Every document and every classification is removed. "
                        "This cannot be undone — download a copy first if you want one."
                    )
                    reset_confirm = gr.Checkbox(
                        label="Yes, permanently delete the spreadsheet and all progress",
                        value=False,
                    )
                    reset_spreadsheet_btn = gr.Button("Reset spreadsheet", variant="stop")
    
    
    def combine_selected_characteristics(a1_vals, a2_vals, a3_vals, b_vals, c_vals, meta_vals, genero_vals):
        """Combine all selected checkboxes into a single list."""
        all_selected = []
        if a1_vals:
            all_selected.extend(a1_vals)
        if a2_vals:
            all_selected.extend(a2_vals)
        if a3_vals:
            all_selected.extend(a3_vals)
        if b_vals:
            all_selected.extend(b_vals)
        if c_vals:
            all_selected.extend(c_vals)
        if meta_vals:
            all_selected.extend(meta_vals)
        if genero_vals:
            all_selected.extend(genero_vals)
        return all_selected
    
    
    def select_all_checkboxes():
        """Select all checkboxes."""
        return (
            [char for char in SORTED_CHARACTERISTICS if char.startswith('a1')],
            [char for char in SORTED_CHARACTERISTICS if char.startswith('a2')],
            [char for char in SORTED_CHARACTERISTICS if char.startswith('a3')],
            [char for char in SORTED_CHARACTERISTICS if char.startswith('b')],
            [char for char in SORTED_CHARACTERISTICS if char.startswith('c')],
             [char for char in SORTED_CHARACTERISTICS if char.startswith('meta_')],
            [char for char in SORTED_CHARACTERISTICS if char == 'genero'],
        )
    
    def deselect_all_checkboxes():
        """Deselect all checkboxes."""
        return ([], [], [], [], [], [], [])
    
    # Event handlers
    def update_pdf_viewer(document_file):
        """Display uploaded PDF in the preview panel and remember its path."""
        pdf_path = resolve_file_path(document_file)
        if not pdf_path:
            return gr.update(value="<p>No PDF uploaded.</p>"), ""
        return gr.update(value=render_pdf_embed(pdf_path, page=1)), pdf_path

    def set_pdf_page(selected_page, pdf_path):
        """Update PDF viewer to a selected page."""
        if not pdf_path:
            return gr.update(value="<p>No PDF uploaded.</p>")
        page = selected_page if selected_page else 1
        return gr.update(value=render_pdf_embed(pdf_path, page=page))

    def render_evidence_from_state(md, html, choices):
        """Render evidence outputs without showing progress."""
        if choices and isinstance(choices[0], (list, tuple)) and len(choices[0]) >= 2:
            value = choices[0][1]
        elif choices:
            value = choices[0]
        else:
            value = None
        return (
            gr.update(value=md),
            gr.update(value=html),
            gr.update(choices=choices, value=value),
        )
    
    document_input.upload(
        update_pdf_viewer,
        inputs=[document_input],
        outputs=[pdf_viewer, pdf_path_state],
        show_progress=False,
    )
    
    generate_button.click(
        generate_prompt_handler,
        inputs=[
            document_input,
            characteristic_dropdown,
            pdf_path_state,
        ],
        outputs=[prompt_output, evidence_md_state, viewer_html_state, evidence_choices_state, pdf_path_state],
        show_progress=True,
    ).then(
        render_evidence_from_state,
        inputs=[evidence_md_state, viewer_html_state, evidence_choices_state],
        outputs=[evidence_notes, pdf_viewer, evidence_jump],
        show_progress=False,
    )

    evidence_jump.change(
        set_pdf_page,
        inputs=[evidence_jump, pdf_path_state],
        outputs=[pdf_viewer],
        show_progress=False,
    )
    
    def process_with_combined_selection_wrapper(document_file, a1_vals, a2_vals, a3_vals, b_vals, c_vals, meta_vals, genero_vals, request: gr.Request):
        """Wrapper to combine checkbox selections and pass request."""
        selected = combine_selected_characteristics(a1_vals, a2_vals, a3_vals, b_vals, c_vals, meta_vals, genero_vals)
        return process_selected_and_export_handler(document_file, selected, request)
    
    def show_progress_on_upload(document_file, request: gr.Request):
        """Show progress when a document is uploaded."""
        if document_file is None:
            return gr.update(visible=False, value="")
        
        username = "guest"
        if request:
            try:
                username = getattr(request, 'username', None) or "guest"
            except Exception:
                username = "guest"
        
        completed = get_completed_characteristics(username, document_file)
        
        if completed:
            completed_list = ", ".join(completed)
            return gr.update(
                visible=True,
                value=f"**Progress:** {len(completed)} characteristic(s) already completed for this document:\n{completed_list}"
            )
        return gr.update(visible=True, value="**No previous progress** - Start classifying!")
    
    def load_editor(request: gr.Request):
        df, path, status = load_spreadsheet_for_editor(request)
        return df, path, status, sheet_summary(df)

    def save_editor(df, request: gr.Request):
        out, path, status = save_spreadsheet_from_editor(df, request)
        return out, path, status, sheet_summary(out)

    EDITOR_OUTPUTS = [spreadsheet_full_df, editor_full_download, editor_status, editor_summary]

    process_selected_button.click(
        process_with_combined_selection_wrapper,
        inputs=[
            document_input,
            a1_group,
            a2_group,
            a3_group,
            b_group,
            c_group,
            meta_group,
            genero_group,
        ],
        outputs=[spreadsheet_output, export_status, batch_results_df],
    ).then(
        # The batch writes the same file the editor shows; refresh it in the same
        # chain so the grid can never lag behind what was just saved.
        load_editor,
        inputs=[],
        outputs=EDITOR_OUTPUTS,
    )
    
    # Update checkboxes when document is uploaded to show completed ones
    document_input.upload(
        update_checkbox_states,
        inputs=[document_input],
        outputs=[a1_group, a2_group, a3_group, b_group, c_group, meta_group, genero_group],
    )
    
    # Show progress info when document is uploaded
    document_input.upload(
        show_progress_on_upload,
        inputs=[document_input],
        outputs=[progress_info],
    )

    def describe_current_document(document_file):
        """Name the document every tab is operating on."""
        path = resolve_file_path(document_file)
        if not path:
            return "**No document loaded** — upload a PDF above to begin."
        pages = len(extract_pdf_pages(document_file))
        return f"**Current document:** `{Path(path).name}` ({pages} pages) — all tabs classify this file."

    document_input.upload(
        describe_current_document,
        inputs=[document_input],
        outputs=[current_doc_banner],
    )
    document_input.clear(
        lambda: "**No document loaded** — upload a PDF above to begin.",
        outputs=[current_doc_banner],
    )
    
    select_all_btn.click(
        select_all_checkboxes,
        outputs=[a1_group, a2_group, a3_group, b_group, c_group, meta_group, genero_group],
    )
    
    deselect_all_btn.click(
        deselect_all_checkboxes,
        outputs=[a1_group, a2_group, a3_group, b_group, c_group, meta_group, genero_group],
    )
    
    # Full-screen spreadsheet editor buttons
    load_sheet_full_btn.click(load_editor, inputs=[], outputs=EDITOR_OUTPUTS)

    # Opening the tab used to show an empty grid until you found the Load button.
    editor_tab.select(load_editor, inputs=[], outputs=EDITOR_OUTPUTS)

    save_sheet_full_btn.click(
        save_editor,
        inputs=[spreadsheet_full_df],
        outputs=EDITOR_OUTPUTS,
    )

    def reset_editor(confirmed, request: gr.Request):
        """Delete the sheet only on an explicit confirmation, and clear both tabs."""
        blank_results = pd.DataFrame(columns=["Characteristic", "Result"])
        if not confirmed:
            df, path, _ = load_spreadsheet_for_editor(request)
            return (
                df, path,
                "Nothing was deleted — tick **Yes, permanently delete…** first.",
                sheet_summary(df), gr.update(value=False),
                gr.update(), gr.update(),
            )
        _, message = reset_spreadsheet(request)
        empty = pd.DataFrame({"Document Name": []})
        return (
            empty, None, message, sheet_summary(empty), gr.update(value=False),
            None, blank_results,
        )

    reset_spreadsheet_btn.click(
        reset_editor,
        inputs=[reset_confirm],
        outputs=EDITOR_OUTPUTS + [reset_confirm, spreadsheet_output, batch_results_df],
    )

    # Footer
    gr.Markdown("© LATAM Chapter")

# Gradio serialises queued events per-event by default, so one long batch would block
# every other interaction (and every other user). Allow independent events to overlap.
demo.queue(default_concurrency_limit=8, max_size=64)


def build_app() -> FastAPI:
    """Gradio app plus the inline PDF preview route."""
    app = FastAPI()

    @app.get("/pdf/{token}")
    def serve_pdf(token: str):
        with _PDF_TOKENS_LOCK:
            path = _PDF_TOKENS.get(token)
        if not path or not os.path.exists(path):
            raise HTTPException(status_code=404, detail="PDF not found")
        # Inline disposition + range support: the browser renders it in place and can
        # fetch only the pages it needs instead of the whole file.
        return FileResponse(
            path,
            media_type="application/pdf",
            headers={"Content-Disposition": "inline", "Cache-Control": "private, max-age=3600"},
        )

    return gr.mount_gradio_app(app, demo, path="/")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(build_app(), host="127.0.0.1", port=int(os.getenv("PORT", "7860")))