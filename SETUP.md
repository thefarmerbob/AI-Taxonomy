# Setup Guide

This guide takes you from nothing to a running app. It assumes **no prior
experience** with Python, the command line, or the Claude API. Follow it top to
bottom and it should take about 15 minutes.

If you get stuck, jump to [Troubleshooting](#troubleshooting) at the bottom —
the errors listed there are the ones people actually hit.

---

## What you are setting up

A small web app that reads a PDF of an AI law or bill and fills in a taxonomy
spreadsheet — one row per document, one column per characteristic (scope,
definition of AI, enforcement approach, and so on). Claude does the reading and
classifying; the app manages the documents, the prompts, and the spreadsheet.

Everything runs **on your own computer**. The only thing that leaves your
machine is the text of the PDF you classify, which is sent to Anthropic's API.

---

## Step 1 — Check you have Python

Open a terminal.

- **macOS**: press `Cmd + Space`, type `Terminal`, press Enter.
- **Windows**: press the Start key, type `PowerShell`, press Enter.
- **Linux**: `Ctrl + Alt + T`.

Type this and press Enter:

```bash
python3 --version
```

You want **3.10 or higher**. If you see something like `Python 3.11.5`, you're
set — skip to Step 2.

If you get "command not found" or a version below 3.10, install Python from
[python.org/downloads](https://www.python.org/downloads/). On Windows, tick
**"Add Python to PATH"** on the first screen of the installer — this is easy to
miss and causes most of the "command not found" problems later.

---

## Step 2 — Get the code

If you were given a link to the repository:

```bash
git clone <the-repository-url>
cd AI-Taxonomy
```

If you were given a zip file, unzip it, then `cd` into the folder. To get the
path right without typing it, type `cd ` (with a trailing space) and then drag
the folder from your file manager into the terminal window.

Confirm you're in the right place — you should see `file_classification.py`
listed:

```bash
ls
```

---

## Step 3 — Create a Claude account

1. Go to **[console.anthropic.com](https://console.anthropic.com)**.
2. Click **Sign up**. Use an email address you can access — you'll need to
   confirm it.
3. Verify your email, then complete the short onboarding (it asks for your name
   and what you're building).

> **Note:** The Claude Console (`console.anthropic.com`) is *not* the same as the
> Claude chat app (`claude.ai`). A claude.ai subscription does **not** give you
> API access, and an API key will not log you into claude.ai. This project needs
> the **Console**. If you already have a claude.ai account, you can sign into the
> Console with the same email, but you will still need to add credits separately.

---

## Step 4 — Add credits

The API is pay-as-you-go and separate from any Claude subscription. A new
account needs credits before it will answer a single request.

1. In the Console, open **Plans & Billing** (left sidebar, or the gear icon).
2. Click **Add credits** and purchase a starting balance. **$10 goes a long
   way** — see [What this costs](#what-this-costs) below for real numbers.

If you skip this step, the app will start up fine and then fail on the first
classification with:

> `Your credit balance is too low to access the Anthropic API.`

That message means exactly what it says — it is not a bug in the app.

---

## Step 5 — Create an API key

An API key is a long password that lets this app talk to Claude on your behalf.

1. In the Console, click **API keys** in the left sidebar.
2. Click **Create key** (top right).
3. Give it a name you'll recognise later, e.g. `latam-taxonomy`.
4. Click **Create key**.
5. **Copy the key now.** It starts with `sk-ant-api03-` and is shown to you
   exactly once. If you navigate away without copying it, you cannot recover
   it — just delete it and create another.

### Keeping the key safe

Treat it like a credit card number:

- **Do not** paste it into Slack, email, a screenshot, or a GitHub issue.
- **Do not** commit it to git. This repo's `.gitignore` already excludes the
  `.env` file so this doesn't happen by accident.
- Anyone who has your key can spend your credits.

If a key is ever exposed, go to **API keys**, click the `⋯` menu next to it, and
delete it. Then create a new one. Deleting takes effect immediately.

---

## Step 6 — Put the key in the project

The app reads your key from a file called `.env` in the project folder.

Copy the provided template:

```bash
cp .env.example .env
```

On Windows PowerShell, use `copy .env.example .env` instead.

Now open `.env` in a text editor and replace the placeholder with your real key.
The finished line should look like this — one line, no quotes, no spaces around
the `=`:

```
ANTHROPIC_API_KEY=sk-ant-api03-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

To open it from the terminal:

- macOS: `open -e .env`
- Windows: `notepad .env`
- Linux: `nano .env` (save with `Ctrl+O`, exit with `Ctrl+X`)

> A file whose name starts with a dot is hidden by default in most file
> managers. On macOS press `Cmd + Shift + .` in Finder to reveal hidden files.

---

## Step 7 — Install the dependencies

A **virtual environment** is a private folder of Python packages for this
project, so installing them can't disturb anything else on your computer.

**macOS / Linux:**

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Windows PowerShell:**

```powershell
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

After activating, your prompt gets a `(venv)` prefix. That prefix is how you
know the environment is on.

**You must re-run the `activate` line every time you open a new terminal.** The
install itself is one-time.

If PowerShell refuses with "running scripts is disabled on this system", run
this once, then retry:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

---

## Step 8 — Run it

```bash
python file_classification.py
```

You should see:

```
INFO:     Uvicorn running on http://127.0.0.1:7860 (Press CTRL+C to quit)
```

Open **<http://127.0.0.1:7860>** in your browser.

To stop the app, press `Ctrl + C` in the terminal.

---

## Step 9 — Your first classification

1. **Upload a PDF.** Use the box at the top. Four sample laws ship with the
   repo in `law-files/` — try `Ley1.pdf`, a Chilean AI bill.
2. Go to the **Single Classification** tab.
3. Leave the characteristic on `a1_1` (Alcance — is the law general or
   sector-specific?) and click **Generate Classification**.
4. After a few seconds you'll get a label — `General` — plus the passages from
   the PDF that justify it, with page numbers you can click to jump to in the
   preview.

If that worked, your setup is complete.

### Then try a batch

The **Batch Classification & Export** tab is where the real work happens. Tick
the characteristics you want (or **Select All** for the full taxonomy), click
**Process Selected & Export to Spreadsheet**, and the app classifies them
concurrently — the full set of 27 takes about a minute.

Results land in `outputs/<username>_classifications.xlsx`, one row per document.
Re-running the same PDF updates that row rather than adding a duplicate.

The **Spreadsheet Editor** tab shows the whole sheet, lets you correct any cell
by hand, and offers a download. Human review is expected — see
[Accuracy](#accuracy-expectations).

---

## What this costs

Costs are per document and depend on its length. A 57-page bill is roughly
26,000 tokens of input.

The app sends the document once per characteristic, but uses Anthropic's
**prompt caching**: the first call stores the document, and the remaining 26
read it back at about 10% of the price. It also classifies up to 8
characteristics at once.

In practice, with the default `claude-sonnet-5`:

| Action | Rough cost |
|---|---|
| One characteristic on one document | under $0.05 |
| Full 27-characteristic batch, one 57-page document | around **$0.50–0.60** |
| Twenty documents, full taxonomy | around **$10–12** |

Your live spend is always visible in the Console under **Usage**, broken down
per API key.

To reduce cost: classify only the characteristics you need rather than
**Select All**, and keep `WARM_PROMPT_CACHE=1` (the default) — turning it off
roughly triples the input cost of a batch.

---

## Accuracy expectations

This tool is an **assistant, not an oracle.** On the sample Chilean bill,
comparing its output against the human-coded reference in
`Taxonomía Proyectos IA. Nov.2024.xlsx`, roughly two thirds of characteristics
matched. The disagreements were mostly polarity flips — the model said "does not
promote X" where the researcher said "promotes X".

Treat the output as a **first pass that a researcher then reviews and
corrects**, using the Spreadsheet Editor tab. The evidence snippets exist
precisely so you can check the model's reasoning against the source text
quickly.

---

## Troubleshooting

**`Your credit balance is too low to access the Anthropic API`**
You skipped Step 4, or you've spent your balance. Add credits in the Console
under Plans & Billing. Nothing is wrong with the code.

**`invalid x-api-key` / `authentication_error`**
The key in `.env` is wrong, truncated, or has been deleted in the Console.
Check there are no stray spaces or quotes, and that the whole key was pasted —
they're long. Create a fresh key if unsure.

**`ANTHROPIC_API_KEY` not found, or the app exits immediately**
The `.env` file is missing, is in the wrong folder, or is named `.env.txt`
(Windows Notepad does this silently). It must sit next to
`file_classification.py` and be named exactly `.env`.

**`command not found: python3`**
Python isn't installed or isn't on your PATH. On Windows try `python` instead of
`python3`. Otherwise reinstall from python.org with "Add Python to PATH" ticked.

**`ModuleNotFoundError: No module named 'gradio'`**
The virtual environment isn't active, or dependencies weren't installed. Re-run
the activate line for your platform, confirm you see `(venv)` in the prompt,
then `pip install -r requirements.txt`.

**`Address already in use` / port 7860 busy**
The app is already running in another terminal. Either use that one, or start
this copy on a different port: `PORT=7861 python file_classification.py`.

**A classification comes back as an error, or a batch reports failures**
The status box names which characteristics failed and why. Failed
characteristics are **not** written to the spreadsheet — any previous good
values for them are left untouched — so it is always safe to just run the batch
again.

**PDF uploads but classifications are nonsense**
The PDF is probably a scan with no embedded text layer. This app reads text, it
does not do OCR. Check by opening the PDF and trying to select text with your
cursor. If you can't, run it through an OCR tool first.

---

## Where things live

| Path | What it is |
|---|---|
| `file_classification.py` | The whole app — UI, prompts, API calls, spreadsheet writing |
| `templates/` | One prompt file per taxonomy characteristic. **Edit these to change how classification works.** |
| `law-files/` | Sample AI bills to test with |
| `outputs/` | Generated spreadsheets (git-ignored — your data) |
| `user_progress/` | Which characteristics are done per document (git-ignored) |
| `Taxonomía Proyectos IA. Nov.2024.xlsx` | The human-coded reference taxonomy this tool reproduces |
| `.env` | Your API key (git-ignored — never commit this) |

To change what a characteristic means or how it's judged, edit its file in
`templates/` — they're plain text with the taxonomy options and decision rules
written out. No Python needed.
