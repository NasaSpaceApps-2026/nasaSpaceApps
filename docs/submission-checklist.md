# Submission checklist

Mirrors the checklist in `PRD.md` ("Demo script and submission checklist"). Status on **Nov 15, 2026**, from what the repo shows. "Open" means the repo cannot show it is done; the team must check it. Confirm the allowed demo length and format against the 2026 Project Submission Guide (not verified here).

| # | Item | Status | Evidence / what is left |
|---|---|---|---|
| 1 | Team registered, all four members on the team page, challenge selected: Interplanetary Survival Guide: Martian Map | Open | Not verifiable from the repo. Team names are a placeholder in [project-page.md](project-page.md). |
| 2 | Project name, one-sentence summary and detailed description written against the five criteria | Drafted | [project-page.md](project-page.md) follows the PRD order. Fill the `TEAM TO FILL` / `TEAM TO CONFIRM` lines. |
| 3 | Demo video or slides in the allowed format, publicly viewable without sign-in | Open | Scripts ready: [demo-script.md](demo-script.md) (2-minute and 30-second, with replay fallbacks in `agent/replays/`). Video not recorded or linked in the repo. |
| 4 | Public repository with licence, README, run steps and data download script | Partly | [LICENSE](../LICENSE) (MIT), [README.md](../README.md) (one run path), `.env.example`. Data download: `python -m sitebuild.build_site` and `pipeline/00_fetch.py` fetch NASA/USGS data (network needed). Open: commit and push the working tree (many files are modified or untracked), and confirm the GitHub repo is public. |
| 5 | NASA data and resources list with links, copied from SOURCES.md | Done, one gap | [data/SOURCES.md](../data/SOURCES.md) and the table in [project-page.md](project-page.md). Gap: `data/build/site/jezero/SOURCES_site.md` (science layers: CRISM, SIM 3464, MMGIS, samples) is generated and git-ignored (`data/build/`), so it is absent from a clone; the project page and README carry its content. |
| 6 | Space agency partner and other references listed, including code libraries | Done | Credits section of [project-page.md](project-page.md); data licences in [README.md](../README.md). CRISM map is CC BY 4.0 (attribution given). |
| 7 | AI use disclosed: which tools helped write code, and that an LLM runs inside the product | Drafted, confirm | [project-page.md](project-page.md) "AI use disclosure" names Claude Code and Cursor and the in-app LLM (DeepSeek via API in the current build; Claude-compatible). The team must confirm the scope line. |
| 8 | No NASA logos or branding used in the app or video | Open | None added in the docs. The viewer and the video were not audited for logos here. |
| 9 | Hosted build or clear local run steps tested from a clean machine | Open | Run steps are in [README.md](../README.md) and [stack/README.md](../stack/README.md); verified only on the developer machine (511 Python and 44 chat-panel tests pass there). Not tested from a clean clone. No hosted build. |
| 10 | Submission page re-opened after saving to confirm every link works | Open | Do last. Links to check: repo, video, any hosted viewer. |

## Evidence quick links

- Science validity: [science-validity.md](science-validity.md) (summary), [../SCIENCE_VALIDITY.md](../SCIENCE_VALIDITY.md) (log), [science-validation.md](science-validation.md) (auto-generated checks).
- Agent evals: [eval-run-deepseek.md](eval-run-deepseek.md) (19 of 24 on full runs; not scored with Claude).
- Frames and what lines up in the viewer: [frames.md](frames.md).
- Progress by stream: [../PROGRESS.md](../PROGRESS.md).
