// Team-list lights on game pages. The build sets them when it runs, but it can run hours late, so
// in the last 2 hours the page asks for the game-day team list itself and redoes the prediction, using
// the same rule as adjust_for_absences in nbl/build.py.
(function () {
  "use strict";
  var box = document.getElementById("avail");
  var src = document.getElementById("avail-data");
  if (!box || !src) return;
  var d = JSON.parse(src.textContent);
  var left = new Date(d.tip) - Date.now();
  if (!d.url || left <= 0 || left > 2 * 3600 * 1000) return;

  var TIPS = { green: "Expected to play: named in the game-day team list", red: "Out: not in the game-day team list" };

  // Abramowitz and Stegun 7.1.26, as in matchup.js
  function erf(x) {
    var t = 1 / (1 + 0.3275911 * Math.abs(x));
    var y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) *
      t * Math.exp(-x * x);
    return x >= 0 ? y : -y;
  }
  function normalCdf(x) { return 0.5 * (1 + erf(x / Math.SQRT2)); }
  function slug(s) {
    return s.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  }
  function esc(s) { return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  function sgn(x) { return (x >= 0 ? "+" : "") + x.toFixed(1); }

  // Points each side loses for the players in `out`, capped per team, and each player's share.
  function lost(out) {
    var raw = { home: 0, away: 0 }, pts = [];
    out.forEach(function (p) { raw[p.side] += p.raw; });
    out.forEach(function (p) { pts.push(raw[p.side] ? p.raw * Math.min(raw[p.side], d.cap) / raw[p.side] : 0); });
    return { home: Math.min(raw.home, d.cap), away: Math.min(raw.away, d.cap), pts: pts };
  }
  function predict(out) {
    var l = lost(out);
    var margin = d.base.margin + l.away - l.home;
    return { margin: margin, pHome: normalCdf(margin / d.sigma), pts: l.pts };
  }
  function fav(margin) { return (margin >= 0 ? d.short.home : d.short.away) + " by " + Math.abs(margin).toFixed(1); }

  function show(named) {
    var all = predict(d.players);
    var keep = d.players.filter(function (p) { return !named[slug(p.name)]; });
    var now = predict(keep);
    var rows = d.players.map(function (p, i) {
      var green = !!named[slug(p.name)];
      var text = green ? "<b>Expected to play</b>. Named in the team list, so no penalty (would have been " + sgn(-all.pts[i]) + " pts)"
        : "<b>Out</b>. Not in the team list, " + sgn(-now.pts[keep.indexOf(p)]) + " pts";
      return '<br><span class="light ' + (green ? "green" : "red") + '" title="' + TIPS[green ? "green" : "red"] + '"></span>' +
        esc(p.name) + " (" + d.code[p.side] + "): " + text;
    });
    var small = box.querySelectorAll("small.mut");
    var note = small[small.length - 1].outerHTML;
    var without = keep.length < d.players.length ?
      '<br><small class="mut">Without the team list: ' + fav(all.margin) + ", " + d.short.home + " " + Math.round(all.pHome * 100) + "%.</small>" : "";
    box.innerHTML = "<b>Missed the last game</b>" + rows.join("") + without + "<br>" + note;

    var total = d.base.total, h = total / 2 + now.margin / 2, a = total / 2 - now.margin / 2;
    var ph = Math.round(now.pHome * 100);
    var score = document.getElementById("pred-score");
    if (score) score.innerHTML = d.short.away + " " + a.toFixed(1) + " <small>–</small> " + h.toFixed(1) + " " + d.short.home;
    var lab = document.getElementById("pred-wlab");
    if (lab) lab.innerHTML = "<span>" + d.short.away + " " + (100 - ph) + "%</span><span>" + d.short.home + " " + ph + "%</span>";
    var bar = document.getElementById("pred-wbar");
    if (bar) { bar.children[0].style.width = (100 - ph) + "%"; bar.children[1].style.width = ph + "%"; }
    var m = document.getElementById("pred-margin");
    if (m) m.textContent = fav(now.margin);
  }

  fetch(d.url + "?id=" + encodeURIComponent(d.id))
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (list) {
      if (!list || !list.players || !list.players.length) return;
      var named = {};
      list.players.forEach(function (p) { named[slug(p.name)] = true; });
      show(named);
    })
    .catch(function () {});
})();
