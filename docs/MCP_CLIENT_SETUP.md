# MCP client setup

This guide covers installation, environment configuration, stdio startup, and
client-specific setup for:

- Claude Code
- Kimi Code
- Codex CLI
- OpenCode
- Cherry Studio
- DeepSeek Harness

For the project overview and tool reference, return to [README.md](../README.md).
The Chinese version of this guide is [MCP_CLIENT_SETUP_zh.md](./MCP_CLIENT_SETUP_zh.md).

## 1. Install the server

Python 3.10 or later is required. The MCP server uses a uv-managed
project-local `.venv`, not Conda. Run these commands from the project root,
represented below by `<project>`. Install uv first if needed, following the
[uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).

### Build the project-local uv environment

```powershell
uv python install --managed-python 3.12
uv python pin 3.12
uv sync --managed-python --python 3.12
```

These commands download uv-managed CPython, create `.python-version`, the
project-local `.venv`, and `uv.lock`, then install the project in editable mode.
Do not run `python -m venv`, `pip install -e .`, or point an MCP client at a
Conda environment. Activation is unnecessary: use `uv run` in a terminal and
configure MCP clients with the environment's absolute interpreter path:

| Platform | `<VENV_PY>` for a project-local `.venv` |
|---|---|
| Windows | `<project>\.venv\Scripts\python.exe` |
| Linux / macOS | `<project>/.venv/bin/python` |

### Replace placeholders with absolute paths

- `<project>` is the absolute path to this repository's root (the directory
  containing `pyproject.toml`).
- `<VENV_PY>` is the absolute path to the Python interpreter that uv created
  in the project-local `.venv`.
- Do not leave either placeholder unchanged in a real client configuration.

For a Windows project at `C:\Users\alice\literature-mcp`:

- `<project>` becomes `C:\Users\alice\literature-mcp`
- `<VENV_PY>` becomes `C:\Users\alice\literature-mcp\.venv\Scripts\python.exe`

For a Linux/macOS project at `/home/alice/literature-mcp`:

- `<project>` becomes `/home/alice/literature-mcp`
- `<VENV_PY>` becomes `/home/alice/literature-mcp/.venv/bin/python`

In other words, `<VENV_PY>` is `<project>` plus the interpreter path inside
`.venv` (`.venv\Scripts\python.exe` on Windows, `.venv/bin/python` on
Linux/macOS).

