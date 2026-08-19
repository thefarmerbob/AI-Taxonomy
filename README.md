# AI Taxonomy — LATAM Chapter

A tool for classifying Latin American AI laws and bills against a structured
policy taxonomy. Upload a PDF, and Claude fills in a spreadsheet: one row per
document, one column per characteristic — scope, definition of AI, regulatory
approach, enforcement authority, human-rights provisions, and so on.

Built to reproduce the coding in `Taxonomía Proyectos IA. Nov.2024.xlsx`, so
that new bills can be processed in minutes and reviewed by a researcher, rather
than coded from scratch.

> **New here? Read [SETUP.md](SETUP.md).** It walks from zero to a running app
> in about 15 minutes — creating a Claude account, getting an API key, and
> installing everything — assuming no Python or command-line experience.

---

## Quick start

For those already comfortable with Python:

```bash
git clone <repository-url> && cd AI-Taxonomy
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then paste your key into .env
python file_classification.py
```

Open <http://127.0.0.1:7860>.

You need an Anthropic API key with credits on it — get one at
[console.anthropic.com](https://console.anthropic.com). Full instructions in
[SETUP.md](SETUP.md).

---

## What it does

**Single Classification** — pick one characteristic, get a label plus the exact
passages from the PDF that support it, with clickable page jumps into an inline
document preview. Use this to spot-check the model's reasoning.

**Batch Classification & Export** — tick any set of characteristics and run them
all at once. The full taxonomy (27 characteristics) takes about a minute on a
57-page bill. Results are written to `outputs/<username>_classifications.xlsx`.

**Spreadsheet Editor** — view the whole sheet, correct any cell by hand, and
download it. The model's output is a first pass; this is where a researcher
reviews it.

Re-running a document **updates its existing row** rather than appending a
duplicate. Characteristics that fail are never written, so a network blip or a
spent balance can't overwrite work you already have.

---

## The taxonomy

27 characteristics across three dimensions, plus project metadata:

| Group | Covers |
|---|---|
| **A1** | Scope, definition of AI, alignment with OECD/UNESCO/EU definitions, who is subject to the law |
| **A2** | New vs. amending law, punitive vs. advisory approach, specific regulatory procedures |
| **A3** | Supervising authority and its powers, international cooperation, stakeholder participation |
| **B** | Enabling factors — talent development, data access, infrastructure |
| **C** | Impacts — human rights, state modernisation, cybersecurity, productive development, R&D, environment |
| **Género** | Gender considerations |
| **meta_** | Country, title, year, authors, political party |

Each one is defined by a plain-text prompt in `templates/`, containing its
options, decision rules, and worked examples. **To change how a characteristic
is judged, edit its template** — no Python required.

---

## Accuracy

This is an assistant, not an oracle. Benchmarked against the human-coded
reference row for the Chilean bill (`law-files/Ley1.pdf`), about two thirds of
characteristics matched, with most disagreements being polarity flips —
the model reporting "does not promote X" where the researcher recorded
"promotes X".

Plan for researcher review of every document. The evidence snippets and page
jumps exist to make that review fast.

---

## Configuration

All optional, set in `.env`. Defaults are sensible; see `.env.example` for the
full annotated list.

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required)* | Your key from the Anthropic Console |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | Use `claude-opus-5` for the strongest results at higher cost |
| `MAX_PARALLEL_CLASSIFICATIONS` | `8` | Characteristics classified concurrently |
| `ANTHROPIC_TIMEOUT` | `180` | Per-request timeout in seconds |
| `WARM_PROMPT_CACHE` | `1` | Prime the prompt cache before fanning out — saves ~90% of batch input cost |
| `PORT` | `7860` | Local port |

---

## Cost

Roughly **$0.50–0.60** for a full 27-characteristic batch on a 57-page bill,
using the default model. Prompt caching does most of the work here: the document
is sent once and re-read by the other 26 calls at about a tenth of the price.

Track spend in the Anthropic Console under **Usage**. More detail in
[SETUP.md](SETUP.md#what-this-costs).

---

## Repository layout

```
file_classification.py    the entire app — UI, prompts, API calls, spreadsheet I/O
templates/                one prompt per characteristic; edit these to tune classification
law-files/                four sample AI bills (Chile, Brazil, Argentina)
api/index.py              Vercel entrypoint
outputs/                  generated spreadsheets        (git-ignored)
user_progress/            per-document completion state (git-ignored)
.env                      your API key                  (git-ignored)
```

`Taxonomía Proyectos IA. Nov.2024.xlsx` is the human-coded reference this tool
is built to reproduce, and the source of truth for what each characteristic
means.

For architecture and development notes, see [README_DEV.md](README_DEV.md) and
[CLAUDE.md](CLAUDE.md).

---

## Deployment

`vercel.json` and `api/index.py` configure a Vercel deployment. Set
`ANTHROPIC_API_KEY` as an environment variable in the Vercel dashboard — never
commit it.

Note that `outputs/` and `user_progress/` are written to local disk, which is
ephemeral on serverless platforms. Spreadsheets will not persist between
requests on Vercel without swapping in external storage. For real research use,
run it locally.

---

© LATAM Chapter
