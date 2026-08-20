# Bedrock Guardrails 能力验证 Demo

纯 CLI，一条命令跑完 113 项断言，覆盖全部 6 类 guardrail 策略（含 Automated Reasoning checks）
× 4 种接入方式。其中 71 项完全不调用大模型：59 项打两个"独立于模型调用"的 API
（`ApplyGuardrail` / `InvokeGuardrailChecks`），12 项做 Automated Reasoning 的策略构建与形式化校验。
另外还实测了 `bedrock-mantle` 端点对 Guardrails 的支持情况。

区域：`us-east-1`。最近一次运行：**113 项，PASS 111，DIFF 2（Classic tier 中文，预期差异），FAIL 0**。

## 快速开始

前置条件：Python 3.9+，一份对 `bedrock:CreateGuardrail / UpdateGuardrail / DeleteGuardrail /
CreateGuardrailVersion / GetGuardrail / ListGuardrails` 与 `bedrock:InvokeModel`、
`bedrock:ApplyGuardrail` 有权限的 AWS 凭证，且目标区域已开通所用模型的访问权限。

```bash
pip install -r requirements.txt
./run_demo.sh                     # 建 guardrail + 发布版本号 + 跑全部阶段 + 出报告
./run_demo.sh --force-publish     # 强制切一个新版本号（默认复用已有最新版本）
./run_demo.sh --no-publish        # 全程用 DRAFT，不发布版本
./run_demo.sh --skip-setup        # 复用已有 demo guardrail 与最新版本
./run_demo.sh --only A,C          # 只跑指定阶段（A,B,C,D,V,S,K）
./run_demo.sh --only S,K          # 只跑两个独立 API 的覆盖测试（不调用任何模型）
./run_demo.sh --only R            # 只跑 Automated Reasoning checks（首次约 2 分钟构建 policy）
PYTHONPATH=src python3 src/cleanup.py   # 只删除本 demo 创建的两个 guardrail（含其所有版本）
```

可通过环境变量切换：`DEMO_REGION`、`DEMO_CONVERSE_MODEL`、`DEMO_OAI_MODEL_RUNTIME`、
`DEMO_OAI_MODEL_MANTLE`。

产物：`results/report.md`（Markdown 表格）、`results/raw_results.json`。有硬失败时退出码为 1，可直接进 CI。
一次真实运行的输出示例见 [`docs/sample-report.md`](docs/sample-report.md)。

## 版本发布与版本固定

Phase 0 在配置就绪后调用 `CreateGuardrailVersion`，把 `DRAFT` 冻结成不可变的数字版本
（首次运行得到版本 `1`），随后所有阶段都按这个版本号评估，报告头部会记录用的是哪一版。
默认复用已有的最新版本，避免反复运行导致版本堆积；`--force-publish` 才会切新版本。

Phase V 用一组实验证明版本固定的意义：往 DRAFT 里临时加一个屏蔽词 `TempWordZeta`，
然后用同一段文本分别打 DRAFT 和数字版本——

| 评估目标 | 结果 |
|---|---|
| `DRAFT` | `GUARDRAIL_INTERVENED`（命中 `wordPolicy.CUSTOM`，立即看到改动） |
| 版本 `N` | `NONE`（已发布版本被冻结，不受 DRAFT 编辑影响） |

跑完自动把 DRAFT 恢复成 `config/guardrail_standard.json`。生产应用应固定引用数字版本，
策略调整在 DRAFT 上做、验证通过后再发新版本并灰度切换。

## 核心结论

### 1. bedrock-mantle 不支持 Guardrails（已实测确认）

文档说明 Guardrails 只在 `bedrock-runtime` 上可用。实测行为比"不支持"更需要注意：

| 端点 | 带 `X-Amzn-Bedrock-Guardrail*` 头 | 实测结果 |
|---|---|---|
| `bedrock-runtime.us-east-1.amazonaws.com/openai/v1` | 是 | HTTP 200，输入被拦截，返回兜底话术 |
| `bedrock-mantle.us-east-1.api.aws/v1` | 是（同样的头） | HTTP 200，**头被静默忽略**，模型正常作答 |

