# Paper Category Property Taxonomy

This vocabulary is not an automatic intake classifier. Category or discipline properties may be written only when the user explicitly triggers PAPER `论文 Property Rule`.

`config/paper_categories.json` is now a **Markdown property vocabulary**, not a directory taxonomy.

Current layout rule: `~/Documents/PAPER/Papers` keeps every paper as a first-level folder. Categories are written into each paper Markdown file as frontmatter/properties so Obsidian can index and filter them.

## Allowed Property Values

| Value | 大类 | English | 使用范围 |
|---|---|---|---|
| `00_uncategorized` | 未分类 | Uncategorized | 临时入口；无法可靠判断是否属于四个核心主题时使用，后续复核。 |
| `01_mbti_psychological_type` | MBTI与心理类型 | MBTI and Psychological Type | MBTI、Jungian psychological types、人格类型、类型学测量、类型稳定性、类型与行为/职业/关系的关联研究。 |
| `02_trauma` | 创伤 | Trauma | 创伤、PTSD、复杂性创伤、童年逆境、依恋创伤、创伤后成长、创伤治疗与评估。 |
| `03_romantic_competence` | Romantic Competence | Romantic Competence | Romantic competence、亲密关系能力、恋爱能力、关系技能、关系教育、恋爱发展任务。 |
| `04_psychometrics_methodology` | 心理测量方法论 | Psychometrics and Methodology | 量表开发、信效度、测量不变性、CFA/EFA、IRT、测量模型、研究设计、元分析、统计方法。 |

## Directory Rule

Do not create these as folders:

```text
01_mbti_psychological_type/
02_trauma/
03_romantic_competence/
04_psychometrics_methodology/
```

Instead, keep the paper folder at root:

```text
~/Documents/PAPER/Papers/<paper_stem>/<paper_stem>.md
```

and write:

```yaml
---
category: "03_romantic_competence"
---
```

## Selection Rules

- First identify the paper's core question, not its journal or method.
- MBTI / psychological type related papers go to `01_mbti_psychological_type`, even if they use psychometric methods.
- Trauma as a core variable, sample background, or intervention object goes to `02_trauma`.
- Explicit romantic competence papers go to `03_romantic_competence`; general close-relationship papers that do not fit should stay `00_uncategorized` until reviewed.
- Papers whose main contribution is scale construction, validity, measurement invariance, CFA/EFA, IRT, model comparison, meta-analysis, or statistics go to `04_psychometrics_methodology`.
- Do not invent new property values during organization; update `config/paper_categories.json` first if a new category is truly needed.
