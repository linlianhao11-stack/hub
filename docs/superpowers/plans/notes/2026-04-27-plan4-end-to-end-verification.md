# Plan 4 端到端验证记录

日期：2026-04-28
执行人：Claude Opus 4.7（自动模式）

## 单测验证（合计 192 PASS）

### Plan 4 新增（85）

| 测试文件 | 数量 | 状态 |
|---|---|---|
| `tests/test_error_codes.py` | 3 | ✅ |
| `tests/test_permissions.py` | 4 | ✅ |
| `tests/test_rule_parser.py` | 6 | ✅ |
| `tests/test_deepseek_provider.py` | 4 | ✅ |
| `tests/test_qwen_provider.py` | 3 | ✅ |
| `tests/test_llm_parser.py` | 9 | ✅（含缺必填字段降级 3 + confidence 非数字降级） |
| `tests/test_chain_parser.py` | 4 | ✅ |
| `tests/test_match_resolver.py` | 6 | ✅ |
| `tests/test_conversation_state.py` | 4 | ✅ |
| `tests/test_pricing_strategy.py` | 7 | ✅（含历史价 403 上抛） |
| `tests/test_erp_breaker.py` | 6 | ✅（含 countable_exceptions） |
| `tests/test_query_product_usecase.py` | 10 | ✅（含 execute_selected + fallback_retail_price 透传 + send 失败上抛 2 条） |
| `tests/test_query_customer_history_usecase.py` | 10 | ✅（含历史价 403 → PERM 翻译 + send 失败上抛 2 条） |
| `tests/test_inbound_handler_with_intent.py` | 5 | ✅ |
| `tests/test_erp4_adapter.py` 追加 | 4 | ✅（keyword 参数 + 熔断 + 历史价超时） |
| **Plan 4 合计** | **85** | ✅ |

### 既有 Plan 2-3（107，含本轮第六轮 review 修复后）

| 类别 | 数量 |
|---|---|
| Plan 3 既有 | 52 |
| Plan 2 既有 | 55 |
| **小计** | **107** |

### 总计

```
$ pytest -q
192 passed
```

### Lint

```
$ ruff check hub/ tests/
All checks passed!
```

## 端到端（依赖真实钉钉测试组织 + ERP staging + AI Provider）

| 项目 | 状态 |
|---|---|
| 查 SKU100 → 卡片（系统零售价） | 🟡 依赖真实 ERP 数据 |
| 查 SKU100 给阿里 → 历史价卡 | 🟡 依赖真实 ERP 数据 |
| 多商品/客户 → 选编号 → 命中 | 🟡 依赖真实 ERP 数据 |
| 自然语言 → AI 解析 → 高置信度直接执行 | 🟡 依赖真实 AI Provider |
| 自然语言 → 低置信度确认卡 → "是" → 执行 | 🟡 依赖真实 AI Provider |
| 无权限用户 → 中文文案拒绝 | ✅（单元测试覆盖） |
| ERP 5xx 5 次 → 熔断 → 友好提示 | ✅（test_circuit_opens_after_repeated_failures） |
| 历史价 3s 超时 → 降级到零售价 | ✅（test_customer_prices_timeout_raises_system_error + pricing fallback） |

🟡 项目：单元测试已完整覆盖整条链路（rule + LLM + match + state + usecase + cards + 错误翻译），
但闭环验证需要真实钉钉测试组织 + ERP-4 staging + AI Provider 凭证。生产联调按 Plan 8 验收清单跑。

## worker.py 启动验证

worker 进 run() 之前同时轮询 `ChannelApp` + `DownstreamSystem` 双就绪；
在 `binding_service` 后追加：
- `load_active_ai_provider()` 加载 ai_provider 表的活跃配置（None 时只用 RuleParser）
- `ChainParser(rule=RuleParser(), llm=LLMParser(ai=ai_provider))`
- `ConversationStateRepository(redis_client, ttl=300)`
- `DefaultPricingStrategy(erp_adapter)`
- 两个 UseCase 注入到 inbound handler

`finally` 块加 `if ai_provider: await ai_provider.aclose()` 释放 httpx 客户端。

main.py / worker.py 双 import 测试通过：
```
$ python -c "import worker; import main; print('OK')"
worker.main <function main at 0x...>
main.app <fastapi.applications.FastAPI object at 0x...>
```

## 已知缺口（Plan 5 处理）

- 完整 Web 后台对话监控（task_log + UI）
- AI Provider 管理 UI 复用 `ai_provider` 表 + factory
- 任务流水查询 UI（task_log / task_payload 表已建）
- cron 调度器集成（每日离职巡检 / 状态缓存清理）