In JSON, `\` starts an escape sequence (an unescaped `\t` or `\n` would
become a control character), so write Windows paths either with forward
slashes:

```json
"C:/Users/alice/literature-mcp/.venv/Scripts/python.exe"
```

or with each backslash doubled:

```json
"C:\\Users\\alice\\literature-mcp\\.venv\\Scripts\\python.exe"
```

Both forms denote the same path. TOML literal strings and single-quoted YAML
strings, as used below, preserve backslashes without JSON-style escaping.

## 2. Configure environment variables

Copy `.env.example` to `.env` in the project root and replace only the values
you intend to configure:

```dotenv
OPENALEX_API_KEY=your-api-key-here
OPENALEX_EMAIL=you@example.org
ZHIPUAI_API_KEY=your-zhipuai-key-here
ELSEVIER_API_KEY=your-elsevier-api-key-here
ELSEVIER_INST_TOKEN=your-elsevier-inst-token-here
BACKFILL_ABSTRACTS=true
BACKFILL_MAX_TARGETS=25
BACKFILL_CONCURRENCY=4
```

Get a free OpenAlex key from [OpenAlex API settings](https://openalex.org/settings/api).
ZhipuAI is available at [bigmodel.cn](https://bigmodel.cn).

### Variable reference

| Variable | Required | Purpose |
|---|---|---|
| `OPENALEX_API_KEY` | Only for OpenAlex operations | Enables keyword, OpenAlex semantic, and ID search, ingestion, autocomplete, and Content API PDF downloads. |
| `OPENALEX_EMAIL` | Recommended | Identifies the caller to OpenAlex and Crossref for provider contactability and polite-pool behavior. |
| `ZHIPUAI_API_KEY` | Only for local semantic search | Enables Embedding-3 generation and the `semantic_query` path in `library_query`. Plain local SQL does not need it. |
| `ELSEVIER_API_KEY` | Only for Elsevier access | Enables `fetch_elsevier_abstracts` and Elsevier/Scopus sources during missing-abstract backfill. |
| `ELSEVIER_INST_TOKEN` | Optional | Supplies an Elsevier institutional token when required by the institution's subscription. |
| `BACKFILL_ABSTRACTS` | No; default `true` | Enables automatic missing-abstract backfill before search results are stored. Set `false` to disable this automatic step. |
| `BACKFILL_MAX_TARGETS` | No; default `25` | When a batch has more missing-abstract targets than this value, ranks Elsevier journal works in the batch by citation count and backfills the top K; skips the batch only when no Elsevier journal target exists. `0` removes this batch-size threshold. It does not limit search ingestion. |
| `BACKFILL_CONCURRENCY` | No; default `4` | Number of DOI backfill pipelines run concurrently; accepted runtime range is 1 to 8. |

Every work is still retained when abstract backfill is disabled, skipped, or
unsuccessful. Backfill can use Crossref without an Elsevier key; the Elsevier
and Scopus stages require `ELSEVIER_API_KEY`.

### Capabilities by configuration

| Configuration | Available capabilities |
|---|---|
| No remote API key | SQL, statistics, BibTeX export, and citation analysis over an existing local library |
| `OPENALEX_API_KEY` | OpenAlex keyword, semantic, and ID search, ingestion, autocomplete, and Content API downloads |
| + `ZHIPUAI_API_KEY` | Local semantic embeddings and vector search |
| + `ELSEVIER_API_KEY` | Abstract retrieval and automatic missing-abstract backfill through Elsevier/Scopus in addition to Crossref |

Local SQL, statistics, export, deletion, connection close, and citation
analysis do not depend on `OPENALEX_API_KEY`. They operate on the existing
SQLite library.

### How the client locates `.env`

Keep secrets in the project `.env` file. In the MCP client configuration, pass
only the absolute file path:

```text
OPENALEX_MCP_ENV_FILE=<project>/.env
```

At startup the server asks `python-dotenv` to load that exact file. If
`OPENALEX_MCP_ENV_FILE` is absent, normal `python-dotenv` discovery is used,
which depends on process context and is less predictable across clients.
Setting the explicit absolute path avoids differences in each client's working
directory. Existing process-level environment values continue to take
precedence over values in `.env`.

Do not place `OPENALEX_MCP_ENV_FILE` inside the same `.env` as a substitute for
the client setting: the server needs the pointer before it can load that file.

## 3. Verify stdio startup first

All supported clients start the same module over stdio:

```bash
<VENV_PY> -m openalex_mcp.registry
```

Run the matching shell command before editing a client configuration:

```powershell
& "<VENV_PY>" -m openalex_mcp.registry
```

```bash
"<VENV_PY>" -m openalex_mcp.registry
```

A healthy process starts and waits for stdio input. Warnings about optional
keys are expected when those keys are not configured. Press Ctrl+C to stop the
standalone process. If Python exits immediately with an import error, fix the
interpreter or installation before troubleshooting the MCP client.

The examples in this guide set four child-process variables:

| Variable | Why it is set |
|---|---|
| `OPENALEX_MCP_ENV_FILE` | Loads the intended `.env` regardless of client working directory. |
| `PYTHONIOENCODING=utf-8` | Forces UTF-8 for Python standard streams and avoids garbled Windows log text. |
| `PYTHONUTF8=1` | Enables Python UTF-8 mode for filesystem and text defaults. |
| `FASTMCP_SHOW_SERVER_BANNER=false` | Suppresses only the FastMCP startup banner; it does not disable logs or change tool behavior. |

MCP uses stdout for protocol messages. Keep diagnostic output on stderr and do
not wrap the command with scripts that print status text to stdout.

## 4. Client configuration summary

The detailed examples below use `literature-mcp` as the server name.

| Client | Configuration | Server key | Environment field | Reload / verification |
|---|---|---|---|---|
| Claude Code | User `~/.claude.json`; project `.mcp.json` | `mcpServers` | `env` | `claude mcp get literature-mcp`, `claude mcp list`, then `/mcp` |
| Kimi Code | User `~/.kimi-code/mcp.json`; project `.kimi-code/mcp.json` | `mcpServers` | `env` | Start a new session, then use `/mcp` or an actual tool call |
| Codex CLI | User `~/.codex/config.toml`; trusted project `.codex/config.toml` | `[mcp_servers.*]` | `[mcp_servers.*.env]` | Start a new session, then use `codex mcp list`, `codex mcp get`, or `/mcp` |
| OpenCode | Global `~/.config/opencode/opencode.json`; project `opencode.json` | `mcp` | `environment` | `opencode mcp list` and an actual tool call |
| Cherry Studio | Settings -> MCP -> MCP Servers -> Add | `mcpServers` | `env` | Enable the server, inspect its tools, and bind it to an Agent when needed |
| DeepSeek Harness | `~/.dsh/profiles/<profile>/cordis.patch.yml` | cordis patch `insert` using `@deepseek-ai/dsh-mcp-client` | `config.env` | Dump the resolved profile, then call an `mcp__literature-mcp__*` tool |

Configuration locations and UI labels may change between client releases. The
linked official references are the source of truth when a newer client differs
from the examples.

## 5. Client-specific setup

### Claude Code

Following the official [Claude Code MCP reference](https://code.claude.com/docs/en/mcp),
register a user-scoped stdio server with:

```bash
claude mcp add --env "OPENALEX_MCP_ENV_FILE=<project>/.env" --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 --env FASTMCP_SHOW_SERVER_BANNER=false --transport stdio --scope user literature-mcp -- "<VENV_PY>" -m openalex_mcp.registry
```

The `--` separator is required: everything after it is the server command, so
Python's `-m` is not parsed as a Claude option. Keep another Claude option such
as `--transport` between the final variadic `--env` value and the server name,
as shown above.

`--scope user` makes the server available in all projects and stores it in
`~/.claude.json`. Use `--scope project` only when you intentionally want a
shareable `.mcp.json` in the project root. Claude Code asks for workspace trust
before running project-scoped commands.

Verify the saved configuration and connection:

```bash
claude mcp get literature-mcp
claude mcp list
```

Then use `/mcp` inside Claude Code and make a real `library_stats` tool call.

### Kimi Code

Kimi Code reads user-scoped configuration from `~/.kimi-code/mcp.json` or
`$KIMI_CODE_HOME/mcp.json`, and project-scoped configuration from
`.kimi-code/mcp.json` in the project root. Project entries override same-named
user entries. See the official
[Kimi Code MCP documentation](https://moonshotai.github.io/kimi-code/en/customization/mcp.html).

```json
{
  "mcpServers": {
    "literature-mcp": {
      "command": "<VENV_PY>",
      "args": ["-m", "openalex_mcp.registry"],
      "cwd": "<project>",
      "env": {
        "OPENALEX_MCP_ENV_FILE": "<project>/.env",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "FASTMCP_SHOW_SERVER_BANNER": "false"
      },
      "startupTimeoutMs": 120000,
      "toolTimeoutMs": 300000
    }
  }
}
```

Kimi does not register a server added during an already-open session. Start a
new session after editing the file, use `/mcp` to inspect connection status, or
use `/mcp-config` to manage servers interactively. `kimi doctor` validates
`config.toml` and `tui.toml`; it is not an MCP connectivity test.

Use a real tool call for end-to-end verification:

```bash
kimi -p "Call the library_stats tool from the literature-mcp MCP server, then reply with ONLY the total number of works in the library as a plain number."
```

### Codex CLI

Codex stores user-level MCP servers in `~/.codex/config.toml`; trusted projects
may instead use `.codex/config.toml`. The CLI command from the official
[Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
creates the base user entry:

```bash
codex mcp add literature-mcp --env "OPENALEX_MCP_ENV_FILE=<project>/.env" --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 --env FASTMCP_SHOW_SERVER_BANNER=false -- "<VENV_PY>" -m openalex_mcp.registry
```

For an explicit working directory and longer startup/tool timeouts, replace
the generated `literature-mcp` block with the following. Do not append a second
`[mcp_servers.literature-mcp]` table: duplicate TOML tables are invalid.

```toml
[mcp_servers.literature-mcp]
command = '<VENV_PY>'
args = ["-m", "openalex_mcp.registry"]
cwd = '<project>'
startup_timeout_sec = 120
tool_timeout_sec = 300

