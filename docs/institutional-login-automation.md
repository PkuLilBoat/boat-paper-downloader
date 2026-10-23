# Institutional Login Automation

This mode is for campus SSO or library proxy flows that need account login before the publisher PDF becomes reachable.

## What It Does

- fills username and password automatically when a login page is detected
- clicks a likely submit button
- pauses for a live OTP / phone token step when needed
- reuses a dedicated persistent Playwright session directory so later downloads can often skip re-login

## Recommended Setup

1. Keep secrets in `local/institution_login.private.json`
2. Point your project downloader config at that private file with `institution_login_config`
3. Reuse your real Edge profile via `user_data_dir` + `profile_directory`
4. Keep the default runtime non-visual (`headless: true`)
5. Switch to `headless: false` only for the specific run where OTP / CAPTCHA needs human input

## Project Config Example

```json
{
  "headless": true,
  "timeout": 90000,
  "max_retries": 2,
  "concurrency": 1,
  "skip_existing": true,
  "browser_channel": "msedge",
  "user_data_dir": "/Users/example/Library/Application Support/Microsoft Edge",
  "profile_directory": "Default",
  "goto_wait_until": "domcontentloaded",
  "institution_login_config": "<skill-dir>/local/institution_login.private.json"
}
```

## Notes on OTP

- OTP is not hardcoded because it changes in real time
- when an OTP field or token prompt is detected, the script waits for manual completion in the browser
- after you submit the OTP, press Enter in the terminal and the downloader continues
- the preferred pattern is still DOI-first; login automation should only activate when the DOI flow lands on an authentication page

## When to Add Site-Specific Selectors

If your school SSO page uses unusual field names or multi-step login, add selectors in the private JSON:

- `pre_login_click_selectors`
- `username_selectors`
- `password_selectors`
- `submit_selectors`
- `success_selectors`
- `otp.detect_selectors`
- `otp.success_selectors`
