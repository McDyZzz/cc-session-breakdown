# cc-session-breakdown（`ccsb`）

[English](README.md) | 简体中文

一个 Claude Code plugin。它显示一个 session 的 token 花在了哪里：按模型、按 agent、按工作类型拆分。
它还能估算接下来几轮对话的成本，对比现在和 `/compact` 之后。

## 功能

- **完整拆分一个 session。** 每个模型一张表。每一行是一种工作类型：思考/回复、搜索网页、读代码、
  改代码、跑命令、派 agent、界面操作。
- **主 session 和 subagent 分开。** 报告标出哪些行属于主 session，并列出最贵的 subagent 任务。
- **每个 session 三个数字：** 5 小时配额条的占比、每周配额条的占比、按 API 标价计算的美元成本。
- **`/compact` 估算。** 一行输出：按当前 context 大小，接下来几轮的成本，以及 `/compact` 之后的成本。
- **任意本地 session。** 可以查当前 session，也可以按标题查另一个。
- **隐藏成本。** 空闲一小时后的冷启动，以及占比达到 10% 的截图。
- **中英文报告。** 报告语言跟随 session 的语言。

## 示例

下面的数字都是编造的。

```markdown
**📊 会话明细（估算）**

- 5h：≈ 18.00%
- 周·全部：≈ 3.00%
- 周·Fable：≈ 1.50%
- API 计价：$24.00

**Opus**（周·全部 1.80%，占比 60.00%）

| 工作类型 | token | 周·全部 | 占比 | 主session |
|---|--:|--:|--:|:-:|
| 搜索网页 | 2.0M | 1.05% | 35.00% | - |
| 跑命令 | 600K | 0.36% | 12.00% | - |
| 思考/回复 | 400K | 0.24% | 8.00% | - |
| 改代码 | 150K | 0.15% | 5.00% | - |

**最费周额度的任务**（第一层子 agent，含它的子 agent）

| # | 模型 | 周·全部 | 占比 | 任务 |
|--:|---|--:|--:|---|
| 1 | Opus+Sonnet | 1.20% | 40.00% | 比较三个小型网站的托管方案 |
| 2 | Opus | 0.60% | 20.00% | 修复不稳定的登录测试 |
```

## 使用要求

| 项目 | 要求 |
|---|---|
| 工具 | Claude Code：CLI、桌面 app 的 Code 标签页，或 IDE 扩展 |
| 套餐 | Claude 订阅套餐。配额数字来自 `/usage` |
| 系统 | macOS 或 Linux |
| Python | 3.9 或更新。不需要额外的包 |

Windows、claude.ai 网页聊天和 Cowork 不在这个 plugin 的支持范围内。

## 安装

```bash
claude plugin marketplace add McDyZzz/cc-session-breakdown
```

```bash
claude plugin install ccsb@cc-session-breakdown
```

然后开一个新 session，或在已打开的 session 里运行 `/reload-plugins`。

## 使用

用自然语言问 Claude，或直接调用 skill。

| 你想要 | 你说 |
|---|---|
| 当前 session | "这个 session 用了多少？" 或 `/ccsb:report` |
| 另一个 session | "查一下写发布说明的那个 session 的用量" |
| `/compact` 估算 | "运行 `ccsb --estimate --context 200000 --turns 10`" |

Claude Code 把 `ccsb` 命令加到 Claude 的 Bash 工具的 PATH 里。你自己的终端里没有这个命令，
所以请让 Claude 来运行。

## 这个 plugin 在你的机器上运行什么

- **一个 Stop hook。** Claude 每轮回复结束后，hook 启动一个后台任务，然后立刻返回。
- **配额采样，hook 最多每 5 分钟一次。** 后台任务运行 `claude -p "/usage"`，保存配额条的百分比。
  这个命令不调用模型，消耗 0 token。查看当前 session 的报告时，也会采样一次。
- **采样开关。** 采样默认打开。要关掉，就说"关掉 ccsb 采样"，或运行 `/ccsb:sampling off`。
  `/ccsb:sampling on` 重新打开，`/ccsb:sampling status` 显示开关、采样数量和已校准的配额条。
  采样关闭时，hook 不启动后台任务，查看报告时也不采样。报告照常显示 token 数量和 API 成本，
  但不显示额度 %。开关保存在数据文件夹的 `config.json` 里。
- **本地读取。** 报告读取 `~/.claude/projects/` 下的 session 记录。

所有数据都留在你的机器上。plugin 自己的代码不发起网络请求。`claude` 程序会联系 Anthropic
读取你的套餐用量，和你输入 `/usage` 时一样。

## 配额数字需要几天时间

plugin 要学习"多少 token 等于你的套餐里某个配额条的 1%"。它从你机器上的采样里学习。

- 采样足够之前，配额条显示 `未校准`。token 数量和 API 成本从第一次运行起就可用。
- 支持的配额条：5 小时条、全部模型的每周条、Fable 的每周条。
- session 用了 Fable，并且你的 `/usage` 里有 Fable 条，报告才显示每周 Fable 数字。
  其他模型的每周条会被忽略。

## 价格

plugin 自带一份价格表 `scripts/pricing.json`。遇到价格表里没有的模型，或每隔 30 天，Claude 会对照
官方价格页面检查。Claude 给你看每一个变化的值，你同意后才写入。写入的位置是数据文件夹里的
`pricing.override.json`，所以 plugin 更新后这些值还在。

## 数据

| 项目 | 值 |
|---|---|
| 文件夹 | `~/.claude/cc-session-breakdown/` |
| 内容 | 配额采样、校准结果、采样开关（`config.json`）、价格覆盖文件、错误日志，以及读取状态（含你的 session 记录文件的路径）。没有对话内容 |
| 改位置 | 设置环境变量 `CCSB_DATA_DIR` |
| 卸载后 | 文件夹保留，重装后校准结果还在 |

只删除采样和校准结果：运行 `/ccsb:sampling delete`。Claude 先列出要删的文件，你确认后才删除。
删除时保留采样开关、读取状态和价格文件。采样打开时，新的采样会重新开始校准。

`/ccsb:sampling` skill 运行下面这些命令。你也可以让 Claude 直接运行：

| 命令 | 作用 |
|---|---|
| `ccsb --sampling on`、`off` 或 `status` | 打开或关闭采样，或显示采样状态 |
| `ccsb --delete-samples` | 列出采样和校准文件，不删除 |
| `ccsb --delete-samples --yes` | 删除这些文件 |

删除全部数据：

```bash
rm -rf ~/.claude/cc-session-breakdown
```

## 更新和卸载

```bash
claude plugin update ccsb@cc-session-breakdown
```

```bash
claude plugin uninstall ccsb@cc-session-breakdown
```

## 限制

- **数字是估算值。** Anthropic 没有公开套餐配额的计算方式。plugin 按 API 价格给 token 加权，
  再用你自己的 `/usage` 校准。
- **`/usage` 没有官方文档保证。** Claude Code 更新后，它的输出可能变化。这时报告的配额部分显示
  `额度读取失败`，token 和成本部分照常工作。
- **只在一种订阅套餐和 macOS 上手动测试过。** Linux 由自动测试覆盖。欢迎其他套餐的用户在 issue 里反馈。
- **按标题查找需要 session 有标题。** 标题由 Claude Code 写进 session 记录。这一点用桌面 app 的
  session 验证过。没有标题的 session 仍然可以用 id 查到。

## 开发

见 [AGENTS.md](AGENTS.md)。

## 许可证

[MIT](LICENSE)
