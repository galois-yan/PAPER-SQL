# Paper-SQL MCP工具

<p align="center">
  <img src="./logo.png" alt="Paper-SQL logo" width="160" />
</p>

> 面向 MCP 客户端的 OpenAlex 学术文献检索与研究库。
>
> English documentation：[README.md](./README.md)

## 功能概览

从「检索 → 入库 → 精读 → 综述」全流程，把论文发现做得更准、更可靠、更深入：

- **多源检索，覆盖全球学术文献。** 直连 OpenAlex（2.5 亿+ 论文），支持关键词、语义、DOI 和 OpenAlex ID 检索，并可通过 Crossref、Elsevier、Scopus 补齐缺失摘要，文献数据更完整。
- **本地 SQL 建库，从源头减少幻觉。** 检索结果自动写入本地 SQLite，模型用只读 SQL 在真实入库记录上筛选、聚合，不再凭空编造文献，论文发现质量更高、更可追溯。
- **多尺度论文分析：标题、关键词、摘要到 PDF 全文。** 既能按标题/关键词/摘要快速过滤，也能按需下载开放获取 PDF、抽取正文全文，支撑深度阅读与逐句取证。
- **引文网络知识图谱，前后追溯检索。** 对选定论文构建引文图，用 PageRank、中心性和社区发现分析，一句话前后追溯参考文献与施引文献。
- **BibTeX 导出 + LaTeX 综述报告。** 一键导出标准 BibTeX，并配合内置综述提示词，让外层模型基于真实文献生成带 `\cite` 引用的 LaTeX 文献调研报告。

## 演示视频

【Paper-SQL】用MCP让智能体查真文献：检索→本地入库→引证分析→PDF下载精读→综述生成 全流程演示

https://www.bilibili.com/video/BV1Vntt66Ejc/?share_source=copy_web&vd_source=601c92cd729f5d300329945382cb791f

## 设计概要

1. **检索即入库。** 每次远端检索结果都会 UPSERT 到本地 SQLite。重复检索只刷新记录，不会产生重复行。
2. **SQL 是文献库接口。** 只读工具 `library_query` 直接暴露 schema，MCP 客户端可以自行探索、筛选、聚合和选择记录，不需要堆叠一组固定查询 API。
3. **向量留在 SQLite。** 向量以 float32 BLOB 存储在 `works.vec`，通过 sqlite-vec 的 `vec_distance_cosine()` 比较，无需单独部署向量数据库。
4. **读操作保持可用。** SQLite WAL 模式允许读写并行推进；只有写操作之间使用 `asyncio.Lock` 串行化。

详细文档：

- [MCP 客户端完整配置指南](./docs/MCP_CLIENT_SETUP_zh.md)：安装、环境变量、六类客户端配置、验证与排障。
- [16 个 Tool 的工作原理](./docs/TOOL_INTERNALS_zh.md)：共享执行链、算法、状态变化和安全边界。

## 项目结构

```
openalex_mcp/
├── registry.py   # FastMCP 实例、工具注册和 stdio 入口
├── common.py     # 共享的零依赖工具函数
├── remote/       # OpenAlex、智谱 AI、Crossref、Elsevier、Scopus 客户端
├── local/        # SQLite 文献库、sqlite-vec、UPSERT 和 BibTeX 导出
└── graph/        # 引文网络分析和 vis.js 可视化
```

## 安装

要求 Python 3.10 或更高版本。

### 1. 用 uv 构建项目内环境

本项目的 MCP 服务端统一使用 uv 管理的项目本地 `.venv`，无需激活 Conda 即可
由 `uv.lock` 固定依赖；工具本身的网络和算法耗时仍取决于实际调用。

