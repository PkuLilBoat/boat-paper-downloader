# Zotero 全文查找功能集成研究报告

> [!abstract] 研究概述
> 本报告深入分析 Zotero 官方的 "Find Available PDF" 功能实现原理，评估其利用机构 IP 下载论文的机制，并提供集成到 paper-downloader skill 的完整方案。

---

## 1. Zotero 全文查找功能分析

> [!info]- 核心功能概述
>
> Zotero 的 "Find Available PDF" 功能是一个多数据源、智能回退的论文下载系统，能够：
> 1. **利用机构 IP 访问订阅资源**（校园网/VPN）
> 2. **自动尝试多个数据源**（DOI 解析、开放获取、自定义解析器）
> 3. **处理复杂的认证流程**（重定向、Cookie、元刷新）
> 4. **智能限流和错误处理**（域名级别的请求控制）

### 1.1 工作原理

```mermaid
graph TD
    A[用户触发查找] --> B{获取 DOI/URL}
    B -->|有 DOI| C[DOI 解析器]
    B -->|有 URL| D[URL 解析器]
    B -->|都有| E[开放获取 API]
    C --> F[访问 doi.org]
    D --> G[访问原始 URL]
    E --> H[Unpaywall API]
    F --> I{检测 IP 认证}
    G --> I
    H --> I
    I -->|校园网/VPN| J[直接下载 PDF]
    I -->|需要登录| K[跳过]
    I -->|开放获取| J
    J --> L[验证文件类型]
    L -->|PDF/EPUB| M[保存附件]
    L -->|其他| K