探针刻意用自定义策略（拒绝话题"投资建议"、自定义屏蔽词 `Project Athena`）而不是有害内容，
这样模型自身安全训练产生的拒答不会被误判成 guardrail 拦截。C2 中模型完整输出了
"Project Athena 路线图汇报"，是 guardrail 未生效的直接证据。

**不会报错、不会有任何告警**，这是最大的风险点：客户从 `bedrock-runtime` 迁到
`bedrock-mantle` 时，请求依然 200，但防护已经悄悄失效。

用 mantle 时的可行方案（Phase C3 已验证）：应用层三段式 sidecar

```
ApplyGuardrail(source=INPUT) → 命中则不调模型
    ↓ 放行
bedrock-mantle /v1/chat/completions
    ↓
ApplyGuardrail(source=OUTPUT) → 拦截或脱敏后再返回用户
```

C3b 实测：输入放行 → 模型输出邮箱和电话 → 后置检查把输出改写为
`You can reach the customer at {EMAIL} or {PHONE}.`

同样的模式适用于 Bedrock Marketplace 模型、Custom Model Import、SageMaker 端点、自托管和第三方模型。

### 2. 独立调用的 API：有，但不存在独立端点

Guardrails 有两个"不调用模型也能用"的 API，但它们都挂在 `bedrock-runtime` 端点上
（`bedrock-runtime.{region}.amazonaws.com`），`bedrock-mantle` 上不提供。所以准确说法是
**独立于模型调用，而不是独立于 bedrock-runtime**：不需要 `modelId`、不消耗模型 token、
不产生模型费用，可以放在应用流程的任意位置（RAG 检索前、agent 每一轮、日志清洗、
甚至给非 Bedrock 模型做前后置校验）。

| | `ApplyGuardrail` | `InvokeGuardrailChecks` |
|---|---|---|
| 路径 | `POST /guardrail/{id}/version/{v}/apply` | `POST /guardrail-checks/invoke` |
| 需要先建 guardrail 资源 | 需要 | **不需要**，检查项内联在请求里 |
| 支持的策略 | 全部 6 类 | content filter、prompt attack、sensitive information |
| 返回 | `action=NONE\|GUARDRAIL_INTERVENED` + `assessments` + 脱敏后的文本 | 每类 `severityScore` / `confidenceScore`（0.0–1.0）+ PII 偏移量 |
| 谁做拦截决策 | Guardrail（按你配置的 BLOCK/ANONYMIZE） | **你的应用**，自己定阈值 |
| 方向 | `source=INPUT` / `OUTPUT` | 按 `messages` 的 role 传入 |
| 典型用途 | 生产拦截与脱敏、版本可控 | agent 循环内打分、灰度调阈值、多模型统一评分 |
| IAM 动作 | `bedrock:ApplyGuardrail` | `bedrock:InvokeGuardrailChecks` |

`InvokeGuardrailChecks` 的枚举与常规策略不同：`promptAttack` 只接受
`PROMPT_LEAKAGE | JAILBREAK | PROMPT_INJECTION`。实测同一条中文注入 prompt，三类分数都是 1.0。

### 3. 价格（us-east-1，2026-08 抓取自官方定价页）

计费单位是 **text unit = 最多 1000 字符**；超过按段数向上取整（5600 字符 = 6 units）。
只对你启用的策略收费，**Standard tier 和 Classic tier 同价**。

模型调用内联 guardrail、以及 `ApplyGuardrail`：

| 策略 | 价格 |
|---|---|
| Content filters（文本） | $0.15 / 1,000 text units |
| Content filters（图像） | $0.00075 / 张 |
| Denied topics | $0.15 / 1,000 text units |
| Sensitive information（PII） | $0.10 / 1,000 text units |
| Sensitive information（自定义 regex） | **免费** |
| Word filters | **免费** |
| Contextual grounding | $0.10 / 1,000 text units |
| Automated Reasoning checks | $0.17 / 1,000 text units / 每个 policy |

