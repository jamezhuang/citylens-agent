# CityLens 场景化诊断评测 v1

目标：发现当前 Agent 的真实问题，形成可复现的失败证据。不是为简历制造通过率，也不在写规格时修改业务来迁就用例。

**增量状态：已新增隔离 ASGI + 内存业务适配器，支持显式执行 mock_rules / mock_model。已开始发展集诊断；真实 Java 服务适配器未实现，F07阶段预算注入仍受阻。原始规格定义阶段没有执行任何场景，历史静态校验文件不代表当前执行结果。**

## 入口

- [场景清单与逐条判据](SCENARIOS.md)
- [30个当前能力诊断场景](scenarios/core.json)
- [6个RAG/记忆待实现场景](scenarios/future.json)
- [合成数据与独立事实快照](fixtures/worlds.json)
- [执行记录约定](RECORD-CONTRACT.md)
- [评测协议与后续执行门槛](PROTOCOL.md)

```text
evaluation/
├── suite.json                  版本、清单和适配器状态
├── scenarios/                  人工定义的输入、预期行为、禁止行为
├── fixtures/                   固定合成世界，不导入实际数据库
├── catalog.py                  规格读取、引用与完整性检查
├── contracts.py                未来适配器的接口约定
├── grading.py                  独立离线判分器
├── __main__.py                 validate / plan / grade
├── tools/author_catalog.py     生成/维护第一版人工场景规格
└── spec_tests/                 判分器契约测试，已写但本轮未运行
```

## 本轮可以做的静态操作

在实验 CityLens 目录：

```powershell
.venv\Scripts\python.exe -m evaluation validate
.venv\Scripts\python.exe -m evaluation plan --split development
```

`validate` 只核对 JSON、字段、引用、数据版本与场景数量。`plan` 只输出场景清单和驱动输入，**不是 dry-run，更不代表场景成功**。它们不会导入 Agent，也不访问网络、Docker、数据库或模型。

`grade --records <文件>` 仅对实际采集的 mock 记录离线判分。执行入口单独放在 `python -m evaluation.execution`，不会后台自动启动，也没有接入原有 lab.manage test / smoke。既有167项回归不能当作这30个新场景的结果。

## 第一版范围

### DeepSeek配置准备

实验版 `CityLens/.env` 已预留空密钥，可参考 `evaluation/deepseek.env.example`。填写 `DEEPSEEK_API_KEY`；模型名预填项目之前使用的 `deepseek-flash`，若当前账号模型ID不同需一起调整。不要把密钥发到聊天中。

`python -m evaluation model-config` 只显示是否填写，不显示密钥、不验证连通、不调用模型。配置读取器只读取实验版文件，不继承系统环境变量、不读取稳定版密钥、不修改进程环境。现有实验工作台仍是规则模式；模型评测必须显式传入 `--allow-model`，填完配置不会自动产生付费调用。

### 场景范围

核心30个：单站6、多轮6、多站4、故障7、动作确认6、跨账号隔离1。发展集24个，本地可见保留集6个。保留集不是秘密，不能声称严格隐藏盲测；之后应独立增加未参与调优的新任务。

未来6个：RAG规则版本、缺证据、文档指令注入，以及临时偏好、永久更新、删除后不复活。它们标记为 deferred，不进入当前基线分母。

部分场景刻意挑战当前已知不足，例如有限重试覆盖范围、部分结果保留、违规候选的解释方式。失败是诊断结果，不能改成 xfail 或放宽断言后宣传整体通过。

## 判分原则

1. 用户需求的标准写在场景中，不把模型生成的计划当成正确答案。
2. 展示价格、评分、类别和总价向独立事实快照核对，不调用业务自己的校验函数判自己。
3. 不仅检查最后答案，还要采集实际调用尝试、写入次数和前后状态。
4. 无日志不等于无写入。完整采集标志缺失，判 not_evaluable，不能通过。
5. 没有结果不代表硬条件校验正确，不能靠空集合使约束断言自动通过。
6. 语义解释与权限泄漏由明确人工rubric复核。暂不使用模型裁判或关键词匹配冒充语义判断。
7. 合法工具顺序可以不同；只在有业务必要时约束调用次数或必须的读取。
8. 场景的自动规则通过而人工复核未完成，整体为 needs_review。
9. 相对价格测试先核对实际初选之后是否还存在更便宜候选；若初选已经最低价，标记blocked而不是强迫Agent编造替代店。

结果状态：pass、fail、needs_review、not_evaluable、blocked、not_run、deferred。报告必须同时给状态数量和判定覆盖率；零已判定案例的成功率为 null，不能写100%。

## 下一步，需另行开始执行阶段

判分器与适配器已有10项契约测试通过。mock适配器使用每场景新建的临时SQLite、内存业务服务和完整HTTP尝试账本；只为动作过期提供受控时钟，不宣称实现通用虚拟时间。F07无法注入的阶段明确blocked。

显式小批执行示例：

```powershell
.venv\Scripts\python.exe -m evaluation.execution --mode mock_rules --ids S01,D01,P01 --max-cases 3
.venv\Scripts\python.exe -m evaluation.execution --mode mock_model --ids S01,D01,P01 --max-cases 3 --allow-model --max-model-calls 20 --max-tokens 60000 --max-seconds 180
```

每次输出到实验区 `runtime/evaluation/<UTC时间>/`。`records.json`保留各步真实API输出与调用，`report.json`记录判分、模型尝试和用量。业务访问全部由MockTransport接管，不连接真实Java、MySQL或Redis。只有mock_model允许向配置中的DeepSeek官方HTTPS端点请求模型。

Token门槛包含调用前保守预留与返回后用量核算，不是货币硬限额；失败请求可能产生无法确认的用量，会单独计数。每场景仍使用Agent自己的任务预算。一次模型失败后Agent可能规则回退，需核对每步response.body.mode及运行事件，不把“配置了模型”等同于“全程模型执行”。

本地运行串行修改独立Python进程中的app.state，结束后恢复；不应在正在服务用户的Python进程中调用此适配器。模型评测先小批，不执行保留集，不自动修改业务代码，也不自动补上人工复核结论。

### 合并前预留集阶段

2026-10-27起，用户授权进入合并前回归。新增显式`--allow-reserved`开关，默认仍只执行开发集；未来场景仍不可执行。预留集现已用于诊断，之后不能将同一批案例宣传为未参与调优的隐藏测试，应另外增加新任务验证泛化。F07阶段注入仍标记blocked，不计通过。原始案例、判据与历史记录保留。

后续增量：de19b9b已实现F07阶段预算注入，最新执行不再blocked。驱动调整候选顺序，使指定101先进入真实详情读取；只有工作流完成实际校验并发出candidate_verified后才设置剩余2秒与get_shop估计3秒。没有预填核验结果、没有sleep碰运气。测量实际HTTP尝试是否在注入deadline之后启动。产品默认估计表为空，本次验证的是可信估计已提供时的拒绝策略，不宣称已实现生产延迟预测。必要详情核验现为确定性顺序读取，停止原因为verified_by_workflow，不再误称该阶段由模型自行结束；模型仍参与对话路由、规划和按需补充查询。
