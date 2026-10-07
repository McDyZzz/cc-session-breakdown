# cc-session-breakdown（`ccsb`）

[English](README.md) | 简体中文

一个 Claude Code plugin。它显示一个 session 的：

- token 用量，按模型、按 agent、按工作类型拆分
- 套餐配额占比
- 按 API 标价算的成本

## 示例

```markdown
**📊 会话明细（估算）**

- 5h：≈ 18.00%
- 周·全部：≈ 3.00%
- 周·Fable：≈ 1.50%
- API 计价：$24.00

**Fable**（周·全部 0.75%，周·Fable 1.50%，占比 25.00%）

| 工作类型 | token | 周·全部 | 占比 | 主session |
|---|--:|--:|--:|:-:|
| 派 agent | 300K | 0.30% | 10.00% | ✓ |
| 思考/回复 | 180K | 0.24% | 8.00% | ✓ |
| 搜索网页 | 90K | 0.12% | 4.00% | ✓ |
| 跑命令 | 60K | 0.09% | 3.00% | ✓ |

**Opus**（周·全部 1.80%，占比 60.00%）

| 工作类型 | token | 周·全部 | 占比 | 主session |
|---|--:|--:|--:|:-:|
| 搜索网页 | 2.0M | 1.05% | 35.00% | - |
| 跑命令 | 600K | 0.36% | 12.00% | - |
| 思考/回复 | 400K | 0.24% | 8.00% | - |
| 改代码 | 150K | 0.15% | 5.00% | - |

**Sonnet**（周·全部 0.45%，占比 15.00%）

| 工作类型 | token | 周·全部 | 占比 | 主session |
|---|--:|--:|--:|:-:|
| 搜索网页 | 1.2M | 0.30% | 10.00% | - |
| 读代码 | 400K | 0.12% | 4.00% | - |
| 其他工具 | 30K | 0.03% | 1.00% | - |

**最费周额度的任务**（第一层子 agent，含它的子 agent）

| # | 模型 | 周·全部 | 占比 | 任务 |
|--:|---|--:|--:|---|
| 1 | Opus+Sonnet | 1.20% | 40.00% | 为一个小型网站比较三个托管方案 |
| 2 | Opus | 0.60% | 20.00% | 修复不稳定的登录测试 |
| 3 | Opus | 0.45% | 15.00% | 起草版本 2 的发布说明 |
```

## 使用要求

- Claude Code
- macOS 或 Linux
- Python 3.9 或更新

## 安装

对 Claude Code 说：

```
安装 McDyZzz/cc-session-breakdown
```

## 使用

查看当前 session：

```
/ccsb:report
```

或者直接问：

```
这个 session 用了多少？
```

按标题查另一个 session：

```
查一下写发布说明的那个 session 的用量
```

## 后台运行什么

- Claude 每轮回复结束后，一个 hook 用 `claude -p "/usage"` 采样配额条，最多每 5 分钟一次。
- 最初几天，配额百分比显示 `未校准`，采样足够后才有数字。
- 看报告时，如果上次价格检查超过 30 天，Claude 对照官方价格页检查。价格有变化时先问你，再保存。
- 所有数据留在你的机器上，位于 `~/.claude/cc-session-breakdown/`。

关闭采样：

```
/ccsb:sampling off
```

`/ccsb:sampling on` 重新打开，`/ccsb:sampling status` 显示状态。

## 更新、卸载、删除数据

更新：

```
更新 plugin ccsb
```

卸载：

```
卸载 plugin ccsb
```

删除采样：

```
/ccsb:sampling delete
```

删除全部数据：

```
删除 ccsb 的数据文件夹 ~/.claude/cc-session-breakdown
```

## 开发

见 [AGENTS.md](AGENTS.md)。

## 许可证

[MIT](LICENSE)