`InvokeGuardrailChecks` 单独一套、且更便宜：

| 策略 | 价格 |
|---|---|
| Content filters（仅文本） | $0.07 / 1,000 text units |
| Prompt attack（可脱离 content filter 单独用） | $0.08 / 1,000 text units |
| Sensitive information | $0.10 / 1,000 text units |

几个影响账单的细节：

- **contextual grounding 把 grounding source + query + 模型回答的字符数全部计入**，RAG 场景里
  检索到的上下文往往几千字符，这一项通常是最贵的，不是 content filter。
- word filter 和自定义 regex 免费，响应里 `sensitiveInformationPolicyFreeUnits` 就是这部分。
  中文关键词屏蔽改用 regex 既解决了分词问题，又不花钱。
- 输入和输出是两次计费。`Converse` 挂 guardrail 会同时评估 prompt 和 response。
- 官方示例：客服机器人每小时 1000 次请求，输入 200 字符（1 unit）+ 回答 1500 字符（2 units），
  只开 content filters 和 denied topics → 3000 units × ($0.15+$0.15)/1000 = **$0.90/小时**。

本 demo 一次全量运行（101 项断言，77 次带计费的 guardrail 调用）的实测消耗，
取自 `results/raw_results.json` 的 `usage` 字段：

| 计费项 | 数量 | 单价 | 折算 |
|---|---|---|---|
| sensitiveInformation（InvokeGuardrailChecks） | 84 units | $0.10/1k | $0.0084 |
| contentPolicyUnits | 54 units | $0.15/1k | $0.0081 |
| topicPolicyUnits | 47 units | $0.15/1k | $0.0071 |
| sensitiveInformationPolicyUnits | 48 units | $0.10/1k | $0.0048 |
| contentFilter（InvokeGuardrailChecks） | 15 units | $0.07/1k | $0.0011 |
| promptAttack（InvokeGuardrailChecks） | 11 units | $0.08/1k | $0.0009 |
| contextualGroundingPolicyUnits | 6 units | $0.10/1k | $0.0006 |
| contentPolicyImageUnits | 1 张 | $0.00075/张 | $0.0008 |
| wordPolicyUnits / regex free units | 48 / 48 | 免费 | $0 |
| **guardrail 合计** | | | **≈ $0.032** |

其中 sensitiveInformation 那 84 units 里有 75 units 来自单条 75,000 字符的截断测试——
这也说明超长文本是最容易把账单推高的场景。

模型调用（nova-lite + gpt-oss-20b 十余次）另计，同样是分币量级。

### 4. 四种接入方式的差异

| 方式 | 是否需要 guardrail 资源 | 返回 | 适用场景 |
|---|---|---|---|
| `Converse` / `InvokeModel` + `guardrailConfig` | 需要 | `stopReason=guardrail_intervened` + 兜底话术 | Bedrock 原生模型，最省事 |
| `ApplyGuardrail` | 需要 | `action` + `assessments` + `usage` | 任意模型/任意位置，RAG 检索前校验 |
| `InvokeGuardrailChecks` | **不需要** | 每类 severity/confidence 分数，不做拦截决策 | agent 循环内自定义阈值 |
| `PutEnforcedGuardrailConfiguration` | 需要 | 账号级强制，应用无需改代码 | 治理/合规兜底（本 demo 未调用，影响面是整个账号） |

`InvokeGuardrailChecks` 的枚举与常规策略不同：`promptAttack` 只接受
`PROMPT_LEAKAGE | JAILBREAK | PROMPT_INJECTION`（不是 `PROMPT_ATTACK`）。

### 5. 中文场景必须用 Standard tier

同一组用例跑 Standard 和 Classic 两个 guardrail：

