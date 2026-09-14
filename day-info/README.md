# day-info · AI 技术收集池（自动化维护）

> 原则：**收集频率高，推送频率低，形成漏斗。替你消化信息，不替你生产焦虑。**

## 机制

| 环节 | 频率 | 产物 |
| --- | --- | --- |
| 收集（静默） | 每天 19:00（自动） | `pool/YYYY-MM-DD.json`（机器读）+ `.md`（人读），三档打标 |
| 周报 | 每周日 20:00（自动） | `digests/YYYY-Www.md`（必须看 + 值得看精选，15 分钟可扫完） |
| 即时提醒 | 触发式 | 只有「必须看且项目强相关」（如 X6/G6/AntV 新版本）才即时提醒，置顶在当天收集结果里 |

分级规则：

- **必须看**：重大模型发布 / 与知识图谱・Agent・X6/G6・Obsidian 直接相关；
- **值得看**：论文、新工具、教程、开源项目、博客；
- **仅存档**：其它新闻与低相关内容（只在池里存档，不进周报）。

`critical = 必须看 且 项目强相关` → 供「即时提醒」使用。

## 目录

- `pool/` 每日收集池（JSON 供机器读，MD 供人读）
- `digests/` 每周周报；`digests/raw/` 周报合并材料
- `cron/` 定时任务配置归档（完整配置 + 提示词，可迁移复用）
- `scripts/collect.py` 收集器（零依赖，可手动跑）
- `scripts/weekly.py` 周材料合并（周报前处理）
- `scripts/daily.sh` 每日收集 + 提交（定时任务调用）
- `scripts/publish.sh` 提交助手（自动尝试 HTTPS → SSH 双通道推送）
- `state.json` 去重 / 版本比对状态（45 天去重窗口）

## 手动使用

```bash
python3 day-info/scripts/collect.py           # 收集今天的（同日重跑会合并，不重复）
python3 day-info/scripts/weekly.py            # 合并最近 7 天为周材料
bash day-info/scripts/daily.sh                # 收集 + 本地提交（+推送，若凭证就绪）
bash day-info/scripts/publish.sh "提交信息"    # 只提交/推送（周报写完后调用）
```

数据来源：arXiv（cs.AI/CL/LG/SE）、HuggingFace（hf-mirror 镜像）、GitHub（新项目搜索 + 热门活跃仓库 + antvis 版本发布）、OpenAI / DeepMind / Anthropic 博客、Hacker News、IT之家、精选技术博客 RSS。

## 推送凭证（本机已配好，无需操作）

推送目标：本仓库 `day-info-for-autoclaw` 分支。两个通道任选其一即可，`publish.sh` 会自动依次尝试。

> **本机（macOS）现状**：通道 A 已可用，**无需任何额外配置，也不需要申请 PAT**。通道 B 在本机当前网络下不可用，仅作其他环境的备选保留。

**通道 A：SSH（本机已配好，推荐）**

本机已满足全部条件，不需要再做任何事：

- `~/.ssh/config` 已把 `github.com` 指向 `ssh.github.com:443`（本机 22 端口被代理接管，直连不通）
- 私钥：`~/.ssh/id_ed25519_github`
- 公钥已添加到 GitHub 且**具备写权限**。验证命令：

```bash
ssh -T git@github.com
# 期望输出：Hi asdbex1078! You've successfully authenticated, but GitHub does not provide shell access.
```

> ⚠️ **该链路经代理时会间歇性抖动**：TCP 建立后立即被对端关闭（报 `Connection closed by 198.18.0.x port 443`）。2026-09-14 实测连续 8 次探测失败 3 次（约 37%）。**这是代理链路问题，不是凭证问题**——所以 `publish.sh` 内置了 6 次重试（可用环境变量 `DAYINFO_PUSH_RETRIES` 调整），失败率可压到 0.3% 以下。单次 SSH 失败不代表推送失败，不要据此去重新配凭证。
>
> 若抖动持续影响使用，根因应在代理软件侧处理：检查其路由规则里 `ssh.github.com` / `github.com` 是否被 fake-IP 接管并走了不稳定的节点/规则。

如需改用本仓库专用的 Deploy Key（而不是个人密钥）：

- 私钥路径：`.secrets/github_deploy_key`（同样不入库）
- 公钥添加到 https://github.com/asdbex1078/Knowrary/settings/keys ，勾选 **Allow write access**

**通道 B：HTTPS + Personal Access Token（本机当前不可用）**

保留此通道仅为兼顾其他网络环境。**在本机当前网络下，到 `github.com:443` 的 TLS 握手直接失败**（`OpenSSL SSL_connect: SSL_ERROR_SYSCALL`），因此 PAT 在此机器上无法使用——申请了也推不上去。若将来换到其他网络环境，可按以下步骤配置：

1. GitHub → 头像 → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token
2. Repository access 选 **Only select repositories**，只勾 `Knowrary`
3. Permissions → Repository permissions → **Contents → Read and write**（push 所需的最小权限）
4. 写入凭证文件：

```bash
mkdir -p .secrets
printf 'https://x-access-token:<TOKEN>@github.com\n' > .secrets/git-credentials
chmod 600 .secrets/git-credentials
```

- 凭证文件路径：`.secrets/git-credentials`（`.gitignore` 已忽略 `.secrets/`，不会入库）
- 内容格式：`https://x-access-token:<TOKEN>@github.com`
- 也可用环境变量 `KNOWRARY_DAYINFO_CREDS` 指向其他凭证文件路径

> 未配置凭证时：一切照常收集并本地提交，只是不推送；配置任一通道后自动开始推送。

**历史备注**：原 AutoClaw 环境的凭证与 Deploy Key 位于其沙箱内（`/root/.openclaw-autoclaw/workspace/.secrets/`），在本机不存在；`publish.sh` 对它们的引用仅作为向后兼容的回退路径，本机走通道 A 即可，无需重建。

---

*本目录由 WorkBuddy 自动化任务维护（原为 AutoClaw，已迁移）：`pool/` 与 `digests/` 以自动生成为准；手动修改请只动说明文档。*
