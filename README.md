# codex-dispatch：让 Claude Code 给 OpenAI Codex 派活

Claude 额度少、Codex 额度多？这个技能让 Claude Code 只负责「想清楚、写任务说明、抽查结果」，把通读文件、检索文献、批量整理、写长文这类耗 token 的活派给本机的 Codex 去做。派出去的会话可以显示在 Codex 桌面版侧边栏里，你能看到它干活的过程。

## 能做什么
- `codex exec` 后台跑小任务
- 把任务发到 Codex 桌面版已有会话（`codex queue`），在侧边栏实时可见；daemon 在运行时不用你点开会话就会执行
- 会话正在跑时中途插话、让它改方向（`scripts/send.py --steer`）
- 用脚本新建 Codex 会话，自己指定工作目录、模型、推理等级和会话名
- 附带选模型、选推理等级、多会话分工、安全规矩的说明

## 需要什么
- Windows，已安装并登录 Codex 桌面版（脚本按 0.166 版协议编写验证）
- Python 3.10+（只用标准库）
- Claude Code

## 最快安装：把下面这段话发给你的 AI

适用于 Claude Code；其他能执行命令、读写文件的 AI 助手也能照做（技能目录和规则文件的位置按它自己的约定放）。原样复制粘贴：

```text
帮我安装并配置 codex-dispatch（让 AI 把耗 token 的重活派给本机 Codex），按顺序做，每步出错就停下告诉我：
1. 把 https://github.com/Draymond-Sreike/codex-dispatch git clone 到 %USERPROFILE%\.claude\skills\codex-dispatch（已存在就在该目录 git pull 更新）。
2. 找到 codex.exe：在 %LOCALAPPDATA%\OpenAI\Codexin\ 下各子文件夹里，取能成功运行 --version 的最新一个。然后运行仓库 README「手动安装」第 2 步的 --check 命令，告诉我能不能连上 Codex、有哪些模型。
3. 检查 Codex 后台 daemon：运行「<codex.exe> app-server daemon version」。输出里是 running 就不用动；报 failed to connect 就运行「<codex.exe> app-server daemon start」，再检查一次。（daemon 不开，发给 Codex 的消息只排队，要我手动点开会话才会执行。）
4. 在我的全局规则文件（Claude Code 是 %USERPROFILE%\.claude\CLAUDE.md）末尾加上下面这条规则（文件不存在就新建；已有同样内容就不要重复加）：

## 重活派给 Codex
凡是会大量消耗 token 的活（通读或扫描很多文件、读长文档或 PDF、网页或文献检索、批量整理改写、写长文档、画图、写较长脚本、为新会话准备背景材料），先用 codex-dispatch 技能派给本机 Codex 去做，Claude 只写任务说明、抽查结果。开新的 Codex 会话前先征得我同意。

5. 做完用三五句话告诉我：技能装好了吗、能连上 Codex 吗、daemon 在运行吗、还缺什么。
```

装好后，以后遇到耗 token 的活，Claude 会主动派给 Codex。

## 手动安装
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
4. 可选：在 `~/.claude/CLAUDE.md` 里加上「最快安装」第 3 步那条规则，Claude 会更主动地派活。

## 已知情况
- **queue 要真正生效，需要本机 daemon 在运行**：先 `codex app-server daemon version` 看是不是 `running`，不是就 `codex app-server daemon start`。daemon 没开时，消息只排队，要你在桌面版点开该会话才会跑。
- queue 发的消息遇到正在跑的会话，是排在这一轮之后；要中途插话请用 `scripts/send.py --steer <turn id>`。
- 脚本新建的会话，有时要重启一次 Codex 桌面版才出现在侧边栏；不重启也能在后台正常干活。
- 发给 Codex 的消息不能含英文双引号（会被命令行拆坏），用「」代替。
- `codex queue` 不能改模型；要换模型就新建会话。
- 桌面版更新后 codex.exe 所在的哈希文件夹会变，脚本会自动找最新可用的那个。
- 协议是实验接口，Codex 升级后可能失效；脚本出错会直接报出失败步骤，不会悄悄降级。

## 安全设计
- 脚本用自己的客户端名字，不冒充官方客户端；禁止模型自动回退；建完会话读回模型和推理等级核对。
- 不重试、不轮询，不修改 `~/.codex` 下的配置和数据库。
- 脚本自己发消息时，Codex 的一切授权请求都会被拒绝并记录；需要授权的任务请走桌面版会话，由你本人点授权。
- 永远不使用 `--dangerously-bypass-approvals-and-sandbox`。
