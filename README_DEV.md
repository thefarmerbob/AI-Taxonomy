## AI Taxonomy – Developer Guide

Quick pointers for editing templates, prompts, and the app flow.

### Key files
- `file_classification.py`: Main Gradio app, prompt orchestration, PDF parsing, spreadsheet logic.
- `templates/*.jinja`: Prompt templates per characteristic.
- `outputs/`: Generated spreadsheets.
- `user_progress/`: Progress tracking per user/document.
- `api/index.py`: FastAPI + Gradio mount.
- External API: Anthropic Claude (default model `claude-sonnet-5`), keyed by `ANTHROPIC_API_KEY` in `.env`.

### Editing classification templates
1) Open the matching Jinja file in `templates/`. Names align with characteristic codes (e.g., `a1_1_prompt.jinja`).
2) Templates receive `document_text` (full PDF text) as context.
3) Keep the expected structure that the model can parse; avoid heavy formatting.
4) To change wording or add guidance, edit the text directly in the Jinja file.
5) Save and rerun `python file_classification.py`; no rebuild step needed.

### How prompts are built and sent
- `process_prompt(...)` in `file_classification.py`:
  - Reads PDF text via `extract_pdf_text` (cached per file version).
  - Renders the Jinja template for the selected characteristic via `build_instructions`.
  - Sends one user message with two content blocks to the Claude Messages API:
    1. `document_block(...)` — the document, marked `cache_control: ephemeral`.
    2. the rendered instructions.
    The document block is byte-identical across every characteristic, so after the first
    call the whole document is served from Claude's prompt cache. `{{ document_text }}` in
    the template is replaced by a back-reference pointing at that block.
  - Returns `full_response`; `extract_clean_answer` pulls the concise label.
- Single classification calls `generate_prompt_handler`, which runs the classification and
  the evidence lookup concurrently over one shared PDF parse.
- Batch uses `process_selected_classifications`: it classifies the first characteristic on
  its own to populate the prompt cache, then fans the rest out across
  `MAX_PARALLEL_CLASSIFICATIONS` threads. Results are returned in the caller's order.

### Evidence snippets & PDF viewer
- `get_supporting_evidence` reuses the cached per-page text and asks the model for up to 3 quoted snippets with page numbers. Failures are logged to stderr and degrade to no evidence.
- `render_pdf_embed` embeds an iframe pointing at the `/pdf/{token}` route (see `build_app`), which serves the file inline with range support. Tokens are opaque and resolved through `_PDF_TOKENS`, so the route cannot be pointed at arbitrary files.
- Evidence list and page jump are updated after a single classification.

### Spreadsheet logic (batch)
- `process_selected_classifications` returns per characteristic:
  - `clean_answer` (concise label)
  - `full_response` (full model text)
  - `summary` (stored alongside the classification)
- `save_to_spreadsheet` writes/updates `outputs/<user>_classifications.xlsx`.
  - Columns are interleaved: `<char>`, `<char>_summary`.
  - Rows are matched by Document Name (case-insensitive).
- The Spreadsheet Editor tab loads/saves the same file, reordering columns to keep `<char>` next to `<char>_summary`.

### Auth and config
- **There is no login.** Authentication was removed; every visitor is the Gradio
  user `guest` unless the app is mounted behind an external auth layer. The
  per-user spreadsheet name (`outputs/<username>_classifications.xlsx`) still
  derives from `request.username`, which is why files are named `guest_*` by
  default. Do not expose this app on a public address as-is.
- Anthropic key: set `ANTHROPIC_API_KEY` in `.env` (or env var).
- Tuning: `ANTHROPIC_MODEL` (default `claude-sonnet-5`; use `claude-opus-5` for the strongest model), `MAX_PARALLEL_CLASSIFICATIONS` (default 8), `ANTHROPIC_TIMEOUT` (default 180s), `WARM_PROMPT_CACHE` (default on, set `0` to fan out immediately), `PORT` (default 7860).
- API mount: `api/index.py` mounts the Gradio app on FastAPI at `/`. To run as FastAPI: `uvicorn api.index:app --reload` (ensure `file_classification.py` imports resolve).

### Styling
- Custom CSS injected via `APP_CSS` at the top of `file_classification.py`.
- UI layout defined in the Gradio Blocks at the bottom of `file_classification.py`.

### Common tweak points
- Change model: set `ANTHROPIC_MODEL` (both call sites read the `MODEL` constant).
- Adjust max tokens: `max_tokens` in the `client.messages.create` calls.
- Batch throughput vs. rate limits: `MAX_PARALLEL_CLASSIFICATIONS`.
- Modify summary column naming: `summary_column_name` helper.
- Reorder columns: `reorder_columns_with_summaries`.
- Adjust PDF evidence extraction: `get_supporting_evidence` and `extract_pdf_pages`.
- UI tweaks (tabs/labels/components): Gradio Blocks section in `file_classification.py`.

### Running locally
- Create/activate venv: `python3 -m venv venv && source venv/bin/activate`
- Install deps: `pip install -r requirements.txt`
- Run: `python file_classification.py`
- Default port: 7860 (override with `PORT`)
