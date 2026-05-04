# Plan 6 v9 GraphAgent 上线 Checklist（Task 8.6）

## 0. 状态总览

> 🚧 **代码完成，staging release gate 未完成 — 禁止 merge main / 禁止生产上线**

实施完成度：54/54 tasks (100%) — 8 phases 全部交付（代码 / 单测 / mock e2e）。
**release gate 未完成** — 真 LLM eval 30 case 未实跑、reviewer 未签字、p50 复测未达标、1 周 staging 真钉钉观察未完成。

| Phase | Status | 关键交付 |
|-------|--------|---------|
| Phase 0 (M0 foundation) | ✅ | 8 tasks，state schemas + thread_id + DeepSeekLLMClient + ConfirmGate + SessionMemory |
| Phase 1 (M1 router + chat) | ✅ | 5 tasks，router 准确率 98.21% |
| Phase 2 (M2 strict tools) | ✅ | 6 tasks，16 tool strict + sentinel + subgraph filter |
| Phase 3 (M3 query) | ✅ | 3 tasks，11 read tool + tool_loop |
| Phase 4 (M4 contract) | ✅ | 10 tasks，6-节点 LangGraph state machine |
| Phase 5 (M5 写操作) | ✅ | 8 tasks，voucher/adjust_price/adjust_stock + confirm 多 pending |
| Phase 6 (M6 quote) | ✅ | 3 tasks，4-节点 quote 子图 |
| Phase 7 (M7 接入) | ✅ | 5 tasks，dingtalk_inbound 切到 GraphAgent + 删 ChainAgent 1890 行 |
| Phase 8 (M8 验收) | ✅ 代码 / 🚧 实跑 | 6 tasks，6 故事 mock e2e + 30 case eval **fixture / driver 完成；实跑未做** |

### 0.1 当前 release gate 阻塞清单（必须全部消除才允许 merge main / 上线）

- [ ] **30 case eval 实跑**：`test_realllm_eval.py` 在 staging 容器内或对 staging 跑全 30 case，0 skipped + 全部产出明确的 PASS/FAIL（不允许"未跑"算 PASS）
- [ ] **机检 ≥ 28/30 PASS**：实跑数据写到 `2026-05-02-eval-results.md`
- [ ] **人工平均分 ≥ 4.0/5**：reviewer 在 `2026-05-02-eval-results.md` 填每个 case 的 1-5 分 + 签名 + 时间戳
- [ ] **reviewer 签字**：`2026-05-02-eval-results.md` 末尾人工签名（无签名 = 未通过）
- [ ] **1 周真钉钉 staging 观察**：录屏 + § 3 的 5 条流程红线 1 周 0 命中
- [ ] **p50 复测**：staging 真生产样本 ≥ 1000 轮，p50 < 5s 达标 **或** 有明确豁免签字（理由 + 签名 + 时间戳）

> 在以上任何一项未消除前，文档底部禁止出现"可上线 / 可 merge main / 可 ship"等措辞。

## 1. 上线前必须确认

### 1.1 代码层
- [x] feature/plan6-agent 分支所有 commits 已推到 origin
- [x] 全量回归测试 PASS（最新基线 776+ tests，0 FAIL）
- [x] mock e2e 7/7 acceptance scenarios PASS（StubToolExecutor）
- [x] 删除遗留代码 1890 行（ChainAgent + ContextBuilder）
- [ ] **PR 评审**：merge feature/plan6-agent → main 之前 ≥ 1 reviewer 签字
- [ ] **migration 检查**：本 plan 不涉及新表（PendingAction.idempotency_key 是 ConfirmGate 内部 redis 字段，非 DB schema）

### 1.2 配置层
- [ ] 生产环境 hub-postgres / hub-redis 健康
- [ ] 生产环境 DeepSeek API key 已在 hub 「配置中心」加密存储（base_url=https://api.deepseek.com，模型 deepseek-v4-flash）
- [ ] HUB_MASTER_KEY 部署机器有
- [ ] 钉钉 channel_app 已配置且 active

### 1.3 监控（staging vs prod 拆分）

**staging gate（最低要求）**：
- [ ] `hub.metrics.incr` 调用路径打到 stdout / 文件 log（当前 stub 即可）
- [ ] worker 容器日志打 `ERROR / WARNING / GraphAgent` 关键字行可见

**prod gate（必须，无豁免）**：
- [ ] 生产 metrics backend（Prometheus / DataDog / SLS / 等价）已替换 `hub.metrics.incr` 的 stub 实现并真 emit
- [ ] LLM fallback 告警阈值已设（fallback 率 > 5% / 分钟告警）+ 实际告警通道（钉钉群 / 邮件）已配
- [ ] cache 命中率监控（≥ 80% 月平均）+ 低于阈值的告警已配
- [ ] 错误率告警（GraphAgent ainvoke 抛 unhandled exception 比例 > 1% / 分钟）已配
- [ ] 在 staging 触发一次模拟 fallback / cache miss / 异常，确认告警链路真到人

