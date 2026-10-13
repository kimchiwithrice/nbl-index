# NBL Index: advanced NBL ratings

A KenPom-style stats site for the NBL. Each run downloads the latest data and rates every team
on adjusted offense, defense and tempo. It then predicts every game, simulates the season and
finals 10,000 times, and writes a complete static website, with one real HTML page per team,
game and player.

## Setup (once)

Python 3.10+, then in this folder:

```
pip install -r requirements.txt
```

## Build and preview

```
python run.py --serve
```

Then open http://localhost:8000. The site is written to `public/`. Without `--serve`, it just
builds. The data is cached in `data/` and re-downloaded at most every 3 hours. `--refresh`
forces a fresh download.

## What's on the site

| Page | What it shows |
|---|---|
| Ratings (home) | AdjEM, AdjO, AdjD and AdjT with ranks, schedule strength, luck, projected wins, finals and title odds, 7-day movers |
| Team pages | Rating cards, rating-through-the-season chart, Four Factors, shooting, luck and résumé, finish-position odds, full schedule with predictions, roster with advanced stats |
| Game pages | Prediction (win %, projected score, margin, total), matchup comparison; for played games, the result and watchability stars |
| Schedule | Every upcoming game with predictions and a thrill score; played games with what the model said |
| Ladder odds | Simulated ladder: 1st, top 2, finals, semis, grand final and title odds, finish-position heatmap |
| Team stats | Four Factors (offense and defense), shooting, style, with ranks |
| Players | Leaderboard with TS%, usage, assist, rebound, steal and turnover rates; a career page for every player since 2015-16 |
| Matchup | Any two teams: win %, projected score, likely margin and total ranges |
| Model | Method, backtest since 2016-17, calibration, and the public prediction ledger |

**Spoiler-free mode:** the Hide scores link at the top hides every score, record and result
(click a blurred cell to peek). The setting is remembered per device.

## Settings: `config.json`

| Key | What it does |
|---|---|
| `site_name`, `rating_name`, `tagline` | Branding |
| `base_url` | Your domain, e.g. `https://nblratings.com`. Used for canonical links and the sitemap |
| `base_path` | `/` normally; `/repo-name/` for a GitHub Pages project site |
| `kofi_url` | Tip-jar link. The "chip in" line appears once this is set |
| `newsletter_url` | Signup link. The newsletter line appears once this is set |
| `contact` | Shown on the About page |
| `ad_html` | Ad code, placed below the main tables. Leave empty for no ads |
| `head_html` | Snippet added to every page's head: Search Console verification tag, privacy-friendly analytics |
| `show_betting_tools` | `false` (default) keeps the public site prediction-only. `true` adds fair odds and an odds/edge/EV calculator, for your own local use |

## Going live checklist

1. **Domain:** buy `nblindex.com` (Cloudflare Registrar or Porkbun, about US$10–11 a year).
2. **GitHub:** create a free account and a new **public** repository (e.g. `nbl-index`).
3. **Workflow file:** `.github/workflows/build.yml` runs the daily builds. If you're starting from a
   copy without the `.github` folder, create it from `github-workflow-build.yml`.
4. **Upload:** push this folder to the repository (GitHub Desktop is easiest). `.gitignore` already
   leaves out the downloaded data and the built site; `data/ledger.json` should be included.
5. **Turn on Pages:** in the repository's Pages settings, choose GitHub Actions as the source.
6. **First build:** run the workflow once by hand from the Actions tab. After that it runs at
   23:40 and 11:40 UTC every day (about 10:40am and 10:40pm in Sydney during daylight saving).
7. **Custom domain:** enter `nblindex.com` in the Pages settings, add the DNS records GitHub
   shows at your registrar, then tick "Enforce HTTPS" once it's offered.
8. **Google:** add the site to Google Search Console (verify with a DNS record, or paste the
   meta tag into `head_html` in `config.json`) and submit `https://nblindex.com/sitemap.xml`.
9. **Optional:** a Ko-fi link in `kofi_url`; cookie-free visitor stats (e.g. Cloudflare Web
   Analytics) by pasting their snippet into `head_html`.

If a scheduled build ever fails (data source down, format change), the last good version of
the site stays up and GitHub emails you. Run `python run.py` locally to see the error.

## Publishing it

The output is plain static files, so any static host works:

- **GitHub Pages (free, automatic):** push this folder to a GitHub repo and set the repo's Pages
  source to GitHub Actions. The workflow rebuilds the site twice a day, keeps the prediction
  ledger, and publishes it.
- **Cloudflare Pages / Netlify:** upload `public/`, or connect the repo with the build command
  `pip install -r requirements.txt && python run.py` and output folder `public`.
- **Your homelab:** run `python run.py` on a schedule and serve `public/` with any web server.

Then add the domain to Google Search Console and submit `sitemap.xml`.

## Prediction ledger

`data/ledger.json` stores every prediction made before tip-off and grades it after the game.
It's what makes the track record on the Model page honest. Keep this file and commit it. The
GitHub workflow does that automatically.

## How the model works (short version)

Possessions = FGA − ORB + TOV + 0.44 × FTA. Points per 100 possessions are modelled as league
average + offense + opponent defense ± home court, solved for all teams at once with ridge
regression. Tempo works the same way. Older games fade with a 120-day half-life, and last
season counts as a fading prior. Win probability comes from the backtest's typical miss
(about ±13.8 points). The walk-forward backtest covers 1,478 games since 2016-17: winner
picked 62.5%, log loss 0.654 against 0.683 for always picking the home team. The settings are
at the top of `nbl/model.py`.

## Code

```
run.py              build (and preview) the site
config.json         branding, links, ads
nbl/data.py         download and tidy the NBL data
nbl/model.py        ratings, predictions, backtest, team stats
nbl/sim.py          season and finals simulator
nbl/players.py      player advanced stats
nbl/build.py        writes every page
nbl/templates/      page layouts (Jinja)
nbl/static/         CSS, browser scripts and the self-hosted Archivo font (SIL Open Font License)
```

## Data and credit

Box scores and schedules come from the NBL's official stats feed (FIBA LiveStats), collected
daily by Jason Zivkovic's open-source [nblR](https://github.com/JaseZiv/nblR) project. Keep the
credit in the footer. Before you monetise, check that the NBL's terms allow republishing
stats derived from its feed.

## Next steps

- Our own scraper for the FIBA LiveStats feed, for current-season play-by-play and shot
  locations (the shared shot data stops at 2024-25). That unlocks shot charts, an
  expected-points shot model, live win-probability charts and lineup data.
- Adjusted plus-minus for players.
- A Next Stars prospect hub.
- WNBL and NBL1.
