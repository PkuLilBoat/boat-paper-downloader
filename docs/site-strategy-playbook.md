# Site Strategy Playbook

This playbook is the stable, human-readable policy layer for publisher handling.

It exists to prevent a bad pattern we explicitly want to avoid: fixing site A by mutating shared downloader behavior, then breaking site B. The rule is:

- keep the core DOI-first runtime stable
- push publisher quirks into site-specific handlers or documented fallbacks
- record every validated fixture in `site-validation-matrix.md`

## How to Use This Playbook

When adding or debugging a site:

1. start from a representative DOI, not from a login page
2. identify the final host reached after DOI navigation
3. map that host to a dedicated handler only if the extraction path is actually repeatable
4. keep mirrors and one-off access routes on generic fallback unless a dedicated handler is clearly safer
5. update both this playbook and `site-validation-matrix.md`

`site-validation-matrix.md` is the source of truth for observed outcomes.
This file is the source of truth for strategy and engineering policy.

## Stable Runtime Policy

Use this baseline unless a site-specific rule says otherwise:

- browser: `msedge`
- profile: real local Edge profile via `user_data_dir` plus `profile_directory`
- visibility: `headless: true`
- navigation: DOI-first with `goto_wait_until: "domcontentloaded"`
- login handling: activate only when DOI navigation lands on an auth page
- exploration budget: bounded, non-visual, and site-relevant

## Non-Visual First Rule

Normal runs should stay non-visual.

Escalate to `headless: false` only when:

- OTP must be entered by a human
- CAPTCHA blocks progress
- a site needs one-time visual diagnosis

Once that interactive step finishes, return to `headless: true`.

## Controlled Autonomous Exploration

Autonomous probing is allowed, but it must stay disciplined:

- begin from the DOI landing page
- inspect DOM, network responses, form actions, and embedded PDF targets
- try only a few site-relevant routes before giving up
- avoid generic wandering, unrelated page hops, or visible browsing by default
- when a new pattern succeeds twice, promote it into a documented handler or explicit fallback rule

## Site Coverage Index

The table below is the quickest way to see what we support, how it is handled, and what should be tested.

| Site family | Representative DOI | Host / route shape | Current handler | Validation status | Strategy category |
| --- | --- | --- | --- | --- | --- |
| Annual Reviews | `10.1146/annurev-clinpsy-061324-074037` | `annualreviews.org/content/...` | `annualreviews` | Verified end-to-end success | Dedicated POST-form handler |
| Elsevier / ScienceDirect | `10.1016/j.paid.2022.111713` | `sciencedirect.com/science/article/...` | `elsevier` | Route verified, anti-bot challenge still active | Dedicated download-button handler |
| Springer | `10.1007/s10964-014-0177-9` | `link.springer.com/article/...` | `springer` | Verified success | Deterministic direct PDF path |
| Wiley | `10.1111/pere.12175` | `onlinelibrary.wiley.com/doi/...` | `wiley` | Verified failure mode | Direct route first, then rendered-page scan |
| APA / PsycNet | `10.1037/0022-3514.79.2.224` | `doi.apa.org/doi/...` or `psycnet.apa.org/...` | `apa_psycnet` | Verified success | Institutional access flow plus fulltext PDF fetch |
| Taylor & Francis | `10.1080/15374410701653120` | `tandfonline.com/doi/abs/...` | `tandf` | Route verified, Cloudflare challenge still active | Deterministic `/doi/pdf/` fallback |
| SAGE native | placeholder fixture pending | `journals.sagepub.com/doi/...` | `sage` | Native success fixture pending | Deterministic `/doi/pdf/` fallback |
| SAGE CNPeReading mirror | `10.1177/0146167217736049` | `sage.cnpereading.com/paragraph/article/...` | generic fallback | Verified success via generic path | Mirror-specific generic extraction |
| Cambridge | `10.1017/S095457941800158X` | `cambridge.org/core/journals/...` | `cambridge` | Verified success | Rendered-page PDF link scan |
| Oxford Academic | `10.1093/sleepadvances/zpae049` | `academic.oup.com/...` | `oxford` | Verified success | Rendered-page PDF link scan |

## Site-by-Site Strategy

### Annual Reviews

Observed pattern:

- DOI resolves to article page
- institutional access is often already active in Edge cookies
- visible PDF control is backed by a POST form, not a plain anchor

Strategy:

1. open DOI in Edge-backed Playwright context
2. confirm access state from page text such as `Access provided by:`
3. locate `form.ft-download-content__form--pdf`
4. resolve the form `action`
5. POST to that action with browser cookies and PDF accept headers
6. save only if response bytes are real PDF

Why dedicated:

- this is a repeatable, site-specific pattern
- generic link scraping misses the POST action

### Elsevier / ScienceDirect

Observed pattern:

- DOI lands on a ScienceDirect article page
- PDF controls can be click-driven rather than directly fetchable

Strategy:

1. open DOI with `domcontentloaded`
2. probe known PDF controls such as `.pdf-download-btn-link`, `a[href*="pdfft"]`, and `data-aa-name*="pdf"`
3. prefer browser download event or authenticated request
4. only if DOI flow redirects to auth, run institutional login and retry

Why dedicated:

- the site often hides the PDF behind UI actions
- generic anchor scanning is less reliable here

Current caution:

- current blocker is anti-bot challenge rather than site detection
- avoid broad core changes for this; keep experiments scoped to Elsevier-specific handling and browser hardening
- the downloader should classify this state explicitly and return a minimal human-step requirement instead of waiting indefinitely

### Springer

Observed pattern:

- DOI often maps to a deterministic `/content/pdf/...pdf` route

Strategy:

1. construct the encoded PDF URL directly
2. fetch with authenticated browser session
3. fall back to page scan only if the deterministic route stops working in the future

Why dedicated:

- stable and low-friction
- no need to complicate the shared runtime

### Wiley

Observed pattern:

- DOI lands correctly
- direct `/doi/pdf` or `/doi/pdfdirect` routes are often predictable
- Cloudflare or site challenges can block simple authenticated fetches

Strategy:

1. try deterministic PDF URL patterns first
2. if those fail, let the page render and scan PDF controls
3. if a viewer page opens, inspect embedded `embed` or `iframe` PDF sources
4. keep this headless by default; escalate only for one-time challenge diagnosis

Why dedicated:

- Wiley is both structured and brittle
- it benefits from a bounded multi-step strategy

Current caution:

- current failure mode is known and expected
- do not weaken global browser defaults just to punch through Cloudflare

### APA / PsycNet

Observed pattern:

- APA traffic may terminate at `doi.apa.org`, `psycnet.apa.org`, or an APA fulltext route
- page-exposed fulltext links are the best first signal

Strategy:

1. treat both `doi.apa.org` and `psycnet.apa.org` as APA/PsycNet traffic
2. if the DOI page shows `Get Access`, click it and then click `CHECK ACCESS`
3. when institutional access is confirmed, capture the revealed `DOWNLOAD PDF` fulltext URL and fetch it with the authenticated browser session
4. otherwise scan for `/fulltext` links already present on the page
5. if still not found, try deterministic `https://psycnet.apa.org/fulltext/{safe_doi}.pdf`

Why dedicated:

- APA routing is recognizably distinct
- host-based detection matters because `doi.apa.org` should not fall through to generic accidentally

Current caution:

- this is now a verified success path for at least one PKU-accessible APA fixture
- future APA failures should first be checked for entitlement differences, not host matching

### Taylor & Francis

Observed pattern:

- DOI lands on `tandfonline.com/doi/abs/...`
- a predictable `/doi/pdf/{doi}` path often exists, but not every route is immediately downloadable

Strategy:

1. derive `/doi/pdf/{doi}` from the landed host
2. fetch using authenticated browser session
3. if this remains flaky, add page-level PDF control detection rather than altering core navigation

Why dedicated:

- route structure is stable enough to justify a specific rule

Current caution:

- current blocker is Cloudflare verification rather than route recognition
- improve only inside the T&F handler, not by widening generic behavior

### SAGE Native vs Mirror Behavior

Observed pattern:

- native SAGE uses `journals.sagepub.com`
- some institutional routes land on `sage.cnpereading.com` mirror pages instead

Strategy:

1. native SAGE stays on the `sage` handler with deterministic `/doi/pdf/{doi}`
2. CNPeReading mirror stays on generic fallback unless a repeatable mirror-only extraction rule proves safer

Why this split matters:

- forcing mirror traffic into the native SAGE handler is risky
- the mirror has already succeeded through generic extraction

### Cambridge

Observed pattern:

- DOI lands on Cambridge article pages with usable PDF links exposed in rendered HTML

Strategy:

1. scan rendered page for `/pdf` or `.pdf` links
2. fetch with authenticated browser session

Why dedicated:

- link discovery is simple and repeatable

### Oxford Academic

Observed pattern:

- Oxford pages often expose `/pdf/` links once the article page renders

Strategy:

1. scan rendered page for `/pdf/` and `.pdf` links
2. fetch with authenticated browser session

Current caution:

- the Oxford handler is now backed by a verified success fixture

## Authentication Policy

### PKU Unified Authentication

Observed pattern:

- do not pre-open the login page unless needed
- DOI-first remains the default
- when the DOI flow lands on auth, the PKU selectors are stable

Strategy:

1. start from DOI
2. if auth page appears, use only the PKU selector set
3. keep selectors strict: `#user_name`, `#password`, `#logon_button`
4. if OTP, SMS, or CAPTCHA appears, pause for human input
5. resume normal download flow after auth

Why this matters:

- it avoids the earlier anti-pattern of opening login first
- it keeps automation predictable and low-noise

## Maintenance Rule

When a new site or route succeeds, record four things:

- the DOI used as fixture
- the final host and access-state signal
- the extraction method that actually produced PDF bytes
- whether it belongs in a dedicated handler, generic fallback, or mirror-specific rule

If you cannot answer all four, the strategy is not ready to promote.
