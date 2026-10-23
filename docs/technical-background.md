# Paper Downloader - 技术背景与实现分析

> [!abstract] 文档概述
> 本文档分析 paper-downloader skill 的技术实现逻辑、可行性评估、替代方案对比以及优化建议。

---

## 1. 当前实现架构

> [!info]- 核心组件
>
> ### 1.1 下载模块 (`scihub_downloader.py`)
> - **技术栈**: Playwright + Python asyncio
> - **浏览器**: Chromium (支持 Edge/Chrome 自定义)
> - **并发模式**: 串行下载（每个 DOI 独立浏览器实例）
> - **超时设置**: 90 秒/请求，最多 2 次重试
>
> ### 1.2 重命名模块 (`rename_papers.py`)
> - **元数据提取**: pdfplumber（可选依赖）
> - **命名规则**: `{year}_{first_author}_{keywords}.pdf`
> - **输入方式**: JSON manifest 或命令行参数

> [!info]- 当前工作流程（2026-04-14）
>
> ```mermaid
> graph TD
>     A[输入 DOI] --> B[Stage 1: Legal OA]
>     B --> B1[Unpaywall]
>     B --> B2[Semantic Scholar]
>     B --> B3[arXiv]
>     B --> B4[PubMed Central]
>     B --> B5[bioRxiv / medRxiv]
>     B1 --> C{命中 PDF?}
>     B2 --> C
>     B3 --> C
>     B4 --> C
>     B5 --> C
>     C -->|是| Z[保存文件]
>     C -->|否| D[Stage 2: DOI / publisher HTTP]
>     D --> E{命中 PDF?}
>     E -->|是| Z
>     E -->|否| F[Stage 3: Institutional Playwright]
>     F --> G{命中 PDF?}
>     G -->|是| Z
>     G -->|否| H[Stage 4: Single Sci-Hub mirror]
>     H --> I{命中 PDF?}
>     I -->|是| Z
>     I -->|否| J[失败 / 留待人工处理]
> ```

---

## 2. 技术可行性分析

> [!success]- 优势
>
> ### 2.1 浏览器自动化
> - **绕过反爬**: Playwright 模拟真实浏览器行为，有效应对 Cloudflare DDoS 防护
> - **动态内容**: 支持 JavaScript 渲染的页面
> - **灵活性**: 可处理复杂的页面结构（embed/iframe/动态链接）
>
> ### 多镜像容错
> - **高可用性**: 5 个镜像站点自动切换
> - **地域优化**: 不同地区可配置优先镜像
>
> ### 2.3 异步架构
> - **资源管理**: 每个下载独立浏览器实例，避免状态污染
> - **错误隔离**: 单个失败不影响其他任务

> [!warning]- 局限性
>
> ### 3.1 性能瓶颈
> - **串行下载**: 当前实现为顺序执行，批量下载效率低
> - **浏览器开销**: 每次启动 Chromium 需要 2-5 秒
> - **内存占用**: 单个浏览器实例约 200-500MB
>
> ### 3.2 稳定性风险
> - **镜像站点变化**: 域名可能失效或被封锁
> - **页面结构变化**: PDF 检测逻辑依赖 HTML 结构
> - **网络依赖**: 无离线缓存机制
>
> ### 3.3 功能缺失
> - **元数据提取不完整**: 依赖 pdfplumber 的简单正则匹配
> - **无进度恢复**: 中断后需重新开始
> - **缺少验证**: 未检查下载内容完整性（文件大小、页数）

---

## 3. 替代技术方案对比

> [!note]- 方案 A: 纯 HTTP 请求（requests + BeautifulSoup）
>
> | 维度 | 评估 | 说明 |
> |------|------|------|
> | **性能** | ⭐⭐⭐⭐⭐ | 无浏览器开销，速度快 10 倍 |
> | **稳定性** | ⭐⭐ | 易被 Cloudflare 拦截 |
> | **维护成本** | ⭐⭐⭐ | 需频繁更新 User-Agent 和 headers |
> | **适用场景** | 小规模下载（<10 篇） | |
>
> **结论**: 不推荐作为主方案，可作为快速通道备选

