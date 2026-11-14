# Ares Atlas: project page (draft submission text)

Challenge: Interplanetary Survival Guide: Martian Map (NASA Space Apps 2026).
Team: `TODO: team name and members`. Demo video: `TODO: public link`. Repository: `TODO: public URL`.

Status of this draft: written against what exists in the repo on 2026-11-14. Items marked `TODO` are not done yet. Nothing here is a measured result unless a source file is named.

## One-sentence summary

Ares Atlas lets an explorer ask Claude spatial questions about Jezero crater ("where can a lander touch down within a 30 minute walk of base?") and have Claude answer from exhaustive searches of NASA terrain data and operate a rover over that terrain, through an open MCP server.

## The challenge

The Martian Map challenge asks for a layered, integrated view of a location or route on Mars that helps a human explorer plan and carry out a Marswalk while doing new science. Planning needs answers to compound questions: flat enough, rough enough, close enough on foot, and, later, near the right minerals. Today that means a person stitching together rasters by hand.

## Our solution

- **A terrain query engine.** From the HiRISE and CTX digital terrain models we build a 2 m analysis grid (elevation, slope, roughness, data-quality flag). Tools such as `find_locations` and `find_regions` take a small condition language (layer thresholds, evidence tiers, `any_of`, a walking-time limit from base, an objective such as flattest or best evidence) and search exhaustively for footprint-sized spots or connected areas.
- **Claude operates it over MCP.** An MCP server exposes about 27 tools: site and layer description, search, routes, rover driving, markers, viewer control. An agent backend runs Claude's tool-calling loop and streams text and tool calls to a chat panel. Any MCP client (Claude Desktop, Claude Code) can use the same server.
- **Honest answers.** Every result carries the thresholds used, where each came from (assumed or cited), the data source, and quality warnings. Empty results come with diagnostics showing which condition removed the candidates.
- **A rover to act on the answers.** Claude can place markers, plan and assess routes, teleport or drive the rover, and fly the viewer camera. `TODO: confirm which of these are shown live with the team's viewer.`

## Significance

An astronaut or mission planner can ask in plain language and get a reproducible, sourced answer, instead of a one-off map. Because the tools are an open MCP server, the same terrain intelligence can be used by any AI client, and new layers (minerals, geologic units) plug in through the same catalogue schema without changing a tool.

## NASA data used

Only datasets used by the current build (see `data/SOURCES.md`, accessed 2026-11-14; all from USGS Astrogeology, Mars 2020 Terrain Relative Navigation products, Fergason et al. 2020):

| Data | Role |
|---|---|
| Mars 2020 TRN HiRISE DTM mosaic, 1 m (`JEZ_hirise_soc_006`, MOLA-referenced), doi:10.5066/P9REJ9JN | Main terrain: elevation, slope (2 m, Horn method), 5x5 roughness, quality flag. Averaged to a 2 m grid. |
| Mars 2020 TRN CTX DTM mosaic, 20 m (`JEZ_ctx_B_soc_008`) | Fills cells with no HiRISE data, always flagged `ctx_fill`. Also used in the CTX-vs-HiRISE comparison. |

`data/SOURCES.md` additionally lists a CTX orthomosaic (comparison only) and a HiRISE orthomosaic (fetched by the tile pipeline, not used by the query engine). The landing site position (18.4447 N, 77.4508 E) is a published coordinate supplied to us, not extracted from imagery. No mineral or geologic-unit layers are used yet.

## Validation and accuracy approach

- **Exhaustive search, checked against an oracle.** Search results come from full-grid masks and distance transforms, not sampling. The test suite (`tests/`) compares the fast tools to slow, simple, independent reference implementations (`tests/oracle.py`, `tests/oracle_travel.py`) on fixture terrain and real-data windows, and checks properties: no returned spot has a failing cell inside its footprint, unknown cells never pass, spots respect spacing.
- **Evidence tiers.** Detected quantities (minerals, geology) are an ordinal tier from `confirmed_in_situ` down to `not_detected` and `no_coverage`, never a probability. `no_coverage` is not treated as "none". (Defined in `contracts/mcp-tools.md`; no evidence layer is loaded yet.)
- **Provenance labels.** Every profile threshold is marked `assumed` or cited, and results state which. All current thresholds in `profiles.yaml` are `assumed`.
- **Quality flags.** The HiRISE mosaic has suspected seam rows and noisy DTM patches. We detect them (`sitebuild/seams.py`, `sitebuild/noise.py`; figures `docs/seam_check.png`, `docs/noise_check.png`), keep the values unaltered, and flag the cells. The landing pad profile requires cells to be clean HiRISE data. CTX-filled cells are flagged because they are artificially smooth.
- **Grounding checks on the agent.** `agent/evals/questions.yaml` defines eval cases that check the tool path and that numbers in a reply appear in a tool result. `TODO: report eval pass rate from a live run.`
- **CTX vs HiRISE finding.** Using `sitebuild/compare_ctx_hirise.py`, in a 2 km window around the landing site the maximum slope is 11.7 degrees from the 20 m CTX DTM and 33.4 degrees from the 2 m HiRISE DTM (figure: `docs/compare_ctx_vs_hirise_2000m.png`; numbers: `docs/compare_ctx_vs_hirise_stats.txt`). Coarse data hides steep slopes, which is why we use HiRISE for hazard questions and flag CTX fill.

## AI use disclosure

- Claude (Anthropic) was used as a coding assistant to write much of the code in this repository. `TODO: team to confirm scope and any other tools used.`
- Claude also runs inside the product: the agent backend calls the Claude API with the MCP tools, and the chat panel shows its answers and every tool call. Claude's answers are meant to come from tool results; the system prompt and evals enforce this, but a language model can still err.

## Limitations

- Walking speed uses a placeholder Earth-derived hiking function (Tobler-style), not Mars or suit data. Rover speed is also an assumption.
- All profile thresholds (slope, roughness, footprint, EVA limit, reserve margin) are team assumptions pending cited values.
- No mineral or geologic-unit layers yet, so carbonate and olivine questions cannot be answered from data today.
- Coverage is one site (Jezero window, 4096 x 4096 cells at 2 m, about 8 km square). Slope has a 2 m baseline and cannot see rocks smaller than a few metres.
- The mission base is placed at the landing site by assumption.
- The 3D viewer, photoreal refine loop and ground view belong to teammates. `TODO: describe their status.`

## Credits and references

USGS Astrogeology Science Center and NASA MRO HiRISE and CTX teams for the data. Libraries: `TODO: list from pyproject and package.json`. No NASA logos are used.
