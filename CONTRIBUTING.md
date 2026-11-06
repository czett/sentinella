# Contributing

Thanks for helping improve **Sentinella**, the high-precision SMA battery monitor for the Homematic CCU3. The project targets a very small environment, so the review bar is mostly about portability and failure behaviour.

## Before you start

- Open an issue for a larger change so the approach can be discussed.
- Keep changes focused. A bug fix should not also reformat the documentation.
- Never commit a real `.env`, password, API key, SID, log or `status.json`.

## Coding rules

### Shell

- Target POSIX `/bin/sh` and BusyBox `ash` on the CCU3.
- No bashisms: no `local`, arrays, `[[ ]]`, `(( ))`, process substitution, `source`, `set -u` assumptions, `date -d` or `read -p`.
- Prefer `printf` over `echo` for data.
- Quote every expansion unless word splitting is deliberate.
- Do not add a dependency that is not in a standard CCU3 image. `curl`, `sed`, `tr`, `cut`, `head`, `tail`, `date` and `logger` are available.
- Every new code path that opens an SMA session must log out, including error and signal paths.
- A new failure mode must not overwrite the last good CCU3 value.
- State files must be written atomically (temporary file plus `mv`).

### Web

- Vanilla HTML, CSS and JavaScript. No CDN, no framework, no build step.
- Keep the dashboard usable without a network connection to a CDN.
- Respect `prefers-reduced-motion`.
- Every interactive element needs a visible `:focus-visible` state.
- Do not put secrets or internal error details in `status.json`.

### Documentation

- English is the primary language. Keep `README.de.md` in sync for user-facing changes.
- Update the configuration tables in both READMEs when a key changes.
- Add troubleshooting entries for new failure modes.

## Checks before a pull request

```sh
sh -n bin/check_soc.sh
sh -n lib/notify_signal.sh
sh -n setup_variables.sh
shellcheck -s sh -S style bin/check_soc.sh lib/notify_signal.sh setup_variables.sh
shellcheck -s bash -S style deploy.sh
node --check web/app.js
python3 tests/integration.py
```

All checks must pass. If you add a behaviour, add an integration test for it in `tests/integration.py`.

## Commit messages

Use a short imperative subject, then a blank line and a body when the reason is not obvious.

```
Fix logout on SIGTERM during getValues

The session id lived only in the fetch_measurements subshell, so the
cleanup trap had nothing to log out. Store the measurement in a global
and run the fetch in the main shell.
```

## Pull requests

- Describe the problem, the change and how you tested it.
- Mention the CCU3 firmware version and inverter model when relevant.
- Do not include generated screenshots of real credentials.

## License

By contributing you agree that your contribution is licensed under the MIT license in [LICENSE](LICENSE).
