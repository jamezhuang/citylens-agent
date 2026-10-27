# Langfuse Cloud 最小观察链路

代码直接位于稳定版CityLens。已安装并固定Python SDK 5.0.4；重新安装环境时执行 `.venv\Scripts\python.exe -m pip install -r requirements-observability.txt`。

## 填写本地配置

1. 在 Langfuse Cloud 创建组织和项目，在项目设置的 API Keys 中生成项目密钥。
2. 打开本目录 `.env`，已为你预留以下配置。只在本机填写，不把密钥发到聊天或提交Git。

```dotenv
CITYLENS_LANGFUSE_ENABLED=true
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_PUBLIC_KEY=填写项目的Public Key
LANGFUSE_SECRET_KEY=填写项目的Secret Key
```

BASE_URL必须和项目区域一致。预填的是欧洲；美国用 `https://us.cloud.langfuse.com`，日本用 `https://jp.cloud.langfuse.com`。密钥来自Langfuse，不是DeepSeek密钥。

官方说明：https://langfuse.com/docs/observability/sdk/overview 。本实现使用当前SDK的嵌套observation，不用旧版trace/span批量写入接口。

## 检查和发送测试链路

在CityLens目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Check-Langfuse.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\Check-Langfuse.ps1 -Send
```

第一条只报告字段是否填写，不发网络请求，也不输出值。缺配置时退出码2。

第二条明确进行Cloud鉴权，并发送 `citylens.connection_check` 测试链路。内部嵌套workflow、synthetic model和tool，没有真实模型调用、没有数据库访问、没有用户数据或真实订单。成功时输出trace_id，再按该ID调用Cloud Observations API读回记录。最多检查10次、两次之间等2秒；只有找到四类节点才输出cloud_delivery_verified=true。鉴权或flush成功本身不等于送达；延迟超过检查窗口时可稍后再次检查。

## 让正常业务开始上报

填完配置后重启原稳定版API，让start-api.ps1重新读取.env。原start.ps1在8090已被占用时不会自动重启旧进程，所以仅再次点击启动不保证新配置生效。可以让助手帮你重启并验证，无需把密钥发出来。

登录后 `GET /api/observability` 查看当前进程是否配置：

- configuration_required：没有密钥或区域地址不合法，业务仍能运行。
- configured：客户端已初始化，不代表云端鉴权或送达已验证。
- initialization_failed：SDK初始化失败，可看安全的异常类型。
- disabled：CITYLENS_LANGFUSE_ENABLED不是true。

正常聊天的Cloud链路为：`citylens.workflow` → `citylens.model` / `citylens.tool`（run_workflow工具内可能继续嵌套模型和工具）。metadata包含阶段、工具名、供应商、业务状态、耗时和合法UUID格式的本地run_id。模型generation有模型名称与供应商返回的input/output token用量。release为启动时业务Python源码的SHA256，便于区分未提交代码版本。

## 采集边界

默认不上传用户问题、模型回复、提示词、工具参数、工具返回正文、手机号、账号ID、会话ID、Cookie、Authorization或密钥；只导出citylens命名的手工span。失败只记录异常类型，不传异常消息或堆栈；取消单独标记。凭证通过SDK认证使用，不进入span数据。

没有独立统计缺失usage时的token数，也不猜费用。模型失败时可能没有用量。SDK故障不重复业务操作、不改变业务返回或重试策略；运行请求不等待flush，关停最多等待3秒flush。

## 本地验证证据

真实SDK配合内存exporter验证同一trace的父子关系、generation/tool跨度和不包含正文；本地测试不访问Cloud。另测试超时/取消、导出异常不影响业务、配置错误安全降级、run_id关联、配置检查不打印密钥、空读回不得算送达等。

2026-10-27用户填入项目密钥后：将.env区域从欧洲纠正为美国，鉴权成功；Cloud实际读回4个测试节点。原稳定版API与Java/MySQL/Redis/静态资源已启动，8090首页200、/ready返回ready。该验证证明合成测试链路上报成功，不代表本轮已经替用户做过真实登录聊天、支付或下单。密钥未写入此文档或测试报告。
