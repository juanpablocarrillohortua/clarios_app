# The chat assistant: plan in three phases

The platform gets a chat assistant that people of the plant can ask about the
platform, their loads and, later, the figures in the database. It started as a
chatbot written for another project (a WhatsApp assistant for a congress) and
is adapted here in three phases:

1. **The assistant works**, through a development page in Streamlit, with no
   tools. *Built on 2026-10-19.*
2. **It answers from data**, with read-only SQL over the `reporting` views.
   When the data a question needs has no table yet, it says so. *Built on
   2026-10-19, on simulated data: the real database is one setting away.*
3. **People use it from the web app**, in an accordion on the dashboard.

Decisions taken with the owner on 2026-10-19:

| Question | Decision |
|---|---|
| The library | **LangChain + OpenAI**, as in the imported code (`create_agent`, `ChatOpenAI`) |
| How phase 2 reads data | **Free read-only SQL** written by the model, run with the read-only `reporting_reader` login |
| Where a conversation lives in the web app | **In the browser.** The API stores nothing |
| What phase 2 reads while the plant has not agreed to send data to OpenAI | **Simulated data** in DuckDB by default; the PostgreSQL adapter is built and off (`CHAT_DATA_SOURCE`) |

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

Phase 2 adds `CHAT_DATA_SOURCE`, `CHAT_SQL_ROW_LIMIT`, `CHAT_SQL_TIMEOUT_SECONDS`,
`CHAT_DB_HOST` and `CHAT_DB_PORT` (section "Phase 2").

### Dependencies

- **`duckdb` is a main dependency** since phase 2, for the simulation.
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

**Tests:** `tests/unit/test_chat.py` (31 tests, two of them added in phase 2) covers:
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

## Phase 2: the assistant answers from data (built 2026-10-19, on simulated data)

The plant has not agreed to send its data to OpenAI. So the assistant's tools
read **simulated data** by default, and the real database is one setting away.

### The switch

| `CHAT_DATA_SOURCE` | Where the tools read | What reaches OpenAI |
|---|---|---|
| `simulated` (default) | Synthetic rows with the names, columns and column order of the 12 `reporting` views, in an in-memory DuckDB (`adapters/simulated_reporting.py`, `adapters/duckdb_reporting_query.py`) | Invented figures only |
| `database` | The real `reporting` views, with the read-only reporting login (`adapters/postgres_reporting_query.py`) | Rows of those views. **Only once the plant agrees** |

**To turn the database on:**
1. Make sure the reporting login exists: `make powerbi` creates or renews it.
2. Set `CHAT_DATA_SOURCE=database` in `.env`.
3. For `make chat-ui` on the host, also set `CHAT_DB_HOST=127.0.0.1` and
   `CHAT_DB_PORT=5433`. That port is the bridge `make powerbi` opens.

**Where the database settings come from:**
- the address is the application's (`DATABASE_URL` or `POSTGRES_*`);
- the login is `REPORTING_DB_USER` and `REPORTING_DB_PASSWORD`;
- without the password, the assistant says it is not configured;
- the application's own login is never used.

[ADR 0011](adr/0011-the-assistant-reads-the-reporting-views.md) records the
decision. It is **Proposed** until the plant agrees, and it amends ADR 0007:
the assistant reads the same views as Power BI, so the two cannot disagree.

### The simulation

The simulation is fiscal year 2026, built the same way every time:

| What | Whose |
|---|---|
| Drivers, standards, multipliers, weights, the report's sections and rows, the data-quality checks | The repository's configuration (revisions `0003` and `0004`), restated |
| Items (`SIM-...`), production lines, receipts, invoices, quantities, closes, the one reclose | Invented |

It holds together as the real views do:
- `equs = cantidad × factor`;
- `equs_mensual` is `aporte_cuenta` summed by month and driver;
- `equs_batt_mensual` is `equs_mensual` summed by process;
- the plastics of `equs_poly_mensual` sum their receipts;
- October to August are closed and September is open;
- January was reclosed once.

Monthly EQUS BATT runs from about 116,000 to 238,000.

### Guards

