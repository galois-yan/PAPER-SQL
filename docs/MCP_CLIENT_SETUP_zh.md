# MCP 客户端配置指南

本文说明如何安装并通过 stdio 将 `openalex-mcp` 接入以下六类客户端：

- Claude Code
- Kimi Code
- Codex CLI
- OpenCode
- Cherry Studio
- DeepSeek Harness

项目概览、工具参数和数据输出位置见[中文 README](../README_zh.md)。

## 1. 安装与路径约定

要求 Python 3.10 或更高版本。MCP 服务端统一使用 uv 管理的项目本地 `.venv`，
不再使用 Conda 环境。先进入本项目根目录；若未安装 uv，请参阅
[uv 安装说明](https://docs.astral.sh/uv/getting-started/installation/)。

### 1.1 构建项目本地 uv 环境

```powershell
uv python install --managed-python 3.12
uv python pin 3.12
uv sync --managed-python --python 3.12
```

上述命令会下载 uv 自管的 CPython、生成 `.python-version`、项目本地 `.venv`
和 `uv.lock`，并以 editable 方式安装本项目。不要再执行
`python -m venv`、`pip install -e .` 或把客户端指向 Conda 环境。

不需要激活虚拟环境；日常验证可使用 `uv run`，而 MCP 客户端直接填写解释器
绝对路径：

- Windows：`<project>\.venv\Scripts\python.exe`
- Linux/macOS：`<project>/.venv/bin/python`

### 1.2 替换示例占位符

本文配置块使用两个占位符：

| 占位符 | 替换内容 |
|---|---|
| `<project>` | 本项目根目录的绝对路径，即包含 `pyproject.toml` 的目录 |
| `<VENV_PY>` | 由 uv 在项目本地 `.venv` 中创建的 Python 解释器绝对路径 |

以 Windows 项目目录 `C:\Users\alice\literature-mcp` 为例：

- `<project>` 替换为 `C:\Users\alice\literature-mcp`
- `<VENV_PY>` 替换为 `C:\Users\alice\literature-mcp\.venv\Scripts\python.exe`

以 Linux/macOS 项目目录 `/home/alice/literature-mcp` 为例：

- `<project>` 替换为 `/home/alice/literature-mcp`
- `<VENV_PY>` 替换为 `/home/alice/literature-mcp/.venv/bin/python`

即 `<VENV_PY>` 是 `<project>` 加上 `.venv` 内解释器的相对路径（Windows 为 `.venv\Scripts\python.exe`，Linux/macOS 为 `.venv/bin/python`）。

在 JSON 中 `\` 是转义符（未转义的 `\t`、`\n` 会被解析成控制字符），Windows 路径有两种等价写法。全部使用正斜杠：

```json
"C:/Users/alice/literature-mcp/.venv/Scripts/python.exe"
```

或把每个 `\` 写成 `\\`：

```json
"C:\\Users\\alice\\literature-mcp\\.venv\\Scripts\\python.exe"
```

两种写法表示同一条路径。

TOML 示例使用单引号字面量，YAML 示例也使用单引号；按示例填写时，Windows 反斜杠无需再次转义。所有客户端的 `command` 都应使用绝对路径，不要依赖客户端进程恰好能从 `PATH` 找到 `python`。

## 2. 配置 `.env`

复制项目根目录的 `.env.example` 为 `.env`：

```powershell
Copy-Item .env.example .env
```

Linux/macOS：

```bash
cp .env.example .env
```

按需填写全部变量：

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

| 变量 | 是否必需 | 用途与默认行为 |
|---|---|---|
| `OPENALEX_API_KEY` | 远端 OpenAlex 功能需要 | 用于关键词、服务端语义和 ID 检索，以及 OpenAlex Content API。可在 [OpenAlex API 设置页](https://openalex.org/settings/api)获取免费 key。 |
| `OPENALEX_EMAIL` | 推荐 | 随 OpenAlex 和 Crossref 请求发送联系邮箱，用于礼貌池和服务商联系。 |
| `ZHIPUAI_API_KEY` | 可选 | 用于 Embedding-3 文献向量、`library_generate_embeddings` 和 `library_query` 的本地语义向量查询。获取地址见 [bigmodel.cn](https://bigmodel.cn)。 |
| `ELSEVIER_API_KEY` | 可选 | 用于 Elsevier Abstract Retrieval API、Scopus 回退和 `fetch_elsevier_abstracts`。 |
| `ELSEVIER_INST_TOKEN` | 可选 | 机构订阅要求时，随 Elsevier 请求发送机构 token。 |
| `BACKFILL_ABSTRACTS` | 可选 | 是否在检索结果入库前自动回填缺失摘要；默认 `true`。`false`、`0`、`no` 或 `off` 可关闭。 |
| `BACKFILL_MAX_TARGETS` | 可选 | 单批缺摘要论文超过此数量时，从本批 Elsevier 期刊文献中按被引数排序取前 K 篇回填；若没有 Elsevier 期刊目标才跳过整批。默认 `25`，设为 `0` 表示不设置该批量阈值。 |
| `BACKFILL_CONCURRENCY` | 可选 | DOI 摘要回填的并发管线数；默认 `4`，实现会限制在 `1` 到 `8`。 |

不同密钥配置对应的能力：

| 配置 | 可用能力 |
|---|---|
| 无远端 API key | 对已有本地文献库执行 SQL、统计、BibTeX 导出和引文分析 |
| `OPENALEX_API_KEY` | OpenAlex 关键词、服务端语义和 ID 检索、入库、实体补全及 Content API 下载 |
| 加上 `ZHIPUAI_API_KEY` | 本地语义向量生成和向量检索 |
| 加上 `ELSEVIER_API_KEY` | Elsevier/Scopus 摘要获取，以及自动回填中对应的摘要来源 |

自动回填开启时会先使用 OpenAlex 已带的摘要，再尝试 Crossref；配置 Elsevier key 后才会继续尝试 Elsevier 和 Scopus。某个回填来源失败不会丢弃论文。

`.env` 已在 `.gitignore` 中排除，不要把真实 key 写入 README、项目级客户端配置或版本控制。若操作系统或客户端已设置同名环境变量，`python-dotenv` 默认不会用 `.env` 覆盖它。

## 3. stdio 启动与环境传递

所有客户端最终都运行同一个服务器入口：

```bash
<VENV_PY> -m openalex_mcp.registry
```

服务器启动时先读取进程环境变量 `OPENALEX_MCP_ENV_FILE`。若该变量非空，则把它指定的文件传给 `python-dotenv`；因此客户端配置应把它设为 `<project>/.env` 的绝对路径。这样不依赖客户端采用哪个工作目录，也不需要把 API key 逐个复制到客户端配置。

本文所有客户端示例还传入以下进程环境变量：

| 变量 | 作用 |
|---|---|
| `OPENALEX_MCP_ENV_FILE` | 指定项目 `.env` 的绝对路径 |
| `PYTHONIOENCODING=utf-8` | 让 Python 标准输入、输出和错误流使用 UTF-8，减少 Windows 日志乱码 |
| `PYTHONUTF8=1` | 启用 Python UTF-8 模式，统一默认文本编码 |
| `FASTMCP_SHOW_SERVER_BANNER=false` | 隐藏 FastMCP 启动横幅；只影响显示，不改变工具注册或协议行为 |

### 3.1 启动前预检

先确认解释器确实能导入项目：

```powershell
& "<VENV_PY>" -c "import openalex_mcp; print('openalex_mcp import OK')"
```

Linux/macOS：

```bash
"<VENV_PY>" -c "import openalex_mcp; print('openalex_mcp import OK')"
```

再手工启动服务器：

```powershell
$env:OPENALEX_MCP_ENV_FILE = "<project>/.env"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:FASTMCP_SHOW_SERVER_BANNER = "false"
& "<VENV_PY>" -m openalex_mcp.registry
```

Linux/macOS：

```bash
OPENALEX_MCP_ENV_FILE="<project>/.env" \
PYTHONIOENCODING=utf-8 \
PYTHONUTF8=1 \
FASTMCP_SHOW_SERVER_BANNER=false \
"<VENV_PY>" -m openalex_mcp.registry
```

成功启动后，进程会等待 MCP 客户端从 stdin 发送协议消息；终端看起来没有继续输出是正常现象。缺少可选 key 时会在 stderr 显示提示，但本地工具仍可使用。预检完成后按 `Ctrl+C` 退出。

### 3.2 客户端速查

| 客户端 | 配置位置 | 服务器字段 | 环境变量字段 | 修改后动作 |
|---|---|---|---|---|
| Claude Code | 用户级 `~/.claude.json`；项目级 `.mcp.json` | `mcpServers` | `env` | 用 `/mcp` 复核 |
| Kimi Code | 用户级 `~/.kimi-code/mcp.json`；项目级 `.kimi-code/mcp.json` | `mcpServers` | `env` | 新建会话 |
| Codex CLI | 用户级 `~/.codex/config.toml`；可信项目级 `.codex/config.toml` | `[mcp_servers.*]` | `[mcp_servers.*.env]` | 新建会话 |
| OpenCode | 全局 `~/.config/opencode/opencode.json`；项目级 `opencode.json` | `mcp` | `environment` | 重新加载客户端后检查列表 |
| Cherry Studio | 设置 → MCP → MCP 服务器 → 添加 | `mcpServers` | `env` | 启用服务器；Agent 中另行绑定 |
| DeepSeek Harness | `~/.dsh/profiles/<profile>/cordis.patch.yml` | cordis patch `insert` | `config.env` | patch 热加载后检查工具 |

## 4. Claude Code

参考 [Claude Code MCP 文档](https://code.claude.com/docs/en/mcp)，以下命令注册用户级 stdio server：

```bash
claude mcp add --env "OPENALEX_MCP_ENV_FILE=<project>/.env" --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 --env FASTMCP_SHOW_SERVER_BANNER=false --transport stdio --scope user literature-mcp -- "<VENV_PY>" -m openalex_mcp.registry
```

注意：

- `--` 是 Claude Code 选项与服务器启动命令的分隔符。分隔符之后的 `-m` 属于 Python，不会被 Claude Code 当作自身选项解析。
- `--env` 是可重复选项。示例把 Claude Code 自身的 `--transport`、`--scope`、服务器名和分隔符放在环境变量参数之后。
- `--scope user` 让配置对所有项目可用，保存到 `~/.claude.json`。
- 只有需要在仓库中共享 MCP 配置时才使用 `--scope project`；它会写入项目根目录 `.mcp.json`，Claude Code 在使用项目级服务器前会要求确认工作区信任。不要把真实 API key 写进这个可共享文件。

检查保存结果和连接状态：

```bash
claude mcp get literature-mcp
claude mcp list
```

进入 Claude Code 后还可输入 `/mcp` 复核。端到端验证时，请让客户端调用不依赖远端 key 的 `library_stats`，并检查是否返回本地库统计。

## 5. Kimi Code

Kimi Code 的用户级 MCP 配置位于 `~/.kimi-code/mcp.json`，设置 `KIMI_CODE_HOME` 时则位于 `$KIMI_CODE_HOME/mcp.json`；项目级配置位于项目根目录 `.kimi-code/mcp.json`。同名服务器同时存在时，项目级条目优先。参考 [Kimi Code MCP 文档](https://moonshotai.github.io/kimi-code/en/customization/mcp.html)。

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

`startupTimeoutMs` 和 `toolTimeoutMs` 的单位均为毫秒。示例分别给服务器启动/工具发现 120 秒，给单次工具调用 300 秒。

Kimi 不会把会话启动后才新增的服务器注册进该会话；编辑配置后应新建会话。用 `/mcp` 查看连接状态，也可用 `/mcp-config` 交互式管理服务器。`kimi doctor` 校验的是 `config.toml` 和 `tui.toml`，不能代替 MCP 连通性检查。

端到端验证：

```bash
kimi -p "Call the library_stats tool from the literature-mcp MCP server, then reply with ONLY the total number of works in the library as a plain number."
```

## 6. Codex CLI

Codex 的用户级 MCP 配置位于 `~/.codex/config.toml`，可信项目也可以使用 `.codex/config.toml`。参考 [Codex MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)。

下面的命令创建基础用户级条目：

```bash
codex mcp add literature-mcp --env "OPENALEX_MCP_ENV_FILE=<project>/.env" --env PYTHONIOENCODING=utf-8 --env PYTHONUTF8=1 --env FASTMCP_SHOW_SERVER_BANNER=false -- "<VENV_PY>" -m openalex_mcp.registry
```

若需要显式工作目录和更长的启动/工具超时，请用下面内容**替换**命令生成的 `literature-mcp` 配置块：

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

`startup_timeout_sec` 和 `tool_timeout_sec` 的单位是秒。TOML 不允许重复表；不要在已有 `[mcp_servers.literature-mcp]` 后再追加同名表，否则整个配置可能无法解析。

检查保存的配置：

```bash
codex mcp list
codex mcp get literature-mcp --json
```

手工修改配置后新建 Codex 会话，并在 TUI 中输入 `/mcp`，确认 `literature-mcp` 已启用。端到端验证时让 Codex 调用 `library_stats`，不要只以“配置条目存在”作为连接成功的依据。

## 7. OpenCode

把下面的 `mcp` 配置加入全局 `~/.config/opencode/opencode.json`。Windows 对应路径通常是 `C:\Users\<you>\.config\opencode\opencode.json`。也可以把它加入项目级 `opencode.json`；OpenCode 还接受 `.jsonc` 后缀。参考官方 [MCP server](https://opencode.ai/docs/mcp-servers/) 和[配置文件](https://opencode.ai/docs/config/)文档。

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

OpenCode 的 `command` 是包含可执行文件和参数的数组，不要拆成 `command` 字符串加 `args`。`timeout` 的单位是毫秒，用于 MCP 连接和工具发现；它不是服务器内部的查询规模限制。

验证配置与真实工具调用：

```bash
opencode mcp list
opencode run "Call the library_stats tool from the literature-mcp MCP server, then reply with ONLY the total number of works in the library as a plain number."
```

## 8. Cherry Studio

参考 Cherry Studio 当前的 [MCP 教程](https://docs.cherryai.com.cn/advanced-basic/extensions/mcp)，进入 **设置 → MCP → MCP 服务器 → 添加**，选择 JSON 导入并使用以下配置：

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

必须使用项目本地 uv `.venv` 的解释器绝对路径，不能使用 Conda 或全局解释器。导入后启用服务器，等待状态正常，再进入详情检查工具列表。

Cherry Studio 的服务器“已启用”不等于每个 Agent 都能使用它。若使用 Agent 工作流，还需在 **工作 → Agent → 编辑 → MCP** 中绑定 `literature-mcp`。端到端验证时在已绑定该服务器的 Agent 中调用 `library_stats`。

## 9. DeepSeek Harness

[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 使用官方 [`@deepseek-ai/dsh-mcp-client`](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/README.md) 插件。每个 MCP server 对应一个插件实例，写入当前 profile 的用户 patch：Web GUI 通常使用 `web`，CLI 通常使用 `headless`。

- `~/.dsh/profiles/web/cordis.patch.yml`
- `~/.dsh/profiles/headless/cordis.patch.yml`

`~/.dsh` 是默认 Harness home；设置 `DSH_HOME` 后路径跟随该变量。向相应 `cordis.patch.yml` 追加以下 stdio `insert` 条目：

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

字段和运行行为：

- `serverName` 成为工具命名空间，因此工具名形如 `mcp__literature-mcp__search_keyword` 和 `mcp__literature-mcp__library_query`。它必须匹配 `[A-Za-z0-9_-]{1,32}`，并在存活的插件实例之间保持唯一。
- `command` 应使用绝对路径。stdio 子进程收到的是清理过的环境，裸 `python` 可能无法从 `PATH` 解析。
- Windows 也可以把 `command` 指向 `<project>\.venv\Scripts\openalex-mcp.exe`；使用该 console script 时省略 `args`。
- `env` 会叠加到清理后的子进程环境。
- `toolCallTimeoutMs` 是客户端侧单次调用超时；插件默认 60 秒，示例改为 300000 毫秒，以容纳较大的本地查询和图分析。
- 本地进程使用 `transport: stdio`。插件也支持 `streamable-http`，但那需要可访问的 HTTP MCP 服务和相应 `url`/`headers`；本项目的标准入口是 stdio。

DSH 会热加载 `cordis.patch.yml`：添加或修改条目会启动或重连服务器，工具列表同步后动态刷新，无需重启 host。先检查 patch 合成结果：

```bash
dsh --profile web --dump-config
```

使用 `headless` profile 时把命令中的 `web` 替换为 `headless`。随后调用 `mcp__literature-mcp__library_stats` 或其他 `mcp__literature-mcp__*` 工具进行端到端验证。

## 10. 常见问题排查

### 10.1 找不到解释器或 `No module named openalex_mcp`

客户端使用的不是安装项目时的 Python，或者路径仍包含未替换的 `<VENV_PY>`。

1. 在终端直接运行本文“启动前预检”的 import 命令。
2. 在项目根目录执行 `uv run --locked python -c "import openalex_mcp; print(openalex_mcp.__file__)"`。
3. 若环境缺失或锁文件变更，执行 `uv sync --managed-python --python 3.12`。
4. 确认客户端配置使用绝对路径，而不是裸 `python`。

无需激活 `.venv`；MCP 客户端直接使用 `.venv\Scripts\python.exe`，终端命令使用 `uv run` 即可。

### 10.2 Windows 路径或配置文件解析失败

- JSON 字符串中的单个反斜杠可能被当作转义序列。改用正斜杠，或把 `\` 写成 `\\`。
- TOML/YAML 按本文示例使用单引号可保留反斜杠字面值。
- 路径含空格时，不要手工把引号写进 JSON 的 `command` 值；JSON 字符串本身已经界定整个路径。
- 确认 `<project>` 指向包含 `pyproject.toml` 的目录，而不是其上一级目录。

### 10.3 Codex 报 TOML 重复表

`codex mcp add` 已经创建 `[mcp_servers.literature-mcp]` 时，应编辑或整体替换该配置块。不要再次追加第二个 `[mcp_servers.literature-mcp]` 或 `[mcp_servers.literature-mcp.env]`。

### 10.4 修改配置后仍看不到服务器或工具

- Kimi Code 和 Codex CLI：新建会话；旧会话不会自动获得新注册的服务器。
- Claude Code：运行 `claude mcp list` 并在会话中输入 `/mcp`。
- OpenCode：重新加载客户端后运行 `opencode mcp list`。
- Cherry Studio：确认服务器已启用；Agent 工作流还必须单独绑定该 MCP。
- DeepSeek Harness：用 `--dump-config` 核对所改 profile，确认 patch 热加载后工具命名空间为 `mcp__literature-mcp__*`。

### 10.5 服务器启动或工具调用超时

先用终端预检区分“解释器/导入失败”和“客户端超时”。首次启动、较大的 SQL 查询、向量生成和图分析可能需要更长时间。按客户端支持的字段调整：

- Kimi Code：`startupTimeoutMs`、`toolTimeoutMs`，单位毫秒。
- Codex CLI：`startup_timeout_sec`、`tool_timeout_sec`，单位秒。
- OpenCode：`timeout`，单位毫秒，主要用于连接和工具发现。
- DeepSeek Harness：`toolCallTimeoutMs`，单位毫秒。

超时只决定客户端等待多久，不会给 SQL、响应体或图分析增加服务器端硬限制。`graph_analyze` 会在大图上自动使用近似中介中心度和 Louvain 社区划分，但查询大量数据时仍应主动使用 SQL `LIMIT`、`substr()` 和聚焦的 work ID 集合。

### 10.6 `.env` 没有加载或 key 看似无效

1. 确认 `OPENALEX_MCP_ENV_FILE` 是 `.env` 文件的绝对路径，而不是目录。
2. 确认该文件存在、当前账户可读，且不是误命名为 `.env.txt`。
3. 不要保留 `your-api-key-here` 之类占位值。
4. 若操作系统或客户端另行设置了同名变量，它会优先于 `.env`；删除或修正旧值后重启客户端。
5. 查看客户端的 MCP stderr 日志。服务器会提示哪些可选 key 未配置，但不要把包含真实 key 的完整环境输出贴到公开 issue。

`library_stats`、本地只读 SQL、BibTeX 导出和引文分析不依赖 `OPENALEX_API_KEY`。因此可先用 `library_stats` 验证 MCP 连接，再单独排查远端服务的 key 或网络问题。

### 10.7 服务器连接后立即断开

- 再次执行手工 stdio 启动，查看 stderr 中的 Python traceback 或依赖错误。
- 确保客户端把 `-m` 和 `openalex_mcp.registry` 作为两个参数传递；OpenCode 则放在同一个 `command` 数组中。
- 不要把普通调试输出写入服务器 stdout。`FASTMCP_SHOW_SERVER_BANNER=false` 可隐藏横幅，但 Python 警告和配置提示正常写到 stderr，不会占用 MCP 协议流。
- 检查配置格式是否符合对应客户端要求，不要在 JSON、TOML 和 YAML 之间照搬字段名。

## 11. 最小验收清单

完成配置后依次确认：

1. `<VENV_PY>` 和 `<project>` 已全部替换成绝对路径。
2. `"<VENV_PY>" -c "import openalex_mcp"` 成功。
3. 手工启动 stdio 服务器后能够保持运行，按 `Ctrl+C` 正常退出。
4. 客户端列表中存在并启用了 `literature-mcp`。
5. 客户端能够发现 16 个工具。
6. `library_stats` 能返回本地库统计。
7. 配置相应 key 后，再分别验证 OpenAlex 检索、本地向量或 Elsevier 摘要功能。
