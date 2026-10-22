// Matchup calculator. Uses the ratings in data.js and the same formulas as nbl/model.py.
(function () {
  "use strict";
  var data = window.NBL;
  if (!data) return;
  var teams = data.teams;
  var cal = data.cal;
  var names = Object.keys(teams).sort();
  var Z80 = 1.2816; // half-width of an 80% range, in standard deviations

  function el(id) { return document.getElementById(id); }
  function inputValue(id) { var input = el(id); return input ? input.value : ""; }

  function expected(home, away, neutral) {
    var side = neutral ? 0 : 1;
    var poss = data.t_mu + teams[home].tempo + teams[away].tempo;
    var homeEff = data.mu + side * data.hfa + teams[home].off + teams[away].def;
    var awayEff = data.mu - side * data.hfa + teams[away].off + teams[home].def;
    return { home: poss * homeEff / 100, away: poss * awayEff / 100, poss: poss };
  }

  // Abramowitz and Stegun 7.1.26
  function erf(x) {
    var t = 1 / (1 + 0.3275911 * Math.abs(x));
    var y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) *
      t * Math.exp(-x * x);
    return x >= 0 ? y : -y;
  }
  function normalCdf(x) { return 0.5 * (1 + erf(x / Math.SQRT2)); }

  function predict(home, away, neutral) {
    var e = expected(home, away, neutral);
    var margin = (e.home - e.away) * cal.k;
    var total = e.home + e.away + cal.tot_bias;
    return {
      homePts: total / 2 + margin / 2, awayPts: total / 2 - margin / 2,
      margin: margin, total: total, poss: e.poss, pHome: normalCdf(margin / cal.sigma)
    };
  }

  // Accepts decimal (1.91) or American (-110, +150) odds; returns decimal odds or null.
  function decimalOdds(text) {
    var o = parseFloat(text);
    if (isNaN(o)) return null;
    if (o >= 100) return 1 + o / 100;
    if (o <= -100) return 1 + 100 / -o;
    return o > 1 ? o : null;
  }
  function pct(p) { return (p * 100).toFixed(1) + "%"; }
  function signed(v) { return (v > 0 ? "+" : "") + v.toFixed(1); }

  var awaySelect = el("ma"), homeSelect = el("mh");
  names.forEach(function (t) {
    awaySelect.add(new Option(teams[t].name, t));
    homeSelect.add(new Option(teams[t].name, t));
  });

  // Teams can be preselected with #HOME-AWAY codes, e.g. #SYD-PER.
  var byCode = {};
  names.forEach(function (t) { byCode[teams[t].code] = t; });
  var byRating = names.slice().sort(function (a, b) { return teams[a].rk.net - teams[b].rk.net; });
  var fromHash = (location.hash || "").slice(1).split("-");
  homeSelect.value = byCode[fromHash[0]] || byRating[0];
  awaySelect.value = byCode[fromHash[1]] || (byRating[1] === homeSelect.value ? byRating[0] : byRating[1]);

  function summaryHtml(home, away, r) {
    var homePct = Math.round(r.pHome * 100);
    return '<div class="score">' + teams[away].code + " " + r.awayPts.toFixed(1) + " <small>–</small> " +
      r.homePts.toFixed(1) + " " + teams[home].code + "</div>" +
      '<div class="wlab"><span>' + teams[away].name + " " + (100 - homePct) + "%</span><span>" +
      teams[home].name + " " + homePct + "%</span></div>" +
      '<div class="wbar"><div class="a" style="width:' + (100 - homePct) + '%"></div>' +
      '<div class="h" style="width:' + homePct + '%"></div></div>';
  }

  function projectionHtml(home, away, r) {
    function byPoints(m) {
      return (m >= 0 ? teams[home].code : teams[away].code) + " by " + Math.abs(m).toFixed(0);
    }
    var leader = r.margin >= 0 ? teams[home].name : teams[away].name;
    var low = r.margin - Z80 * cal.sigma, high = r.margin + Z80 * cal.sigma;
    var homeBy10 = 1 - normalCdf((10 - r.margin) / cal.sigma);
    var awayBy10 = normalCdf((-10 - r.margin) / cal.sigma);
    var totalLow = r.total - Z80 * cal.sigma_tot, totalHigh = r.total + Z80 * cal.sigma_tot;
    return '<div class="tw"><table class="dense"><tbody>' +
      '<tr><td class="l">Projected margin</td><td>' + leader + " by " + Math.abs(r.margin).toFixed(1) + "</td></tr>" +
      '<tr><td class="l">Likely margin (8 in 10 games)</td><td>' + byPoints(low) + " to " + byPoints(high) + "</td></tr>" +
      '<tr><td class="l">' + teams[home].name + " by 10 or more</td><td>" + pct(homeBy10) + "</td></tr>" +
      '<tr><td class="l">' + teams[away].name + " by 10 or more</td><td>" + pct(awayBy10) + "</td></tr>" +
      '<tr><td class="l">Projected total</td><td>' + r.total.toFixed(1) + " (" + totalLow.toFixed(0) + "–" +
      totalHigh.toFixed(0) + ")</td></tr>" +
      '<tr><td class="l">Possessions</td><td>' + r.poss.toFixed(1) + "</td></tr>" +
      "</tbody></table></div>";
  }

  // Only shown when show_betting_tools is on in config.json.
  function oddsHtml(home, away, r) {
    var line = parseFloat(inputValue("o_sl"));
    var totalLine = parseFloat(inputValue("o_tl"));
    if (isNaN(line)) line = -Math.round(r.margin * 2) / 2;
    if (isNaN(totalLine)) totalLine = Math.round(r.total * 2) / 2 + 0.5;
    var pHomeCovers = normalCdf((r.margin + line) / cal.sigma);
    var pOver = 1 - normalCdf((totalLine - r.total) / cal.sigma_tot);
    var markets = [
      { label: teams[away].code + " to win", p: 1 - r.pHome, odds: "o_ma", other: "o_mh" },
      { label: teams[home].code + " to win", p: r.pHome, odds: "o_mh", other: "o_ma" },
      { label: teams[home].code + " " + (line > 0 ? "+" : "") + line, p: pHomeCovers, odds: "o_so", other: null },
      { label: teams[away].code + " " + (-line > 0 ? "+" : "") + (-line), p: 1 - pHomeCovers, odds: null, other: "o_so" },
      { label: "Over " + totalLine, p: pOver, odds: "o_to", other: "o_tu" },
      { label: "Under " + totalLine, p: 1 - pOver, odds: "o_tu", other: "o_to" }
    ];
    var html = '<div class="tw"><table class="dense"><thead><tr><th class="l">Market</th><th>Model</th>' +
      "<th>Fair odds</th><th>Book</th><th>Edge</th><th>EV</th></tr></thead><tbody>";
    markets.forEach(function (m) {
      var odds = m.odds ? decimalOdds(inputValue(m.odds)) : null;
      var otherOdds = m.other ? decimalOdds(inputValue(m.other)) : null;
      // Market probability with the bookmaker's margin removed when both sides are entered.
      var marketP = odds ? (otherOdds ? (1 / odds) / (1 / odds + 1 / otherOdds) : 1 / odds) : null;
      var ev = odds ? m.p * (odds - 1) - (1 - m.p) : null;
      var fair = (1 / Math.min(Math.max(m.p, 0.01), 0.99)).toFixed(2);
      html += "<tr" + (ev !== null && ev > 0.03 ? ' class="hl"' : "") + '><td class="l">' + m.label + "</td>" +
        "<td>" + pct(m.p) + "</td><td>" + fair + "</td><td>" + (odds ? odds.toFixed(2) : "–") + "</td>" +
        "<td>" + (marketP === null ? "–" : signed((m.p - marketP) * 100) + "%") + "</td>" +
        '<td class="' + (ev === null ? "" : ev > 0 ? "pos" : "neg") + '">' + (ev === null ? "–" : signed(ev * 100) + "%") +
        "</td></tr>";
    });
    return html + '</tbody></table></div><p class="note">Edge compares the model with the bookmaker\'s price ' +
      "after removing their margin (when both sides are entered). Rows with EV above +3% are highlighted.</p>";
  }

  function comparisonHtml(home, away) {
    function row(label, key) {
      var a = teams[away], h = teams[home];
      var fmt = function (v) { return key === "net" ? signed(v) : v.toFixed(1); };
      return '<tr><td class="l">' + label + "</td>" +
        '<td class="' + (a.rk[key] < h.rk[key] ? "adv" : "") + '">' + fmt(a[key]) + '<span class="rk">' + a.rk[key] + "</span></td>" +
        '<td class="' + (h.rk[key] < a.rk[key] ? "adv" : "") + '">' + fmt(h[key]) + '<span class="rk">' + h.rk[key] + "</span></td></tr>";
    }
    return '<h2 style="margin-top:0">Ratings</h2><div class="tw"><table class="dense"><thead><tr><th class="l"></th>' +
      '<th><a href="../teams/' + teams[away].slug + '/">' + teams[away].code + "</a></th>" +
      '<th><a href="../teams/' + teams[home].slug + '/">' + teams[home].code + "</a></th></tr></thead><tbody>" +
      row("AdjEM", "net") + row("Adj. offense", "o") + row("Adj. defense", "d") + row("Adj. tempo", "t") +
      "</tbody></table></div>";
  }

  function update() {
    var home = homeSelect.value, away = awaySelect.value;
    if (home === away) {
      el("mres").innerHTML = '<p class="note">Pick two different teams.</p>';
      el("mcmp").innerHTML = "";
      return;
    }
    location.replace("#" + teams[home].code + "-" + teams[away].code);
    var r = predict(home, away, el("mn").checked);
    el("mres").innerHTML = summaryHtml(home, away, r) +
      (data.betting ? oddsHtml(home, away, r) : projectionHtml(home, away, r));
    el("mcmp").innerHTML = comparisonHtml(home, away);
    if (window.markTips) window.markTips();
    if (window.fitTables) window.fitTables();
  }

  document.querySelectorAll("#ma, #mh, #mn, .ctl input").forEach(function (input) {
    input.addEventListener("change", update);
    input.addEventListener("input", update);
  });
  update();
})();