> [!note]- 方案 B: Selenium + undetected-chromedriver
>
> | 维度 | 评估 | 说明 |
> |------|------|------|
> | **性能** | ⭐⭐⭐ | 比 Playwright 稍慢 |
> | **稳定性** | ⭐⭐⭐⭐ | 更好的反检测能力 |
> | **维护成本** | ⭐⭐⭐⭐ | 社区活跃，更新频繁 |
> | **适用场景** | 需要绕过严格反爬的场景 | |
>
> **结论**: 可作为 Playwright 的替代方案

> [!note]- 方案 C: 学术 API（Semantic Scholar / Unpaywall）
>
> | 维度 | 评估 | 说明 |
> |------|------|------|
> | **性能** | ⭐⭐⭐⭐⭐ | 官方 API，速度快且稳定 |
> | **稳定性** | ⭐⭐⭐⭐⭐ | 无反爬风险 |
> | **覆盖率** | ⭐⭐⭐ | 仅限开放获取论文 |
> | **适用场景** | 优先尝试合法渠道 | |
>
> **结论**: 强烈推荐作为第一选择，Sci-Hub 作为备用

> [!note]- 方案 D: 混合架构（合法 OA + publisher + 机构 + Sci-Hub）
>
> ```python
> def download_paper(doi):
>     # 1. 先跑合法 OA 阶段
>     if pdf := try_open_access_stage(doi):
>         return pdf
>
>     # 2. 再跑 DOI / publisher HTTP
>     if pdf := try_publisher_http(doi):
>         return pdf
>
>     # 3. 最后再用机构浏览器
>     if pdf := try_institutional_browser(doi):
>         return pdf
>
>     # 4. 最终兜底才用 Sci-Hub
>     return try_scihub(doi)
> ```
>
> **结论**: 当前已采用的最佳实践方案，兼顾合法性、覆盖率和可维护性

---

## 4. 优化建议

> [!tip]- 4.1 性能优化
>
> ### 并行下载
> ```python
> # 当前: 串行
> for doi in dois:
>     await download_paper(doi)
>
> # 优化: 并行（限制并发数）
> async with asyncio.Semaphore(3):  # 最多 3 个并发
>     tasks = [download_paper(doi) for doi in dois]
>     await asyncio.gather(*tasks)
> ```
>
> ### 浏览器复用
> - 使用单个浏览器实例 + 多个 context
> - 减少启动开销，提升 50% 速度
>
> ### 智能镜像选择
> - 记录每个镜像的成功率和响应时间
> - 动态调整优先级

> [!tip]- 4.2 稳定性增强
>
> ### 镜像站点自动发现
> ```python
> def discover_scihub_mirrors():
>     # 从 https://sci-hub.now.sh/ 获取最新镜像列表
>     # 或使用 DNS 查询 sci-hub.* 域名
>     pass
> ```
>
> ### 断点续传
> ```python
> # 保存下载状态到 JSON
> {
>     "completed": ["10.1037/xxx"],
>     "failed": ["10.1016/yyy"],
>     "pending": ["10.1038/zzz"]
> }
> ```
>
> ### PDF 完整性验证
> - 检查文件大小 > 10KB
> - 验证 PDF 页数 > 0
> - 尝试打开 PDF 确认可读

> [!tip]- 4.3 功能扩展
>
> ### 元数据增强
> - 集成 Semantic Scholar API 获取准确元数据
> - 支持从 DOI 自动查询标题、作者、年份
>
> ### 合法 OA 阶段扩展
> - **已集成**: Unpaywall、Semantic Scholar、arXiv、PubMed Central、bioRxiv/medRxiv
> - **后续可加**: Europe PMC、OpenAlex、Crossref license hints
> - **保持原则**: 新数据源优先放进 OA stage，而不是散落在各个 fallback 分支
>
> ### 批量管理
> ```bash
> # 从文献管理软件导入
> python scihub_downloader.py --from-zotero library.json
> python scihub_downloader.py --from-endnote library.xml
> ```

> [!tip]- 4.4 用户体验优化
>
> ### 进度显示
> ```python
> from tqdm import tqdm
>
> for doi in tqdm(dois, desc="Downloading"):
>     await download_paper(doi)
> ```
>
> ### 交互式配置
> ```bash
> # 首次运行时引导配置
> python scihub_downloader.py --setup
> # 选择浏览器、设置代理、配置镜像优先级
> ```
>
> ### 日志系统
> - 使用 `logging` 模块替代 `print`
> - 支持不同日志级别（DEBUG/INFO/WARNING/ERROR）
> - 输出到文件便于排查问题

---

