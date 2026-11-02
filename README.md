# codex-dispatch：让 Claude Code 给 OpenAI Codex 派活

Claude 额度少、Codex 额度多？这个技能让 Claude Code 只负责「想清楚、写任务说明、抽查结果」，把通读文件、检索文献、批量整理、写长文这类耗 token 的活派给本机的 Codex 去做。派出去的会话可以显示在 Codex 桌面版侧边栏里，你能看到它干活的过程。

## 能做什么
- `codex exec` 后台跑小任务
- 把任务发到 Codex 桌面版已有会话（`codex queue`），在侧边栏实时可见
- 用脚本新建 Codex 会话，自己指定工作目录、模型、推理等级和会话名
- 附带选模型、选推理等级、多会话分工、安全规矩的说明

## 需要什么
- Windows，已安装并登录 Codex 桌面版（脚本按 0.166 版协议编写验证）
- Python 3.10+（只用标准库）
- Claude Code

## 安装
1. 把本仓库放到 `C:\Users\<你的用户名>\.claude\skills\codex-dispatch\`：
   ```powershell
   git clone https://github.com/Draymond-Sreike/codex-dispatch "$env:USERPROFILE\.claude\skills\codex-dispatch"
   ```
   没装 git 的话，在 GitHub 页面点 Code → Download ZIP，解压后把文件夹改名为 `codex-dispatch` 放到同一位置。
2. 测试脚本能否连上 Codex（只列模型，不建会话）：
   ```powershell
   python "$env:USERPROFILE\.claude\skills\codex-dispatch\scripts\new_session.py" --cwd "$env:USERPROFILE" --model x --effort x --name x --check
   ```
   能列出模型就说明可用（最后会因为模型名 x 不存在而报错，属正常）。
3. 之后在 Claude Code 里说「把这件事交给 Codex 做」，Claude 就会按技能流程派活。
4. 装好即可：技能会在遇到耗 token 的活时主动派给 Codex，不需要再改 CLAUDE.md。

## 已知情况
- 脚本新建的会话，要重启一次 Codex 桌面版才出现在侧边栏；不重启也能在后台正常干活。
- 发给 Codex 的消息不能含英文双引号（会被命令行拆坏），用「」代替。
- `codex queue` 不能改模型；要换模型就新建会话。
- 桌面版更新后 codex.exe 所在的哈希文件夹会变，脚本会自动找最新可用的那个。
- 协议是实验接口，Codex 升级后可能失效；脚本出错会直接报出失败步骤，不会悄悄降级。

## 安全设计
- 脚本用自己的客户端名字，不冒充官方客户端；禁止模型自动回退；建完会话读回模型和推理等级核对。
- 不重试、不轮询，不修改 `~/.codex` 下的配置和数据库。
- 脚本自己发消息时，Codex 的一切授权请求都会被拒绝并记录；需要授权的任务请走桌面版会话，由你本人点授权。
- 永远不使用 `--dangerously-bypass-approvals-and-sandbox`。
