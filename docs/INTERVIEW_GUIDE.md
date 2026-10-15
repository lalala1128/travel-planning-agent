# 面试演示手册

这份手册的目标不是让你背代码，而是帮助你把项目讲成一个完整的工程故事：问题是什么、为什么这样设计、系统如何运行、失败时怎么办、还有哪些可以继续改进。

## 一句话介绍

这是一个基于 FastAPI 和 LangGraph 的旅行规划后端。它把天气、景点、路线和本地旅游知识检索成结构化上下文，再让 DeepSeek 生成计划；生成后通过业务规则校验，不合格就带着错误原因重试，同时把进度、版本和执行记录持久化。

## 3 至 5 分钟讲解顺序

### 1. 先说业务问题

普通大模型可以生成旅行文案，但常见问题是信息可能过时、预算对不上、日期和天数错误、来源不可追踪、失败后难以排查。这个项目的目标是把“生成一段文本”升级为“生成一份可验证、可追踪、可恢复的结构化旅行计划”。

### 2. 再说核心架构

入口是 FastAPI，使用客户空间 API Key 鉴权。旅行记录、计划版本和执行日志存 MySQL，并按客户空间隔离；临时任务状态和工具结果优先存 Redis；旅游文档切分并向量化后存 Qdrant，向量检索按客户空间过滤；LangGraph 把天气、景点、路线、RAG、生成、校验组织成有状态工作流；DeepSeek 负责结构化计划生成和部分查询改写。

### 3. 强调为什么使用 LangGraph

如果所有逻辑都写在一个函数里，任何一步失败都只能整体重跑，也很难知道当前执行到哪里。LangGraph 把每一步拆成节点，共享同一个状态对象：

```text
weather -> attractions -> route -> knowledge -> generate_plan -> validate_plan
                                                        ^              |
                                                        |---- retry ---|
```

工具节点失败时能明确定位；计划校验失败时只回到生成节点，不重新请求天气、景点和路线；SSE 还可以在节点切换时把进度推给客户端。

### 4. 强调 RAG 不只是向量搜索

上传的 Markdown、TXT 或 PDF 会被解析、切分、向量化并写入 Qdrant。查询时先尝试用 DeepSeek 改写问题并识别城市；改写失败就使用原问题，不阻断检索。召回后同时考虑向量相似度和关键词覆盖度，得到二阶段排序；回答和旅行计划保留来源标题、文件名和原文片段，减少不可解释的生成。

### 5. 强调可靠性

系统对计划做确定性检查，包括天数、日期、预算、每日活动数量、费用合计、活动时间顺序、天气适配和每日通勤时间。失败原因会加入下一次生成提示，超过最大重试次数才返回失败。每次执行都会记录 mode、status、current_node、error 和 plan_version，所以能区分是外部工具、模型输出还是业务约束导致失败。

### 6. 最后说工程化

Alembic 管理数据库迁移，Docker Compose 启动 API、MySQL、Redis 和 Qdrant，pytest 隔离外部依赖做回归测试，GitHub Actions 在提交时自动运行测试。计划保存为版本历史，支持恢复旧版本，说明系统不仅能生成，也考虑了数据生命周期和可运维性。

## 演示前检查

先创建客户空间，并保存命令输出的密钥。后续请求都需要携带该密钥。

```powershell
docker compose exec api python -m scripts.manage_tenants create demo
$apiKey = "<命令输出的密钥>"
$headers = @{ "X-API-Key" = $apiKey }
```

```powershell
docker compose up -d --build
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/health
```

预期四个容器均在运行，MySQL 和 Redis 显示 healthy，健康检查返回正常结果。

## 完整演示命令

### 1. 创建未来日期的旅行

```powershell
$startDate = (Get-Date).AddDays(3).ToString("yyyy-MM-dd")
$body = @{
    destination = "成都"
    start_date = $startDate
    days = 3
    budget = 3000
    interests = @("美食", "历史文化")
} | ConvertTo-Json

$trip = Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8000/trips `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body

$trip | Format-List
```

后续命令统一使用 `$trip.id`，避免把数据库 ID 写死。

### 2. 上传并验证知识库

```powershell
curl.exe -X POST http://127.0.0.1:8000/knowledge/documents `
  -H "X-API-Key: $apiKey" `
  -F "file=@.\knowledge\chengdu.md" `
  -F "city=成都"

