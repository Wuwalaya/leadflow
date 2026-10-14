# 数据约定与解释边界

本文依据 [leadflow.py](../leadflow.py)、[target_config.py](../target_config.py)、[company_names.py](../company_names.py) 和 [contact_enrichment.py](../contact_enrichment.py) 记录现有行为，不定义新 schema 或迁移方案。修改相关代码时同步更新本文。

## 目标与证据

`TargetConfig.as_dict()` 输出 `country`、`product`、`material`、`forms`、`scope`、`allowed_forms` 和 `support_status`。国家/产品使用规范名称；支持组合见 [README](../README.md)。`support_status` 是配置标记，不是某次运行成功的证明。

不锈钢 `flat` 包含 sheet_plate / coil_strip；`profiles` 包含 flat_bar / angle_channel / profiles_other；碳钢仅 sheet_plate。泛称 bars 不自动证明 flat_bar，材料与形态必须属于同一产品。美国地域规则要求明确国家文字，不把 `contact us`、`.com` 或单独州缩写视为所在地证据。

搜索命中只是候选。记录应保留官网来源 URL 和对应摘录；证据存在性校验不等于语义真实、法人核实或采购意向确认。未知采购意向、进口能力、规格、数量与认证保持 unknown，不以模型概率补齐。

## discover 分桶与业务目标

默认 high threshold 为 0.75，review threshold 为 0.40；CLI 要求 `0 <= review <= high <= 1`。`qualification_bucket()` 同时使用总体相关性、材料相关性、产品形态相关性及客户类型：

| qualification_status | 当前条件 |
| --- | --- |
| HIGH | 三项概率均达到 high，客户类型不属于 unknown / other / mill |
| REVIEW | 总体与材料达到 review，且产品达到 review 或客户类型属于上述模糊类别；未先进入 HIGH |
| LOW | 其余成功分类结果 |
| UNCLASSIFIED | Jev 批次失败后保留的未分类审计记录，概率可为 null |

`relevance_probability`、`material_relevance_probability`、`product_relevance` 是相应概率；不锈钢另保留兼容字段 `stainless_relevance_probability`。这些值不是成交率或正确率。

`final_qualified_leads` 是 HIGH + REVIEW 的不同 `account_id` 数，`target_met` 判断该数是否达到 target。`jev_high_match` / `jev_review` / `jev_low_match` / `jev_unclassified` 按记录计数；账户指标按各自摘要定义去重，不要把搜索条数当成公司数量。

旧 `reference/live` 使用 `fit_score`、`eligible_for_business_review` 与 `classification_mode`，没有同样的分桶契约。只有 `llm_evidence_checked` 且产品、买方角色、国家三项得分均大于零时，`eligible_for_business_review` 才为 true；rules_only 不进入该计数。`reference` 的 `target_met` 固定为 false；旧 `live` 的目标按不同账户的证据审核候选计数，不能解释成 Jev 合格线索。

## discover 技术状态与退出码

| run_status | 含义 | CLI 退出码 |
| --- | --- | --- |
| completed | 没有未解决的阶段失败；允许没有候选或未达到业务目标 | 0 |
| partial | 存在失败，仍有有效 Jev 分类结果，包括 LOW | 1 |
| failed | 存在失败，且没有有效 Jev 分类结果 | 1 |

参数校验和缺少必要凭据返回 2；主流程中断也可直接返回 1，此时不保证完整摘要已写出。该状态表仅适用于 discover，不能套用于旧 live/reference 或独立评估命令。

`stages` 保存请求成功/失败、页面失败和输出数量，状态为 completed / partial / failed / skipped；跳过时保存原因。`evidence_validation` 单独记录候选输入、有效证据和拒绝数量。

`pipeline_coverage.complete` 要求 search、extract_primary、jev 三个核心阶段均 completed 且有输出；它与业务目标、整体 run_status 不等价。正常空搜索可 completed 且覆盖为 false；可选阶段失败也可能在核心覆盖为 true 时令运行 partial。

复放不调用 API、不重新分类。它的 completed 只表示该次本地持久化完成，API 阶段 skipped，覆盖为 false，不能用来覆盖源运行的失败事实。

## 名称与主体状态

| name_status | 含义 |
| --- | --- |
| official_observed | 自动识别到与官网主体对应的名称证据，仍非人工确认 |
| needs_review | 域名/搜索提示、证据冲突、集团歧义或未核验的旧记录 |
| unknown | 无足够官网正文或主体无法确认 |
| human_confirmed | 可信人工审查输入；自动提取和 Jev 不生成此状态 |

`name_evidence` 保存 URL 与 quote；`name_reason` 解释状态，`name_candidates` 保留候选。CSV 名称非空不表示已确认，`name_review.json` 提供旁路审查信息。