| Guard | Where |
|---|---|
| One statement, starting with `SELECT` or `WITH`, at most 10,000 characters. Literals, quoted names and comments are masked first, so a `;` inside them counts for nothing and a statement hidden after a comment is still seen | `domain/chat_sql.py`, before any engine |
| Read-only: the login's rights (database), a transaction that is always rolled back (both engines) | the adapters |
| No files, no network, no extensions, settings locked | DuckDB |
| `statement_timeout` (PostgreSQL), an interrupt (DuckDB): `CHAT_SQL_TIMEOUT_SECONDS`, default 15 | the adapters |
| At most `CHAT_SQL_ROW_LIMIT` rows (default 200), and `truncated` when there were more | the adapters |
| The model's SQL is sent as written: no `%` is taken for a parameter | PostgreSQL adapter |

### Tools and the sources catalogue

`services/chat_data_service.py` gives the model three tools. Each answers JSON
text, and each answer says `data_source`:

- **`list_sources`:** `config/chat/sources.yaml`, which lists each file as
  implemented (with its views) or pending:
  - **implemented:** the seven EQUS exports, with the 12 views;
  - **pending:** `Base_de_datos.xlsx`, `SCADA-Base de datos F&A.xlsx`,
    `SCADA-Base de datos Energia RC.xlsx` and
    `SCADA-Base de datos GAS RC.xlsx`;
- **`describe_reporting`:** the views and their columns;
- **`run_sql`:** one query. It answers in one of three ways:
  - `ok` with the rows;
  - `pending_implementation` when a table does not exist;
  - `error` with a reason the model can act on, such as a wrong column, a
    refusal or a timeout.

The catalogue also goes with **every question** as a note, so a question about
a pending source is answered without a query. In the first smoke test, without
that note, the model searched the EQ views for energy and did not give the
pending sentence.

Each query is logged with its SQL, its rows and its time, never the question.

### The prompt

`system_prompt.txt` gained a "Data" section:
- use the tools in order;
- write PostgreSQL;
- aggregate in SQL;
- where the EQ of a month is;
- retry a failed query at most twice.

It also quotes two Spanish sentences word for word, a deliberate exception to
the repository's language rule:
- **"No encontrado: la tabla está pendiente de implementación."** for data that
  has no table yet;
- **"(Datos simulados: no son cifras reales de la planta.)"** at the end of
  every answer with a figure when `data_source` is `simulated`.

### Tests

- **`tests/unit/test_chat_sql.py`** (32): what the check accepts and refuses.
- **`tests/unit/test_chat_data.py`** (51):
  - the simulation: deterministic, consistent, invented items;
  - the DuckDB guards: PostgreSQL-style SQL, truncation, missing table, wrong
    column, no file, time limit, nothing kept, settings locked;
  - the catalogue and its file;
  - the three tools' answers and the sources note;
  - a fake model that calls `run_sql` and reads the rows;
  - the switch: the login and the bridge, refused without the password.
- **`tests/integration/test_chat_reporting_query.py`** (13), against
  TimescaleDB with the login `create-reader` makes:
  - **the simulated views have exactly the real views' columns, in order;**
  - a `SELECT` works, and a `%` passes through;
  - truncation, a missing table;
  - `bronze`, `silver` and `gold` denied;
  - a write refused;
  - the time limit, and a usable connection after a failure;
  - an unreachable database.

### Checked on 2026-10-19

With the real model and simulated data:

| Question | Answer |
|---|---|
| "¿Cuántas EQ hubo en septiembre de 2026?" | 196,712.47, the simulation's figure, with the simulated notice |
| "¿Cuánta energía consumió OREC en agosto de 2026?" | The pending sentence, with no query |
| "¿Cuánto gas usó RC el año pasado?" | The pending sentence, with no query |
| "¿Y qué proceso aportó más EQ en ese mes de septiembre?" | OREC, 73,502.30, with the notice. Its first query named a column that does not exist; it corrected it from the error |

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
- **Data leaves the premises in database mode.** Rows of the reporting views
  (EQ figures, item codes) would be sent to OpenAI. That is why
  `CHAT_DATA_SOURCE` defaults to `simulated`; **the plant must agree before it
  is set to `database`.**
- **Cost and latency.** Each question is a paid call of one to a few seconds.
  There is no limit per person; one can be added if needed.
- **The model's name** (`CHAT_MODEL`) is a setting. A name OpenAI does not know
  makes every answer fail with `chat_model_unavailable`.
