# Context

现有 `SkillAdapter` 已实现第三方 CLI 的用户级凭证注入、独立执行目录、Redis 状态命名空间、超时、错误归一化与秘密脱敏，但只是独立适配器，尚未接入 AgentScope 官方 Hook 生命周期。用户已明确选择 **AgentScope 1.0.21**，目标是在不重写、不重复执行现有逻辑的前提下，自定义 `pre_acting` Hook，注册到真实 `ReActAgent`，并让 Toolkit 工具委托同一个 `SkillAdapter` 完成 CLI 执行。

## 实施方案

1. **固定官方依赖**
   - 修改 `backend/pyproject.toml`，加入 `agentscope>=1.0.21,<2`。
   - 使用 1.x 官方 `register_instance_hook`、`pre_acting`、`ReActAgent`、`Toolkit`、`OpenAIChatModel` 和 `OpenAIChatFormatter`，不混用 2.x Middleware API。

2. **复用适配器校验与执行入口**
   - 修改 `backend/app/skill/adapter.py`，将 `_validate_request` 提升为公开的 `validate_request`，`execute()` 与 Hook 共用同一校验逻辑。
   - 保持 `SkillAdapter.execute()` 为唯一 CLI 执行入口，不改变现有结果契约和隔离行为。

3. **实现自定义 Hook 与真实 Agent 工厂**
   - 新建 `backend/app/skill/agentscope_hook.py`。
   - 定义 `AgentScopeSkillHook`：持有 `SkillAdapter`，提供 `pre_acting(agent, kwargs)`；只处理约定的 `execute_travel_skill` 工具，从 `ToolUseBlock.input` 构造 `SkillRequest` 并在执行前校验，其他工具直接放行。
   - 提供实例注册方法，调用官方 `agent.register_instance_hook(hook_type="pre_acting", ...)`。
   - 在 Toolkit 中注册异步 `execute_travel_skill` 工具：将请求委托给 `adapter.execute()` 一次，并把 `SkillResult` 以 JSON `TextBlock` 包装为 `ToolResponse`。Hook 不直接执行 CLI。
   - 提供 `build_skill_agent(settings, adapter, model=None)`：生产环境使用现有讯飞 LLM 配置创建 `OpenAIChatModel`，组合 `OpenAIChatFormatter`、`InMemoryMemory` 和 Toolkit 创建真实 `ReActAgent`，随后注册 Hook；可选模型注入仅用于无网络单测。
   - 不使用 `post_acting` 处理 `SkillResult`：官方 1.0.21 的该输出主要是结构化回复结果，并非本次 `ToolResponse`，避免错误契约。

4. **接入生产服务装配**
   - 修改 `backend/app/api/main.py`，在 `build_services()` 中用同一个 `skill_adapter` 创建并注册真实 Agent，将实例加入 `AppServices`。
   - `AppServices.skill_agent` 可声明为可选字段，以便现有独立 API 测试工厂保持轻量；生产 `build_services()` 必须创建真实实例，不做假运行时降级。
   - 本步不新增 HTTP 端点或模型自主 `reply()` 编排，保持现有 `SkillRequest` 为受信任内部调用输入，不扩大任意命令/用户身份的外部暴露面。

5. **补充真实生命周期测试**
   - 新建 `backend/tests/unit/test_agentscope_hook.py`，使用真实 AgentScope `ReActAgent`、Toolkit 和无网络模型替身。
   - 直接调用官方 `_acting(ToolUseBlock)`，验证 metaclass 确实触发 `pre_acting`，不是只检查注册字典。
   - 验证合法请求经 Toolkit 调用适配器一次，真实 Python CLI 获得正确用户凭证、独立目录和状态命名空间，结果可从 Agent 工具响应消息中恢复且不泄露秘密。
   - 验证非法请求在适配器/子进程执行前被 Hook 拦截，非目标工具不受影响。
   - 复用现有 `test_skill.py` 与 `test_skill_isolation.py` 回归凭证、目录、Redis 状态和双用户并发隔离。

## 验证

在 `backend` 目录依次执行：

```powershell
python -m pip install -e ".[dev]"
python -m pytest tests/unit/test_agentscope_hook.py tests/unit/test_skill.py tests/unit/test_app_services_api.py -q -rs
python -m pytest tests/integration/test_skill_isolation.py -q -rs
python -m pytest tests/integration/test_live_llm.py -q -rs
python -m pytest -q -rs
```

真实模型测试沿用已配置的讯飞凭证；完整路由测试若仍缺 Embedding，必须保持跳过并明确说明，不伪造通过结果。

## 参考

- [AgentScope Agent Hooks 官方文档](https://doc.agentscope.io/_sources/tutorial/task_hook.rst.txt)
- [AgentScope ReActAgent 官方示例](https://doc.agentscope.io/_sources/tutorial/quickstart_agent.rst.txt)
- [AgentScope Model 官方文档](https://doc.agentscope.io/tutorial/task_model.html)