[mcp_servers.literature-mcp.env]
OPENALEX_MCP_ENV_FILE = '<project>/.env'
PYTHONIOENCODING = 'utf-8'
PYTHONUTF8 = '1'
FASTMCP_SHOW_SERVER_BANNER = 'false'
```

Inspect the saved entry with:

```bash
codex mcp list
codex mcp get literature-mcp --json
```

Start a new Codex session after a manual configuration edit and use `/mcp` in
the TUI to confirm that the server is active. Finish with a real
`library_stats` tool call.

### OpenCode

Add the following `mcp` block to global
`~/.config/opencode/opencode.json` (Windows:
`C:\\Users\\<you>\\.config\\opencode\\opencode.json`) or project-level
`opencode.json`. OpenCode also accepts the `.jsonc` extension. See its official
[MCP server](https://opencode.ai/docs/mcp-servers/) and
[configuration](https://opencode.ai/docs/config/) references.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "literature-mcp": {
      "type": "local",
      "command": ["<VENV_PY>", "-m", "openalex_mcp.registry"],
      "cwd": "<project>",
      "environment": {
        "OPENALEX_MCP_ENV_FILE": "<project>/.env",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "FASTMCP_SHOW_SERVER_BANNER": "false"
      },
      "enabled": true,
      "timeout": 120000
    }
  }
}
```