$askBody = @{
    query = "成都有哪些适合历史文化爱好者的地方？"
    city = "成都"
    limit = 3
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8000/knowledge/ask `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $askBody | ConvertTo-Json -Depth 6
```

这里重点展示 `rewritten_query`、`answer` 和 `citations`，说明答案来自本地资料而不是只依赖模型记忆。

### 3. 流式生成计划

```powershell
curl.exe -N -X POST "http://127.0.0.1:8000/trips/$($trip.id)/generate-plan/stream" -H "X-API-Key: $apiKey"
```

观察天气、景点、路线、知识检索、生成和校验等节点事件。最终事件应包含完整结构化计划。

### 4. 查看版本和执行记录

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/trips/$($trip.id)/plans" -Headers $headers |
    Select-Object id, version, created_at |
    Format-Table

Invoke-RestMethod "http://127.0.0.1:8000/trips/$($trip.id)/executions" -Headers $headers |
    Select-Object id, mode, status, current_node, plan_version, error |
    Format-Table
```

再生成一次会得到新版本。然后可以恢复旧版本：

```powershell
Invoke-RestMethod `
    -Method Post `
    -Uri "http://127.0.0.1:8000/trips/$($trip.id)/plans/1/restore" `
    -Headers $headers
```

## 高频面试问题

### 为什么不直接让大模型调用所有工具？

工具调用、状态管理、失败恢复和业务校验如果都交给模型，行为更难预测。当前设计让程序控制流程和规则，让模型只处理擅长的自然语言理解与生成，确定性更高，也更容易测试。

### 为什么同时需要 MySQL、Redis 和 Qdrant？

- MySQL 保存有业务关系且要求持久化的数据：旅行、计划版本、执行记录。
- Redis 保存短生命周期且高频访问的数据：任务进度和工具缓存；它不可用时可以降级。
- Qdrant 负责高维向量相似度检索，解决传统关系数据库不擅长的语义搜索。

### 为什么向量召回后还要重排？

向量相似度擅长找语义接近内容，但专有名词、路线名和数字规则可能匹配不稳定。关键词覆盖度作为第二信号，可以把包含关键实体的片段排到前面，提高小型知识库里的可解释性和命中稳定性。

### 为什么既有同步接口又有 SSE？

同步接口简单，适合程序间调用和快速测试；完整规划包含多个外部请求，耗时更长，SSE 能让客户端看到当前节点并避免长时间没有反馈。二者复用同一工作流，只是响应方式不同。

### Redis 挂了会怎样？

缓存层捕获 Redis 连接错误并回退到进程内存和文件缓存，主生成流程仍能工作。缺点是多实例间状态无法共享，所以生产环境仍需要监控和恢复 Redis。

### 什么情况会触发重试？

模型输出不是合法结构、计划天数或日期不匹配、费用超过预算、费用合计不一致、活动时间逆序、活动不足、天气与活动冲突、每日通勤时间过长等。校验器返回具体错误，生成节点根据错误重新生成。

### 为什么需要计划版本和执行记录？

计划版本解决“成功结果如何回退”的问题；执行记录解决“某次请求为什么失败、执行到哪里”的问题。前者面向业务数据，后者面向审计和排障，它们关注点不同。

### Docker Compose 解决了什么？

它把 API、MySQL、Redis 和 Qdrant 的版本、网络、健康检查、持久卷和启动依赖写成配置，让面试官或协作者不用手工安装每个中间件。API 启动前自动执行 Alembic 迁移，减少环境不一致。

## 失败时怎么解释

- Overpass 返回 406/504：公共节点限流或超时，项目会尝试备用节点；这属于外部依赖稳定性问题。
- DeepSeek 超时或返回无效 JSON：记录错误并重试，最终失败会写入 execution。
- RAG 查询改写失败：回退原问题继续检索。
- Redis 不可用：回退本地缓存；不会让旅行规划立即崩溃。
- Docker 容器无法访问外网：检查 `.env` 中的 `OUTBOUND_PROXY`，不要把个人代理端口写死在 `compose.yaml`。

## 主动说明的项目边界

面试时主动说边界：当前使用客户空间 API Key，缺少终端用户登录和细粒度权限；路线是近似估算而非实时导航；公共地图接口有速率限制；任务仍运行在 API 进程内；生产化下一步应加入 Celery/RQ、限流熔断、OpenTelemetry 指标和告警，以及独立前端。

## 最后一句总结

这个项目展示的重点不是“调用了一个大模型”，而是如何把 LLM、RAG、实时工具、确定性校验、状态管理、版本管理和部署测试组合成一个可运行、可解释、可恢复的后端系统。
