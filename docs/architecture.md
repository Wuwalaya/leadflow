# 架构与运行入口

## 模块与数据流

| 模块 | 职责 |
| --- | --- |
| [leadflow.py](../leadflow.py) | CLI、Tavily / Jev 请求、证据处理、SQLite、导出及运行状态 |
| [target_config.py](../target_config.py) | 显式国家/产品配置、术语与查询模板 |
| [company_names.py](../company_names.py) | 官网主体名称、来源与歧义判断 |
| [contact_enrichment.py](../contact_enrichment.py) | 官网联系入口、主体归属校验、缓存/联网补检及诊断 |
| [evaluate_company_names.py](../evaluate_company_names.py) | 名称开发回归、可选人工标签校验、来源哈希 |
| [evaluate_contacts.py](../evaluate_contacts.py) | 固定候选集联系方式评估、来源哈希及人工审核模板 |
| [tests](../tests/) | 模拟 API、临时 SQLite 和离线回归 |

主流程统一解析 TargetConfig，搜索并过滤非企业页面，抽取官网及有限补充产品证据，校验名称与邮箱，批量请求 Jev 后分桶。可选补检仅处理 HIGH / REVIEW 且无邮箱的候选。随后持久化并导出，保留失败阶段与人工审核信息。

Jev 直连 TypeSafe System One，固定 `JEV_MODEL = "jev-1.13.0"`。确定性代码负责归一化、摘录存在性、主体和数据库校验；模型概率不能替代业务判断。

## 入口与路径

| 命令 | 数据与作用 | 联网 / SQLite |
| --- | --- | --- |
| `plan` | 生成目标查询计划 | 均不使用 |
| `reference` | 默认合成 Fixture，规则处理、去重与旧格式导出 | 离线 / 写入 |
| `live` | 保留的旧搜索路径，规则或可选 LLM | 联网 / 写入 |
| `jev-smoke` | 参考文件第一家企业的最小 typed-decision 检查 | TypeSafe / 不使用 |
| `discovery-smoke` | 巴西不锈钢小规模 Tavily → Jev 检查 | 联网 / 不使用 |
| `discover` | 配置化发现、Jev 分桶及可选补检 | 联网 / 写入 |
| `discover --replay-from` | 复放已有 discover 数据，不重新分类 | 离线 / 写入 |

`reference` 和两个 smoke 仅支持巴西不锈钢；smoke 的 scope 固定为 both。`reference` 默认使用与源码同目录的 `reference_pages.json`，可用 `--reference-file` 替换。默认合成输入用于 Jev smoke 时只验证连接与契约，不验证真实企业。

从仓库根目录运行；环境变量优先于 `.env`。plan、reference、复放及离线评估不加载 `.env`。discover 默认输出路径保留公开版的 `output/leadflow-latest`；实际运行建议显式指定新目录，防止覆盖审计文件。复放使用者自有数据，公开样例不能当作完整实时运行缓存。

## 联网操作参考（本轮未执行）

在本地从空白 [.env.example](../.env.example) 配置凭据；不得发布真实值。以下命令可能计费，只有明确授权预算后才执行：

```bash
python3 leadflow.py jev-smoke
python3 leadflow.py discovery-smoke
python3 leadflow.py discover --country Brazil --product "Stainless Steel Flat/Profiles" \
  --candidate-limit 3 --supplemental-limit 0 --jev-batch-size 3 \
  --target 1 --max-api-calls 20 --db data/discover-check.sqlite --out output/discover-check
```

Jev smoke 最多尝试 3 次；discovery-smoke 使用内部固定小规模配置，并非由 CLI 的 `--max-api-calls` 控制。discover 的 Tavily、Jev、重试及可选补检共享总尝试上限。`--contact-max-api-calls` 默认为 0，启用后还受共享预算、候选数与每企业页数限制。预算是请求次数，不是金额，也不保证足够完成所有阶段。

旧 live 只有环境变量同时提供 `LLM_API_KEY` / `LLM_MODEL` 才使用可选 LLM；`LLM_CHAT_URL` 可配置，默认 OpenRouter。它不是 Jev 主流程，其分数与 HIGH / REVIEW 不可混用；本轮没有验证该联网路径。

两个评估工具使用 `--help` 查看参数，默认离线、要求新输出目录、不打开 SQLite。联系方式评估仅显式 `--live` 才请求 Tavily；尚无联系方式人工标签导入评分。名称评估默认使用公开合成样本清单。评估会在本地报告保存来源路径/哈希，**报告可能含绝对路径，不能直接提交**。

未实现 GUI、Codex SDK 编排、ERP / CRM 集成或自动外联。状态、CSV 与保护规则见[数据约定](data-contracts.md)。
