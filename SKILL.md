---
name: codex-dispatch
description: 把耗 token 的重活（通读大量文件、文献检索、批量整理、写长文档、画图、写脚本）派给本机 OpenAI Codex 去做，Claude 只写任务说明、抽查结果。支持三种派法：codex exec 后台小任务、queue 发到桌面版已有会话、用脚本新建 Codex 会话并指定模型和推理等级。当用户要求「交给 Codex」「派给 Codex」「让 Codex 做」，或任务明显需要大量读写、而用户希望节省 Claude 额度时使用。仅适用于 Windows + Codex 桌面版。
---

# 给 Codex 派活

目的：重活交给 Codex（消耗用户的 ChatGPT 额度），Claude 负责商量方案、写任务说明、抽查结果，并用大白话向用户汇报。用户能在 Codex 桌面版侧边栏看到每个会话在干什么。

## 0. 先选派法

| 情况 | 用法 |
|---|---|
| 小任务、不用人盯、几分钟完（检查、改个文件、画图、写脚本） | `codex exec` 后台跑（第 5 节），侧边栏看不到 |
| 要用户看得到、已有合适会话 | queue 发到那个会话（第 3 节） |
| 新任务线、要换模型/推理等级、要并行分工 | 脚本新建会话（第 2 节），再 queue 发任务 |

## 1. 找工具

- codex.exe：`%LOCALAPPDATA%\OpenAI\Codex\bin\<哈希>\codex.exe`，不在 PATH 里。桌面版更新后哈希会变，取能跑 `--version` 的最新那个。
- 新建会话脚本：本技能目录下的 `scripts/new_session.py`（只用 Python 3.10+ 标准库）。
- 在用户自己的 PowerShell 里如果 `codex` 被执行策略拦住，改用 `codex.cmd`。
- 第一次用时，先跑 `python scripts/new_session.py --cwd <任意目录> --model x --effort x --name x --check`，确认能握手，并看本机有哪些模型和推理等级。

## 2. 新建会话（脚本）

1. **先征得用户同意**：说明要开几个会话、各做什么、用什么模型和推理等级、大概消耗多少额度。
2. 运行（外面最好套一个超时）：
   `python <技能目录>/scripts/new_session.py --cwd '<项目目录>' --model <模型> --effort <等级> --name '<项目-任务>'`
   - 只读任务加 `--sandbox read-only`。
   - 从输出里记下 threadId 和会话 jsonl 路径。
3. 新会话要等**用户下次重启 Codex 桌面版**才会出现在侧边栏。不必专门请用户重启，后台照样能派活。只有任务需要用户在桌面版里操作（点授权、回答问题）时，才请用户重启并点开该会话。
4. 用第 3 节的 queue 发第一条任务。
   - 脚本也能用 `--message-file` 直接发任务，但这样是脚本进程在跑：审批请求一律被拒绝，用户也看不到进度。只在不需要用户参与时使用。
5. 脚本的安全设计：用自己的客户端名字，不冒充官方客户端；禁止模型回退；建完读回模型和推理等级核对，不一致就报错；不重试、不轮询；不修改 `~/.codex` 下的配置和数据库。**不要为了让会话显示出来去冒充 Codex Desktop，也不要改 sqlite。**

## 3. 给已有会话派任务（queue）

1. **找会话 UUID**：脚本建的看脚本输出；用户在桌面版建的，在 `%USERPROFILE%\.codex\.codex-global-state.json` 里搜会话名。不要按会话文件的修改时间找，不可靠。打开 jsonl 第一行看 `cwd`，确认工作目录正确。
2. **写任务说明**：写进一个 UTF-8 文件。
   - 开头写：【来自 Claude · 发给本地 Codex】用户已同意（用户原话：……）。
   - 接着写背景、要做什么、限制、汇报格式，汇报要限定行数。
   - **全文不能有英文双引号**，需要引号时用「」。
