简体中文 | [English](README.en.md)

# LeadFlow

**从海外公开信息中，发现有依据、可复核的 B2B 潜在客户。**

LeadFlow 是一个使用 Python 构建的海外企业发现与销售情报原型。输入目标国家和产品，系统自动搜索企业官网、提取业务证据、判断产品与客户角色的相关性，并通过历史去重和分级审核，输出结构化的候选客户名单。

海外客户开发的难点不只是找到更多网站，而是**识别哪些企业值得进一步研究，并让判断过程能够被核查和复用。**

## 实际运行结果

以巴西不锈钢板材及型材市场为例，一次早期真实运行得到：

| 搜索结果 | 去重候选域名 | 具有可用官网证据 | HIGH 待复核 |
|---:|---:|---:|---:|
| 114 | 47 | 41 | 20 |

其余 21 条证据候选分为 REVIEW 10 条和 LOW 11 条；12 条记录观察到公开邮箱。

这是一组历史原型运行数据，并非当前发布版本的独立复测。HIGH 表示较高的人工审核优先级，**不等于真实采购意向或销售认可的有效客户**。

后续版本已完成美国不锈钢市场的小规模端到端实时验证，并针对跨市场切换、企业名称识别与联系方式处理进行了迭代。

[查看公开示例](example_output/README.md) · [查看验证记录](docs/verification.md)

### 输出预览

![LeadFlow 客户发现结果预览](assets/leadflow-preview.png)

*历史真实运行结果的脱敏摘录。分级代表模型初筛优先级，不代表已确认采购意向。*

## LeadFlow 如何工作

输入是 **Country + Product**，例如 `Brazil + Stainless Steel Flat/Profiles`。

| 环节 | 处理内容 |
|---|---|
| 发现 | 结合市场、材质、产品形态及客户角色生成搜索查询 |
| 取证 | 筛选企业官网，提取产品、经营活动与市场信息 |
| 判断 | 通过规则与 TypeSafe Jev 判断相关性及商业角色 |
| 治理 | 核验企业名称、发现重复记录、保护历史业务状态 |
| 交付 | 输出分级候选、来源证据、联系方式及人工审核事项 |

主要输出包括：

- `leads.csv`：供业务人员浏览和筛选的企业名单
- `leads.json`：更完整的企业记录与证据
- `summary.json`：任务结果、API 用量、异常与执行状态
- 审核文件：名称冲突、疑似重复和联系方式归属等待确认事项

### 三个关键设计

**1. 证据优先，而不是让 AI 直接生成客户名单。**

搜索命中不等于企业匹配。系统优先使用官网内容，并保存来源 URL 和证据摘录。缺少依据的信息保留为未知，不推测企业采购量、进口能力或联系人邮箱。

**2. 用确定性规则处理明确问题，用 AI 处理语义判断。**

域名规范化、网页过滤、邮箱归属和数据更新等操作由 Python 规则执行；TypeSafe Jev 负责评估产品相关性与可能的商业角色。HIGH / REVIEW / LOW 用于安排复核优先级，而非直接替代销售判断。

**3. 把一次性搜索变成可重复运行的工作流。**

系统使用 SQLite 保存企业历史，避免重复建档；企业名称变化、跨域疑似重复和联系方式歧义有独立的审核机制。同时限制 API 请求预算，记录部分失败，避免将“程序运行结束”误认为“业务目标已达成”。

## 快速开始

需要 Python 3.10+。运行时仅使用 Python 标准库和外部 API，不需要安装第三方 Python 包。

克隆仓库并运行离线测试：

```bash
git clone https://github.com/Wuwalaya/leadflow.git
cd leadflow

python3 -m unittest -v
```

查看不同市场的搜索计划：

```bash
python3 leadflow.py plan \
  --country USA \
  --product "Stainless Steel Flat/Profiles"
```

不使用 API，也可以通过随仓库提供的**合成测试数据**验证处理与去重流程：

```bash
CHECK_DIR=$(mktemp -d)

python3 leadflow.py reference \
  --db "$CHECK_DIR/demo.sqlite" \
  --out "$CHECK_DIR/run1"

python3 leadflow.py reference \
  --db "$CHECK_DIR/demo.sqlite" \
  --out "$CHECK_DIR/run2"
```

预期首次新增 2 条合成账户，第二次识别并更新原有 2 条，总账户数保持不变。该 Fixture 不包含真实客户或真实邮箱，也不代表联网发现能力。

### 联网使用

联网发现需要配置 `TAVILY_API_KEY` 与 `TYPESAFE_API_KEY`，参考 [.env.example](.env.example)。

首次使用建议先进行小规模 Smoke Test，再根据明确的调用预算运行 `discover`。外部请求可能产生费用，调用次数上限不等于金额上限。

详细命令和参数说明见 [架构与运行说明](docs/architecture.md)。

## 技术实现

| 组件 | 技术 |
|---|---|
| 工作流与数据处理 | Python 3.10+ |
| 网页搜索与提取 | Tavily Search / Extract |
| 语义判断 | TypeSafe Jev · System One API |
| 数据存储 | SQLite |
| 测试与评估 | unittest、离线 Fixture、人工审核模板 |

项目由开发者独立设计与实现，使用 Codex 辅助代码开发、测试与迭代。Codex 不参与运行时客户发现流程。

## 当前验证状态

| 场景 | 状态 |
|---|---|
| 巴西不锈钢板材/型材 | 有历史真实运行记录及离线回归 |
| 美国不锈钢板材/型材 | 小规模实时全链路验证通过，仍属实验性市场配置 |
| 美国碳钢板材 | 配置和处理流程通过离线验证；未做实时效果评估 |
| 联系方式补检 | 已验证单页面 Contact Extract；尚未证明邮箱覆盖率提升 |
| 业务质量 | 缺少独立人工标注及销售反馈，不能报告可靠的准确率或实际 ROI |

当前支持的目标组合以项目配置为准。企业官网证据可能不完整或过时，域名不一定对应独立法人，公开邮箱也不代表可投递或允许联系。

LeadFlow 当前定位为**辅助研究和人工复核的原型**，尚未接入 ERP / CRM、自动发送营销邮件或投入企业生产环境。它是个人独立项目，不代表任何企业的官方系统。

## 进一步阅读

[架构设计](docs/architecture.md) · [数据约定](docs/data-contracts.md) · [验证记录](docs/verification.md) · [变更记录](CHANGELOG.md) · [发布检查](docs/release-checklist.md)

[English documentation](README.en.md)