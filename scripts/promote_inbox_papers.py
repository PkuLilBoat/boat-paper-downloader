from __future__ import annotations
from pathlib import Path
import shutil, json, re
from datetime import datetime

try:
    from markdown_blockify import blockify_markdown_file
except ImportError:  # pragma: no cover
    from .markdown_blockify import blockify_markdown_file  # type: ignore

ROOT=(Path.home() / "Documents" / "PAPER" / "Papers")
INBOX=ROOT/'inbox'
SCAN_BASES=[INBOX/'03_collections_to_split', INBOX/'02_reference_fragments_review']
IMG_EXTS={'.jpg','.jpeg','.png','.webp','.gif','.bmp','.tif','.tiff'}
SKIP_PARTS={'download_reports','semantic_scholar_reports','logs','source','OSF_data','osf_downloads','md'}

moves=[]; promoted=[]; skipped=[]

def safe_name(s:str)->str:
    s=re.sub(r'[<>:"/\\|?*]', '', s.strip())
    s=re.sub(r'\s+', '_', s).strip('._')
    return s[:180] or 'paper'

def resolve(path:Path)->Path:
    if not path.exists(): return path
    for i in range(2,10000):
        cand=path.with_name(f'{path.name}_{i}') if path.is_dir() or path.suffix=='' else path.with_name(f'{path.stem}_{i}{path.suffix}')
        if not cand.exists(): return cand
    raise FileExistsError(path)

def is_candidate(d:Path)->tuple[bool,str]:
    if any(part in SKIP_PARTS for part in d.parts):
        # allow if it has actual pdf+md in a leaf PDF folder under PDFs/downloads, but skip reports/logs/source docs.
        if not any(part.lower() in {'pdfs','downloads','downloaded_pdfs','downloaded_pdfs_corrected','test_output','papers'} for part in d.parts):
            return False,'skip_part'
    if not d.is_dir(): return False,'not_dir'
    files=[p for p in d.iterdir() if p.is_file()]
    dirs=[p for p in d.iterdir() if p.is_dir()]
    pdfs=[p for p in files if p.suffix.lower()=='.pdf']
    mds=[p for p in files if p.suffix.lower()=='.md']
    content=d/'content.md'
    # Single-paper folder: one PDF and at least one MD at same level.
    if len(pdfs)==1 and len(mds)>=1:
        return True,'pdf_md'
    # Markdown-only paper-like conversion folder: content.md plus extracted page images.
    loose_imgs=[p for p in files if p.suffix.lower() in IMG_EXTS]
    if content.exists() and (loose_imgs or (d/'images').is_dir()):
        return True,'content_md_no_pdf'
    return False,'no_marker'

