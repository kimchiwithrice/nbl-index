# NBL Index

The code behind [nblindex.com](https://nblindex.com), a free stats site for the NBL.

It rates every team on offense, defense and tempo, adjusted for who they've played and where,
in the style of Ken Pomeroy's college basketball ratings. From there it predicts every game,
simulates the rest of the season and the finals, and keeps a public record of how the
predictions went.

The site rebuilds itself twice a day once new box scores come in.

## How the ratings work

- Every game is broken down into possessions and points per 100 possessions for each side.
- All teams' offenses, defenses and home court are fitted together, so a big night against a
  bad defense counts for less than a solid one against a good defense.
- Recent games count more, and last season carries over early on and fades as the season goes.
- Predictions turn the two teams' ratings into an expected score and a win probability, checked
  against every season since 2016-17.

There's more detail on the site's [model page](https://nblindex.com/model/).

## Running it yourself

Python 3.10 or newer:

```
pip install -r requirements.txt
python run.py --serve
```

Then open http://localhost:8000. Model settings are at the top of `nbl/model.py`; site settings
are in `config.json`.

## Data

Box scores and schedules come from the NBL's official stats feed, collected by Jason
Zivkovic's open-source [nblR](https://github.com/JaseZiv/nblR) project. Thanks Jason.

Not affiliated with the NBL or any club.