| 用例 | Standard tier | Classic tier |
|---|---|---|
| 提示词注入（英文） | 拦截 | 拦截 |
| 提示词注入（中文） | 拦截 | **漏过** |
| 暴力请求（英文） | 拦截 | 拦截 |
| 暴力请求（中文） | 拦截 | **漏过** |
| 拒绝话题（中英文） | 拦截 | 拦截 |

Classic tier 官方只支持英/法/西。内容过滤和提示词攻击在中文下会漏检；denied topics 因为
基于话题定义和示例，中文仍能命中。Standard tier 强制使用跨区域推理
（`crossRegionConfig.guardrailProfileIdentifier = us.guardrail.v1:0`）。

延迟对比：Standard 约 340–570ms，Classic 约 110–220ms。

### 6. 两个非显而易见的坑（实测踩到）

**PII 脱敏默认不作用于输入方向。** 只写 `action: ANONYMIZE` 时，`source=INPUT` 的
ApplyGuardrail 返回 `NONE`，assessment 里连 EMAIL 条目都不出现；同样文本走
`source=OUTPUT` 正常脱敏。必须显式写 `inputAction: ANONYMIZE` + `inputEnabled: true`
才会对输入生效。BLOCK 动作不受此影响。

**词过滤（wordPolicy）按分隔符匹配，对嵌入式中文不命中。** 词表里有 `机密代号海神`：

- `帮我总结一下机密代号海神的进展。` → 不拦截
- `帮我总结一下 机密代号海神 的进展。` → 拦截

中文关键词屏蔽请改用 sensitive information 的自定义 regex（`ProjectAthenaCN` 用例已验证
可以直接命中无分隔符的中文），或用 denied topics。

## 两个独立 API 的覆盖测试（Phase S / K，不调用任何模型）

`./run_demo.sh --only S,K` 只打这两个 API，59 项断言，零模型调用、零模型费用。Phase S 用
一个专门配置的 guardrail（`config/guardrail_apitest.json`，同时包含 BLOCK / ANONYMIZE /
NONE 三种动作、单方向启用、图像模态、两条 regex），Phase K 完全不需要 guardrail 资源。

覆盖维度：

| API | 覆盖内容 | 断言数 |
|---|---|---|
| `ApplyGuardrail` | `source` INPUT/OUTPUT、`outputScope` FULL/INTERVENTIONS、6 类 content filter、BLOCK/ANONYMIZE/NONE 三种动作、按方向启用与门控、denied topics、自定义词 + PROFANITY 托管词表、PII 实体、自定义 regex、contextual grounding（GROUNDING + RELEVANCE + threshold 回显 + 部分覆盖）、`qualifiers`（grounding_source / query / guard_content）、多 content block、图像模态、text unit 计费、`guardrailCoverage`、`actionReason`、4 条错误路径 | 34 |
| `InvokeGuardrailChecks` | 5 个 contentFilter 类别逐个打分、3 个 promptAttack 类别、7 种 PII 实体、偏移量与 message/content 索引校验、role 枚举、单项检查、逐项 usage、超长输入截断、6 条错误路径、"不需要 guardrail 资源"的结构性证明 | 25 |

这套测试把一些文档里没写清的行为钉死成了断言：

- **detect-only 模式**：`action: NONE` 的策略（content filter MISCONDUCT、PII AGE、regex
  `SecretTag`）在 assessment 里 `detected: true`、`action: NONE`，整体 `action` 仍是 `NONE`，
  文本原样返回。适合先观察再决定要不要拦。
- **按方向门控会同时省钱**：denied topic 设 `outputEnabled: false` 后，`source=OUTPUT` 的调用
  `topicPolicyUnits` 直接是 **0**，即不评估也不计费。
- **BLOCK 优先于 ANONYMIZE**：同一段文本里 SSN（BLOCK）+ 邮箱（ANONYMIZE）→ 返回兜底话术，
  邮箱不会被脱敏输出，assessment 里连 EMAIL 条目都不出现。
