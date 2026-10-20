# The chat assistant: plan in three phases

The platform gets a chat assistant that people of the plant can ask about the
platform, their loads and, later, the figures in the database. It started as a
chatbot written for another project (a WhatsApp assistant for a congress) and
is adapted here in three phases:

1. **The assistant works**, through a development page in Streamlit, with no
   tools. *Built on 2026-10-19.*
2. **It answers from the database**, with read-only SQL over the `reporting`
   views. When the data a question needs has no table yet, it says so.
3. **People use it from the web app**, in an accordion on the dashboard.

Decisions taken with the owner on 2026-10-19:

| Question | Decision |
|---|---|
| The library | **LangChain + OpenAI**, as in the imported code (`create_agent`, `ChatOpenAI`) |
| How phase 2 reads data | **Free read-only SQL** written by the model, run with the read-only `reporting_reader` login |
| Where a conversation lives in the web app | **In the browser.** The API stores nothing |

## 0. Starting point and rules for every phase

### What was imported, and what was kept

The imported package `src/energy/agent/` held:

- a WhatsApp entry point;
- a conversation service and a prompt builder;
- a LangChain agent;
- a Streamlit page;
- three database tables keyed by a WhatsApp number;
- the congress's tools and data;
- an empty prompt file.

Its imports pointed at the other project, so none of it ran here.

| Imported piece | What happened to it |
|---|---|
| The cutoff of the history (`PromptBuilder._apply_cutoff`) | Kept: `apply_cutoff` in `domain/chat.py` |
| The date-and-time system message, in Bogotá | Kept, in English: `time_note` in `domain/chat.py` |
| History → LangChain messages | Kept: `_as_langchain` in `adapters/langchain_chat_model.py` |
| `OpenAIAgent` (`create_agent`, its prompt file) | Kept: `LangChainChatModel` and `load_system_prompt` |
| The prompt file `prompts/system_prompt.txt` | Moved beside its reader, `adapters/prompts/system_prompt.txt`, and written (it was empty) |
| The Streamlit chat loop | Kept: `scripts/chat_streamlit.py` |
| WhatsApp parsing (`rag_service.py`) | Dropped |
| The congress's tools and data (`tools.py`) | Dropped |
| The conversation tables (`models.py`, `session_manager.py`) | Dropped: the conversation lives with the caller |
| `end_to_end_test.py` (calls the real API, holds a phone number) | Dropped |

The imported service saved the question and then added it again at the end of
the context, so the model saw it twice. Here the history never holds the
question, and the question goes last once.

### Where each piece lives

The package was dissolved into the layers of the hexagon, so
`tests/unit/test_architecture.py` holds unchanged:

| Piece | Module |
|---|---|
| Messages, roles, cutoff, the time note, the context (pure) | `domain/chat.py` |
| The model's port: `ChatModel.reply(context) -> str` | `ports/chat_model.py` |
| LangChain's agent on OpenAI, the prompt file | `adapters/langchain_chat_model.py`, `adapters/prompts/system_prompt.txt` |
| The use case: `ChatService.answer(history, question)`, stateless | `services/chat_service.py` |
| Wiring for the command line and Streamlit | `pipelines/flows/chat.py` (`build_chat_service()`) |
| Wiring for the API (phase 3) | `api/deps.py`, the API's composition root (ADR 0005) |
| The development page | `scripts/chat_streamlit.py`, run by `make chat-ui` |

A context is, in this order: the notes (the date and time at the plant), the
last messages of the history (at most `CHAT_CONTEXT_WINDOW_LIMIT`), then the
question. The adapter puts the system prompt first, then the notes as system
messages, then the conversation.

### Settings

