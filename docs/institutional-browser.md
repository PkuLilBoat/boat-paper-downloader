# Institutional Browser Profile Notes

Use this document when publisher access depends on a signed-in browser session, campus VPN, or institution-bound cookies.

## When to Use It

Use `user_data_dir` only when the fast DOI/OA path fails and the browser-backed institutional flow needs your existing authenticated session.

## Recommended Order

1. try the downloader with your stable default browser profile first: Edge + `Default` profile + `headless: true`
2. keep DOI-first navigation and let the site redirect you where it wants
3. retry with `headless: false` only if you truly need to inspect OTP, CAPTCHA, or broken site behavior
4. use `user_data_dir` when cookies or saved login state are required

## Config Shape

```json
{
  "headless": true,
  "browser_channel": "msedge",
  "user_data_dir": "/path/to/browser-profile-root",
  "profile_directory": "Default",
  "institution_login_config": "/path/to/private-login.json",
  "goto_wait_until": "domcontentloaded"
}
```

## Common Profile Roots

- macOS Edge: `~/Library/Application Support/Microsoft Edge`
- macOS Chrome: `~/Library/Application Support/Google/Chrome`
- Windows Edge: `%LOCALAPPDATA%\\Microsoft\\Edge\\User Data`
- Windows Chrome: `%LOCALAPPDATA%\\Google\\Chrome\\User Data`
- Linux Chromium: `~/.config/chromium`
- Linux Chrome: `~/.config/google-chrome`

## Notes

- the script copies a minimal subset of profile files into a temporary directory to avoid profile lock conflicts
- this mode is slower than the default ephemeral browser path, so only use it when needed
- if the browser still cannot reach the PDF automatically, the Playwright institutional path can pause for manual download completion
- `institution_login_config` lets the Playwright flow auto-fill login fields and then pause for live OTP entry when needed
- `goto_wait_until: "domcontentloaded"` is the preferred setting for DOI -> publisher navigation because many publisher pages never settle cleanly on full `load`
- default policy should stay non-visual; only expose the browser UI when a human actually needs to intervene
- for the OTP-enabled setup, see `docs/institutional-login-automation.md`
