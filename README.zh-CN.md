# Toolcall Contract · 工具调用协议验收套件

在接入新模型、升级 SDK 或更换网关之前，用同一组测试检查工具调用语义是否保持完整。

重点检查：**重复调用、参数丢失、结束事件缺失**。流式输出、取消、重试和 fallback 都记录为统一事件轨迹，
交给独立检查器判定，输出可阻断 CI 的退出码与 JUnit 报告。

## 立即运行

需要 Python 3.11+，核心没有第三方运行依赖。

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .
tcc demo --save-traces
```

默认运行 **3 种协议 × 9 场景 = 27 项离线检查**，不需要 API Key。`reports/` 包含：

- `report.json`：结果、失败代码、attempt 和事件位置。
- `junit.xml`：CI 测试报告。
- `summary.md`：目标与场景结果表。
- `*.trace.json`：使用 `--save-traces` 时保存，包含实际参数，可离线复验。

```powershell
# 故意丢失参数，应返回退出码 1
tcc demo --mutation argument-loss --scenario basic

# 本机真实 HTTP 客户端验收
tcc local

# 加入实际 OpenAI / Anthropic Python SDK，共 54 项
python -m pip install -e ".[sdk]"
tcc local --sdk --report-dir reports/local
```

## 已实现

支持 Chat Completions、Responses、Anthropic Messages。原始 HTTP 和 Python SDK 使用相同语义检查器。
SSE 支持任意字节切分、UTF-8 跨包、多行 data、CR/LF/CRLF、心跳和大小限制。

场景包括普通调用、中文/emoji/转义、嵌套参数、并行交错调用、参数生成中取消/重试/fallback，以及
参数完成但响应未提交时的取消/重试。核对名称、ID、参数类型和值、调用次数、完成事件、关闭和执行记录。

故障库制造重复调用、参数丢失、缺失结束标记、身份变化、JSON 截断、重复完成和快照不一致。
本地服务器还支持断线与 429，用来验证实际客户端的处理行为。

## 接入模型和网关

复制 `examples/targets.json` 为 `targets.local.json`，修改 `base_url` 和 `model`。
`base_url` 为 API 根路径，通常以 `/v1` 结尾，不包含 `/chat/completions` 等操作路径。

```powershell
$env:MODEL_API_KEY = "your-key"
tcc probe --config targets.local.json --report-dir reports/live
```

多模型、SDK、fallback 配置参考 `examples/matrix.json`。真实探测会产生 API 用量，完整 9 场景每个目标最多
12 个请求。测试使用合成数据，只记录执行事件，不调用业务函数。

退出码：`0` 全部通过；`1` 语义/传输失败；`2` 配置/输入错误；`130` 手动中断。空选择不会显示通过。
默认报告不保存参数，不记录服务端错误正文和 API Key。

## 结果边界

离线 fixture 通过，只说明检查器和内置适配器通过测试；本地 HTTP/SDK 验证真实客户端如何处理这些流。
只有配置真实端点执行 `probe`，才能得出该端点的验收结果。

Chat SDK 隐藏 `[DONE]`，其结束证据标记为 `sdk-eof`。缺失 `[DONE]` 的流可能在 SDK 中正常结束，
而 HTTP 会报错。严格 CI 应额外运行原始 HTTP，或使用 `probe --require-wire`。

内置重试/fallback 在客户端注入断线，验证参考执行器隔离状态，不能证明网关内部路由行为。
现有 Agent、网关或跨语言 SDK 应按 [统一轨迹规范](docs/contract.md) 记录实际事件，再用 `tcc replay`。

取消发生于同步事件边界，验证关闭和停止执行，不能证明服务端停止计费或异步竞态安全。记录器等整个流
验收完成才记录执行；生产业务跨进程幂等仍由业务系统负责。

## 开源工程

包含中英文文档、MIT 许可证、贡献指南、问题模板、GitHub Actions、发行包构建及 SDK 集成测试。
当前 0.1 alpha，暂不覆盖 Realtime/WebSocket、旧式 function_call、服务端工具、多轮结果回传。

```powershell
python -m pip install -e ".[test,sdk]"
python -m pytest
python -m ruff check .
python -m build
```

参见 [英文 README](README.md)、[适配器接入](docs/adapters.md)、[CI 配置](docs/ci.md)。
