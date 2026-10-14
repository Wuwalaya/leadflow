# 合成离线输入

本目录全部由开发者构造，不含真实公司观察、联系人或 API 响应。`.example` 是保留域名，所有文本仅用于离线测试。结果中的 `official_observed` 等字段是解析器对合成输入的技术状态，不能用作公司事实。

- [根目录 reference_pages.json](../reference_pages.json)：两家虚构巴西不锈钢企业、两个页面、无邮箱，用于 reference 和去重演示。没有伪造采集时间。
- [名称评估输入](name_evaluation/synthetic-name-cases/)：一条自述可识别名称、一条集团歧义。`leads.csv` 只有加载器所需的 Company Name / Website 两列，**不是完整 discover CSV**；`leads.json` 是明确标记的合成记录。`extraction/synthetic.json` 借用提取结果结构测试加载器，不是保存的真实响应。
- [联系方式输入](contact_pages.json)：合成表单文字，无邮箱，用于缓存评估路径演示。它不证明真实表单可提交或邮箱覆盖率提高。

`evaluate_company_names.py` 的内置清单只指向这些合成样本。评分、证据校验和人工标签要求沿用源基线；没有内置私人目录或真实客户名单。联系方式评估可复用相同候选，使用上述表单缓存。

测试内的 URL、名称和邮箱字面量用于断言；过滤器测试中的目录/社交站域名是被拒绝的目标。此前已公开的少量企业例子留在 [example_output](../example_output/README.md)，与本目录用途分开。