`ChatSettings` (`core/settings.py`), read from the environment and `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `CHAT_API_KEY` | empty | The OpenAI key. A secret: it reaches no log and no browser. Empty means "not configured": the platform works, and the assistant says it is not available |
| `CHAT_MODEL` | `gpt-4.1-mini` | The OpenAI model |
| `CHAT_CONTEXT_WINDOW_LIMIT` | 20 | How many earlier messages go with each question |
| `CHAT_TIMEOUT_SECONDS` | 60 | How long one answer may take before the model counts as unavailable |
| `CHAT_SYSTEM_PROMPT_FILE` | empty | Another file of instructions; the packaged one when empty |

The imported variable was the generic `API_KEY`. It is now `CHAT_API_KEY`: rename
it in your `.env`.

Phase 2 adds `CHAT_SQL_ROW_LIMIT` and `CHAT_SQL_TIMEOUT_SECONDS`.

### Dependencies

- **`langchain` and `langchain-openai` are main dependencies.** The adapter
  lives in `src/`, so it must import and type-check in CI, and the API image
  needs them in phase 3.
- **`streamlit` is the optional extra `chat-ui`**, for the development page only.
- They were added as CLAUDE.md says: `pyproject.toml`, `uv lock`, then an install
  that keeps the notebook packages. pip-audit finds no known vulnerability,
  extras included.
- The lockfile is part of the images' dependency layer, so the next `make up`
  reinstalls every Python dependency. Phase 1 does not need the images; phase
  3 does.

### The system prompt

- **Where it is.** The assistant's instructions are a text file,
  `src/energy/adapters/prompts/system_prompt.txt`, packaged with the module
  (the wheel carries it). `CHAT_SYSTEM_PROMPT_FILE` can point at another file.
- **A missing or empty file is refused** when the service is built
  (`ChatNotConfiguredError`), as an empty key is.
- **What it says.** It is written in English. It gives the scope (Clarios'
  plants and areas, energy and gas, equivalent batteries, data loads and the
  dashboard) and tells the model:
  - to answer in Spanish, briefly;
  - never to invent a figure;
  - to say when it does not know;
  - to stay within the platform.
- **Edit it freely:** a unit test only checks that it is there and asks for
  Spanish.

### Rules

- **No network in tests.** The service is tested with a fake `ChatModel`, and
  the adapter with LangChain's fake chat model.
- **Nothing of a conversation is logged,** neither the question nor the answer:
  only that a question was answered, how much history went with it, and how long
  it took.
- **No phone numbers, addresses or real identifiers** in the code or the
  fixtures.
- **Language.** Code, docstrings and the prompt are in English. The development
  page's labels are English too; the web app's texts are keys of `es.json` (phase
  3).

## Phase 1: the assistant works in Streamlit, no tools (built 2026-10-19)

**What was built:**
- the modules of the table above;
- `make chat-ui`, which runs `uv run --extra chat-ui streamlit run
  scripts/chat_streamlit.py` on the host, at http://localhost:8501.

**How the page behaves:**
- The conversation lives in the tab's session state. "New conversation" clears
  it.
- A refusal is shown as a message, not a stack trace: not configured, an empty
  question, or the model not answering.

**Tests:** `tests/unit/test_chat.py` (29 tests) covers:
- the cutoff;
- the time note at the plant (any time zone, a naive time refused);
- the order of the context;
- an empty question;
- the service with a fake model, logging no text;
- the packaged prompt, another file, and an empty or missing file;
- what the adapter sends LangChain;
- a vendor failure becoming `ChatModelUnavailableError`;
- the settings (unset by default, blanks unset, the key kept out of `repr`);
- the wiring, refused without a key or with an empty prompt.

**Checked on 2026-10-19:**
- ruff, mypy strict and bandit pass;
- the 1,241 unit tests pass (architecture test unchanged), and pip-audit finds
  nothing;
- the wheel carries the prompt;
- three real questions in a row were answered in Spanish, and the third ("¿Qué
  te pregunté primero?") was answered from the history;
- `make chat-ui` serves the page.

**To try it:** put `CHAT_API_KEY` in `.env`, run `make chat-ui`, and open
http://localhost:8501.

## Phase 2: the assistant answers from the database

### The rule it changes

[ADR 0007](adr/0007-figures-reach-people-through-power-bi.md) says figures
reach people through Power BI only. **ADR 0011, "The assistant reads the
reporting views with read-only SQL"**, amends it:

- the assistant reads the same `reporting` views as Power BI, so the two cannot
  disagree;
- section 9 of `docs/api.md` (every way into the database) gains the
  assistant.

### Guards, the database first

- **The login.** The model's SQL runs as `reporting_reader`. That login:
  - is read-only (`default_transaction_read_only`);
  - has `search_path = reporting`;
  - is refused on `bronze`, `silver` and `gold`.

  It already exists (`energy reporting create-reader`, `REPORTING_DB_USER` and
  `REPORTING_DB_PASSWORD`).
- **Each query** runs in a read-only transaction under `SET LOCAL
  statement_timeout` (`CHAT_SQL_TIMEOUT_SECONDS`, default 15). It fetches at
  most `CHAT_SQL_ROW_LIMIT` + 1 rows (default 200), so the tool can say the
  result was cut.
- **Before a query runs,** `domain/chat_sql.py` (pure) refuses:
  - more than one statement;
  - a statement that does not start with `SELECT` or `WITH`;
  - a query over a size cap.

  A refusal goes back to the model as the tool's result, never to the person
  as an error.

### Port and adapter

- **`ports/reporting_query.py`:**
  - `describe()`: the views of `reporting` and their columns, from
    `information_schema`;
  - `run(sql)`: the rows.
- **`adapters/postgres_reporting_query.py`:**
  - its own engine for the reporting login;
  - its host and port are settings: `db:5432` inside compose, and
    `127.0.0.1:5433` through `make powerbi`'s bridge for the development page on
    the host.

### Sources that have a table, and sources that do not

`config/chat/sources.yaml` lists every Excel the plant keeps, with its state and,
when it has a table, the views that hold it:

| Source | State | Where |
|---|---|---|
| The five EQUS exports: transactions, invoices, structures, catalogues, production lines | implemented | the twelve `reporting` views: `equs_mensual`, `equs_batt_mensual`, `aporte_cuenta`, `recibos_produccion`, `articulos`, `cuentas`, `equs_poly_mensual`, `calidad_datos_mensual`, `cierres`, `cierres_calidad`, `equs_cerrado`, `equs_cerrado_historial` |
| `Base_de_datos.xlsx` (monthly energy by area) | pending | no table yet |
| `SCADA-Base de datos F&A.xlsx` (formation rectifiers) | pending | no table yet |
| `SCADA-Base de datos Energia RC.xlsx` (RC's meters) | pending | no table yet |
| `SCADA-Base de datos GAS RC.xlsx` (RC's gas) | pending | no table yet |

### Tools

Three LangChain tools, built over the ports:

- `list_sources()`;
- `describe_reporting()`;
- `run_sql(query)`.

### When there is no table

Where it can be, this is decided by code, not by the model:

- **A pending source:** `list_sources` marks it pending.
- **A missing table:** a query fails because a table or column does not exist.

In both cases the tool answers `pending_implementation`. The prompt's new "Data"
section then makes the model answer exactly:

> **No encontrado: la tabla está pendiente de implementación.**

That Spanish sentence, quoted in the English prompt, becomes a deliberate
exception to the repository's language rule, recorded in CLAUDE.md.

The "Data" section also says:
- ask `list_sources`, then `describe_reporting`, then `run_sql`;
- never give a figure the tools did not return.

### Logs

- Each SQL that runs is logged with its row count; in phase 3, also with the
  caller's `subject`.
- The question is never logged.

### Tests

- **Unit:**
  - the SQL checks: several statements, `UPDATE`, `COPY`, a second statement
    hidden behind a comment;
  - the catalogue;
  - the tools' results;
  - the pending path, with a fake port.
- **Integration** (TimescaleDB container, reusing the `reporting_reader` setup
  of `TestReportingSchema`):
  - a `SELECT` on a view;
  - a write refused by the database;
  - `bronze`, `silver` and `gold` denied;
  - the timeout;
  - the row cap;
  - a missing table answered as pending.

### Done when

- `make check` passes.
- In the development page, with `make powerbi` up:
  - "¿Cuántas EQ hubo en septiembre de 2026?" gives the figure of
    `make resumen MONTH=2026-09`;
  - "¿Cuánta energía consumió OREC?" gives the pending sentence.

## Phase 3: an accordion on the dashboard

### API

`POST /api/chat`, mounted in `api/main.py` behind
`Depends(requires(Permission.CHAT_USE))`.

- **Who may use it.** `chat:use` is new in `api/security.py`. In `GRANTS` it
  goes to viewer, operator, engineer and admin, as `dashboard:view` does, and
  the table's test restates it.
- **The request.** Body `{history: [{role, content}], question}`. The history
  holds at most `CHAT_CONTEXT_WINDOW_LIMIT` messages, and each text at most
  4,000 characters; beyond that, 422.
- **The answer.** `{answer}`.
  - 503 `chat_not_configured` without a key or a prompt;
  - 502 `chat_model_unavailable` when the model fails;
  - 422 `empty_question`.
- **Nothing is stored.**
- **Tests that hold it:** `docs/api.md` gets its row and a sample answer, which
  `tests/unit/test_api_reference.py` requires. `tests/unit/test_api_auth.py`
  covers the 401.
- The api image is rebuilt with the new dependencies (`make up`, on an idle
  machine).

### Web app

- **Where it shows.** `api/chat.ts` and `dashboard/ChatAccordion.tsx`, a Mantine
  `Accordion` titled with an `es.json` key under `chat.*`. It is:
  - closed by default;
  - above the report's frame, which it never covers;
  - shown only when `GET /me` lists `chat:use`.
- **Where the conversation lives.** The component's state and `sessionStorage`:
  a reload keeps it, and closing the tab clears it. The last
  `CHAT_CONTEXT_WINDOW_LIMIT` messages go with each question. "Nueva
  conversación" starts again.
- **Answers are plain text, never HTML.**
- **Sending.** "Enviar" is guarded against a double send, as "Confirmar carga"
  is. A pending state appears after 300 ms; errors are said plainly (not
  configured, no answer).
- **Look.** Design tokens only (`styles/tokens.css`). No motion under
  `prefers-reduced-motion`. It works at phone width.

### Tests (Vitest)

- hidden without `chat:use`;
- a question sent with the history trimmed to the limit;
- a double click sends one request;
- each error;
- the conversation restored from `sessionStorage`.

### Docs

- a row in section 8 of `docs/frontend-plan.md`;
- the assistant's path in `docs/architecture.md`;
- in CLAUDE.md, the build state and the "Chat assistant" section.

### Done when

- `make check` and `make frontend-check` pass.
- After `make up`, `/api/chat` answers 401 without a token.
- The owner's check, signed in: the accordion on `/dashboard`, at desktop and
  phone width, answers a figure and the pending case.

## Risks and open points

- **Valid SQL can still be wrong.** The guards keep the model's SQL harmless,
  not correct. Showing the query behind an answer is an option for later.
- **Prompt injection through data.** The text columns of the views reach the
  model. It can only read, and its answer is rendered as plain text.
- **Data leaves the premises.** From phase 2, rows of the reporting views (EQ
  figures, item codes) are sent to OpenAI. **The plant must agree before phase 2
  runs on real data.**
- **Cost and latency.** Each question is a paid call of one to a few seconds.
  There is no limit per person; one can be added if needed.
- **The model's name** (`CHAT_MODEL`) is a setting. A name OpenAI does not know
  makes every answer fail with `chat_model_unavailable`.
