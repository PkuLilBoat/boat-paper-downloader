# Site Validation Matrix

This file records real DOI-first tests used to validate the site strategy playbook.

## Test Baseline

- browser: `msedge`
- profile: real local Edge `Default`
- mode: `headless: true`
- navigation: `domcontentloaded`
- method: DOI-first, then matched site handler or generic fallback
- date: 2026-04-12

## Results

| Site | Representative DOI | Final URL / Route | Status | Notes |
| --- | --- | --- | --- | --- |
| Annual Reviews | `10.1146/annurev-clinpsy-061324-074037` | `annualreviews.org/...` | Verified in end-to-end downloader | Main downloader succeeded via `playwright_annualreviews`; article page exposed `Access provided by: Peking University`; PDF came from POST form action. |
| Elsevier / ScienceDirect | `10.1016/j.paid.2022.111713` | `sciencedirect.com/science/article/...` | Route verified, challenge-aware human-step state now classified | DOI resolves cleanly to Elsevier. The downloader now classifies this fixture as `cloudflare_challenge` plus `anti_bot_blocked`, so it can fail fast and hand off only the minimum required human step instead of hanging. |
| Springer | `10.1007/s10964-014-0177-9` | `link.springer.com/article/...` | Verified success | Dedicated `springer` handler succeeded and wrote PDF. |
| Wiley | `10.1111/pere.12175` | `onlinelibrary.wiley.com/doi/...` | Verified failure mode | DOI resolves correctly, but current handler is blocked by Cloudflare / 403 challenge on direct PDF routes. Needs site-specific hardening. |
| APA / doi.apa.org | `10.1037/0022-3514.79.2.224` | `psycnet.apa.org/doiLanding?...` -> `recordAccess/institutional/...` -> `/fulltext/...pdf` | Verified success | `doi.apa.org` is now mapped to the APA/PsycNet handler. The downloader now follows the `Get Access` -> `CHECK ACCESS` flow, confirms PKU institutional access, and fetches the authenticated fulltext PDF URL. |
| Taylor & Francis | `10.1080/15374410701653120` | `tandfonline.com/doi/abs/...` | Route verified, blocked by Cloudflare challenge in current headless run | DOI resolves correctly; current blocker is the challenge page rather than route recognition. |
| SAGE | `10.1177/0146167217736049` | `sage.cnpereading.com/paragraph/article/...` in authenticated browser runs; `journals.sagepub.com/doi/...` in unauthenticated route probes | Verified success via generic path; native route also observed | Authenticated browser validation succeeded on CNPeReading mirror via generic extraction. A plain DOI route probe can also land on native `sagepub.com`, so native and mirror behavior should stay separated in strategy docs. |
| Cambridge | `10.1017/S095457941800158X` | `cambridge.org/core/journals/...` | Verified success | Dedicated `cambridge` handler succeeded and wrote PDF. |
| Oxford Academic | `10.1093/sleepadvances/zpae049` | `academic.oup.com/sleepadvances/article/...` | Verified success | Dedicated `oxford` handler succeeded and wrote PDF in project-level regression. |

## Notes

- The matrix is intentionally strict: it separates route detection from actual PDF success.
- A site is only marked `Verified success` if a PDF was actually written in the test run or by the validated end-to-end downloader path.
- Some DOI examples are useful for route validation even if they are not yet good success fixtures.

## Next Recommended Fixtures

- Wiley: keep `10.1111/pere.12175` as blocked fixture and add one more Wiley DOI after Cloudflare-aware handling exists.
- APA: `10.1037/0022-3514.79.2.224` is now a verified success fixture.
- Elsevier: keep `10.1016/j.paid.2022.111713` as the active anti-bot fixture until a stable success path is found.
- Taylor & Francis: keep `10.1080/15374410701653120` as the active Cloudflare fixture until a stable success path is found.
- Oxford Academic: `10.1093/sleepadvances/zpae049` is now a verified success fixture.
