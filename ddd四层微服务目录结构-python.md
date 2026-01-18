```
services/{service-name}/                      # 微服务根目录（单限界上下文的实现入口）
# 交互流标记：
# [BOOT] 启动装配  [HTTP] HTTP 入站  [MQ] MQ 入站  [SAGA] 编排  [EVT] 领域→集成事件  [OUT] Outbox 转发  [PUB] 发布到总线
├── http/                           # [HTTP] 接口回归用例：验证 routers→controllers→inbound_routing→services→repos→events 全链路
│   └── *.http|*.json|*.py          # [HTTP] 接口用例/断言脚本：上一跳 开发者/CI 调用测试框架；下一跳 presentation/api/v1/routers/*.py→controllers/*.py→application/ports/inbound/*.py→application/inbound_routing/*→application/services/*.py→domain/repositories(RepoPort)→infrastructure/persistence/repositories/*.py 全链路；类型：测试用例/断言（非生产代码）
├── presentation/                    # [HTTP] 用户接口层：协议适配/DTO 校验/异常映射，作为入站的最外层适配器（统一经 InPort→InRouter 分流至命令/查询管线）；上一跳 平台侧 API Gateway/Ingress 经 main.py 装配后路由到此
│   ├── api/                        # [HTTP] RESTful 适配器集合
│   │   ├── v1/                     # [HTTP] 版本分组（同一路由下多版本共存）
│   │   │   ├── controllers/        # [HTTP-2] 控制器：router→controller→(dto.request)→ports.inbound 用例
│   │   │   │   ├── health.py      # [HTTP-2] 探活：上一跳 routers/*.py 绑定；下一跳 application/ports/inbound/*.py（健康检查用例）；读取 monitoring/metrics 以反映 [BOOT] 初始化结果；职责：提供健康检查入口，反映进程与依赖存活状态；承载：接口适配/探活（只读检查/观测 I/O）
│   │   │   │   └── *.py           # [HTTP-2] 业务控制器：上一跳 routers/*.py；下一跳 application/ports/inbound/*.py（统一经 application/inbound_routing/* 分流进 CmdPipe/QryPipe）；完成入参校验→驱动用例→调用 Presenter（presentation/assemblers）将 AppDTO 映射为 response DTO 返回；职责：承接请求、绑定参数与错误映射，返回标准响应；承载：接口适配（不直接执行业务 I/O，调用 InPort）
│   │   │   └── routers/           # [HTTP-1] 路由：在 [BOOT] 装配时注册；把请求分派给 controllers
│   │   │       └── *.py           # [HTTP-1] 定义路径/方法/中间件链；上一跳 main.py（[BOOT] 装配；外部流量由平台网关/Ingress 转发至此）；下一跳 controllers/*.py；职责：组织路由拓扑与中间件，统一入口配置；承载：路由装配（配置/无业务 I/O）
│   │   └── middleware/            # [HTTP] 中间件：认证/追踪/异常，决定是否进入 controllers/resolvers/services（WS 仅握手阶段）
│   │       ├── auth_middleware.py # [HTTP][可选] 认证授权：若平台网关已完成鉴权/限流，此处可仅做主体解析与上下文透传；上一跳 presentation/api/v1/routers/*.py（[BOOT] 装配）；下一跳 presentation/api/v1/controllers/*.py、presentation/graphql/resolvers/*.py、presentation/grpc/services/*.py；WebSocket 仅握手阶段生效；失败→短路响应；职责：鉴别主体并校验权限，保护受控资源；承载：横切拦截（无业务 I/O）
│   │       ├── cors_middleware.py # [HTTP][可选] CORS：平台模式退化为 no-op（网关统一策略，可选择不打包进镜像）；本地模式启用简易白名单放行；上一跳 presentation/api/v1/routers/*.py（[BOOT] 装配）；下一跳 presentation/api/v1/controllers/*.py、presentation/graphql/resolvers/*.py、presentation/grpc/services/*.py、WebSocket 握手（前置）；职责：控制跨域策略，保障浏览器访问安全与合规；承载：横切拦截（无业务 I/O）
│   │       └── exception_middleware.py # [HTTP] 捕获异常→规范错误体；上一跳 presentation/api/v1/routers/*.py（[BOOT] 装配）；下一跳 presentation/api/v1/controllers/*.py、presentation/graphql/resolvers/*.py、presentation/grpc/services/*.py、WebSocket 握手；并向 monitoring/tracing/logging 记录 trace/log/metrics；职责：兜底捕获异常，统一错误体与可观测性；承载：横切拦截（观测 I/O：日志/指标/追踪）
│   ├── graphql/                   # [HTTP][可选] GraphQL 适配器：resolver→ports.inbound 用例（默认模板可不生成）
│   │   ├── schema/                # [HTTP] Schema 声明：由 [BOOT] 统一装配；被 resolvers 引用
│   │   │   └── *.py               # [HTTP] 类型/查询/变更定义；上一跳 infrastructure/container/bootstrap.py（[BOOT] 装配）；下一跳 presentation/graphql/resolvers/*.py；职责：声明 GraphQL 契约，约束类型与操作；承载：契约声明（无外部 I/O）
│   │   └── resolvers/             # [HTTP-2] 解析器：参数→入站端口→响应（统一经 InRouter 进入命令/查询管线）
│   │       └── *.py               # [HTTP-2] 上一跳 schema/*.py；下一跳 application/ports/inbound/*.py→application/inbound_routing/*；并由 Presenter 映射 AppDTO→dto/response/*.py；职责：解析 GraphQL 调用并驱动用例，整合输出；对外由平台网关/Ingress 暴露入口；承载：接口适配（不直接执行业务 I/O，调用 InPort）
│   ├── grpc/                      # [HTTP][可选] gRPC 适配器（同样走 ports.inbound；默认模板可不生成）
│   │   ├── services/              # [HTTP-2] gRPC 服务实现：proto msg↔dto；调用入站端口（统一经 InRouter 进入命令/查询管线）
│   │   │   └── *.py               # [HTTP-2] 上一跳 proto 生成的 stub；下一跳 application/ports/inbound/*.py→application/inbound_routing/*；响应侧经 Presenter→dto/response/*.py；职责：承接 RPC 调用与消息映射，转为入站接口；外部客户端可通过平台网关/Ingress 访问；承载：接口适配（协议转换/无业务 I/O）
│   │   └── proto/                 # [HTTP] IDL；由代码生成供 services 使用
│   │       └── *.proto            # [HTTP] 定义服务/消息；上一跳 构建工具/CI 代码生成；下一跳 presentation/grpc/services/*.py（生成 stub 后被注入）；职责：定义跨语言 RPC 契约，驱动代码生成；承载：IDL 契约（模型/无 I/O）
│   ├── websocket/                 # [HTTP][可选] WS 推送：可由 [EVT]/[SAGA] 触发通知（仅握手阶段经过中间件/拦截；默认模板可不生成）
│   │   ├── handlers/              # [HTTP-2] WS 事件处理：查询/订阅→入站端口/读模型（统一经 InRouter）
│   │   │   └── *.py               # [HTTP-2] 上一跳 连接管理/订阅事件；下一跳 application/ports/inbound/*.py→application/inbound_routing/*（进入查询/命令管线）；并向 websocket/events/*.py；职责：处理订阅/推送会话逻辑，接入读写模型；外部握手请求可通过平台网关/Ingress 进入；承载：接口适配（网络 I/O；不直接访问 DB/外部系统）
│   │   └── events/                # [HTTP] WS 推送消息体：与 Presenter 输出保持一致
│   │       └── *.py               # [HTTP] 推送消息体定义；与 Presenter 输出保持一致；职责：定义推送消息格式，保障前后端一致性；承载：数据类模型（无 I/O）
│   ├── cli/                       # [HTTP][可选] CLI 适配器：命令行/批任务入口→调用 ports.inbound 用例（默认模板可不生成）
│   │   ├── commands/              # [HTTP-2] CLI 命令实现：参数解析→用例→输出
│   │   │   └── *.py               # [HTTP-2] 上一跳 CLI 入口；下一跳 application/ports/inbound/*.py→application/inbound_routing/*（进入命令/查询管线）；并向 stdout，通过 Presenter 输出；职责：提供运维/批处理入口，复用同一用例管线；承载：接口适配（命令行交互 I/O；不直接访问 DB/外部系统）
│   │   └── interceptors/          # [HTTP] CLI 命令拦截器：参数校验/鉴权/幂等键注入
│   │       └── *.py               # [HTTP] 上一跳 CLI 入口；下一跳 application/ports/inbound/*.py（经 InRouter 进入管线）；职责：在 CLI 场景做校验/鉴权/幂等增强；承载：横切增强（无业务 I/O）
│   ├── assemblers/                # Presenter：把 AppDTO 映射为接口响应 DTO
│   │   └── *.py                   # [HTTP] 上一跳 application/app_dto/*.py；下一跳 presentation/dto/response/*.py；职责：将 AppDTO 映射为响应 DTO，屏蔽表现层差异；承载：纯装配（无 I/O）
│   └── dto/                       # [HTTP]/[MQ] DTO 层：与领域隔离的输入/输出合同
│       ├── request/               # [HTTP]/[MQ] 入参模型：各入站适配器使用（REST/GraphQL/gRPC/WebSocket/CLI）
│       │   └── *.py               # [HTTP]/[MQ] 上一跳 presentation/api/v1/controllers/*.py、presentation/graphql/resolvers/*.py、presentation/grpc/services/*.py、presentation/websocket/handlers/*.py、presentation/cli/commands/*.py；下一跳 application/ports/inbound/*.py→application/inbound_routing/*；职责：定义入参模型与校验，隔离协议与领域；承载：数据类模型（无 I/O）
│       └── response/              # [HTTP] 出参模型：由 Presenter（presentation/assemblers）组装
│           └── *.py               # [HTTP] 上一跳 presentation/assemblers/*.py（输入为 AppDTO）；下一跳 presentation/api/v1/controllers/*.py、presentation/graphql/resolvers/*.py、presentation/grpc/services/*.py、presentation/websocket/handlers/*.py、presentation/cli/commands/*.py 返回给客户端；并协同 presentation/websocket/events/*.py 映射推送载荷；职责：定义响应模型与序列化规则，稳定对外合同；承载：数据类模型（无 I/O）
├── application/                     # 用例编排/事务边界/流程管理；主要依赖领域端口，同时可通过出站端口与外部上下文/总线交互
│   ├── inbound_routing/            # 入站策略门面/路由：统一入口；认证/授权/验证/审计；对命令/查询分流；支持系统路由标记
│   │   ├── system/                 # 系统路由：内部主体/服务凭据；跳过用户认证/频控；保留审计/追踪/幂等
│   │   │   └── *.py               # [HTTP/MQ] 上一跳仅限受信入口（MQ 入站适配器/Workflow 回调/平台签名网关）设置系统标记；下一跳 pipelines/* 应用差异策略；职责：标记系统调用场景，调整认证/频控策略；承载：系统路由标记（无 I/O）
│   │   └── pipelines/              # 命令/查询管线（横切关注；系统路由标记不得由外部请求直接携带）
│   │       ├── command/            # 验证→授权→幂等→执行
│   │       │   ├── validators/     # 参数/语义校验
│   │       │   │   └── *.py           # 职责：校验命令的结构与业务前置约束；承载：规则/校验（无 I/O）
│   │       │   ├── authorizers/    # 授权策略（可因系统路由跳过用户认证）
│   │       │   │   └── *.py       # 职责：评估主体权限与操作授权；承载：授权策略（无业务 I/O）
│   │       │   └── idempotency/    # 幂等/去重
│   │       │       └── *.py       # 职责：提供命令幂等保障与去重控制；承载：幂等策略（可能通过缓存 provider 间接 I/O）
│   │       └── query/               # 验证→授权→缓存
│   │           ├── validators/
│   │           │   └── *.py       # 职责：校验查询参数与范围合法性；承载：规则/校验（无 I/O）
│   │           ├── authorizers/
│   │           │   └── *.py       # 职责：校验查询访问权限与数据隔离；承载：授权策略（无业务 I/O）
│   │           └── caching/
│   │               └── *.py       # 职责：应用查询缓存策略，降低读负载与延迟；承载：缓存编排（I/O 通过 CachePort/Port 间接发生；应用层不直接依赖缓存实现）
│   ├── services/                   # [HTTP/MQ/SAGA-3] 应用服务：经 RepoPort 加载/持久化聚合→执行业务命令→记录领域事件；可直接或由 CmdHandler 委派；按需编排 Workflow；提交点由 UoW 统一处理事件
│   │   └── *.py                   # [HTTP/MQ] 上一跳 application/ports/inbound/*.py 或 application/commands/handlers/*.py 与 application/workflows/*.py 调用；下一跳 domain/*、application/ports/outbound/*.py；成功后触发 UoW→DomainEventPublisher；职责：编排用例、协调领域与出站端口，控制事务边界；承载：用例编排（不直接业务 I/O；通过 RepoPort/OutPort 触发）
│   ├── ports/                      # 六边形端口：入站（用例）/出站（外部依赖）
│   │   ├── inbound/               # [HTTP-3]/[MQ-3] 入站端口：controllers/handlers 的稳定接口
│   │   │   └── *.py               # [HTTP-3]/[MQ-3] 例如 ICreateOrderUseCase；上一跳 presentation/* 与 application/workflows/*.py（系统路由回调）；下一跳 application/inbound_routing/*→application/services/*.py 或 application/commands/handlers/*.py；职责：定义用例入口接口，隔离适配器变化；承载：端口接口（抽象/无 I/O）
│   │   └── outbound/              # [SAGA-3]/[EVT] 出站端口：外部依赖抽象（事件发布/外部上下文调用/同步集成）
│   │       └── *.py               # [SAGA-3]/[EVT] 上一跳 application/services/*.py 与 application/workflows/*.py 调用；下一跳 infrastructure/external_services/adapters/*.py（[ACL] 翻译→ContextB）与 infrastructure/messaging/publishers/*.py（桥接集成事件→Bus）；职责：抽象外部依赖能力，面向适配器实现；例如：file_storage.py（MinIO/S3）、search_index.py（Elasticsearch）、graph_store.py（Neo4j）；承载：端口接口（抽象/无 I/O）
│   ├── commands/                   # [HTTP/MQ] 写模型：显式表达意图与幂等键
│   │   ├── handlers/              # [可选] 命令处理器：模块级 opt-in 的特制管线；可委派到 AppSvc
│   │   │   └── *.py               # [HTTP/MQ] 上一跳 InRouter/CmdPipe；下一跳 application/services/*.py 或内部处理→发布 [EVT]；职责：承载模块化命令管线，必要时委派应用服务；承载：管线/协调（无直接 I/O；可委派 AppSvc）
│   │   └── models/                # [HTTP/MQ] 命令数据与校验、幂等键；被 commands.handlers 使用
│   │       └── *.py               # [HTTP/MQ] 上一跳 application/commands/handlers/*.py 使用；下一跳 application/services/*.py（以领域命令形式传入）；内部校验失败时调用 domain/exceptions/*.py 抛错；职责：表达命令意图与幂等键，并自校验；承载：数据类模型（无 I/O）
│   ├── queries/                    # [HTTP] 读模型：直达投影/读库
│   │   ├── handlers/              # [HTTP] 查询→读库/投影→Assembler→AppDTO；Presenter 仅做表示层映射
│   │   │   └── *.py               # [HTTP] 上一跳 InRouter/QryPipe（验证→授权→缓存策略）；cache miss 时下一跳 application/ports/outbound/*.py（ReadModelPort/CachePort 等接口，由容器注入 infra 实现）→application/assemblers/*.py→application/app_dto/*.py；职责：执行读模型/投影访问并装配 AppDTO；承载：读路径编排（I/O 通过 Port/OutPort 间接发生；应用层不直接依赖基础设施实现）
│   │   └── models/                # [HTTP] 查询参数与分页/排序；被 queries.handlers 使用
│   │       └── *.py               # [HTTP] 上一跳 application/queries/handlers/*.py 使用；下一跳 application/ports/outbound/*.py（读模型/缓存等接口，便于依赖倒置）；异常时调用 domain/exceptions/*.py 报错；职责：定义查询参数/分页排序模型，支撑读路径；承载：数据类模型（无 I/O）
│   ├── assemblers/                # [HTTP/EVT] 组装：领域对象/读模型↔AppDTO/集成事件载荷
│   │   └── *.py                   # [HTTP] 上一跳 application/services/*.py 或 queries/handlers/*.py；下一跳 application/app_dto/*.py；[EVT] 上一跳 application/event_handlers/*.py（输入为领域事件载荷）；下一跳 application/integration_events/*.py；职责：把领域/读模型组装为 AppDTO 或集成事件载荷；承载：纯装配（无 I/O）
│   ├── app_dto/                   # 用例返回模型（AppDTO）：被 Presenter 映射为接口响应 DTO
│   │   └── *.py                   # [HTTP] 上一跳 application/assemblers/*.py；下一跳 presentation/assemblers/*.py；职责：承载用例返回的内部传输模型，供表示层映射；承载：数据类模型（无 I/O）
│   ├── event_handlers/            # [EVT-1] 接收领域事件→装配 IntegrationEvent→调用 OutboxPort（严禁外部 I/O）
│   │   └── *.py                   # [EVT-1] 上一跳 infrastructure/event_bus/dispatcher.py（进程内分发）；下一跳 application/integration_events/*.py→application/ports/outbound/*.py（OutboxPort 接口；由容器注入 infra 实现以同事务写入 Outbox）；职责：桥接领域事件到集成事件，保证幂等与无副作用；承载：桥接装配（无外部 I/O；仅通过 Port 在同事务落库）
│   ├── integration_events/         # [EVT] 集成事件契约：跨上下文传播
│   │   └── *.py                   # [EVT] 载荷/版本/序列化；上一跳 application/event_handlers/*.py 装配；下一跳 application/ports/outbound/*.py（OutboxPort）→（容器绑定的 infra 实现）→infrastructure/messaging/outbox_relayer.py→infrastructure/messaging/publishers/*.py；职责：定义跨上下文传播的事件契约与版本控制；承载：数据类模型（无 I/O）
│   ├── transaction/               # 事务与工作单元（UoW）：收集领域事件；提交前桥接事件
│   │   └── uow.py                 # [EVT-0] 上一跳 application/services/*.py；下一跳 application/event_bus/publisher.py（提交前：进程内分发领域事件到应用层处理器）；职责：聚合提交点与领域事件收集，并在提交前分发；承载：事务边界（数据库事务 I/O；无外部系统 I/O）
│   ├── event_bus/                 # 进程内事件发布器（应用层控制，保持依赖倒置）
│   │   └── publisher.py           # [EVT-0] 上一跳 application/transaction/uow.py；下一跳 application/ports/outbound/*.py（InProcDispatcherPort 接口；由容器注入 infra/event_bus/dispatcher.py 实现以分发至应用层处理器）；职责：统一发布领域事件到进程内分发器；承载：进程内发布（无外部 I/O）
│   └── workflows/                 # [SAGA] 编排式 Saga/流程管理器(PM)：被 AppSvc 发起或由总线事件触发；维护状态/超时/补偿
│       └── *.py                   # [SAGA-2] 上一跳 [MQ] messaging/subscribers/*.py 与 application/services/*.py 触发；下一跳 application/services/*.py（推进步骤）与 application/ports/outbound/*.py（访问外部上下文）；回调 InPort 时显式设置系统路由标记（跳过用户认证/频控；保留审计/幂等）；职责：实现 Saga/流程管理，推进步骤与补偿；承载：编排（通过 OutPort/消息触发 I/O，回调 InPort）
├── domain/                         # 领域层：不变量/规则/模型
│   ├── aggregates/                # [HTTP/MQ] 聚合根：命令变更入口→发布领域事件
│   │   └── *.py                   # [EVT-0] 上一跳 application/services/*.py 或 domain/services/*.py 调用；下一跳 domain/policies/*.py、domain/specifications/*.py（组合校验）；发布 domain/events/*.py 供应用层处理；职责：封装业务不变式与行为的一致性边界；承载：领域模型与行为（无外部 I/O）
│   ├── entities/                  # 领域实体
│   │   └── *.py                   # 上一跳 domain/aggregates/*.py 维护；下一跳 value_objects/*.py（组合/校验）；职责：承载有标识的业务对象及行为；承载：领域模型（无 I/O）
│   ├── value_objects/             # 值对象
│   │   └── *.py                   # 上一跳 domain/entities/*.py、domain/aggregates/*.py 组合使用；下一跳 自身验证逻辑调用 domain/exceptions/*.py、支撑 domain/specifications/*.py 判定；持久化时供 infrastructure/persistence/mappers/*.py 序列化；职责：表达不可变的值语义与自校验；承载：值对象数据类（无 I/O）
│   ├── services/                  # 领域服务
│   │   └── *.py                   # 上一跳 application/services/*.py 调用；下一跳 domain/aggregates/*.py、domain/policies/*.py、domain/factories/*.py；必要时发布 domain/events/*.py；跨实体的纯领域逻辑，不直接依赖基础设施；职责：实现跨实体/聚合的纯领域规则与计算；承载：纯领域逻辑（无外部 I/O）
│   ├── events/                    # 领域事件定义
│   │   ├── base.py                # 事件基类：aggregate_id/occurred_at/version/trace；上一跳 domain/events/*.py（具体事件）继承；下一跳 application/event_handlers/*.py（统一解析领域上下文）；职责：统一事件公共字段与追踪元数据；承载：数据类模型（无 I/O）
│   │   └── *.py                   # 上一跳 domain/aggregates/*.py 或 domain/services/*.py 发布；下一跳 application/transaction/uow.py 收集；职责：记录领域内事实，供应用层处理；承载：数据类模型（无 I/O）
│   ├── repositories/              # 仓储端口（抽象）
│   │   └── *.py                   # 上一跳 application/services/*.py 调用；下一跳 infrastructure/persistence/repositories/*.py（端口/适配器实现）；职责：定义聚合持久化与加载的抽象端口；承载：端口接口（抽象/无 I/O）
│   ├── specifications/            # 规约：规则组合
│   │   └── *.py                   # 上一跳 domain/aggregates/*.py、domain/services/*.py 使用；下一跳 domain/value_objects/*.py 与 domain/exceptions/*.py（组合判定与抛错）；可被 domain/policies/*.py 聚合；职责：组合领域规则形成可复用断言条件；承载：规则组合（无 I/O）
│   ├── policies/                  # 策略：可替换规则
│   │   └── *.py                   # 上一跳 domain/services/*.py、domain/aggregates/*.py 使用；下一跳 domain/specifications/*.py 与 domain/factories/*.py（封装策略执行与实例创建）；按需调用 domain/exceptions/*.py；职责：封装可替换的策略与决策；承载：策略（无 I/O）
│   ├── factories/                 # 工厂：复杂创建
│   │   └── *.py                   # 上一跳 domain/services/*.py、application/services/*.py 调用；下一跳 domain/aggregates/*.py、domain/value_objects/*.py（构造/组装）；职责：集中复杂创建/组装，保证不变式成立；承载：构造/装配（无 I/O）
│   └── exceptions/                # 领域异常
│       └── *.py                   # 上一跳 domain/aggregates/*.py 或 domain/services/*.py 抛出；下一跳 presentation/api/middleware/exception_middleware.py 映射为 HTTP/WS 握手错误，或被 infrastructure/messaging/handlers/*.py 捕获映射为 NACK；职责：表达领域失败语义，指导上层错误映射；承载：异常类型（无 I/O）
├── infrastructure/                 # 端口适配/外部系统/技术细节
│   ├── persistence/               # 数据持久化
│   │   ├── repositories/          # [HTTP/MQ] 仓储实现：事务/并发→返回聚合
│   │   │   └── *.py              # 上一跳 application/services/*.py（经 RepoPort）调用；下一跳 domain/aggregates/*.py 与 persistence/models/*.py、persistence/mappers/*.py→DB；收集/回流 aggregate.domain_events 供 UoW 收集；职责：实现仓储端口，处理事务/并发并回流事件
│   │   ├── models/                # ORM/ODM 模型
│   │   │   └── *.py              # 上一跳 mappers/*.py 与 repositories/*.py 使用；下一跳 DB（持久化）；职责：定义 ORM/ODM 模型与表/集合/图结构，落地物理模型；承载：ORM/ODM 模型（数据类+元数据，通常无直接 I/O）
│   │   ├── mappers/               # 领域↔ORM/ODM 映射
│   │   │   └── *.py              # 上一跳 repositories/*.py 调用；下一跳 persistence/models/*.py（ORM/ODM 实体）；完成 domain↔ORM/ODM 映射；职责：完成领域对象与 ORM/ODM 实体的双向映射；承载：装配/映射（无 I/O）
│   │   ├── migrations/            # 迁移脚本
│   │   │   └── *.py              # 上一跳 平台交付流水线/DB 迁移作业 或 CI 迁移框架执行；下一跳 DB（变更应用）；职责：描述数据库结构演进，支持回滚/审计；承载：迁移脚本（数据库 I/O）
│   │   └── outbox/
│   │       ├── models.py         # [OUT-0] Outbox 模型/仓储实现：上一跳 application/ports/outbound/*.py（OutboxPort 的实现由容器绑定到此；调用点通常来自 application/event_handlers/*.py）；下一跳 infrastructure/messaging/outbox_relayer.py（[OUT-1] 轮询）；职责：同事务持久化待发布集成事件，供转发器扫描；承载：ORM 模型 + 仓储实现（持久化 I/O 由 ORM 执行）
│   │       └── cleanup.py        # [OUT] Outbox 清理/归档；上一跳 平台调度任务/运维作业 调用；下一跳 outbox/models.py；职责：清理/归档已投递记录，控制表规模；承载：维护任务（数据库 I/O；必须纳入定期调度以防表膨胀）
│   ├── event_bus/                 # 进程内领域事件分发器（仅分发至应用层处理器；不连接 MQ）
│   │   └── dispatcher.py          # [EVT-0] 上一跳 application/event_bus/publisher.py（依赖 InProcDispatcherPort 接口；由容器注入此实现）；下一跳 application/event_handlers/*.py；职责：将领域事件分发给应用层处理器；承载：进程内分发（无外部 I/O）
│   ├── messaging/                 # 消息（事件发布/订阅，驱动 Saga）
│   │   ├── publishers/            # [PUB-1] 发布器：实现 IEventPublisher 出站端口；读取集成事件→发送 MQ；确认/重试/DLX
│   │   │   └── *.py              # 上一跳 infrastructure/messaging/outbox_relayer.py（[OUT-1]）调用；下一跳 消息总线（发送 application/integration_events/*.py 载荷并传播 trace/correlation）；成功确认/失败重试/死信转移；职责：可靠发布集成事件到总线，含确认/重试/死信；承载：I/O 实现（MQ 发布）
│   │   ├── subscribers/           # [MQ-1] 订阅器：声明队列/QoS/手动 ACK；分发到 handlers
│   │   │   └── *.py              # 上一跳 消息总线 推送；下一跳 infrastructure/messaging/handlers/*.py（按队列/Topic 分发）；职责：接收并分发消息到处理器，控制 QoS/ACK；承载：I/O 实现（MQ 消费）
│   │   ├── handlers/              # [MQ-2] MQ 入站适配器：反序列化/幂等/ACK/NACK；把控制流交给 Application InPort（系统路由）
│   │   │   └── *.py              # 上一跳 subscribers 分发；下一跳 application/ports/inbound/*.py（或 application/inbound_routing/system/* 统一入口，显式标记系统主体/系统路由）；用例编排发生在 application（可触发 workflows、投影更新与缓存失效；I/O 通过 Port 间接发生）；成功→ACK；可重试→NACK+requeue；不可重试→DLX；职责：协议适配与消息投递边界，不承载业务用例编排；承载：适配器（I/O：MQ 消费；业务 I/O 委托给应用层 Port）
│   │   └── outbox_relayer.py      # [OUT-1] 转发器（有集成事件即必启）：扫描 Outbox→调用 publishers；可由平台 sidecar/job 统一承担；上一跳 infrastructure/persistence/outbox/models.py；下一跳 infrastructure/messaging/publishers/*.py；记录重试/死信；由 bootstrap 启动；职责：扫描 Outbox 并调用发布器，实现最终一致的对外投递；承载：I/O 实现（DB→MQ 转发）
│   ├── external_services/         # 防腐层（外部同步调用/ACL）：隔离外部模型；实现 OutPort 适配
│   │   ├── adapters/              # [SAGA-3] 出站端口实现(IExternalGateway 等)：可组合重试/熔断/超时
│   │   │   └── *.py              # 上一跳 application/ports/outbound/*.py 或 application/workflows/*.py 调用；下一跳 infrastructure/external_services/translators/*.py→infrastructure/external_services/clients/*.py→(ContextB)；职责：实现 OutPort、做 ACL 防腐与超时/重试/熔断编排；例如：minio_adapter.py、search_index_adapter.py、graph_store_adapter.py（对应 OutPort file_storage/search_index/graph_store）；承载：适配实现（外部 I/O，经 clients/策略包裹）
│   │   ├── clients/               # 外部客户端
│   │   │   └── *.py              # 上一跳 infrastructure/external_services/adapters/*.py 使用；下一跳 外部 API/SDK（HTTP/gRPC/SDK）；职责：封装外部 API/SDK 调用细节；例如：minio_client.py（S3 兼容）、elasticsearch_client.py、neo4j_client.py；承载：外部客户端（网络 I/O）
│   │   ├── translators/           # 模型翻译
│   │   │   └── *.py              # 外部 DTO ↔ 领域；上一跳 infrastructure/external_services/adapters/*.py；下一跳 infrastructure/external_services/clients/*.py；职责：在外部 DTO 与领域对象之间转换；承载：装配/转换（无 I/O）
│   │   ├── retry/                 # 重试策略（含退避/抖动）
│   │   │   └── *.py              # 上一跳 infrastructure/external_services/adapters/*.py；平台模式仅保留接口占位，透传到 sidecar/SDK，移除自定义策略实现；本地模式仅做一次幂等兜底；承载：策略（无 I/O）
│   │   └── circuit_breaker/       # 断路器
│   │       └── *.py              # 上一跳 infrastructure/external_services/adapters/*.py；平台模式仅保留接口占位，由 sidecar/SDK 执行熔断，移除自定义策略实现；本地模式提供轻量兜底断路；承载：策略（无 I/O）
│   ├── caching/                   # 缓存
│   │   ├── providers/             # 提供者（Redis/Memory）
│   │   │   └── *.py              # 上一跳 application/ports/outbound/*.py（CachePort 接口被注入此实现）；下一跳 缓存后端驱动（Redis/Memory）执行读写；职责：对接缓存后端，提供统一读写接口；承载：I/O 实现（缓存；写路径使用前需定义失效/一致性策略）
│   │   ├── strategies/            # 策略（穿透/一致性）
│   │   │   └── *.py              # 上一跳 infrastructure/caching/providers/*.py 组合使用；职责：编排穿透/一致性等策略，权衡新鲜度与成本；承载：策略编排（无直接 I/O）
│   │   └── store/                 # 底层存储实现
│   │       └── redis/             # Redis 存储驱动
│   │           └── *.py          # 上一跳 infrastructure/caching/providers/*.py；下一跳 Redis 实例；职责：封装 Redis 读写细节与连接管理；承载：I/O 实现（Redis）
│   ├── monitoring/                # 可观测性（建议仅保留对平台观测 SDK 的薄封装）
│   │   ├── metrics/               # 指标：请求/事件/ACK/NACK/重试/耗时；被中间件/controllers/services/messaging 使用
│   │   │   └── *.py              # 调用平台观测 SDK/sidecar 采集上报，禁用自建导出器；平台缺席时回退 stdout/console 简版；上一跳 中间件/controllers/services/messaging；下一跳 平台 OTel Collector/Agent（OTLP）或本地控制台；职责：采集并上报关键指标，支撑平台看板与告警；承载：可观测性 I/O（非业务数据）
│   │   ├── tracing/               # 追踪：上下文传播（HTTP 头/MQ header）；被中间件/controllers/services/messaging 使用
│   │   │   └── *.py              # 基于平台 SDK 生成/传播/结束 Span，禁用自建导出器；平台缺席时仅输出控制台调试；上一跳 中间件/controllers/services/messaging；下一跳 平台追踪链路或本地控制台；职责：传播与记录分布式追踪上下文，支撑链路分析；承载：可观测性 I/O（非业务数据）
│   │   └── logging/               # 日志：结构化，关联 trace/correlation；被中间件/controllers/services/messaging/repositories 使用
│   │       └── *.py              # 调用平台日志 SDK 统一格式输出，禁用自建采集器；平台缺席时输出 stdout；上一跳 中间件/controllers/services/messaging/repositories；下一跳 平台日志管道或本地控制台；职责：输出统一结构化日志，关联 trace/correlationId，实现日志-追踪联动；承载：可观测性 I/O（非业务数据）
│   ├── container/                 # [BOOT] 依赖注入与启动装配
│   │   ├── providers.py           # [BOOT-1] 端口→适配器绑定；构建 publishers/subscribers/repos/workflows；上一跳 container/bootstrap.py 调用；下一跳 infrastructure/messaging/publishers/*.py、infrastructure/messaging/subscribers/*.py、infrastructure/persistence/repositories/*.py、application/workflows/*.py、infrastructure/external_services/adapters/*.py；职责：注册依赖与端口适配，构建运行时对象图；承载：依赖注册/对象图（无业务 I/O）
│   │   └── bootstrap.py           # [BOOT-2] 启动：加载配置→初始化连接→注册/订阅（消息/事件）→启动 subscribers/outbox_relayer（存在集成事件时必启）；停机：优雅关闭；上一跳 main.py；下一跳 infrastructure/container/providers.py 与 infrastructure/configuration/settings.py；职责：初始化配置与依赖，拉起订阅/转发等后台进程；承载：启动装配（I/O：连接/订阅/进程拉起）
│   └── configuration/             # 配置管理
│       ├── settings.py            # [BOOT] 服务配置（DB/MQ/ObjectStorage/Search/Graph/RemoteAPI/Outbox/重试/DLX/prefetch/工作流超时）；上一跳 infrastructure/container/bootstrap.py 与 infrastructure/container/providers.py 读取；下一跳 infrastructure/configuration/providers/*.py（env/文件）、infrastructure/configuration/policies_loader.py、.env.example；职责：集中管理服务配置项，支持多源合并；承载：配置数据类与合并策略（无 I/O，加载委托 providers）
│       ├── policies_loader.py     # [BOOT] 跨切策略加载器占位：治理契约已停用，保留接口供 bootstrap 调用并记录沿用默认值；承载：兼容占位（无外部 I/O）
│       └── providers/             # [BOOT] 配置提供者（env/文件；平台注入通过 env）；被 configuration.settings/bootstrap 使用
│           ├── file.py            # [BOOT] 平台模式不打包；仅在显式开关启用且本地离线时，从 services/ddd_temp/config.yaml 兜底加载开发默认值；上一跳 settings.py；下一跳 YAML 文件；职责：提供可选本地静态兜底配置；承载：文件 I/O
│           ├── env_file.py        # [BOOT] 平台模式不打包；需开关启用，仅本地/CI 缺平台时解析项目根 .env；上一跳 settings.py；下一跳 本地 .env 文件；职责：提供开发态覆盖，平台模式默认禁用；承载：文件 I/O
│           └── env.py             # [BOOT] 读取进程环境变量；上一跳 settings.py；下一跳 os.environ；职责：平台模式的主配置通道（Env/ConfigMap/Secret/ESO），覆盖前置默认值；承载：环境变量 I/O
├── tests/                          # 测试：验证各交互流
│   ├── unit/                      # 单元：领域/应用/基础设施
│   │   ├── domain/                # 聚合不变量/事件发布
│   │   │   └── test_*.py          # 上一跳 pytest 通过 tests/conftest.py 装配执行；下一跳 domain/aggregates/*.py、domain/events/*.py（断言不变量与事件流）；类型：单元测试（非生产代码）
│   │   ├── application/           # 用例/事件桥接/工作流步骤
│   │   │   └── test_*.py          # 上一跳 pytest（fixtures 注入 mocks/数据库）驱动；下一跳 application/services/*.py、application/ports/*.py、application/event_handlers/*.py、application/inbound_routing/*、application/transaction/uow.py；类型：单元测试（非生产代码）
│   │   ├── infrastructure/        # 仓储/发布器/订阅器/Outbox 转发器
│   │   │   └── test_*.py          # 上一跳 pytest 与 tests/fixtures/*.py 提供依赖；下一跳 infrastructure/persistence/repositories/*.py、infrastructure/messaging/*.py、infrastructure/caching/*.py、infrastructure/event_bus/*.py 实现；类型：单元测试（非生产代码）
│   ├── integration/               # 集成：端到端
│   │   ├── api/                   # routers→controllers→ports→inbound_routing→services→repos→response
│   │   │   └── test_*.py          # 上一跳 pytest 集成套件/CI 触发；下一跳 main.py→presentation/api/v1/routers/*.py→application/ports/inbound/*.py→application/inbound_routing/*→application/services/*.py→infrastructure/persistence/repositories/*.py 全链路；类型：集成测试（端到端，非生产代码）
│   │   └── persistence/           # ORM/迁移/事务一致性
│   │       └── test_*.py          # 上一跳 pytest 与测试夹具/平台提供的 DB 环境准备；下一跳 infrastructure/persistence/migrations/*.py、repositories/*.py 与真实 DB；类型：集成测试（非生产代码）
│   ├── functional/                # 功能：Saga happy/compensation paths
│   │   └── test_*.py              # 上一跳 pytest BDD/场景驱动；下一跳 application/ports/inbound/*.py（含系统路由入口）→application/workflows/*.py→application/ports/outbound/*.py，以及 infrastructure/messaging/handlers/*.py（仅适配/投递边界）；类型：功能/场景测试（非生产代码）
│   ├── golden/                    # 黄金快照：tests/golden/<service>/ 存放请求/响应/事件 payload 的基线，用于防漂移比对
│   ├── fixtures/                  # 测试夹具
│   │   └── *.py                   # 上一跳 unit/integration/functional 测试用例引用；下一跳 infrastructure/container/bootstrap.py（准备依赖）、infrastructure/persistence/migrations/*.py（初始化库）、tests/mocks/*.py（组合替身）
│   ├── mocks/                     # 假实现/替身（端口）
│   │   └── *.py                   # 上一跳 tests/unit|integration 用例注入到 application/ports/*.py 或 domain/repositories(RepoPort)；下一跳 application/services/*.py、application/workflows/*.py（驱动用例）
│   └── conftest.py               # PyTest 装配：上一跳 tests 下测试用例自动发现与导入；下一跳 container/bootstrap.py、数据库（migrations/）、消息总线（messaging/*）与 event_bus 初始化依赖
├── .env.example                   # 环境变量示例（仅作本地兜底）；平台模式不打包；上一跳 开发/运维/CI 可复制生成 .env（不作为生产真值源）；下一跳 infrastructure/configuration/providers/*.py 与 infrastructure/configuration/settings.py 解析（仅本地开关启用时）；承载：配置样例（无代码执行）
│                                   # 连接信息（数据库/对象存储/消息队列）由平台注入（Env/ConfigMap/Secret/ESO）；.env 中的相关键仅用于本地无平台时的兜底，需显式开启文件读取。
├── main.py                        # [BOOT-0] 入口：上一跳 运行器(ASGI/CLI) 调用；下一跳 presentation/api/v1/routers/*.py（装配路由）与 infrastructure/container/bootstrap.py（启动/停机钩子）；承载：启动入口/装配（无业务 I/O）
├── requirements.txt               # 依赖清单；上一跳 开发者/CI/Docker build 阶段执行 pip install 读取；下一跳 生成虚拟环境/镜像供 main.py 与 infrastructure/container/bootstrap.py 运行；承载：依赖清单（非运行时代码）
└── Dockerfile                     # 容器镜像（多阶段构建/最小运行时）；上一跳 构建/部署流水线调用 docker build；下一跳 运行 main.py 与 infrastructure/container/bootstrap.py 完成装配；承载：构建脚本（非运行时业务逻辑）
```

平台编排说明
- 平台负责：网关/服务发现/可观测/配置与密钥/中间件编排等“非业务逻辑”，业务服务目录不内置平台级编排文件。
- 本地联调：按平台提供的统一脚手架/运行环境启动共享中间件；服务侧通过 `Env + ConfigMap/Secret(ESO)` 获取连接信息，`.env.example` 仅作无平台时兜底。
