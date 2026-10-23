# BOAT Paper Downloader

从 DOI 清单到本地论文全文：批量尝试开放获取、出版社直连与浏览器辅助路线，保留成功的 PDF，默认通过本地 MinerU 转成 Markdown，并记录未完成项。

这是 `paper-downloader` Skill 的公开发布副本。GitHub 仓库及公开 Skill 名称使用 `boat-paper-downloader`；脚本名、结果 schema 和 `p0_import_source: paper-downloader` 保留原标识，兼容已有调用。

## 能做什么

- 输入一篇或多篇论文的 DOI，批量获取 PDF。
- 并行尝试开放获取与出版社 HTTP；失败后使用已有可用代理、受限并发的浏览器回退，以及源码中保留的最终 Sci-Hub 回退。
- 校验 PDF，记录失败与需要人工处理的情况；不保证每篇论文都能获取。
- 默认将成功下载的 PDF 转为同名 Markdown，正文图片放入 `images/`。
- 可选执行重命名、去重、SQLite 索引、文献集合管理和中英交错翻译。

默认只写入指定下载目录。正式文献库整理需要显式使用相应参数，并遵守使用者自己的入库规则。需要机构登录的来源须自行配置有效访问权限；仓库不附带任何登录状态。

## 安装

核心下载协调器需要 Python 3.10+，支持 macOS / Linux（使用 Unix `fcntl` 文件锁）。浏览器辅助需要 Playwright。

```bash
git clone https://github.com/PkuLilBoat/boat-paper-downloader.git
cd boat-paper-downloader
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
```

作为 Codex / Claude Skill 使用时，将此目录放入客户端支持的 Skill 目录，或建立到此目录的链接；入口为 [SKILL.md](SKILL.md)。

### 可选依赖

- PDF → Markdown：另行安装提供 `magic-pdf` 命令的 MinerU 版本及模型。当前转换包装器按 Apple Silicon 的 MPS 环境配置，其他设备需适配转换环境；缺少转换器会明确记录转换失败，不会悄悄换成其他提取方式。
- 翻译：`scripts/translate_markdown.py` 需要已安装并登录的 `codex` CLI。
- Finder 标签：仅 macOS 适用。

## 快速使用

下载并默认转换：

```bash
python scripts/scihub_downloader.py ./papers 10.1037/0022-3514.73.2.345
```

先验证下载链、暂不进行格式转换：

```bash
python scripts/scihub_downloader.py ./papers 10.1037/0022-3514.73.2.345 --no-convert
```

批量下载并写出结果清单：

```bash
python scripts/scihub_downloader.py ./papers \
  10.1037/0022-3514.73.2.345 10.1016/j.paid.2007.07.016 \
  --concurrency 2 --result-json ./download-result.json
```

默认输出结构：

```text
papers/
└── <doi-safe-stem>/
    ├── <doi-safe-stem>.pdf
    ├── <doi-safe-stem>.md
    └── images/
```

PDF 获取与 Markdown 转换分别记录结果。PDF 已下载不代表转换成功；以结果清单中的各阶段状态为准。

## 本地配置与数据

- [local/institution_login.example.json](local/institution_login.example.json) 只含占位符。需要时复制为 `local/institution_login.private.json`，自行填写；实际文件已被 Git 忽略。
- `browser_profile/`、`logs/`、运行清单、下载文件和数据库均属于本地数据，不进入公开仓库。
- 默认文献库路径为 `~/Documents/PAPER/Papers`。整理工具支持的 `--library-dir` / `--destination-dir` 参数以对应脚本 `--help` 为准；运行整理、移动或去重前先查看实际目标，支持预演的命令先用 `--dry-run`。
- PDF 下载不会自动把文件搬进正式文献库；`--organize-after-download` 才开启该交接。
- 个人 Obsidian Rule 文件与 Skill 管理台账不随仓库发布；使用者应采用自己的命名、分类和来源规则。

## 文档

- [浏览器会话配置](docs/institutional-browser.md)
- [机构登录配置](docs/institutional-login-automation.md)
- [知网下载衔接](docs/cnki-download-integration.md)
- [站点策略](docs/site-strategy-playbook.md)
- [文献库组织](docs/library-organization.md)
- [Markdown 翻译](docs/markdown-translation.md)

部分文档保留了开发阶段的站点观察，不能视为当前网站可访问性保证。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试主要使用临时文件与模拟网络响应；不代表所有出版社登录、真实 DOI 获取、MinerU 模型或翻译服务均已在线验收。

## 发布说明

此公开副本从当前源码整理，使用全新 Git 历史，排除了原仓库中的浏览器 profile、凭据文件、日志、数据库和个人项目复盘。它与作者本机的运行 Skill 分开维护。

仓库当前未指定开源许可证。
