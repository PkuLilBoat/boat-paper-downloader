# Semantic Scholar 集成说明

> 测试日期：2026-03-06

本文档记录通过 Semantic Scholar 查询 `openAccessPdf` 和 `externalIds` 的方式，作为 paper-downloader 中合法 OA 阶段的一部分。

---

## 1. 工具调用方式

> [!info]- MCP 工具参数格式
> 工具名称：`mcp__semantic-scholar__paper_details`
>
> 必填参数：
> - `paper_id`：支持多种格式前缀
>   - `DOI:10.xxxx/xxxxx`
>   - `ARXIV:2106.15928`
>   - `PMID:19872477`
>   - `ACL:N19-1423`
>   - 直接使用 Semantic Scholar 内部 ID（40位哈希）
> - `fields`：字段列表，查询 OA 信息时至少包含：
>   ```json
>   ["openAccessPdf", "externalIds", "title", "year"]
>   ```
>
> 调用示例（伪代码）：
> ```python
> result = mcp__semantic-scholar__paper_details(
>     paper_id="DOI:10.1038/s41586-021-03819-2",
>     fields=["openAccessPdf", "externalIds", "title", "year"]
> )
> ```

---

## 2. openAccessPdf 字段结构

> [!info]- 返回结构说明
> `openAccessPdf` 是一个对象，包含三个子字段：
>
> ```json
> {
>   "openAccessPdf": {
>     "url": "https://...",
>     "status": "HYBRID | GOLD | GREEN | BRONZE | CLOSED | null",
>     "license": "CCBY | null"
>   }
> }
> ```
>
> **status 含义**：
> | 值 | 含义 |
> |---|---|
> | `GOLD` | 期刊本身是 OA 期刊 |
> | `HYBRID` | 混合期刊中的 OA 文章（作者付费） |
> | `GREEN` | 预印本/机构库中的 OA 版本 |
> | `BRONZE` | 出版商临时免费，无正式许可证 |
> | `CLOSED` | 无 OA 版本 |
> | `null` | 未知状态 |
>
> **判断是否可用**：`url` 字段非空且非 `""` 时，表示有可直接下载的链接。

---

## 3. 三个测试 DOI 的实际返回结果

> [!tip]- DOI 1：AlphaFold2（有 OA 版本）
> **DOI**: `10.1038/s41586-021-03819-2`
> **论文**: Highly accurate protein structure prediction with AlphaFold (2021)
>
> 返回结果：
> ```json
> {
>   "paperId": "dc32a984b651256a8ec282be52310e6bd33d9815",
>   "externalIds": {
>     "PubMedCentral": "8371605",
>     "DOI": "10.1038/s41586-021-03819-2",
>     "CorpusId": 235959867,
>     "PubMed": "34265844"
>   },
>   "title": "Highly accurate protein structure prediction with AlphaFold",
>   "year": 2021,
>   "openAccessPdf": {
>     "url": "https://www.nature.com/articles/s41586-021-03819-2.pdf",
>     "status": "HYBRID",
>     "license": "CCBY"
>   }
> }
> ```
>
> 结论：有效 OA 链接，status=HYBRID，license=CCBY，可直接下载。

> [!tip]- DOI 2：BERT 论文（有 ArXiv，但 openAccessPdf 为空）
> **DOI**: `10.18653/v1/N19-1423`
> **论文**: BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding (2019)
>
> 返回结果：
> ```json
> {
>   "paperId": "df2b0e26d0599ce3e70df8a9da02e51594e0e992",
>   "externalIds": {
>     "MAG": "2951055169",
>     "ACL": "N19-1423",
>     "DBLP": "journals/corr/abs-1810-04805",
>     "ArXiv": "1810.04805",
>     "DOI": "10.18653/v1/N19-1423",
>     "CorpusId": 52967399
>   },
>   "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
>   "year": 2019,
>   "openAccessPdf": {
>     "url": "",
>     "status": null,
>     "license": null
>   }
> }
> ```
>
> 结论：`openAccessPdf.url` 为空，但 `externalIds.ArXiv` = `1810.04805`，可通过 ArXiv 链接 `https://arxiv.org/pdf/1810.04805` 获取。这说明 **externalIds 中的 ArXiv ID 是重要的备用来源**。

