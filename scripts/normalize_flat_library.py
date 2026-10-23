from __future__ import annotations
from pathlib import Path
import shutil, json, re
from datetime import datetime

ROOT=(Path.home() / "Documents" / "PAPER" / "Papers")
INBOX=ROOT/'inbox'
SUB={
 'intermediate': INBOX/'00_intermediate_outputs',
 'downloads': INBOX/'01_download_artifacts',
 'reference': INBOX/'02_reference_fragments_review',
 'collections': INBOX/'03_collections_to_split',
 'empty': INBOX/'04_empty_legacy_dirs',
 'attachments': INBOX/'05_loose_attachments_review',
}
for p in SUB.values(): p.mkdir(parents=True, exist_ok=True)

moved=[]; created=[]; reports={'reference_fragments':[], 'collections_to_split':[], 'missing_markdown_stubs':[], 'normalized_images':[], 'kept_paper_folders':[]}

def resolve(target: Path)->Path:
    if not target.exists(): return target
    if target.is_dir():
        for i in range(2,1000):
            c=target.with_name(f'{target.name}_{i}')
            if not c.exists(): return c
    else:
        for i in range(2,1000):
            c=target.with_name(f'{target.stem}_{i}{target.suffix}')
            if not c.exists(): return c
    raise FileExistsError(target)

def move(src: Path, dst_dir: Path, reason: str):
    if not src.exists(): return None
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst=resolve(dst_dir/src.name)
    shutil.move(str(src), str(dst))
    moved.append({'from':str(src),'to':str(dst),'reason':reason})
    return dst

def sanitize(name: str)->str:
    s=re.sub(r'[<>:"/\\|?*]', '', name.strip())
    s=re.sub(r'\s+', '_', s).strip('._')
    return s or 'paper'

# 1. obsolete empty category dirs created by the previous rule.
for name in ['00_uncategorized','01_mbti_psychological_type','02_trauma','03_romantic_competence','04_psychometrics_methodology']:
    p=ROOT/name
    if p.is_dir() and not any(p.iterdir()):
        move(p, SUB['empty']/'obsolete_category_dirs', 'obsolete_empty_category_dir_property_only_now')

# 2. known root loose/download/intermediate files.
for pattern, dst, reason in [
    ('*cloudflare_block.html', SUB['downloads']/'cloudflare_blocks', 'cloudflare_download_block'),
    ('MBTI_download_progress.md', SUB['downloads']/'progress_logs', 'download_progress_log'),
    ('*plain_extract_backup.md', SUB['intermediate']/'backup_extracts', 'plain_extract_backup'),
]:
    for p in ROOT.glob(pattern):
        if p.is_file(): move(p,dst,reason)

# 3. loose attachments into matching paper folder when obvious.
for p in list(ROOT.glob('*_attachment*.csv')):
    stem=p.name.split('_attachment')[0]
    paper_dir=ROOT/stem
    if paper_dir.is_dir():
        move(p, paper_dir/'attachments', 'supplement_attachment_for_matching_paper')
    else:
        move(p, SUB['attachments'], 'loose_attachment_no_matching_paper_folder')

# 4. loose paper bundle: pdf/md/images with same stem -> one paper folder.
for pdf in list(ROOT.glob('*.pdf')):
    stem=pdf.stem
    folder=ROOT/sanitize(stem)
    folder.mkdir(exist_ok=True)
    created.append(str(folder))
    move(pdf, folder, 'loose_pdf_to_paper_folder')
    md=ROOT/f'{stem}.md'
    if md.exists(): move(md, folder, 'matching_loose_md_to_paper_folder')
    img=ROOT/f'{stem}_images'
    if img.exists() and img.is_dir():
        dst=move(img, folder, 'matching_loose_images_to_paper_folder')
        # rename to images for clean Obsidian layout
        if dst and dst.name != 'images':
            final=resolve(folder/'images')
            shutil.move(str(dst), str(final))
            moved.append({'from':str(dst),'to':str(final),'reason':'normalize_images_folder_name'})

# 5. loose duplicate Faber MinerU outputs after paper folder already exists.
faber=ROOT/'2019_Faber_IRRC_Interpersona'
if faber.is_dir():
    for p in [ROOT/'2019_Faber_IRRC_Interpersona_mineru.md', ROOT/'2019_Faber_IRRC_Interpersona_mineru_images']:
        if p.exists(): move(p, SUB['intermediate']/'duplicate_mineru_outputs'/'2019_Faber_IRRC_Interpersona', 'duplicate_loose_mineru_output')

# 6. root-level abstract/reference markdown files.
for name in ['心理测量综述文献库_20260426.md','文献摘要汇编.md','自由能心理学论文.md']:
    p=ROOT/name
    if p.exists() and p.is_file():
        dst=move(p, SUB['reference']/'root_markdown_fragments', 'abstract_reference_fragment_for_review')
        reports['reference_fragments'].append({'name':name,'new_path':str(dst) if dst else ''})

