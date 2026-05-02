# M0 DeepSeek 兼容性验证结果（2026-05-02）

⚠️ **状态**：测试代码已写，但**实际验证延后**（需要 DEEPSEEK_API_KEY，当前环境无）。

## 跑测试命令

```bash
cd /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend

# 方法 A：直接传 env
DEEPSEEK_API_KEY=sk-xxx uv run pytest tests/integration/test_deepseek_compat.py -v -m realllm -s

# 方法 B：从 hub admin AI provider 拉
DEEPSEEK_API_KEY=$(uv run python -c "
import asyncio
from hub.capabilities.factory import load_active_ai_provider
ai = asyncio.run(load_active_ai_provider())
print(ai.api_key)
asyncio.run(ai.aclose())
") uv run pytest tests/integration/test_deepseek_compat.py -v -m realllm -s
```

## 6 项验证

| 验证项 | 结果 | 影响 |
|---|---|---|
| prefix completion JSON 强制 | _待跑_ | router 走 prefix 没问题 |
| strict + sentinel ('') | _待跑_ | spec v3.4 默认方案确认可用 |
| **strict + anyOf+null 实验** | _待跑_（可能 SKIP）| 通过则 Phase 2 升级到 anyOf 优先 |
| thinking disabled + tools | _待跑_ | tool 节点关 thinking 可行 |
| KV cache usage 解析 | _待跑_ | 监控就绪 |
| thinking enabled reasoning | _待跑_ | adjust_price.preview / contract.validate 可开 |

## 验证后填写

跑完后用实际结果替换 `_待跑_` 列。如果 anyOf+null 实验通过 → 写一份升级 plan 让 Phase 2 改用 anyOf-null 写法（可选）。