> [!note]- DOI 3：心理学论文（无 OA 版本）
> **DOI**: `10.1037/0022-3514.73.2.345`
> **论文**: Sensory-processing sensitivity and its relation to introversion and emotionality (1997)
>
> 返回结果：
> ```json
> {
>   "paperId": "9aaacdf11f36d2e6c0fb7f4cf705bcdfd70f1794",
>   "externalIds": {
>     "MAG": "2087775892",
>     "DOI": "10.1037/0022-3514.73.2.345",
>     "CorpusId": 17856492,
>     "PubMed": "9248053"
>   },
>   "title": "Sensory-processing sensitivity and its relation to introversion and emotionality.",
>   "year": 1997,
>   "openAccessPdf": {
>     "url": "",
>     "status": "CLOSED",
>     "license": null
>   }
> }
> ```
>
> 结论：status=CLOSED，无 OA 版本，需走机构访问或 Unpaywall 等其他渠道。

---

## 4. 当前集成方式

> [!abstract]- 现在的正式位置
>
> Semantic Scholar 不再是插进 institutional 分支的临时补丁，而是放进独立的 `oa_stage.py`。
>
> 当前正式流程：
>
> ```
> Stage 1: Legal OA
>   1. Unpaywall
>   2. Semantic Scholar
>   3. arXiv
>   4. PubMed Central
>   5. bioRxiv / medRxiv
>
> Stage 2: Publisher / DOI HTTP
> Stage 3: Institutional browser
> Stage 4: Sci-Hub fallback
> ```
>
> ### Semantic Scholar 在 OA 阶段中的职责
>
> ```python
> def try_semantic_scholar(doi: str) -> tuple[dict | None, dict]:
>     result = query_semantic_scholar(doi)
>
>     oa_pdf = result.get("openAccessPdf", {})
>     if oa_pdf.get("url"):
>         return {"url": oa_pdf["url"], "source": "semantic_scholar"}, result
>
>     # 不直接命中时，把 externalIds 留给 arXiv / PMC 子步骤继续用
>     return None, result
> ```
>
> ### 注意事项
>
> - 当前实现默认使用 Semantic Scholar REST API
> - `openAccessPdf.url` 为空字符串 `""` 时等同于无结果
> - `externalIds.ArXiv` 和 `externalIds.PubMedCentral` 都是高价值备用字段
> - 即使 Semantic Scholar 本身没有直接 PDF，也可能为 arXiv / PMC 子步骤提供命中线索

---

## 5. 优先级说明

> [!warning]- 为什么 Semantic Scholar 现在放在这个位置
>
> **为什么放在 Unpaywall 之后**：
> - Unpaywall 数据更全面，覆盖更多 OA 类型（包括机构库）
> - Semantic Scholar 的 `openAccessPdf` 主要覆盖出版商直链和部分预印本
>
> **为什么仍然放在 Sci-Hub 之前**：
> - Semantic Scholar 提供的是合法 OA 线索
> - 它还能为 arXiv / PMC 提供外部 ID，提升合法来源命中率
>
> **ArXiv / PMC 提取的价值**：
> - BERT 案例说明：即使 `openAccessPdf` 为空，`externalIds.ArXiv` 也可能存在
> - 当前实现已经把 `externalIds` 作为下游 OA 子步骤的输入，而不是把 Semantic Scholar 当成孤立查询

---

## 6. REST API 替代方案（MCP 不可用时）

> [!note]- 直接调用 Semantic Scholar REST API
>
> 若 MCP 工具不可用，可直接调用官方 REST API：
>
> ```python
> import requests
>
> def query_semantic_scholar_rest(doi: str) -> dict:
>     url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
>     params = {"fields": "openAccessPdf,externalIds,title,year"}
>     headers = {}  # 无需 API Key（有速率限制：100 req/5min）
>     # 若有 API Key：headers = {"x-api-key": "YOUR_KEY"}
>
>     resp = requests.get(url, params=params, headers=headers, timeout=10)
>     resp.raise_for_status()
>     return resp.json()
> ```
>
> 速率限制：
> - 无 Key：100 请求 / 5 分钟
> - 有 Key：可申请更高配额（https://www.semanticscholar.org/product/api）