- **自定义 regex 用自己的名字做占位符**：`TCK-1234` → `{TicketId}`，不是 `{REGEX}`。
- **contextual grounding 的 coverage 与计费口径不同**：grounding source + query 计入
  `contextualGroundingPolicyUnits`，但不算进 `guardrailCoverage.textCharacters.guarded`
  （实测 guarded=27 / total=110）。
- **text unit 计费边界**：1001 字符 → 每个启用策略各 2 units，两个 API 一致。
- **图像单独计量**：一张 PNG → `contentPolicyImageUnits: 1`、`guardrailCoverage.images 1/1`。
- **`actionReason` 三种取值**：`No action.` / `Guardrail blocked.` / `Guardrail masked.`，
  可以直接用来区分"拦截"和"脱敏"。
- **InvokeGuardrailChecks 的偏移量可直接切片**：`messages[messageIndex].content[contentIndex]
  .text[beginOffset:endOffset]` 精确等于被识别的 PII 原文，方便自己做脱敏。
- **超长输入会截断**：75,000 字符 → `truncated: true`，findings 上限 1000 条，计费 75 units。
- **枚举边界**：`contentFilter` 只接受 `HATE|INSULTS|SEXUAL|VIOLENCE|MISCONDUCT`（没有
  PROMPT_ATTACK），`promptAttack` 只接受 `PROMPT_LEAKAGE|JAILBREAK|PROMPT_INJECTION`，
  PII 实体共 32 种；传错一律 `ValidationException`。
- **打分不等于分类**：同一条攻击文本，`JAILBREAK` 和 `PROMPT_LEAKAGE` 常常同时 1.0，而
  `PROMPT_INJECTION` 只有在明确要求"无条件执行后续指令"时才升到 1.0。做阈值决策时建议取
  三类的最大值，不要依赖单一类别。

## Automated Reasoning checks（Phase R，第 6 类策略，不调用模型）

`./run_demo.sh --only R` 走完整条流水线，12 项断言。它跟其他 5 类策略最大的不同：**不是填几个
配置字段，而是要先把自然语言规则文档编译成形式化逻辑**。

```
CreateAutomatedReasoningPolicy
  → StartAutomatedReasoningPolicyBuildWorkflow(INGEST_CONTENT, 规则文档 txt/pdf)
  → 轮询 GetAutomatedReasoningPolicyBuildWorkflow 直到 COMPLETED（实测约 90 秒）
  → GetAutomatedReasoningPolicyBuildWorkflowResultAssets(POLICY_DEFINITION)
  → UpdateAutomatedReasoningPolicy        ← 关键一步，见下面的坑
  → CreateAutomatedReasoningPolicyVersion
  → 用 automatedReasoningPolicyConfig.policies 挂到 guardrail 上
  → ApplyGuardrail(source=OUTPUT) 拿 findings
```

源文档是 `config/ar_policy_source.txt`（一份 8 条规则的差旅报销政策，30 行）。实测从里面抽出了
**3 个自定义类型、11 个变量、19 条规则**，例如 `(=> (> receiptSubmissionDaysAfterExpense 30)
(not isExpenseReimbursable))`。换成你自己的业务文档只要替换这个文件。

七类 finding 里实测到五类（`impossible` / `tooComplex` 需要更极端的输入）：

| 断言 | 送进去的"模型回答" | finding |
|---|---|---|
| R01 | 45 天后提交 → "该费用不可报销" | `valid` + `supportingRules` |
| R02 | 我是外部承包商 → "你无权提交报销" | `valid` |
| R03 | 总额 1500 → "需要经理审批" | `valid` |
| R04 | 房价 400/晚 → "该住宿费可报销" | `invalid` + `contradictingRules` |
| R05 | 我坐了商务舱 → "商务舱可报销" | `satisfiable`（只在飞行 >8h 时成立） |
| R06 | "法国的首都是巴黎" | `noTranslations`（不属于该 policy 领域） |
| R07 | 单日餐费 120 → "餐费可报销" | `translationAmbiguous` |