若尚未安装 uv，请先按 [uv 安装说明](https://docs.astral.sh/uv/getting-started/installation/)
安装。然后在项目根目录执行：

```powershell
uv python install --managed-python 3.12
uv python pin 3.12
uv sync --managed-python --python 3.12
```

这会生成 `.python-version`、项目本地 `.venv` 和 `uv.lock`，并以 editable
方式安装当前项目及其依赖。日常命令用 `uv run` 执行，例如：

```powershell
uv run python -m unittest discover -s tests -v
```

无需激活环境；后文所有 `<VENV_PY>` 都指向 `.venv` 内的解释器。

### 2. 配置 API 密钥

复制 `.env.example` 为 `.env`，按需填写：

```dotenv
OPENALEX_API_KEY=your-api-key-here          # OpenAlex 请求必需
OPENALEX_EMAIL=you@example.org              # 推荐，用于服务商联系和礼貌池
ZHIPUAI_API_KEY=your-zhipuai-key-here       # 可选：本地语义向量
ELSEVIER_API_KEY=your-elsevier-api-key-here # 可选：摘要获取和回填
ELSEVIER_INST_TOKEN=your-elsevier-inst-token-here
BACKFILL_ABSTRACTS=true
BACKFILL_MAX_TARGETS=25
BACKFILL_CONCURRENCY=4
```

可在 [OpenAlex API 设置页](https://openalex.org/settings/api) 获取免费 API key；智谱 AI 见 [bigmodel.cn](https://bigmodel.cn)。

不同配置对应的能力：

| 配置 | 可用能力 |
|---|---|
| 无远端 API key | 对已有本地文献库执行 SQL、统计、BibTeX 导出和引文分析 |
| `OPENALEX_API_KEY` | OpenAlex 关键词、服务端语义和 ID 检索、入库、实体补全及 Content API 下载；已包含 Crossref 摘要回填，无需额外 key |
| 加上 `ZHIPUAI_API_KEY` | 本地语义向量和向量检索 |
| 加上 `ELSEVIER_API_KEY` | 为摘要回填增加 Elsevier 和 Scopus 两级，并启用 `fetch_elsevier_abstracts` 工具。一个 key 同时覆盖 Elsevier 和 Scopus，没有单独的 Scopus key。 |

自动回填按 **Crossref → Elsevier → Scopus** 顺序尝试，命中即停。不配置
Elsevier key 时只有 Crossref 一级生效，因此结果是回填覆盖率下降，而不是功能
关闭。任何一级失败都不会丢弃论文。

三个 `BACKFILL_*` 变量的行为：

| 变量 | 默认值 | 行为 |
|---|---|---|
| `BACKFILL_ABSTRACTS` | 不设置时为开启 | 只有 `1`、`true`、`yes`、`on` 视为真，其余值都会关闭回填。 |
| `BACKFILL_MAX_TARGETS` | `25` | 批量阈值见下文说明；设为 `0` 表示完全不限制。 |
| `BACKFILL_CONCURRENCY` | `4` | 会被静默限制在 1–8，配置成 `32` 实际按 `8` 运行。 |

两个数值变量填成非整数时会静默回退到默认值，不会报错。

> **Elsevier API 需在校园网环境下使用。** Elsevier 摘要获取与自动摘要回填只有在校园网（或校园网 VPN）下才能正常工作；在校外，`ELSEVIER_API_KEY` / `ELSEVIER_INST_TOKEN` 会返回 401/403，导致基于 Elsevier 的回填取不到摘要。Crossref 回填不受影响。
> 当单批缺摘要数量超过 `BACKFILL_MAX_TARGETS` 时，自动回填会从本批 Elsevier 期刊文献中按被引数排序取前 K 篇优先回填，而不是直接整批跳过。

## 接入 MCP 客户端

> **完整教程：** [MCP 客户端配置指南](./docs/MCP_CLIENT_SETUP_zh.md)提供可直接使用的配置块、各客户端差异、端到端验证命令和常见问题排查。README 仅保留通用启动方式与配置位置速查。

所有客户端都通过同一条 stdio 命令启动服务器：

```bash
<VENV_PY> -m openalex_mcp.registry
```

`<VENV_PY>` 必须是已安装本项目的 Python 解释器绝对路径。若采用上文的项目内 `.venv`，路径为：

- Windows：`<project>\.venv\Scripts\python.exe`
- Linux / macOS：`<project>/.venv/bin/python`

将 `<VENV_PY>` 和 `<project>` 替换为本机绝对路径。`<VENV_PY>` 必须指向上文
由 uv 创建的项目本地 `.venv`，不要指向 Conda 环境。Windows 的 JSON 路径可使用
正斜杠（`C:/path/to/python.exe`），也可把每个反斜杠转义成双反斜杠（`C:\\path\\to\\python.exe`）。

`uv sync` 还会在解释器旁安装 `openalex-mcp` 命令行入口（Windows 为
`.venv\Scripts\openalex-mcp.exe`，其他平台为 `.venv/bin/openalex-mcp`）。它不带
参数即可启动同一个服务器，客户端可以用它替代"解释器 + `-m openalex_mcp.registry`"。
本文和配置指南统一使用 `-m` 写法，因为解释器配错时它会直接报错，便于排查。

API key 继续保存在项目 `.env` 中，客户端配置只通过 `OPENALEX_MCP_ENV_FILE` 传递该文件的绝对路径。额外的 `PYTHON*` 设置用于避免 Windows 日志乱码；`FASTMCP_SHOW_SERVER_BANNER=false` 只负责隐藏 FastMCP 启动横幅。

接入客户端前，先确认服务器能够启动并等待 stdio 输入。按当前 shell 选择命令：

```powershell
& "<VENV_PY>" -m openalex_mcp.registry
```

```bash
"<VENV_PY>" -m openalex_mcp.registry
```

单独运行时按 Ctrl+C 退出。

### 客户端配置速查

| 客户端 | 配置位置 | 服务器字段 | 环境变量字段 |
|---|---|---|---|
| Claude Code | 用户级 `~/.claude.json`；项目级 `.mcp.json` | `mcpServers` | `env` |
| Kimi Code | 用户级 `~/.kimi-code/mcp.json`；项目级 `.kimi-code/mcp.json` | `mcpServers` | `env` |
| Codex CLI | 用户级 `~/.codex/config.toml`；可信项目级 `.codex/config.toml` | `[mcp_servers.*]` | `[mcp_servers.*.env]` |
| OpenCode | 全局 `~/.config/opencode/opencode.json`；项目级 `opencode.json` | `mcp` | `environment` |
| Cherry Studio | 设置 → MCP → MCP 服务器 → 添加 | `mcpServers` | `env` |
| DeepSeek Harness | `~/.dsh/profiles/<profile>/cordis.patch.yml` | cordis patch `insert`（插件 `@deepseek-ai/dsh-mcp-client`） | `config.env` |

### Claude Code

通过 `claude mcp add` 注册，并保留 `--` 作为 Claude Code 选项与 Python 启动命令的分隔符。用户级/项目级作用域、完整命令和验证方法见[配置指南](./docs/MCP_CLIENT_SETUP_zh.md#4-claude-code)。

### Kimi Code

使用 `mcpServers` JSON 配置；同名项目级条目优先，修改配置后需新建会话。完整配置、超时单位和验证命令见[配置指南](./docs/MCP_CLIENT_SETUP_zh.md#5-kimi-code)。

### Codex CLI

使用 `codex mcp add` 或 `[mcp_servers.literature-mcp]` TOML 配置；已有同名表时必须替换，不能重复追加。完整命令、`cwd`、超时和验证方法见[配置指南](./docs/MCP_CLIENT_SETUP_zh.md#6-codex-cli)。

### OpenCode

本地 MCP 的 `command` 是包含解释器和参数的数组，环境变量字段为 `environment`。完整 JSON/JSONC 示例、超时语义和验证命令见[配置指南](./docs/MCP_CLIENT_SETUP_zh.md#7-opencode)。

### Cherry Studio

在 MCP 设置中导入 JSON 并启用服务器；Agent 工作流还需在 Agent 编辑页绑定该 MCP。完整导入内容和验证步骤见[配置指南](./docs/MCP_CLIENT_SETUP_zh.md#8-cherry-studio)。

### DeepSeek Harness (DSH)

通过 `@deepseek-ai/dsh-mcp-client` 在对应 profile 的 `cordis.patch.yml` 中插入 stdio 插件实例。完整 YAML、命名空间、热加载和验证说明见[配置指南](./docs/MCP_CLIENT_SETUP_zh.md#9-deepseek-harness)。

## 工具

### 远端检索与入库

| 工具 | 输入 | 返回 | 适用场景 |
|---|---|---|---|
| `search_keyword` | 可选 `query`（支持布尔、短语、通配符）加下方过滤条件与公共选项 | 命中预览和入库统计 | 按明确关键词或主题检索 |
| `search_semantic` | 必填自然语言 `query`，加同一组过滤条件与公共选项 | OpenAlex 服务端语义匹配和入库统计 | 关键词不确定，按含义找论文 |
| `search_ids` | 逗号分隔的 OpenAlex ID/URL 或 DOI 值/URL | 摘要和入库统计 | 拉取已知论文 |
| `autocomplete` | 实体类型和名称片段 | 最多 10 个候选及其 ID | 将作者、期刊、机构、出版商或资助方解析为 ID |

`search_keyword` 的 `query` 可以留空：留空时按过滤条件列出全部命中文献。
`search_semantic` 的 `query` 必填。`search_ids` 既不接受过滤条件，也不分页。

#### 过滤条件

`search_keyword` 和 `search_semantic` 接受同一组过滤条件；名称先用
`autocomplete` 解析成 ID。

| 过滤条件 | 示例 | 含义 |
|---|---|---|
| `publication_year` | `>2021`、`2019` | 发表年份或范围，原样传给 OpenAlex |
| `cited_by_count` | `>50` | 被引数阈值 |
| `cites` | `W2741809807` | 引用了这些 ID 的文献，即向后追踪施引文献 |
| `cited_by` | `W2741809807` | 这些 ID 引用的文献，即其参考文献列表 |
| `related_to` | `W2741809807` | OpenAlex 标记的相关文献 |
| `source_id` | `S4210208519` | 期刊等来源 |
| `institution_id` | `I129432676` | 作者所属机构 |
| `author_id` | `A5023888391` | 作者 |
| `publisher_id` | `P4310319901` | 出版商 |
| `funder_id` | `F4320306076` | 资助方 |

#### 公共检索选项

| 选项 | 默认值 | 行为 |
|---|---|---|
| `publication_year` | `>2021` | **不传时同样生效**，因此两个检索默认只返回 2021 年之后的文献。传空字符串可取消年份过滤。`search_ids` 不接受该参数。 |
| `page` | `1` | `search_keyword` 每页 100 条；`search_semantic` 每页最多 50 条，且限速每秒 1 次请求。`search_ids` 不分页。 |
| `fetch_fulltext` | `false` | 提取正文写入 `works.fulltext`。 |
| `fulltext_limit` | `2` | 单次调用最多转换的 PDF 数；不调高时，即使一页 100 条结果也只有 2 篇拿到全文。 |
| `use_openalex_content_api` | `false` | 允许回退到收费的 OpenAlex Content API，计费说明见 `download_pdf` 一节。 |

### 本地文献库

| 工具 | 输入 | 返回 | 适用场景 |
|---|---|---|---|
| `library_query` | 只读 SQL，可选 `semantic_query` | 行对象组成的 JSON 数组；出错或无结果集时为带 `error`/`message` 的 JSON 对象 | 探索 schema、筛选论文、读取全文或执行向量检索 |
| `library_stats` | 无 | 数量、年份跨度、覆盖率、OA 率和 Top 来源/概念 | 检查文献库状态 |
| `library_generate_embeddings` | 无 | 论文总数和新增向量数 | 批量入库后补齐本地向量 |
| `library_export` | ID 或 `*`（默认 `*`）、单一 `.bib` 文件名（默认 `export.bib`）、排序和 cite key 样式 | BibTeX 路径和条数 | 导出最终参考文献 |
| `literature_review_prompt` | 无 | 基于 BibTeX 撰写综述的系统提示词 | 会话已提供 BibTeX，或外层模型已读取本地 `.bib` 文件时生成综述 |
| `library_delete` | ID 或 `*` | 删除数和剩余数 | 用户明确要求删除记录时 |
| `library_close` | 无 | 关闭确认 | 释放 SQLite 文件供其他进程访问 |

### 摘要与全文

| 工具 | 输入 | 返回 | 适用场景 |
|---|---|---|---|
| `fetch_elsevier_abstracts` | 单次最多 25 个逗号分隔值，另有 `input_type`、`update_library`、`overwrite` | 摘要、来源和回写状态 | 手动补齐缺失摘要 |
| `download_pdf` | 逗号分隔的 work ID；只有用户明确要求保存到项目目录时才设 `save_to_project=true` | 状态表和 PDF 路径 | 需要保留开放获取 PDF 文件 |

`fetch_elsevier_abstracts` 默认 `input_type="doi"`，传 `"work_id"` 则按
OpenAlex ID 查找。它**默认会把结果回写进 `works.abstract`**
（`update_library=true`）；设为 `false` 则只读取不写库。本地已有摘要默认保留，
除非 `overwrite=true`。单次调用超过 25 个值会直接报错。与自动回填不同，这个
工具只查询 Elsevier Abstract Retrieval API，不会回退到 Crossref 或 Scopus，
所以自动回填能靠 Scopus 找到的摘要，这里仍可能返回"未找到"。

`download_pdf` 使用 OpenAlex Content API，**每篇计费 0.01 美元**（免费额度约
每天 100 篇）。同名文件已存在时跳过而非重新下载，重复调用不会重复计费。检索
工具的 `use_openalex_content_api` 回退走的是同一个收费接口，因此两者都默认关闭。

全文提取通过 `search_keyword`、`search_semantic` 和 `search_ids` 的 `fetch_fulltext=true` 选项启用：系统会把可用 OA PDF 下载到临时目录，提取文本写入 `works.fulltext`，随后删除临时 PDF。单次调用最多转换 `fulltext_limit` 篇（默认 2）。默认关闭，仅在需要方法、参数、实验步骤或正文证据时启用。

### 引文网络

| 工具 | 输入 | 返回 | 适用场景 |
|---|---|---|---|
| `graph_analyze` | 逗号分隔的 work ID | PageRank、度、中介中心度和社区；大图自适应近似 | 分析选定文献集合内部的引文结构 |
| `graph_neighbors` | 逗号分隔的 work ID 和 `direction`（`in`/`out`/`both`，默认 `both`） | 选定集合内的邻居 | 查找引用或被引用的论文 |
| `graph_visualize` | work ID，可选 `output_dir` | 交互式 HTML 图路径 | 浏览可点击的引文图；超过 120 篇库内文献时自动筛选 120 个重要节点 |

三个工具都在导出子图上工作：只有传入的 ID 会成为节点，只有两端都在集合内的
引用关系才成为边。不在本地库中的 ID 会先被丢弃，然后才应用 120 节点上限。

`graph_visualize` 裁剪选集时，"重要"指被引数、PageRank、集合内被引次数和
年代的加权排序，因此高被引、结构中心和奠基性文献会被保留。`graph_analyze`
不设上限，需要完整集合时用它。

`library_export.sort` 允许 `id`、`title`、`publication_year`、
`publication_date`、`cited_by_count`、`source_name` 或 `is_oa` 中的单一字段，
后面可选 `:asc` 或 `:desc`。不传 `sort` 并非"不排序"，而是回退到
`publication_year DESC`。`cite_key_style` 可选 `author_year`（默认，如
`liu2024`）或 `openalex_id`。

向量检索使用带 `{query_vec}` 占位符的普通 SQL：

```sql
SELECT *, vec_distance_cosine(vec, {query_vec}) AS score
FROM works
WHERE vec IS NOT NULL
ORDER BY score
LIMIT 30;
```

`search_semantic` 使用 OpenAlex 的远端语义索引；`library_query` 的可选
`semantic_query` 则由智谱生成查询向量，并与本地 SQLite 中的文献向量比较。
SQL 查询和图分析不设结果规模硬限制；`graph_analyze` 会在大图上自动使用
近似中介中心度和 Louvain 社区划分。仍建议使用 SQL `LIMIT`、有边界的
`substr()` 和聚焦的 work ID 集合，避免响应过大或图计算耗时过长。

## 典型工作流

1. 用 `search_keyword` 或 `search_semantic` 检索并入库。两者不传
   `publication_year` 时都默认 `">2021"`，需要更早文献时传空字符串取消该过滤。
2. 用 `library_query` 筛选候选文献，例如：

   ```sql
   SELECT id, title, publication_year, cited_by_count
   FROM works
   WHERE cited_by_count > 50 AND publication_year > 2020
   ORDER BY cited_by_count DESC
   LIMIT 50;
   ```

3. 按需使用 `semantic_query` 和 `{query_vec}` 做语义筛选。
4. 让已配置的摘要回填流程运行，或对精选文献调用 `fetch_elsevier_abstracts`。
5. 只有需要正文证据时才启用 `fetch_fulltext=true`，并查询有边界的片段：

   ```sql
   SELECT substr(fulltext, 1, 12000)
   FROM works
   WHERE id = 'W123';
   ```

6. 将选定 ID 交给 `graph_analyze` 或 `graph_visualize`。
7. 用 `library_export` 导出最终参考文献。
8. 只有需要保留原始文件时才使用 `download_pdf`。

## 运行时数据与输出路径

`~` 表示当前账户的主目录；在 Windows 上即 `%USERPROFILE%`。默认目录会在
首次使用相应功能时自动创建。

| 数据或工具 | 默认路径 | 自定义路径规则 | 生命周期与写入行为 |
|---|---|---|---|
| 本地文献库 | `~/.AI-CACHE/openalex/library.db` | 没有 tool 参数或专门的环境变量可以修改。四个默认路径都基于 `~` 解析，因此在客户端 `env` 中设置 `USERPROFILE`（Windows）或 `HOME`（POSIX）可整体迁移该目录树。 | 持久化 SQLite 数据库；服务器运行期间 WAL 模式可能生成 `library.db-wal` 和 `library.db-shm`。 |
| `library_export` | `~/.AI-CACHE/openalex/collections/<target>`（默认 `export.bib`） | `target` 本身必须是 `.bib` 文件名（大小写不敏感，如 `EXPORT.BIB` 也会被接受），不会自动补后缀；拒绝绝对路径、子目录、路径分隔符和 `..`。 | 持久保留；再次导出到同一个合法文件名会替换该 BibTeX 文件。 |
| `download_pdf` | `~/.AI-CACHE/openalex/pdfs/<WORK_ID>.pdf` | **严格默认**：除非用户明确要求保存到项目目录，否则不得改路径；仅此时设 `save_to_project=true`，写入 `<cwd>/pdfs/<WORK_ID>.pdf`，其中 `<cwd>` 是 MCP 服务器进程启动时的工作目录。不接受任意路径。目录不存在时自动创建。 | 持久保留；同名 `<WORK_ID>.pdf` 已存在时跳过下载，不覆盖文件。文件名中的 work ID 会转成大写。 |
| `graph_visualize` | `~/.AI-CACHE/openalex/graphs/graph_<digest>_<N>n.html` | `output_dir` 可传绝对目录；相对目录以 MCP 服务器进程的工作目录为基准。目录不存在时自动创建。 | 持久保留；12 位 `digest` 由实际渲染的 work ID 生成，`N` 是渲染节点数；重新生成相同选集会替换同名 HTML。每个文件内嵌 vis.js 库，单个图大约 0.7 MB。 |
| 使用 `fetch_fulltext=true` 的检索 | 系统临时目录：`openalex-fulltext-*/paper.pdf` | 目录前缀和 `paper.pdf` 文件名固定，但父级临时目录仍遵循 `TMPDIR` / `TEMP` / `TMP` 变量。 | PDF 完成文本提取后立即删除；提取结果写入 `library.db` 的 `works.fulltext`，不永久保留原 PDF。 |

检索结果、摘要、向量和提取出的全文都保存在 `library.db` 内，不会生成独立
文件。`autocomplete`、`library_query`、`library_stats`、`library_delete`、
`library_close`、`graph_analyze` 和 `graph_neighbors` 不生成独立输出文件。
只有 `download_pdf` 工作流会永久保留原始 PDF。

默认目录结构如下：

```
~/.AI-CACHE/openalex/
├── library.db    # SQLite 文献库
├── library.db-wal / library.db-shm  # SQLite WAL 临时伴随文件
├── collections/  # BibTeX 导出
├── pdfs/         # 下载的开放获取 PDF
└── graphs/       # 引文网络 HTML 文件
```

## 联系方式

- 严寅中 · 西北工业大学民航学院
- 邮箱：<yinzhong.yan@nwpu.edu.cn>

## 致谢

感谢我的两位学生 **李林泽**、**石耀轩**，自去年起与我一起合作，共同研究学习智能体开发技术。
