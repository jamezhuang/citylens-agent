# 5 分钟演示 CityLens Agent

这个案例从自然语言找店开始，到确认领券、查询订单结束。所有请求经过项目现有 FastAPI、Agent、Workflow、Harness 和 SQLite 会话逻辑；商户、登录身份、优惠券和订单使用固定合成数据及内存 HTTP 服务。

默认是**规则 Agent 演示**，不调用大模型、不需要密钥、不需要 Docker。它演示工程闭环和工具调用，不代表大模型理解能力或真实交易。

## 最快开始

需要 Python 3.11+。在终端执行：

```bash
git clone https://github.com/jamezhuang/citylens-agent.git
cd citylens-agent/CityLens
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe demo.py --interactive
```

macOS / Linux：

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python demo.py --interactive
```

也可以在仓库的 **Code → Codespaces → Create codespace on main** 中打开，等待依赖安装，然后运行：

```bash
cd CityLens
python demo.py --interactive
```

在领券草稿出现后，输入 `yes` 才提交内存演示订单；输入其他内容停止，程序返回非零状态。自动播放或 CI 使用 `python demo.py`，脚本将明确模拟用户确认。

## 跟着讲一遍

| 步骤 | 输入 / 操作 | 观看重点 |
|---|---|---|
| 1 | 找人均150以内的火锅，不限商圈 | 解析条件，查商户并读取详情校验 |
| 2 | 预算改成100以内 | 同一个 session 保留火锅条件，更新预算 |
| 3 | 两人先吃火锅再唱歌，总预算400元 | 同一会话切换两站需求；按固定事实验证人均参考价之和 × 2 |
| 4 | 找人均150以内的火锅，有优惠券吗，不限商圈 | 查询商户及可用券，为演示固定选择101号店的9001号免费券 |
| 5 | prepare_voucher | 展示 prepared 草稿，此时没有创建业务订单 |
| 6 | 输入 yes / 脚本模拟确认 | 提交一次内存订单，显示 order_id |
| 7 | 再确认一次 | 相同草稿返回相同订单，不重复提交 |
| 8 | 查询动作状态 | 回查订单，核对回执和订单号 |

终端逐步输出真实 API JSON 和业务 HTTP 工具调用名称；这些名称对应业务适配器操作，不应解释成每一步都由 LLM 自主选择工具。

每次运行创建独立临时 SQLite 和内存商户世界，结束后清理。不同次运行的 session_id、run_id、action_id 会变化；重复运行不会累积订单。固定场景时间是2030年，只用于可复现的券有效期测试，不是实时商户信息。

## 看结果与验收

- [已执行的完整示例记录](demo/transcript.md)：无需安装即可在 GitHub 阅读。
- `CityLens/reports/demo/transcript.md`：本次逐轮执行结果。
- `CityLens/reports/demo/records.json`：API、调用账本及状态证据。
- `CityLens/reports/demo/summary.json`：7项验收；全部通过返回退出码0，否则为1。

验收覆盖：8步 API 成功、会话连续、预算和品类保留、两站独立事实算价、确认前零订单写入、重复确认只生成一单、回执一致。它是这一个固定案例的验收，不是通用成功率。输出目录可用 `--output <目录>` 修改。

## 演示完整产品界面

终端入口无需启动网页。要操作已有 Vue 工作台，请按[仓库首页](../README.md#本地运行)安装 Docker、Python 和 Node 依赖并启动，然后访问 `http://127.0.0.1:8090`。

1. 使用测试手机号登录，在本机 Java 调试日志或 Redis 中读取演示验证码。
2. 在“智能助手”依次输入前三条需求，展开候选店铺与行程卡片。
3. 查询店铺优惠券，打开领券预览，讲解草稿与订单的区别。
4. 点击确认，打开“我的订单 / 券包”查看结果。

网页使用本地 Java 数据库，候选店铺和券可能与本指南的合成世界不同；选择页面实际可用的券，不要硬套101/9001。截图见[智能助手](images/assistant.png)、[发现店铺](images/discover.png)、[探店社区](images/community.png)。当前仓库没有公开在线业务服务。

## 需要真实模型时

完整产品的模型配置见首页及 `.env.example`。现有评测适配器也支持 `mock_model`，调用真实模型但仍模拟业务接口；配置后按[评测说明](../CityLens/evaluation/README.md)显式运行。`demo.py` 始终使用规则模式，不会读取密钥后自动付费调用。