### 踩到的坑与需要注意的点

- **构建完成不等于策略生效。** `INGEST_CONTENT` 的 build workflow 跑到 `COMPLETED` 之后，
  policy 的 DRAFT 定义**仍然是空的**——抽取结果只存在 build workflow 的
  `POLICY_DEFINITION` 资产里，必须自己取出来再调 `UpdateAutomatedReasoningPolicy` 写回。
  我第一次漏了这步，直接 `CreateAutomatedReasoningPolicyVersion`，结果发布出一个
  **0 规则 0 变量的空 policy，而且没有任何报错**。所以 Phase R 有一条断言专门校验
  "已发布版本里 rules>0 且 variables>0"。
- **AR 是 detect-only。** 即使 finding 是 `invalid`，`ApplyGuardrail` 返回的 `action` 依然是
  `NONE`，不会拦、不会改写。放行 / 重写 / 追问 / 兜底完全由你的应用决定。
- **配置漏了会静默跳过。** 用一个没挂 AR policy 的 guardrail 打同样的文本：
  `automatedReasoningPolicyUnits: 0`、`findings` 为空、**不报错**。上线前必须断言这个 units > 0
  （Phase R 的 R11 就是这条负向对照）。同理在 `Converse` / `InvokeModel` 上不打
  `guardContent` / XML 标签也会静默跳过。
- **`ApplyGuardrail` 必须带 claim。** 只传 `qualifiers: ["query"]` 的内容会报
  `ValidationException: No claim found in the content`——因为 `ApplyGuardrail` 不会像
  `Converse` 那样替你把模型回答追加成 claim。
- **findings 自带审计链**：每条规则引用同时给出 `identifier`（如 `CVQQRL3RH1Q2`）和
  `policyVersionArn`（精确到 `:2`），可以直接落审计日志、复现当时的判定。
- **翻译存在非确定性**：边界措辞会在 `invalid` 和 `translationAmbiguous` 之间摆动（同一句话
  实测 3 次里 2 次 ambiguous、1 次 invalid）。把前提写清楚（明确员工类型、明确房价字段）之后
  就稳定了。Phase R 因此给 finding 类型断言留了一次重试，但期望值本身不放宽。
- **多条 finding 时取最严重的那条**：严重度顺序 `translationAmbiguous/tooComplex` >
  `impossible` > `invalid` > `satisfiable` > `valid` > `noTranslations`，R12 断言了这个聚合逻辑。
- **计费**：$0.17 / 1,000 text units / 每个 policy，是所有策略里最贵的；本次 Phase R 实测
  共 7 units（7 次校验各 1 unit），约 $0.0012。

## 验证矩阵

| 阶段 | 内容 |
|---|---|
| 0 | 幂等创建/更新 demo guardrail（standard / classic，跑 S 时再加 apitest），`CreateGuardrailVersion` 发布数字版本 |
| A | ApplyGuardrail，Standard tier，19 个用例，`outputScope=FULL` |
| A2 | 同一批语言对照用例跑 Classic tier，做 tier 差异对比 |
| B | Converse + `guardrailConfig` + `trace=enabled_full`（`amazon.nova-lite-v1:0`）端到端拦截 |
| C | `bedrock-runtime` vs `bedrock-mantle` 端点对比 + mantle sidecar 模式 |
| D | InvokeGuardrailChecks 打分模式，带阈值断言 |
| V | 版本固定：DRAFT 改动可见、已发布版本冻结 |
| S | **ApplyGuardrail 覆盖套件，34 项断言，不调用模型** |
| K | **InvokeGuardrailChecks 覆盖套件，25 项断言，不调用模型、不需要 guardrail 资源** |
| R | **Automated Reasoning checks，12 项断言：从规则文档构建 policy → 发版本 → 校验 7 类 finding** |

