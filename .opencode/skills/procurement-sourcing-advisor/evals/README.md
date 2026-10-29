# 评测记录

## 当前状态

技能已从 `cn-scraper-mcp` 迁移到 **AI + playwright-mcp**，候选粒度从 listing 改为 variant（SKU）。旧的能力评测（iteration-1）建立在链接级数据上，**结论已作废**，只保留作历史对照：

| eval | with_skill | without_skill | 说明 |
|---|---|---|---|
| eval-0-monitor-budget | 7/7 | 6/7 | 旧链路（MCP 链接级价格） |
| eval-1-hidden-price | 6/6 | 6/6 | 旧链路 |
| eval-2-scorecard-planning | 6/6 | 3/6 | 评分卡规划，仍部分适用 |

## 新链路的评测输入

- `evals/inputs/monitor-case/raw_capture.json` — 6 个 listing / 6 个配置
- `evals/inputs/hidden-price-case/raw_capture.json` — 5 个 listing / 5 个配置
- `evals/evals.json` — 3 个评测任务与断言

离线回归：

```powershell
$py = "$env:USERPROFILE\.conda\envs\procurement-advisor\python.exe"
& $py -m pytest tests -q
& $py scripts\validate_capture.py evals\inputs\monitor-case\raw_capture.json
```

## 新增断言方向（iteration-2 待跑）

1. 同一链接的多个配置被展开为多条候选，价格分别记录；
2. `option_path` 为空、价格缺 snippet 时校验失败；
3. 评价样本缺失时输出 `UNKNOWN`，不得写"口碑好"；
4. 报告包含"同链接配置对比"与"评价摘要"；
5. 超预算配置被单独淘汰，不影响同链接其他配置。

## 触发评测

`evals/trigger_eval_set.json` 仍适用。`skill_eval` 在本机把所有运行记为 error（子进程非零退出），改用 `evals/trigger_harness.js`：

```powershell
node evals/trigger_harness.js evals/trigger_eval_set.json 2 6 90000 trigger-results.json
```

需在未安装同名技能的项目根下运行，避免真技能抢触发。
