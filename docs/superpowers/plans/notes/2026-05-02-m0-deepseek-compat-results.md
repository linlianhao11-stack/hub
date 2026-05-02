# M0 DeepSeek 兼容性验证结果（2026-05-02）

✅ **状态**：6 项 case 全部 PASS（11.6s）。Plan 6 v9 spec § 1.1-1.5 + § 5.2 的 DeepSeek 假设**全部成立**。

## 跑测试命令

```bash
cd /Users/lin/Desktop/hub/.worktrees/plan6-agent/backend

# 方法 A：直接传 env
DEEPSEEK_API_KEY=sk-xxx uv run pytest tests/integration/test_deepseek_compat.py -v -m realllm -s

# 方法 B：写到项目根 .env（已 gitignore）
echo 'DEEPSEEK_API_KEY=sk-xxx' >> /Users/lin/Desktop/hub/.worktrees/plan6-agent/.env
# conftest.py _load_dotenv_for_realllm_tests() 自动读
uv run pytest tests/integration/test_deepseek_compat.py -v -m realllm -s
```

## 6 项验证结果

| 验证项 | 结果 | 关键数据 / 影响 |
|---|---|---|
| prefix completion JSON 强制 | ✅ PASS | router prefix `{"intent": "` 方案可行 |
| strict + sentinel `""` | ✅ PASS | spec v3.4 sentinel 默认方案确认可用 |
| **strict + anyOf+null 实验** | ✅ **意外 PASS** | beta 接受 `null` arg；后续 Phase 2 可选升级（不强制） |
| thinking disabled + tools | ✅ PASS | tool 节点 `thinking={"type":"disabled"}` 与 tools 共存 |
| KV cache usage 解析 | ✅ PASS | run1: hit_rate **0.00**；run2: hit_rate **0.84**（spec 目标 ≥ 0.80） |
| thinking enabled 输出 reasoning_content | ✅ PASS | adjust_price.preview / contract.validate 节点可开 |

## anyOf+null 实验通过的影响

意外发现 DeepSeek beta 接受 `{"shipping_address": null}` 形式的 anyOf+null 参数。

**spec v3.4 默认走 sentinel `""`**，原因是 plan 写时假设 anyOf+null 不可靠。
现在确认可靠：
- **不强制改动**：sentinel 方案正常工作，Phase 2 继续按 plan 推进。
- **后续可选升级**：如果想干净掉 sentinel 归一化（每个 tool handler 入口的 `x = x or None`），可以在 Phase 2 完成后单独立一份升级 plan，把 17 个 tool 的 schema 升级到 anyOf+null。优势是少一层归一化、语义更直白；代价是改 17 处 schema + 单测。
- **结论**：本次 Plan 6 v9 不动；记录在案，留给后续优化。

## KV cache 命中率验证

run1（首次 prompt）hit_rate=0.00 → run2（同 system / few-shot）hit_rate=0.84。

→ 说明 DeepSeek beta `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` 字段确实暴露 KV cache 命中数据，且 **静态 prefix 第二次命中 84%**，超过 spec § 1.1 设定的 80% 阈值。

→ 后续 Phase 1 router / Phase 2 子图 prompt 都按 "完全静态 system + few-shot" 设计，可以期望同样的 ≥ 80% 命中率。

## 总结

M0 已经通过。所有 spec 假设兑现：

1. ✅ prefix completion 工作（router 设计成立）
2. ✅ strict mode 拒绝错误参数（sentinel 方案可用）
3. ✅ thinking 模式开 / 关都正常工作
4. ✅ KV cache 监控字段存在且符合阈值预期
5. ✅ anyOf+null 是 bonus 选项（不阻塞）

**M0 PASSED at 2026-05-02T19:42:00+08:00**
