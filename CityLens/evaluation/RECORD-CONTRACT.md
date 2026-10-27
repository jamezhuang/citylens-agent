# 执行记录与证据约定

这是未来适配器需要产出的格式约定，不是本轮已经采集的结果。示例中的哈希需由实际规格计算，禁止拿示例生成通过率。

## 外层记录

每个 JSON 数组条目对应一个场景的一次重复运行：

```json
{
  "record_version": 1,
  "case_id": "S01",
  "suite_version": "citylens-diagnostic-v1",
  "case_sha256": "由catalog.digest计算",
  "fixture_sha256": "由catalog.digest计算",
  "execution_status": "executed",
  "mode": "mock_rules",
  "adapter_version": "尚未实现",
  "repeat_index": 0,
  "code_revision": "实际提交",
  "dirty_diff_sha256": null,
  "prompt_version": null,
  "model": null,
  "started_at": "实际UTC时间",
  "duration_ms": 0,
  "steps": [],
  "reviews": {}
}
```

`execution_status` 为 executed / blocked / not_run。异常中断或适配器能力不足必须保留 reason，不能悄悄丢掉场景。重复实验每轮单独存文件，当前离线grade拒绝一个文件中的重复 case_id，以免覆盖样本。

## 每一步

```json
{
  "id": "ask",
  "actor": "alice",
  "outcome": "success",
  "error_code": null,
  "response": {"http_status": 200, "body": {}},
  "calls": [],
  "evidence": {"calls_complete": false},
  "state_before": {},
  "state_after": {},
  "metrics": {
    "calls_started_after_cancel": 0,
    "calls_started_after_deadline": 0
  }
}
```

`response.body` 保留API结构。直接工作流调用需要显式标注transport，不要虚构真实HTTP请求；可在规范化层记录等价API业务结果，但真实HTTP边界场景必须由ASGI适配器采集。

`outcome` 与 `error_code` 必须从实际异常、HTTP、业务响应及事件共同推导。不能按场景期望填值。success/no_results/upstream_error/business_rejected/rate_limited/timeout/cancelled/partial 等需要适配器提供明确映射；无法确定时标记证据不足，不用答案关键词猜测。

## 调用账本

每次尝试单独记一条，包括失败和重试：operation、kind、参数摘要、开始/结束相对时间、结果类型、异常类型。成功事件数量不能代替调用尝试次数。

标准操作名包括：search_shops、get_shop、create_voucher_order、query_voucher_order、cancel_order、mock_payment、publish_blog、update_memory、delete_memory。具体API到标准名的映射由适配器固定版本化，不能临时为通过测试改变。

kind=read：业务或记忆读取。kind=write：显式业务/长期记忆修改。每轮聊天自动保存的会话消息、轨迹和只读检查点属于内部状态，单独留在状态快照，不计入订单写入账本。测试初始化数据也不算被测Agent的业务写入。

`calls_complete=true` 只能在指定入口的全部工具/业务调用均被拦截记录后设置。若只采集了前端事件，不能保证所有调用都在其中。

## 动态引用与故障注入

输入中的 `{"$ref":{"step":"find","path":"response.body.run_id"}}` 只允许读取之前步骤的观察字段，不能eval、执行模板表达式或访问本地文件。引用解析是驱动器职责，不由Agent决定。

faults 是驱动器私有配置：按操作、次数或对象ID注入超时、429、业务拒绝、写入成功但响应丢失等行为。只将用户消息和必要上下文交给Agent，不将断言、标准答案或未来故障计划注入提示词。

可控时钟必须进入被测模块相应时间来源，不能仅改变记录中的时间。F07的阶段注入尚待实现，无法实现时应blocked，不准用预制回答代替Agent执行。

## 人工复核

```json
{
  "reviews": {
    "subjective-disclosure": {
      "decision": "pass",
      "reviewer": "实际复核者",
      "reviewed_at": "实际UTC时间",
      "rationale": "对照原始答复与场景rubric给出理由"
    }
  }
}
```

没有具名复核、时间、理由与decision，manual断言保持needs_review，不自动通过。

## 数据与保密

记录不得包含真实模型密钥、登录token、Cookie头、真实手机号或其他用户数据。测试身份使用alice/bob替代。原始事件也需要脱敏；真实服务快照与模拟fixture分别管理。

当前离线判分只接受mock_rules/mock_model，因为它们有固定事实世界。lab_rules/lab_model需要独立采集实时事实，框架会拒绝把合成fixture当成真实Java数据。