Phase A 覆盖的策略：content filters（6 类）、denied topics、word filters（自定义词 + PROFANITY）、
sensitive information（PII 脱敏 / 信用卡拦截 / 自定义 regex）、contextual grounding
（幻觉拦截 + 正确回答放行）、输入与输出双方向、以及"正常问题不误杀"的基线用例。

## 目录结构

```
config/guardrail_standard.json   Standard tier，中英双语，5 类策略
config/guardrail_classic.json    Classic tier 对照
src/setup_guardrail.py           幂等创建/更新 + CreateGuardrailVersion 发布版本
src/cases.py                     用例与期望值
src/runner_apply.py              Phase A / A2
src/runner_converse.py           Phase B
src/runner_mantle.py             Phase C（端点对比 + sidecar）
src/runner_checks.py             Phase D
src/runner_version.py            Phase V（版本固定验证）
src/runner_standalone.py         Phase S / K（两个独立 API 的高覆盖测试，不调模型）
config/guardrail_apitest.json    Phase S 专用：detect-only、方向门控、图像模态、regex 脱敏
src/runner_ar.py                 Phase R（Automated Reasoning：建 policy → 发版本 → 校验 findings）
config/ar_policy_source.txt      Phase R 的源规则文档（差旅报销政策，8 条规则）
config/guardrail_ar.json         Phase R 专用 guardrail，只挂 AR policy
src/report.py                    控制台表格 + Markdown 报告
src/cleanup.py                   仅删除本 demo 的 guardrail
```

## 成本与安全说明

- 一次全量运行约 120 次 guardrail 评估 + 十余次模型调用，实测 guardrail 侧 ≈ $0.033
  （含 AR 的 7 units × $0.17/1k），模型侧同为分币量级。`--only S,K,R` 是 0 次模型调用。
  报告里保留了 `usage` 的 text units 明细，可用于成本估算。
- 默认按发布出来的数字版本评估（生产推荐做法）；`--no-publish` 可切回 `DRAFT` 做快速调策略。
  `DeleteGuardrail` 不带版本号时会连同所有版本一起删除。
- `cleanup.py` 只按名字删除 `demo-guardrail-standard` / `demo-guardrail-classic` /
  `demo-guardrail-apitest` / `demo-guardrail-ar` 以及 AR policy `demo-ar-expense-policy`，
  不会误删其它资源。
- 本 demo 不调用 `PutEnforcedGuardrailConfiguration`，避免影响账号内其它 Bedrock 调用。

## 免责声明

- 本仓库是个人验证 Demo，不是 AWS 官方项目，与 AWS 无关联。
- 结论基于 2026-08 在 `us-east-1` 的实测以及当时的 AWS 公开文档；AWS 的服务行为、tier
  能力、端点特性都可能变化，请以最新文档和你自己的实测为准。
- 用例中的邮箱、电话、员工号、项目代号均为构造的示例数据，不含任何真实个人信息。
- 运行本 demo 会在你的 AWS 账号里创建 guardrail 资源并产生少量费用，请先确认账号和区域。

## 更新记录

- **Phase R**：补上第 6 类策略 Automated Reasoning checks——从规则文档自动抽取形式化规则、
  发布 policy 版本、校验 `valid / invalid / satisfiable / noTranslations / translationAmbiguous`
  五类 finding 与规则溯源，12 项断言、零模型调用。
- **Phase S / K**：新增两个独立 API 的高覆盖测试套件（59 项断言，零模型调用），配套
  `config/guardrail_apitest.json` 与 `src/runner_standalone.py`。
- **价格与独立 API 说明**：补充 `ApplyGuardrail` 与 `InvokeGuardrailChecks` 的能力对比、
  逐策略价格表，以及本 demo 的实测计费明细。
- **Phase 0 / V**：`CreateGuardrailVersion` 发布数字版本，并用一组实验证明已发布版本不受
  DRAFT 编辑影响。
- **Phase C**：实测 `bedrock-mantle` 静默忽略 `X-Amzn-Bedrock-Guardrail*` 请求头，并给出
  三段式 sidecar 替代方案。