3. **点开会话**：如果会话已在侧边栏，先请用户在桌面版里点开它。没打开的话，任务照样会在后台执行，只是用户看不到进度。
4. **发送**：先记下会话 jsonl 当前的行数 N，再在 PowerShell 里运行：
   ```powershell
   $msg=[IO.File]::ReadAllText('<任务文件>',[Text.Encoding]::UTF8)
   if ($msg.Contains('"')) {'有双引号'} else { & '<codex.exe>' queue --thread <UUID> --message $msg --resume }
   ```
   - 看到 `Queued message ...` 就说明发成功了。
   - 随后出现的 `could not resume` 或 `already has an active writer` 警告是正常的，不用处理。
5. **等它做完**：在后台等（run_in_background），不要手动轮询：
   ```bash
   for i in $(seq 1 360); do tail -n +<N+1> '<jsonl>' | grep -q '"task_complete"' && { echo done; exit 0; }; sleep 10; done; echo timeout
   ```
6. **读结果**：从第 N+1 行起，找 `payload.type=message` 且 `role=assistant` 的行，读最后一两条。逐条对照任务要求抽查；有不清楚的地方，再派一条追问。

另外注意：queue 不能改模型（不支持 `--model`）。要换模型或推理等级，就新建会话。

## 4. 选模型和推理等级

模型名以 `--check` 列出的为准。下表按 Codex 官方描述举例：

| 定位 | 例子 | 用在 |
|---|---|---|
| 最新主力 | gpt-6.1-sol | **默认**：写作、改文档、文献检索、写代码、调研 |
| 最强最贵 | gpt-6-astra | 真正难的事：复杂设计、疑难 bug、关键论证审查 |
| 快而便宜 | gpt-6-luna | 机械活：批量改名、格式转换、照模板填写、简单检查 |

推理等级越高越慢，也越费额度：

| 等级 | 用在 |
|---|---|
| low | 纯机械、步骤写死的活 |
| medium | 常规小任务、测试、画图、简单脚本 |
| high | **默认**：写作、检索、审查、多步骤任务 |
| xhigh / max | 难题、第一次做设计、high 没做好重来 |
| ultra | 基本不用；要用先问用户 |

原则：先用够用的最低档，没做好再升一档。

## 5. codex exec（不进侧边栏的后台小任务）

```powershell
$prompt | codex.cmd exec --skip-git-repo-check -C <目录> -s workspace-write -m <模型> -c model_reasoning_effort=<等级> -o <结果文件> -
```

- 放到后台运行，完成后读 `-o` 指定的结果文件。
- 目录不是 git 仓库、也不在 Codex 信任列表里时，必须加 `--skip-git-repo-check`。
- 需要联网时加 `-c sandbox_workspace_write.network_access=true`。如果本机要走代理才能上网，设置 `HTTPS_PROXY`。
- 任务说明里写「网络请求走代理」时，要写明插件除外，否则 Codex 可能会跳过插件。

## 6. 开多个会话分工

- 按产出能不能互相独立来拆分，例如报告一条线、PPT 一条线。互相依赖的步骤放在同一个会话里。
- **同一个目录，同一时间只派一个会改文件的 Codex。** 要并行，就让它们写不同的文件夹。
- 默认同时跑 1～2 个，最多 3 个。每多开一个，都先征得用户同意。
- 会话命名用「项目-任务」，方便用户在侧边栏辨认。
- 中途换会话时，让前一个会话把进度写成交接文件，下一个会话从这个文件接着做，不重做前面的工作。

## 7. 规矩

- 只读检查、在项目目录里改文件、编译、测试，可以直接派。推送、合并、删除、改 GitHub 或账号设置、花钱、连服务器，必须先问用户，并在任务说明里写上用户原话。
- 任务说明和聊天里不能出现密码、密钥、token、代理账号。Codex 的汇报里如果出现了，要提醒用户。
- 永远不用 `--dangerously-bypass-approvals-and-sandbox`。
- Codex 停下来提问或要求授权时，原样转述给用户，不替用户点授权。登录、验证码由用户本人完成。
- 任务说明要实事求是地写清用途。不要为了避开 Codex 的拒绝而改换说法。
- 汇报给用户时要简短，只说结果和需要用户做的事。
