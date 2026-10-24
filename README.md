# NBL Index

The code behind [nblindex.com](https://nblindex.com). KenPom-style ratings for the NBL, plus game predictions and a season simulator. It updates itself every hour. If one of the data sources stops working, the build opens an issue here and closes it again once the source is back.

To run it locally:

```
pip install -r requirements.txt
python run.py --serve
```

Data comes from Jason Zivkovic's [nblR](https://github.com/JaseZiv/nblR). Not affiliated with the NBL.