普通复放不重做名称识别。旧记录缺少元数据时使用 needs_review / legacy_unverified。人工标签中的姓名、审核人和摘录必须经过相应校验；名称评估的开发参考答案不属于 human gold，也不是独立测试集。

## 联系方式与评估指标

主流程已有邮箱提取与可选补检是两层行为。补检默认 `--contact-max-api-calls 0`，启用后只选择 HIGH / REVIEW 且 `emails` 为空的候选。企业数、页数和调用次数分别受限；重试消耗补检与共享总请求预算。

新增邮箱要求官网来源、匹配的规范化 hostname、可核查的主体名称证据，且不属于占位、制作方、其他企业或非销售用途。集团域名、免费邮箱以及子域/根域差异可能被拒并保留原因；不能为了提高覆盖率放宽身份要求。

`contact_observations` 保存 type（email/contact_form）、value、role、source_url、quote、observed_text、validation_status、verification、reasons、identity_evidence。accepted 只表示公开证据通过自动规则；不保证邮箱可投递或表单可提交。

`contact_diagnostics` 分别保存主体校验、页面发现、正文获取、匹配数量和内容可见性限制；`outcomes` 可同时保留多种结果。没有观察到邮箱不等于官网没有邮箱。抓取失败、缓存缺失、保护/编码、规则拒绝分别保留；提取遗漏或错误拒绝需要人工证据才能确认。

联系方式覆盖率以前后相同的 HIGH / REVIEW 候选记录为分母；分母为零时为 null。旧导出邮箱继续保留，不因出现在覆盖率中而升级为身份已验证。`legacy_email_records_requiring_review` 另记待复核旧邮箱记录；`new_email_records` 计数原无邮箱而新增邮箱的候选记录。

`additional_cost` 记录实际尝试、API 报告的 credits 和用量完整性；credits 不是货币金额，返回 0 也不证明账单免费。缓存读取不算 API 调用。

人工模板中 sales_decision 为 accepted / rejected / hold；销售接受率的约定分母为 accepted + rejected，另报未审核与 hold。当前联系方式评估只有空白模板，`human_review` 的销售接受率和 HIGH 准确率为 null，尚无人工标签导入评分。

## SQLite 与去重

现有表为 accounts、observations、reviews、events、runs。accounts 存当前展示信息、名称证据和 latest_json；observations 保存每次运行的原始观察，reviews 保存待审核关系，events / runs 保存动作及运行摘要。增量列迁移由 `Store` 执行。

- hostname 归一化去除 www、大小写与路径差异，保留子域；没有用末两段域名替代完整 hostname，也没有 Public Suffix List 实现。
- 优先使用人工批准的域名别名或已人工核实的注册号；注册号冲突可保留不同账户。同域默认更新原账户，跨域同名/近似名进入审核，不自动合并集团。
- 更新保留 owner、status、first_seen。自动名称不会覆盖人工确认；较弱证据和同级冲突按现有保护规则处理，原始新观察仍可追踪。
- 受保护的旧主体与新联系方式主体冲突时，新增联系方式会被拒，保留 `saved_account_identity_conflict`，不归入旧主体。
- 同数据复放保持账户去重，不代表数据库完全不写入：观察、事件和运行记录仍会增加。

## 导出文件

两种 CSV 均使用 UTF-8 BOM，并对可能触发表格公式的值作转义。现有列顺序保持如下。

`reference/live`：

```text
Company Name, Website, Emails, Email Roles, Type, Matched Products,
Fit Score, Review Eligible, Classification Mode, Purchase Intent,
Import Capability, Dedup Action, Account ID, Source URLs, Unknowns
```

`discover`（包括复放）：

```text
Company Name, Website, Country, Customer Type, Public Email,
Product Match, Product Relevance Probability, Qualification Status,
Evidence URL, Evidence Summary, Review Notes
```

旧格式没有 Country 列，国别保存在 JSON / SQLite。JSON 中旧格式 `emails` 为带 value / role 等信息的对象列表；discover 通常为字符串列表，导出兼容两者。

`leads.json` 保留完整记录，`summary.json` 保存运行摘要，`duplicate_review.json` 是当前运行的重复审核项，`name_review.json` 保存名称状态。存在补检诊断时，discover 另导出 `contact_review.json`。搜索/抽取、Jev 请求/响应、拒绝原因、错误与证据文件按实际经过的阶段生成，不能假定每个模式都有全部文件。

独立联系方式评估生成 `contact_evaluation.json`、`contact_comparison.csv`、`leads.json` 和 `lead_review_template.json`；实时模式另保存 `contact_api_responses.json`。它与 discover 的同名 `contact_evaluation.json` 内容层级不同：独立评估包含 metrics / samples / 来源哈希等报告，discover 保存补检 metrics。不要混用两者结构。
