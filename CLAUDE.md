# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

A Gradio web app that classifies Latin American AI laws (PDFs) against a
27-characteristic policy taxonomy using the Anthropic API, writing results to an
Excel spreadsheet. It reproduces the human coding in
`Taxonomía Proyectos IA. Nov.2024.xlsx`.

Nearly everything lives in **one file**: `file_classification.py` (~1,750
lines). Structure, top to bottom:

1. Config constants and `APP_CSS`
2. User-progress helpers (`user_progress/*.json`, keyed by md5 of username)
3. `CHARACTERISTIC_DEFINITIONS` — the taxonomy registry
4. Response parsing, PDF extraction, spreadsheet column ordering
5. `get_supporting_evidence`, the `/pdf/{token}` viewer plumbing
6. `process_prompt` → `process_selected_classifications` → `save_to_spreadsheet`
7. The Gradio `Blocks` UI and its event wiring
8. `build_app()` — FastAPI wrapper, the Vercel entrypoint

## Running and testing

```bash
source venv/bin/activate
python file_classification.py     # http://127.0.0.1:7860
```

There is **no test suite**. To verify a change end to end, drive the running
server through its HTTP API rather than only importing functions:

```python
from gradio_client import Client, handle_file
c = Client("http://127.0.0.1:7860/", verbose=False)
c.predict(document_file=handle_file("law-files/Ley1.pdf"),
          current_characteristic="a1_1", api_name="/generate_prompt_handler")
```

`curl http://127.0.0.1:7860/gradio_api/info` lists every named endpoint and its
signature — worth checking after touching the UI, since renaming a handler
renames its endpoint.

**Real API calls cost real money and the key is prepaid.** A full
27-characteristic batch on a 57-page bill is roughly $0.50. When testing logic
that doesn't need a real model response, monkeypatch `fc.process_prompt` instead
of burning credits:

```python
fc.process_prompt = lambda **k: (_ for _ in ()).throw(Exception("simulated"))
```

Before testing anything destructive (the reset flow), back up `outputs/` and
`user_progress/` — API calls run as user `guest` and will hit the real
`guest_classifications.xlsx`.

## Conventions that matter

**Characteristics are registered in one place.** To add one, add an entry to
`CHARACTERISTIC_DEFINITIONS` *and* create its `templates/<name>_prompt.jinja`.
A template with no registry entry is silently dead code — `meta_id_proyecto` and
`meta_observaciones` are currently in exactly that state, despite both existing
as columns in the reference spreadsheet.

**Prompt caching is load-bearing.** The document travels as its own
`cache_control: ephemeral` content block that is byte-identical across every
characteristic; `{{ document_text }}` in each template is swapped for a
back-reference by `build_instructions`. Anything that makes that block differ
per characteristic silently triples the cost of a batch. `WARM_PROMPT_CACHE`
runs one call alone first so the other 26 read from cache rather than all
missing at once.

**Model responses are `label\n_____\nrationale`.** `extract_clean_answer` takes
the first line and runs it through `strip_label_decoration`, which removes
markdown the model sometimes adds. That sanitizing is not cosmetic: the label
column is the categorical value the whole spreadsheet is filtered on, so
`# General` and `General` must not become two categories.

**Failed classifications are never persisted.** `classify()` marks failures with
`"failed": True`; `save_to_spreadsheet` filters them out before writing. This
exists because an API outage mid-batch previously overwrote 27 good values with
`Error: ...` strings. Keep that guarantee — a failure must always leave prior
data intact.

**Spreadsheet columns are interleaved** `<char>`, `<char>_summary`, with
`Document Name` first and `meta_*` next; see `reorder_columns_with_summaries`.
Rows are matched by document name, case-insensitively, so re-running a document
updates its row rather than appending.

**Writing to an existing row needs an object dtype.** pandas may infer `int64`
for a column like `meta_anio`; assigning a string into it raises on newer
pandas. `save_to_spreadsheet` widens the column first — don't remove that.

## Gradio specifics

- Chain dependent updates with `.then()`, not a second `.click()` on the same
  component. Two listeners race; the batch handler and the editor refresh must
  stay in one chain or the grid can show pre-run data.
- Handlers annotated `request: gr.Request` get it injected — don't pass it in
  `inputs`.
- `gr.State` outputs are not exposed over the HTTP API, so
  `/generate_prompt_handler` advertises one return value while the browser
  receives five. Not a bug.
- Destructive UI actions belong behind a confirmation. Reset lives in the
  "Danger zone" accordion on the Spreadsheet Editor tab and requires a checkbox.

## Things to leave alone unless asked

- `venv/`, `outputs/`, `user_progress/`, `.env` are git-ignored. Never commit a
  real API key; `.env.example` is the template.
- `opt,py` and `optimization_*.png` are unrelated leftovers from another
  project.
- `GIDE-LATAM-UTDT/` is a separate repository with its own git history.
- The four `law-files/*.pdf` are the sample corpus; `Ley1.pdf` (Chile) is the
  one with a human-coded reference row to check accuracy against.

## Known gaps

- `meta_id_proyecto` and `meta_observaciones` templates exist but are unwired.
- No login: every visitor is `guest`. Don't expose this publicly as-is.
- `outputs/` is local disk, so spreadsheets won't persist on Vercel.
- Accuracy against the human reference is roughly two thirds, with most
  disagreements being polarity flips. Prompt templates are where to improve it.