## 5. 潜在问题与解决方案

> [!danger]- 5.1 法律与伦理问题
>
> ### 问题
> - Sci-Hub 在部分国家/地区属于非法服务
> - 可能违反出版商版权协议
>
> ### 解决方案
> - **优先使用合法渠道**: Unpaywall、机构订阅
> - **添加免责声明**: 明确工具仅供学术研究使用
> - **用户责任**: 由用户自行判断使用合法性
>
> ```python
> # 添加确认提示
> print("警告: 请确保您有权访问这些论文")
> confirm = input("继续? (y/n): ")
> if confirm.lower() != 'y':
>     sys.exit(0)
> ```

> [!danger]- 5.2 反爬虫对抗升级
>
> ### 问题
> - Cloudflare 可能升级验证机制（验证码、行为分析）
> - 镜像站点可能限制单 IP 请求频率
>
> ### 解决方案
> - **代理池**: 支持配置多个代理 IP
> - **请求延迟**: 添加随机延迟（1-5 秒）
> - **浏览器指纹随机化**: 修改 User-Agent、屏幕分辨率等
>
> ```python
> # 随机延迟
> await page.wait_for_timeout(random.randint(1000, 5000))
>
> # 代理配置
> context = await browser.new_context(
>     proxy={"server": "http://proxy.example.com:8080"}
> )
> ```

> [!danger]- 5.3 依赖环境问题
>
> ### 问题
> - Playwright 需要下载浏览器二进制文件（~300MB）
> - Windows 用户可能缺少 Visual C++ 运行库
>
> ### 解决方案
> - **自动安装脚本**:
>   ```bash
>   pip install playwright
>   playwright install chromium
>   ```
> - **Docker 镜像**: 提供预配置环境
> - **便携版**: 打包成独立可执行文件（PyInstaller）

---

## 6. 技术选型建议

> [!success] 推荐架构（v2.0）
>
> ### 核心改进
> 1. **多数据源**: Unpaywall → 机构访问 → Sci-Hub
> 2. **并行下载**: asyncio.Semaphore 限制并发
> 3. **元数据集成**: Semantic Scholar API
> 4. **断点续传**: JSON 状态文件
> 5. **智能重试**: 指数退避 + 镜像切换
>
> ### 技术栈
> - **浏览器自动化**: Playwright（保持不变）
> - **HTTP 请求**: httpx（异步 HTTP 客户端）
> - **元数据**: semanticscholar（官方 Python SDK）
> - **PDF 处理**: pypdf（轻量级，替代 pdfplumber）
> - **进度显示**: rich（现代化终端 UI）
>
> ### 预期提升
> - **速度**: 3-5 倍（并行 + 浏览器复用）
> - **成功率**: +15%（多数据源 + 智能重试）
> - **用户体验**: 显著改善（进度条 + 彩色日志）

---

## 7. 实现优先级

> [!abstract] 开发路线图
>
> ### Phase 1: 性能优化（高优先级）
> - [ ] 并行下载（限制并发数为 3）
> - [ ] 浏览器实例复用
> - [ ] 断点续传功能
>
> ### Phase 2: 稳定性增强（高优先级）
> - [ ] PDF 完整性验证
> - [ ] 智能镜像选择
> - [ ] 错误日志系统
>
> ### Phase 3: 功能扩展（中优先级）
> - [ ] Unpaywall API 集成
> - [ ] Semantic Scholar 元数据
> - [ ] 进度条显示
>
> ### Phase 4: 高级特性（低优先级）
> - [ ] 北大图书馆支持
> - [ ] CNKI 下载
> - [ ] Zotero/EndNote 集成

---

## 8. 出版商访问兼容性矩阵

> [!info] 实测结论（2026-03-15，基于 httpx 纯 HTTP 请求）
>
> 本节记录各主流出版商对 `institutional_downloader.py`（纯 httpx）的兼容性，
> 以及推荐的下载路径。数据来自实际测试和 Zotero translator 源码分析。

### 8.1 可用出版商（httpx 可直接访问）