def normalize_inside(folder:Path):
    stem=folder.name
    # Rename content.md or sole differently named md to folder stem when safe.
    target_md=folder/f'{stem}.md'
    if not target_md.exists():
        if (folder/'content.md').exists():
            shutil.move(str(folder/'content.md'), str(target_md)); moves.append({'from':str(folder/'content.md'),'to':str(target_md),'reason':'rename_content_md'})
        else:
            mds=[p for p in folder.iterdir() if p.is_file() and p.suffix.lower()=='.md']
            if len(mds)==1 and mds[0].name != target_md.name:
                shutil.move(str(mds[0]), str(target_md)); moves.append({'from':str(mds[0]),'to':str(target_md),'reason':'rename_single_md_to_folder_stem'})
    # Rename sole pdf to folder stem when it is DOI-ish or mismatched? Keep original if already meaningful; only if content stem folder has content.
    pdfs=[p for p in folder.iterdir() if p.is_file() and p.suffix.lower()=='.pdf']
    if len(pdfs)==1:
        target_pdf=folder/f'{stem}.pdf'
        if not target_pdf.exists() and pdfs[0].stem in {'content','paper','download'}:
            shutil.move(str(pdfs[0]), str(target_pdf)); moves.append({'from':str(pdfs[0]),'to':str(target_pdf),'reason':'rename_generic_pdf_to_folder_stem'})
    # Normalize image folders and loose images.
    for imgdir in list(folder.iterdir()):
        if imgdir.is_dir() and imgdir.name.endswith('_images') and not (folder/'images').exists():
            shutil.move(str(imgdir), str(folder/'images')); moves.append({'from':str(imgdir),'to':str(folder/'images'),'reason':'normalize_images_folder'})
    loose_imgs=[p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXTS]
    if loose_imgs:
        (folder/'images').mkdir(exist_ok=True)
        for img in loose_imgs:
            dst=resolve(folder/'images'/img.name)
            shutil.move(str(img), str(dst)); moves.append({'from':str(img),'to':str(dst),'reason':'move_loose_image_to_images'})
    # Update common image path prefixes after image folder rename.
    for md in folder.glob('*.md'):
        txt=md.read_text(encoding='utf-8', errors='ignore')
        new=txt
        new=new.replace('content_images/','images/').replace('./content_images/','images/')
        new=re.sub(r'([\]\(\s])([^\]\(\s/]+_images/)', r'\1images/', new)
        if new!=txt:
            md.write_text(new, encoding='utf-8')
        if blockify_markdown_file(md):
            moves.append({'from':str(md),'to':str(md),'reason':'blockify_markdown'})

def collect_candidates():
    c=[]
    for base in SCAN_BASES:
        if not base.exists(): continue
        for d in sorted([p for p in base.rglob('*') if p.is_dir()], key=lambda p: len(p.parts), reverse=True):
            ok, reason=is_candidate(d)
            if ok:
                c.append((d,reason))
    # remove candidates nested inside another candidate (promote deepest only already sorted; if parent candidate contains candidate child, skip parent later)
    selected=[]
    selected_paths=[]
    for d,reason in c:
        if any(str(d).startswith(str(s)+'/') for s in selected_paths):
            skipped.append({'path':str(d),'reason':'inside_selected_candidate'})
            continue
        selected.append((d,reason)); selected_paths.append(d)
    return list(reversed(selected))

for src,reason in collect_candidates():
    if not src.exists(): continue
    name=safe_name(src.name)
    dst=resolve(ROOT/name)
    shutil.move(str(src), str(dst))
    moves.append({'from':str(src),'to':str(dst),'reason':f'promote_{reason}'})
    normalize_inside(dst)
    promoted.append({'from':str(src),'to':str(dst),'reason':reason})

# remove empty directories inside inbox bottom-up, but keep standard inbox category dirs.
keep={INBOX, *(p for p in INBOX.iterdir() if p.is_dir())}
for d in sorted([p for p in INBOX.rglob('*') if p.is_dir()], key=lambda p: len(p.parts), reverse=True):
    if d in keep: continue
    try:
        if not any(d.iterdir()):
            d.rmdir(); moves.append({'from':str(d),'to':'','reason':'remove_empty_inbox_dir'})
    except FileNotFoundError:
        pass

report=INBOX/f'promote_inbox_papers_{datetime.now().strftime("%Y%m%d_%H%M%S")}.md'
lines=['# Promote Inbox Papers Report','',f'- Promoted: {len(promoted)}',f'- Moves/normalizations: {len(moves)}','']
lines.append('## Promoted Papers')
for p in promoted:
    lines.append(f"- {p['reason']}: `{p['from']}` -> `{p['to']}`")
lines.append('')
lines.append('## Move Log')
for m in moves:
    lines.append(f"- {m['reason']}: `{m['from']}`" + (f" -> `{m['to']}`" if m['to'] else ''))
report.write_text('\n'.join(lines)+'\n', encoding='utf-8')
print(json.dumps({'report':str(report),'promoted':len(promoted),'moves':len(moves)}, ensure_ascii=False, indent=2))