> ⚠️ § 5 follow-up "hub.metrics 留 stub"**只针对 staging**；prod gate 必须对接真 backend，不允许 follow-up 跳过。

## 2. Staging 部署步骤（必须按 Docker 镜像顺序，先验镜像再验真 LLM）

> 本节验收对象 = **staging Docker / k8s 环境**，**不是**本地 venv。本地 uv 跑通不等于 staging 跑通。

```bash
# ── 1. 拉分支 ──
cd /path/to/hub-staging-clone
git fetch origin feature/plan6-agent
git checkout feature/plan6-agent
git pull origin feature/plan6-agent

# ── 2. 先 build Docker 镜像（先验依赖打进镜像）──
docker compose build hub-gateway hub-worker hub-migrate

# ── 3. 启动基础设施 + migrate ──
docker compose up -d hub-postgres hub-redis
docker compose run --rm hub-migrate                       # migration 必须 0 错
docker compose up -d hub-gateway hub-worker

# ── 4. 镜像健康验收（必须全过才进 § 5）──
sleep 10
docker compose ps hub-gateway hub-worker hub-postgres hub-redis | grep -v "Up" && echo "ERROR: some containers not Up" && exit 1
docker compose exec hub-gateway curl -fsS http://localhost:8091/health | grep -i ok          # gateway health
docker compose logs hub-worker | grep -E "ImportError|ModuleNotFoundError" && echo "ERROR" && exit 1
docker compose logs hub-gateway | grep -E "ImportError|ModuleNotFoundError" && echo "ERROR" && exit 1
docker compose exec hub-gateway python -c "from hub.agent.graph.agent import GraphAgent; print('GraphAgent import OK')"

# ── 5. 真 LLM 测试（必须**对 staging 容器或 staging URL** 跑，不能在 host venv） ──
# 5a. 7 个 acceptance scenarios（含跨轮 query→contract）
docker compose exec -e DEEPSEEK_API_KEY="$STAGING_DEEPSEEK_KEY" hub-gateway \
    pytest tests/agent/test_acceptance_scenarios.py \
           tests/agent/test_per_user_isolation.py \
           tests/agent/test_cache_hit_rate.py \
           -v -m realllm -s

# 5b. 30 case eval 实跑（机检 hard gate）
docker compose exec -e DEEPSEEK_API_KEY="$STAGING_DEEPSEEK_KEY" hub-gateway \
    pytest tests/agent/test_realllm_eval.py -v -m realllm -s 2>&1 \
    | tee /tmp/eval_run_$(date -Iseconds).log

# 5c. benchmark 在 staging 容器内跑
docker compose exec -e DEEPSEEK_API_KEY="$STAGING_DEEPSEEK_KEY" hub-gateway \
    python /app/scripts/benchmark_graph_agent.py
```

### 2.1 真 LLM 验收口径（review issue 7 加严）

每条真 LLM 命令必须**全部**满足才算通过：

1. **DEEPSEEK_API_KEY 在 staging 容器 env 中存在**（不是宿主 .env）
2. **pytest 输出 0 skipped**（`==== N passed, 0 skipped ====`）；任何 skipped 视为未通过
3. **acceptance scenarios 必须 7/7 PASS**（含跨轮 query→contract，含 multi-pending）
4. **30 case eval 必须 30/30 实跑**（无 skip / 无 collect-only）
5. **机检 ≥ 28/30 PASS**（机检通过率写入 `2026-05-02-eval-results.md`）
6. **人工评分 30 case × 4 维度全部填到** `docs/superpowers/plans/notes/2026-05-02-eval-results.md`（不允许部分留空）
7. **reviewer 在 eval-results.md 末尾签名 + 时间戳**

skipped / 空产物 / 无签名 → 一律视为未通过，不允许进 § 3。

## 3. Staging 真钉钉验收（≥ 1 周观察）

**录屏要求**：每个核心场景跑一遍真钉钉群对话，录屏存档：
1. 闲聊 — 自然回复，不反问业务
2. 查询库存 — Markdown 表格输出，无业务反问
3. 单轮合同 — 信息齐 → 直接生成，无二次确认
4. 跨轮合同 — 多客户候选 → 选 N → 续地址 → 生成
5. 报价 — 同合同流程，无 shipping
6. 调价 + 确认 — preview → 等"确认" → 落申请
7. 跨会话隔离 — 同群两个 user 互不串状态
8. 多 pending — 用户回"1"选第 1 个

**观察 1 周流程类 bug 红线（任何一条命中 → 立即回滚）**：
- ❌ 反复确认（生成前再问"是否确认"）
- ❌ 调多余 tool（合同流程跑 check_inventory 等）
- ❌ 群聊串状态（A 用户的 pending 被 B claim）
- ❌ 候选不消费（用户回"1"被判 unknown）
- ❌ 跨轮信息丢（第二轮重新问地址）

如出现以上任何一条 → **停止上线**，回退到 ChainAgent（feature/plan6-agent rollback）。

## 4. Release Gate 数据汇总