| 出版商 | 域名 | 访问方式 | 备注 |
|--------|------|---------|------|
| PLOS ONE / PLOS journals | `journals.plos.org` | `citation_pdf_url` meta 标签 | ✅ 实测成功，开放获取 |
| PubMed Central (PMC) | `pmc.ncbi.nlm.nih.gov` | 直接 PDF 链接 | ✅ 开放获取，无反爬 |
| arXiv | `arxiv.org` | 直接构造 `/pdf/{id}` | ✅ 开放获取 |
| bioRxiv / medRxiv | `biorxiv.org` | `citation_pdf_url` | ✅ 预印本，开放获取 |
| MDPI | `mdpi.com` | `citation_pdf_url` | ✅ 开放获取 |
| Frontiers | `frontiersin.org` | `citation_pdf_url` | ✅ 开放获取 |
| Springer Open / BioMed Central | `springeropen.com`, `biomedcentral.com` | `citation_pdf_url` | ✅ 开放获取 |
| Hindawi | `hindawi.com` | `citation_pdf_url` | ✅ 开放获取 |

### 8.2 不可用出版商（httpx 无法访问）

| 出版商 | 域名 | 失败原因 | 推荐替代路径 |
|--------|------|---------|------------|
| **Wiley Online Library** | `onlinelibrary.wiley.com` | Cloudflare 拦截（"Just a moment..." 403） | Sci-Hub（`scihub_downloader.py`） |
| **APA PsycNET** | `psycnet.apa.org` | Angular SPA，httpx 只拿到空壳（7946字节，0链接） | Sci-Hub（`scihub_downloader.py`） |
| **Elsevier / ScienceDirect** | `sciencedirect.com` | Cloudflare + JS 渲染 | Sci-Hub |
| **Taylor & Francis** | `tandfonline.com` | Cloudflare 拦截 | Sci-Hub |
| **SAGE Journals** | `journals.sagepub.com` | Cloudflare 拦截 | Sci-Hub |
| **Springer Link**（订阅内容） | `link.springer.com` | 订阅内容需机构 IP + 可能有 Cloudflare | Sci-Hub 或 VPN + Playwright |
| **Nature / Springer Nature** | `nature.com` | Cloudflare 拦截 | Sci-Hub |
| **Oxford Academic** | `academic.oup.com` | Cloudflare 拦截 | Sci-Hub |
| **Cambridge Core** | `cambridge.org` | Cloudflare 拦截 | Sci-Hub |
| **ACS Publications** | `pubs.acs.org` | Cloudflare 拦截 | Sci-Hub |

### 8.3 根本原因分析

**Cloudflare 拦截**：Wiley、Taylor & Francis、SAGE 等主流出版商均部署了 Cloudflare Bot Management。
纯 httpx 请求会被识别为机器人并返回 403（"Just a moment..." challenge page）。
解决方案：使用真实浏览器（Playwright），即 `scihub_downloader.py` 路径。

**JavaScript SPA**：APA PsycNET 使用 Angular 框架，页面内容完全由 JS 渲染。
httpx 只能拿到 Angular 的空壳 HTML（约 7946 字节），无任何实际链接。
解决方案：同上，需要 Playwright 执行 JS。

**DOI 注册问题**：部分老旧 DOI（如 `10.1348/000709905X37396`，2006年）在 doi.org 和 CrossRef 均返回 404，
但出版商网站本身有该论文。这是 DOI 注册缺失问题，与代码无关。

### 8.4 下载路径决策树

```
输入 DOI
  ↓
institutional_downloader.py（纯 httpx）
  → 适合：开放获取期刊（PLOS、PMC、arXiv、bioRxiv、MDPI、Frontiers 等）
  → 不适合：Wiley、APA、Elsevier、T&F、SAGE、Nature、Oxford、Cambridge
  ↓ 失败
OA 查询（Unpaywall / Zotero OA service）
  → 适合：有开放版本的订阅论文
  ↓ 失败
scihub_downloader.py（Playwright 浏览器）
  → 适合：所有出版商（绕过 Cloudflare，执行 JS）
  → 需要：Playwright 已安装，浏览器可用
```

---

## 9. 参考资源

> [!info]- 相关技术文档
>
> - [Playwright Python 文档](https://playwright.dev/python/)
> - [Unpaywall API](https://unpaywall.org/products/api)
> - [Semantic Scholar API](https://api.semanticscholar.org/)
> - [Sci-Hub 镜像列表](https://sci-hub.now.sh/)
> - [反爬虫对抗技术](https://github.com/ultrafunkamsterdam/undetected-chromedriver)
> - [Zotero translator 源码（omni.ja）](https://github.com/zotero/zotero) — 各出版商 PDF URL 提取逻辑的权威参考
