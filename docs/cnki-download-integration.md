# CNKI Download Integration

This note folds the old `cnki-download` skill logic into `paper-downloader`.

Use this flow when:
- the user gives a CNKI paper detail URL
- the paper is easier to fetch from the CNKI detail page than from DOI-based resolvers
- the actual download must be triggered by the live browser session

## Contract

`paper-downloader` remains PDF-first.

That means:
- prefer CNKI PDF when available
- allow CAJ only as a manual fallback
- only archive into the canonical library once you have a PDF that can be renamed and tagged cleanly

## Preconditions

- user is logged in to CNKI and has download permission
- user is on the paper detail page, or a detail URL is available
- if CNKI shows a slider captcha, the user must solve it first

## Recommended flow

1. Open the CNKI detail page directly.
2. Run a single page-side check that:
   - waits for the title area
   - detects captcha
   - detects not-logged-in state
   - prefers `#pdfDown`
   - falls back to `#cajDown`
3. Let the browser complete the download.
4. Immediately archive the fresh PDF with `scan_download_folder.py` or `rename_papers.py`.

## One-shot CNKI page script

Replace `FORMAT` with `"pdf"` or `"caj"`. Leave it as `"pdf"` in normal use.

```javascript
async () => {
  await new Promise((resolve, reject) => {
    let attempts = 0;
    const check = () => {
      if (document.querySelector('.brief h1')) {
        resolve();
      } else if (++attempts > 30) {
        reject(new Error('timeout'));
      } else {
        setTimeout(check, 500);
      }
    };
    check();
  });

  const captcha = document.querySelector('#tcaptcha_transform_dy');
  if (captcha && captcha.getBoundingClientRect().top >= 0) {
    return {
      error: 'captcha',
      message: 'CNKI is showing a slider captcha. Solve it in the browser first.',
    };
  }

  const format = "FORMAT";
  const pdfLink = document.querySelector('#pdfDown') || document.querySelector('.btn-dlpdf a');
  const cajLink = document.querySelector('#cajDown') || document.querySelector('.btn-dlcaj a');
  const notLogged =
    document.querySelector('.downloadlink.icon-notlogged') ||
    document.querySelector('[class*="notlogged"]');

  if (notLogged) {
    return {
      error: 'not_logged_in',
      message: 'Download requires a logged-in CNKI account.',
    };
  }

  const title = document
    .querySelector('.brief h1')
    ?.innerText?.trim()
    ?.replace(/\s*网络首发\s*$/, '') || '';

  if (format === 'pdf' && pdfLink) {
    pdfLink.click();
    return { status: 'downloading', format: 'PDF', title };
  }
  if (format === 'caj' && cajLink) {
    cajLink.click();
    return { status: 'downloading', format: 'CAJ', title };
  }
  if (pdfLink) {
    pdfLink.click();
    return { status: 'downloading', format: 'PDF', title };
  }
  if (cajLink) {
    cajLink.click();
    return { status: 'downloading', format: 'CAJ', title };
  }

  return {
    error: 'no_download',
    message: 'No CNKI download link found on the page.',
    hasPDF: !!pdfLink,
    hasCAJ: !!cajLink,
  };
}
```

## Verified selectors

- title: `.brief h1`
- PDF download: `#pdfDown` or `.btn-dlpdf a`
- CAJ download: `#cajDown` or `.btn-dlcaj a`
- not logged in: `.downloadlink.icon-notlogged` or `[class*="notlogged"]`
- captcha: `#tcaptcha_transform_dy`

## Result handling

- `status: downloading`
  - wait for the browser to finish the download
  - then archive the file
- `error: not_logged_in`
  - log in first, then rerun
- `error: captcha`
  - solve the slider captcha first
- `error: no_download`
  - inspect the page manually; CNKI may be exposing only a restricted format or a different DOM

## Archive the downloaded file

If the PDF lands in the default download folder:

```bash
python scripts/scan_download_folder.py \
  --hours 6 \
  --keywords 文献,知网 \
  --finder-tags 文献,知网
```

If you want to archive one specific file:

```bash
python scripts/rename_papers.py ~/Downloads/example.pdf \
  --auto-metadata \
  --keywords 文献,知网 \
  --finder-tags 文献,知网
```

## CAJ note

This skill does not yet treat CAJ as a canonical archive format.

If CNKI only provides CAJ:
- download it manually if the user explicitly wants it
- convert it to PDF before sending it into the normal archive workflow