`command` is one array containing the executable and its arguments. `timeout`
is measured in milliseconds and controls connection/tool discovery, not the
duration of every tool invocation.

Verify with:

```bash
opencode mcp list
opencode run "Call the library_stats tool from the literature-mcp MCP server, then reply with ONLY the total number of works in the library as a plain number."
```

### Cherry Studio

Following Cherry Studio's current
[MCP guide](https://docs.cherryai.com.cn/advanced-basic/extensions/mcp), open
**Settings -> MCP -> MCP Servers -> Add**, choose JSON import, and import:

```json
{
  "mcpServers": {
    "literature-mcp": {
      "command": "<VENV_PY>",
      "args": ["-m", "openalex_mcp.registry"],
      "env": {
        "OPENALEX_MCP_ENV_FILE": "<project>/.env",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "FASTMCP_SHOW_SERVER_BANNER": "false"
      }
    }
  }
}
```

Use the absolute interpreter path where the package was installed. Enable the
server, wait for a healthy status, and inspect its tool list. For Agent
workflows, also bind the server under **Work -> Agent -> Edit -> MCP**. A server
that is healthy in Settings but not bound to the active Agent will not expose
its tools in that Agent conversation.

Verify by asking that Agent to call `library_stats` and return the total work
count.

### DeepSeek Harness (DSH)

[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) uses the
official
[`@deepseek-ai/dsh-mcp-client`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/README.md)
plugin. Configure one plugin instance per MCP server in the user patch for the
profile you actually run: `web` for the Web GUI or `headless` for the CLI.

- `~/.dsh/profiles/web/cordis.patch.yml`
- `~/.dsh/profiles/headless/cordis.patch.yml`

`~/.dsh` is the default Harness home and follows `$DSH_HOME` when that variable
is set. Append this stdio `insert` entry:

```yaml
- insert:
    - id: mcp-literature-mcp
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: literature-mcp
        transport: stdio
        command: '<VENV_PY>'
        args: ['-m', 'openalex_mcp.registry']
        cwd: '<project>'
        env:
          OPENALEX_MCP_ENV_FILE: '<project>/.env'
          PYTHONIOENCODING: 'utf-8'
          PYTHONUTF8: '1'
          FASTMCP_SHOW_SERVER_BANNER: 'false'
        toolCallTimeoutMs: 300000
```

Field notes:

- `serverName` becomes the tool namespace, for example
  `mcp__literature-mcp__library_stats`. It must match `[A-Za-z0-9_-]{1,32}` and be
  unique across live plugin instances.
- Use an absolute `command`. stdio children receive a scrubbed ambient
  environment, so a bare `python` may not resolve. On Windows, `command` may
  instead point to `<project>\.venv\Scripts\openalex-mcp.exe`; omit `args` in
  that form.
- `env` is merged into the scrubbed child environment.
- `toolCallTimeoutMs` is a client-side per-call timeout. The plugin default is
  60 seconds; this example allows five minutes for larger local queries and
  graph analysis.
- Local processes require `transport: stdio`. DSH also supports
  `streamable-http`, which uses `url` and optional `headers` instead of
  `command` and `args`; this server's documented launch mode is stdio.

DSH hot-applies `cordis.patch.yml`: adding or changing the entry starts or
reconnects the server, and a successful tool-list synchronization refreshes
registered tools without restarting the host. Verify the resolved profile and
then make a real tool call:

```bash
dsh --profile web --dump-config
```

Call `mcp__literature-mcp__library_stats` (or another `mcp__literature-mcp__*` tool) in the
matching profile. Use `--profile headless` instead when that is the configured
profile.

## 6. Troubleshooting

### The executable cannot be found or the server exits immediately

- Confirm that `<VENV_PY>` was replaced with an existing absolute file path.
- Run `"<VENV_PY>" -m openalex_mcp.registry` directly in a shell.
- On PowerShell, use the call operator: `& "<VENV_PY>" -m openalex_mcp.registry`.
- Avoid a bare `python`; GUI clients and DSH may not inherit the same `PATH` as
  an interactive terminal.

### `No module named openalex_mcp` or a dependency import error

The client is not using the project-local uv interpreter. From the project root,
run `uv sync --managed-python --python 3.12`, then verify with
`uv run --locked python -c "import openalex_mcp; print(openalex_mcp.__file__)"`.

### A Windows JSON configuration does not parse

Use forward slashes in paths, or double every backslash. A path such as
`C:\temp\new` is unsafe in raw JSON because `\t` and `\n` are escapes; write
`C:/temp/new` or `C:\\temp\\new`. Also remove trailing commas because strict
`.json` files do not accept them.

### Codex reports an invalid or duplicate TOML table

Find the existing `[mcp_servers.literature-mcp]` and
`[mcp_servers.literature-mcp.env]` blocks and edit them in place. Do not append a
second table with the same header. Restart the Codex session after manual TOML
edits.

### The server was added but the client does not show its tools

- Start a new Kimi or Codex session after editing its configuration.
- In Claude Code, inspect `claude mcp get literature-mcp`, `claude mcp list`, and
  `/mcp`.
- In Cherry Studio, enable the server and bind it to the active Agent.
- In DSH, make sure the patch belongs to the profile that is currently running.
- Check for a project-scoped entry overriding a same-named user entry.

### Startup, discovery, or tool calls time out

First run the stdio preflight command to separate server startup from client
configuration. Then raise the client-specific timeout in the documented unit:

- Kimi: `startupTimeoutMs` and `toolTimeoutMs`, in milliseconds.
- Codex: `startup_timeout_sec` and `tool_timeout_sec`, in seconds.
- OpenCode: `timeout`, in milliseconds, for connection/tool discovery.
- DSH: `toolCallTimeoutMs`, in milliseconds, for each tool call.

Large SQL responses and graph analysis are intentionally not capped by the
server. `graph_analyze` uses approximate betweenness and Louvain communities
for large graphs, but you should still prefer SQL `LIMIT`, bounded `substr()`,
and focused graph work-ID sets before increasing timeouts further.

### `.env` is not loaded or a key still appears missing

- Confirm `OPENALEX_MCP_ENV_FILE` is present in the client's child-process
  environment block, not only inside `.env`.
- Make the value an absolute path to an existing file.
- Check that the file is really named `.env`, not `.env.txt` on Windows.
- Remove placeholder values such as `your-api-key-here` when enabling a
  provider.
- Restart or reconnect the MCP server after changing `.env`.
- Remember that an already-defined process environment value takes precedence
  over the corresponding value in `.env`.

### Output is garbled or the MCP handshake fails

Keep `PYTHONIOENCODING=utf-8` and `PYTHONUTF8=1`, particularly on Windows. Keep
`FASTMCP_SHOW_SERVER_BANNER=false`, and ensure no wrapper writes banners or
status messages to stdout before MCP protocol traffic. Capture diagnostics from
stderr or the client's MCP log instead.