| 指标 | 目标 | 实测 | 通过 | 来源 |
|------|------|------|------|------|
| Router 准确率 | ≥ 95% | **98.21%** | ✅ | Task 1.3（55/56 真 LLM） |
| Cache 命中率 | ≥ 80% | **84.80%** | ✅ | Task 8.3（5 次连续 router 调用平均） |
| 6 故事 mock e2e | 100% | **7/7** | ✅ | Task 8.1（StubToolExecutor） |
| 单测 PASS 数 | ≥ 95% | **776+** | ✅ | Phase 7 baseline |
| p50 延迟 | < 5s | **5.05s** | ❌ 微超 0.05s | Task 8.5（StubExecutor 8 轮样本） |
| p99 延迟 | < 15s | **10.97s** | ✅ | Task 8.5 |
| 真 LLM eval 30 case 机检 | ≥ 28/30 | __ | ❌ 未跑 | Task 8.2 fixture 完成；实跑未做 |
| 真 LLM eval 人工分 | ≥ 4.0/5 | __ | ❌ 未评 | reviewer 待填 `2026-05-02-eval-results.md` |
| 1 周真钉钉观察 | 0 红线命中 | __ | ❌ 未启动 | § 3 |
| reviewer 签字 | 必须 | __ | ❌ 未签 | `2026-05-02-eval-results.md` 末尾 |

⚠️ **p50 5.05s 当前不视为通过** — 8 轮样本不充分。staging 真生产数据 (≥ 1000 轮) 重新测后达标 **或** reviewer 显式豁免（写明理由 + 签名 + 时间戳）才视为通过。

## 5. 已知技术债 / 留作 follow-up

> ⚠️ 以下 follow-up **不影响 staging gate**，但会影响 **prod gate**（监控对接是必须，不是 follow-up）。

1. **ToolCallLog DB 集成未做**（Task 8.3 用了简化版直接读 LLMResponse.cache_hit_rate）。生产想做月度趋势统计需要补 ToolCallLog.cache_hit_rate 字段 + migration。**staging 可省，prod 想做月报必须补。**
2. **`hub.metrics`** 当前是 stub（log 出来）。**staging 可用 stub；prod 必须接 Prometheus / DataDog / SLS 等价后端**（参见 § 1.3 prod gate）— 不是 follow-up，是 prod 硬要求。
3. **30 case eval 人工分**（Task 8.2）— `notes/2026-05-02-eval-results.md` 占位文件，等 reviewer 跑完真 LLM 后填分。**这是 release gate，不是 follow-up。**
4. **`prompt/builder.py`** 仍保留 `PromptBuilder` 类 + 部分 `_BEHAVIOR_RULES` 通用规则（Task 7.3 评估保留）。GraphAgent 不再用，但旁边有 `test_prompt_builder.py`。下次清理可一并删。
5. **anyOf+null tool schema 升级**（M0 实验意外通过）— 当前 v3.4 走 sentinel；后续可选迁移到 anyOf+null 让 schema 更直白（参见 `notes/2026-05-02-m0-deepseek-compat-results.md`）。

## 6. 上线后一周 review

部署成功 + 1 周观察期结束后，写一份 release retrospective：
- `docs/superpowers/plans/notes/2026-05-XX-graph-agent-prod-week1.md`
- 内容：实际 router 准确率 / cache 命中率 / p50/p99 / 用户反馈 / 流程类 bug 数量 / 滞留 pending 数 / fallback 率
- reviewer 签字 → 视为完整交付

---

## 总结状态

**允许继续 staging 验收** — 代码、单测、mock e2e、metric 数据都到位。

**禁止合并 main / 禁止生产上线** — § 0.1 列出的 6 项 release gate 阻塞还没全部消除：
1. 30 case eval 未实跑
2. 机检 ≥ 28/30 未达
3. 人工分 ≥ 4.0/5 未评
4. reviewer 未签字
5. 1 周真钉钉观察未启动
6. p50 5.05s 复测未达标且无豁免

每个阻塞消除时回这份文档把对应 checkbox 勾上 + 备注实测数据 + 时间。**全部勾完且 reviewer 签名才允许 merge / ship**。

---

ChainAgent → GraphAgent 状态机重构 8 天代码部分完成。Plan 共：
- **54 tasks** 全部 done
- **9 phases** 全部 PASS（代码层）
- **776+ 单测** 全过
- **7/7 mock e2e acceptance scenarios** PASS
- **删除 ChainAgent / ContextBuilder 1890 行遗留代码**
- **新增 22 个 graph 节点 + 8 个子图（含 chat/query/contract/quote/voucher/adjust_price/adjust_stock + 主图 + commit_*）**
- **关键架构发现**：LangGraph + Pydantic 状态传播 model_fields_set 陷阱（fix 跨 9 文件）

Reviewer：__________________________  日期：__________

签字含义 = § 0.1 所有 6 条阻塞已消除 + § 1.1 / 1.2 / 1.3 staging gate 全勾 + § 2.1 真 LLM 验收口径全过。
