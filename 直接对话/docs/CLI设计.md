# 独立 CLI 设计（尚未实现）

返回 [使用方法](../使用方法.md)。本文件保存未来目标接口，不是安装或运行指南。

### 当前状态

CLI 尚未实现。下面定义第一版必须遵守的使用界面，使未来的程序、人类文档和 Agent 实现指向同一套行为。

### 目标命令

```powershell
# 查看人物包是否可用
persona doctor richard-feynman

# 使用配置文件中的默认模型开始对话
persona chat richard-feynman

# 临时指定本地模型适配器
persona chat richard-feynman --provider ollama --model <local-model>

# 临时指定远程 API 适配器
persona chat richard-feynman --provider openai --model <api-model>

# 从上一会话继续
persona chat richard-feynman --resume <session-id>
```

程序启动后至少提供以下会话命令：

```text
/help                 显示命令
/research on|off      切换自然对话层与研究层
/sources              审计上一回答的相关记忆与来源
/counterevidence      显示相反材料与限制
/save                 保存经过用户确认的会话摘要
/new                  清空当前会话历史，保留人物
/exit                 退出
```

### 配置位置与优先级

建议使用项目本地配置 `.persona/config.toml`，密钥只从环境变量读取。配置优先级固定为：

```text
命令行参数 > 环境变量 > .persona/config.toml > 程序默认值
```

建议配置结构：

```toml
[runtime]
personas_dir = "personas"
sessions_dir = "直接对话/sessions"
language = "zh-CN"

[llm]
provider = "openai"       # openai / anthropic / ollama / openai-compatible
model = "<model-name>"
base_url = ""             # 本地或兼容服务需要时填写
api_key_env = "OPENAI_API_KEY"
max_output_tokens = 1600

[retrieval]
mode = "auto"             # 小包整体读入；大包由模型自主调用检索
small_package_max_cards = 100
top_k = 6
include_source_text = false

[knowledge]
web_access = "off"        # 用户明确要求当前资料核验时才临时开启
posthumous_mode = "user-context-reasoning" # 接受用户提供的事实；按需核验，不伪装成人物真实记忆

[conversation]
show_sources_by_default = false
save_full_transcript = false
```

远程 API 示例只在当前 PowerShell 会话中设置密钥：

```powershell
$env:OPENAI_API_KEY = "<your-api-key>"
persona chat richard-feynman --provider openai --model <api-model>
```

不要把真实密钥写入 `config.toml`、人物包、对话日志或 Git 仓库。

### 本地模型与 API 模型

| 方式 | 优点 | 代价与注意点 |
| --- | --- | --- |
| 本地模型 | 资料不必发送给第三方；可离线；成本可控 | 需要本地算力；长上下文、中文表达和指令遵循能力取决于模型 |
| 远程 API | 部署简单；通常有更稳定的推理、长上下文和生成质量 | 需要密钥与费用；选中的证据、问题和对话历史会发送给服务商 |

CLI 必须在启动时明确显示本轮会把哪些数据发送到哪里。私密人物包不得因为默认配置而意外联网。

### CLI 每轮内部流程

```text
启动会话
  -> 读入人物规范、对话示例和可容纳的人物记忆
用户问题
  -> 模型结合人物记忆与对话状态自主选择回应方式
  -> 需要具体回忆、反例或核验时，模型自主调用人物检索工具
  -> 输出自然回答
  -> 仅记录实际调用过的检索证据 ID，不默认展示
```

若人物材料在会话开始整体读入，模型无法可靠证明某句话在生成时“具体使用了哪张卡”。此时 `/sources` 是事后审计：寻找能支持或反驳上一回答的记忆来源，并明确标注为审计结果。只有模型实际调用检索工具时，CLI 才能把调用过的证据 ID 作为真实运行记录。

第一版不需要模型微调或复杂向量数据库。当前费曼和鲁迅的人物包都可以在会话开始整体读入证据卡与情境记忆，让模型先形成连贯人物记忆。资料超过上下文容量后，再提供关键词/BM25 检索工具；工具由模型按需使用，而不是把对话固定成检索流水线。

### CLI 的最低验收条件

- `doctor` 能发现缺失文件、无效 JSON、未启用能力和 `draft` 准入问题。
- 同一人物在本地模型与 API 模型之间切换时使用相同人物包。
- 默认回答不显示检索日志，`/sources` 能回溯上一回答。
- 无证据时不制造原话或经历。
- API 配置缺失时给出明确错误，不回显密钥。
- 私密模式默认不联网，不自动保存完整对话。
- 会话日志与人物研究文件分开，删除会话不影响人物包。

---

## 两种方式如何选择

| 需求 | 推荐方式 |
| --- | --- |
| 现在就试一个人物是否好聊 | Agent 直读模式 |
| 修改人物规范后快速复测 | Agent 直读模式 |
| 固定模型、参数和可复现行为 | CLI 模式 |
| 让普通用户无需编码 Agent 使用 | CLI，之后再接 Web 界面 |
| 完全本地处理私密人物资料 | CLI + 本地模型 |
| 自动化评测、批量会话、产品集成 | CLI/API 运行时 |

推荐顺序是：先用 Agent 直读模式验证人物体验，积累失败案例；再实现最小 CLI，把已经验证过的加载、检索、模型调用和会话行为固化下来。