# 7. empty/intermediate dirs.
for name in ['_downloads_20260415','_mineru_output','_mineru_output_debug','PROJECT PAPER','~']:
    p=ROOT/name
    if p.exists() and p.is_dir():
        dstbase=SUB['empty'] if not any(p.iterdir()) else SUB['intermediate']/'legacy_misc_dirs'
        move(p, dstbase, 'legacy_empty_or_intermediate_dir')

# 8. derivative single-paper workspaces, keep for review but remove from root.
for name in ['2023_Wang_scientific_discovery_AI_Nature_md','2025_Wulff_semantic_embeddings_psychological_measurement_NatHumBehav_mineru']:
    p=ROOT/name
    if p.exists() and p.is_dir():
        dst=move(p, SUB['reference']/'derived_paper_workspaces', 'derived_workspace_or_enriched_notes_for_review')
        reports['reference_fragments'].append({'name':name,'new_path':str(dst) if dst else ''})

# 9. multi-paper/source collection folders: stage in inbox for later split or special folder decision.
collection_names=['AI-ToM-Research','AI_Research_Papers','class测量','mbti_jung_literature','paper','paper-downloader-artifacts','psychometrics_review_downloads','Romantic_Competence_CN','romantic_competence_psychology_20260401_201023','romantic_relationship_20260402','thin_slice_research','uk-museums-research','博弈论','大五人格测量方法综述','投射测验领域调研','游戏化心理测评_20260409_143052','高级测量-阅读充电桩2025']
for name in collection_names:
    p=ROOT/name
    if p.exists() and p.is_dir():
        pdfs=len(list(p.rglob('*.pdf'))); mds=len(list(p.rglob('*.md'))); files=sum(1 for q in p.rglob('*') if q.is_file())
        dst=move(p, SUB['collections'], 'multi_paper_or_reference_collection_needs_review_or_split')
        reports['collections_to_split'].append({'name':name,'pdf_count':pdfs,'md_count':mds,'file_count':files,'new_path':str(dst) if dst else ''})

# 10. Normalize remaining top-level paper folders: images folder and markdown stub.
for d in sorted([p for p in ROOT.iterdir() if p.is_dir() and p.name != 'inbox'], key=lambda x:x.name.lower()):
    pdfs=list(d.glob('*.pdf'))
    mds=list(d.glob('*.md'))
    if len(pdfs)==1:
        stem=d.name
        # rename one image dir if it is stem_images
        img=d/f'{stem}_images'
        if img.is_dir() and not (d/'images').exists():
            shutil.move(str(img), str(d/'images'))
            moved.append({'from':str(img),'to':str(d/'images'),'reason':'normalize_images_folder_name'})
            for md in d.glob('*.md'):
                txt=md.read_text(encoding='utf-8', errors='ignore')
                new=txt.replace(f'{stem}_images/', 'images/').replace(f'./{stem}_images/', 'images/')
                if new!=txt:
                    md.write_text(new, encoding='utf-8')
            reports['normalized_images'].append(str(d))
        if not mds:
            md=d/f'{stem}.md'
            pdf_name=pdfs[0].name
            md.write_text(
                '---\n'
                f'paper_key: "{stem}"\n'
                f'p0_local_pdf: "Papers/{stem}/{pdf_name}"\n'
                f'p0_local_md: "Papers/{stem}/{stem}.md"\n'
                'p0_import_source: local\n'
                '---\n\n'
                f'# {stem}\n\n[PDF]({pdf_name})\n',
                encoding='utf-8',
            )
            created.append(str(md))
            reports['missing_markdown_stubs'].append(str(md))
        reports['kept_paper_folders'].append(str(d))

# Write report.
report=INBOX/f'normalization_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.md'
lines=['# Paper Library Normalization Report','',f'- Root: `{ROOT}`',f'- Moves: {len(moved)}',f'- Created: {len(created)}','']
lines += ['## Reference / Abstract Fragments For Review','']
for item in reports['reference_fragments']:
    lines.append(f"- `{item['name']}` -> `{item['new_path']}`")
lines += ['','## Collections To Split Or Place In A Separate Folder','']
for item in reports['collections_to_split']:
    lines.append(f"- `{item['name']}` ({item['pdf_count']} PDFs, {item['md_count']} MD, {item['file_count']} files) -> `{item['new_path']}`")
lines += ['','## Markdown Stubs Created','']
for p in reports['missing_markdown_stubs']:
    lines.append(f'- `{p}`')
lines += ['','## Image Folders Normalized','']
for p in reports['normalized_images'][:200]:
    lines.append(f'- `{p}`')
lines += ['','## Move Log','']
for m in moved:
    lines.append(f"- {m['reason']}: `{m['from']}` -> `{m['to']}`")
report.write_text('\n'.join(lines)+'\n', encoding='utf-8')
print(json.dumps({'report':str(report),'moves':len(moved),'created':len(created),'collections_to_review':len(reports['collections_to_split']),'reference_fragments':len(reports['reference_fragments']),'paper_folders':len(reports['kept_paper_folders']),'markdown_stubs':len(reports['missing_markdown_stubs'])}, ensure_ascii=False, indent=2))
