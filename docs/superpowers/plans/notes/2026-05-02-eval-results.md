# Plan 6 v9 GraphAgent 真 LLM Eval 结果

⚠️ 此文件为 release gate 的硬性产物。pytest stdout 不算。

## 机检结果

跑：
```bash
DEEPSEEK_API_KEY=... pytest tests/agent/test_realllm_eval.py -v -m realllm -s 2>&1 | tee /tmp/eval_run.log
```

填：
- 机检 PASS：__ / 30
- 机检 FAIL（列 case id）：__
- 跑完时间：__

## 人工评分（30 case × 4 维度 × 1-5 分）

人工 review 每个 case 的 final_response，按 rubric 评分：

- `natural_tone`：回复语气是否自然（不像机器人 / 不堆砌敬语）
- `brevity`：长度是否克制（1-3 段，无废话）
- `table_quality`：列表 / 数据是否用 Markdown 表格清晰呈现（n/a 表示 case 不涉及表格）
- `per_user_isolation`：跨 user / 跨 conv 没串状态（仅 isolation case）

| Case ID | 机检 | natural | brevity | table | isolation | 备注 |
|---------|------|---------|---------|-------|-----------|------|
| chat-01 | __ | __ | __ | n/a | n/a | __ |
| chat-02 | __ | __ | __ | n/a | n/a | __ |
| chat-03 | __ | __ | __ | n/a | n/a | __ |
| chat-04-edge-emoji | __ | __ | __ | n/a | n/a | __ |
| query-01-inventory | __ | __ | __ | __ | n/a | __ |
| query-02-orders | __ | __ | __ | __ | n/a | __ |
| query-03-balance | __ | __ | __ | n/a | n/a | __ |
| query-04-product-detail | __ | __ | __ | n/a | n/a | __ |
| query-05-aging | __ | __ | __ | __ | n/a | __ |
| query-06-top-customers | __ | __ | __ | __ | n/a | __ |
| contract-01 ~ contract-07 | __ | __ | __ | __ | n/a | __ |
| quote-01 ~ quote-04 | __ | __ | __ | __ | n/a | __ |
| voucher-01 ~ voucher-02 | __ | __ | __ | n/a | n/a | __ |
| adjust_price-* | __ | __ | __ | n/a | n/a | __ |
| adjust_stock-* | __ | __ | __ | n/a | n/a | __ |
| isolation-01 | __ | __ | __ | n/a | __ | __ |

## Release Gate

- ✅ 机检 ≥ 28/30 PASS（≥93%）
- ✅ 人工平均分 ≥ 4.0/5

| 指标 | 实际值 | 通过 |
|------|--------|------|
| 机检 PASS 数 / 30 | __ | __ |
| 人工 natural 平均 | __ | __ |
| 人工 brevity 平均 | __ | __ |
| 人工 table 平均 | __ | __ |
| 人工总平均 | __ | __ |

---

**Reviewer 签字**：__________________________

**评分日期**：__________

⚠️ 这份文件**必须**有 reviewer 签名才算 Plan 6 v9 release gate 通过。
