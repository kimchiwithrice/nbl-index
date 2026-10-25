// Site-wide behaviour: spoiler toggle, theme, local times, row links, table sorting, stat tooltips,
// player filter.
(function(){
  "use strict";
  var root = document.documentElement;
  function store(k, v){ try{ if(v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); }catch(e){ return null; } }

  // Spoiler-free mode, remembered per browser
  var sp = document.getElementById("sp-btn");
  function label(btn, text){ (btn.querySelector(".tx") || btn).textContent = text; }
  function spLabel(){ if(sp) label(sp, root.dataset.sp === "hide" ? "Show scores" : "Hide scores"); }
  spLabel();
  if(sp) sp.onclick = function(){
    root.dataset.sp = root.dataset.sp === "hide" ? "show" : "hide";
    store("nbl-sp", root.dataset.sp); spLabel();
  };
  document.addEventListener("click", function(e){
    var s = e.target.closest && e.target.closest(".sp");
    if(s && root.dataset.sp === "hide" && !s.closest(".spblock.open")){ s.classList.toggle("peek"); e.preventDefault(); e.stopPropagation(); }
  }, true);

  document.querySelectorAll(".sp-reveal").forEach(function(b){
    b.onclick = function(){ var blk = b.closest(".spblock"); blk.classList.add("open"); blk.querySelectorAll(".sp").forEach(function(s){ s.classList.add("peek"); }); if(window.fitTables) window.fitTables(); };
  });

  // Light/dark theme
  var th = document.getElementById("th-btn");
  function isDark(){ return root.dataset.theme === "dark" || (!root.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches); }
  function thLabel(){ if(th) label(th, isDark() ? "Light mode" : "Dark mode"); }
  thLabel();
  if(th) th.onclick = function(){
    root.dataset.theme = isDark() ? "light" : "dark"; store("nbl-theme", root.dataset.theme); thLabel();
  };

  // Side menu: collapse to icons only, remembered per browser
  var navBtn = document.getElementById("nav-btn");
  if(navBtn) navBtn.onclick = function(){
    if(root.dataset.nav === "mini") delete root.dataset.nav; else root.dataset.nav = "mini";
    store("nbl-nav", root.dataset.nav || "full");
    if(window.fitTables) window.fitTables();
  };

  // Show game times in the visitor's time zone
  document.querySelectorAll(".t[data-utc]").forEach(function(el){
    var d = new Date(el.dataset.utc); if(isNaN(d)) return;
    var day = document.createElement("span"), time = document.createElement("span");
    var narrow = window.matchMedia("(max-width:600px)").matches;
    day.textContent = d.toLocaleDateString(undefined, narrow ? {day:"numeric", month:"short"} :
                                           {weekday:"short", day:"numeric", month:"short"}) + ", ";
    time.className = "h";
    time.textContent = d.toLocaleTimeString(undefined, {hour:"numeric", minute:"2-digit"});
    el.replaceChildren(day, time);
    el.title = "Your local time";
  });

  // Make whole table rows clickable
  document.querySelectorAll("tr.link[data-href]").forEach(function(tr){
    tr.addEventListener("click", function(e){ if(!e.target.closest("a") && !(root.dataset.sp === "hide" && e.target.closest(".sp"))) location.href = tr.dataset.href; });
  });

  // Long tables: if a table fits its box, its header sticks to the top of the screen as you scroll.
  // If it has to scroll sideways (phones), the rank and name columns stick to the left instead.
  function fitTables(){
    document.querySelectorAll(".tw").forEach(function(tw){
      tw.classList.remove("fit");
      tw.querySelectorAll(".stick").forEach(function(c){ c.classList.remove("stick", "last"); c.style.left = ""; });
      if(!tw.clientWidth) return;  // hidden for now (spoiler-free mode)
      if(tw.scrollWidth <= tw.clientWidth + 1){ tw.classList.add("fit"); return; }
      var first = tw.querySelector("tbody tr"); if(!first) return;
      var cells = Array.prototype.slice.call(first.cells), upto = -1;
      cells.some(function(td, i){ if(td.classList.contains("l")){ upto = i; return true; } });
      if(upto < 0 || cells[upto].offsetLeft + cells[upto].offsetWidth > tw.clientWidth / 2) return;
      var lefts = cells.slice(0, upto + 1).map(function(td){ return td.offsetLeft; });
      tw.querySelectorAll("tr").forEach(function(tr){
        // header rows that group columns don't line up cell for cell, so leave them be
        if(tr.classList.contains("grp") || tr.cells.length !== cells.length) return;
        for(var i = 0; i <= upto; i++){
          tr.cells[i].classList.add("stick"); tr.cells[i].style.left = lefts[i] + "px";
          if(i === upto) tr.cells[i].classList.add("last");
        }
      });
    });
  }
  fitTables();
  if(document.fonts) document.fonts.ready.then(fitTables);
  var fitTimer;
  window.addEventListener("resize", function(){ clearTimeout(fitTimer); fitTimer = setTimeout(fitTables, 150); });
  window.fitTables = fitTables;  // the matchup page redraws its tables and calls this again

  // On narrow screens the menu is one row you can swipe; start it with the current page in view
  var nav = document.querySelector("nav"), navOn = nav && nav.querySelector("a.on");
  if(navOn && nav.scrollWidth > nav.clientWidth) nav.scrollLeft = navOn.offsetLeft - nav.offsetLeft - 20;

  // Sortable tables: click a header that has data-sort
  function cellVal(td, kind){
    var v = td.dataset.v !== undefined ? td.dataset.v : td.textContent.trim();
    if(kind === "text") return v.toLowerCase();
    var n = parseFloat(String(v).replace(/[^0-9.\-]/g, ""));
    return isNaN(n) ? -Infinity : n;
  }
  document.querySelectorAll("table.sortable").forEach(function(tbl){
    var heads = tbl.querySelectorAll("thead tr:last-child th");
    heads.forEach(function(th, ci){
      var kind = th.dataset.sort; if(!kind) return;
      th.addEventListener("click", function(){
        if(root.dataset.sp === "hide" && th.classList.contains("sp-col")) return;  // sorting would give results away
        var asc = th.classList.contains("on") ? !th._asc : (kind === "text" || kind === "num-asc");
        heads.forEach(function(h){ h.classList.remove("on"); });
        th.classList.add("on"); th._asc = asc;
        var body = tbl.tBodies[0], rows = Array.prototype.slice.call(body.rows);
        rows.sort(function(a, b){
          var x = cellVal(a.cells[ci], kind), y = cellVal(b.cells[ci], kind);
          if(kind === "text") return asc ? (x < y ? -1 : x > y ? 1 : 0) : (x < y ? 1 : x > y ? -1 : 0);
          return asc ? x - y : y - x;
        });
        rows.forEach(function(r){ body.appendChild(r); });
      });
    });
  });

  // Short explanations for the less obvious stats. Shown on hover, keyboard focus or tap.
  var TIPS = {
    "AdjEM": "How many points per 100 possessions better than an average team. Based on the opponents each team actually played.",
    "AdjO": "Points scored per 100 possessions, adjusted for the defenses each team actually faced.",
    "Adj. offense": "Points scored per 100 possessions, adjusted for the defenses each team actually faced.",
    "AdjD": "Points allowed per 100 possessions, adjusted for the offenses each team actually faced. Lower is better.",
    "Adj. defense": "Points allowed per 100 possessions, adjusted for the offenses each team actually faced. Lower is better.",
    "AdjT": "Possessions per 40 minutes, adjusted for the opponents faced. Pace, not quality.",
    "Adj. tempo": "Possessions per 40 minutes, adjusted for the opponents faced. Pace, not quality.",
    "SOS": "Average AdjEM of the teams played so far. Higher = tougher schedule.",
    "Schedule strength": "Average AdjEM of the teams played so far. Higher = tougher schedule.",
    "Opponents played (avg AdjEM)": "Average AdjEM of the teams played so far. Higher = tougher schedule.",
    "Left SOS": "Average AdjEM of the teams still to play.",
    "Opponents still to play": "Average AdjEM of the teams still to play.",
    "Luck": "Wins above or below what their play says they should have. Positive usually means winning close games. Tends to even out.",
    "Wins vs expected (luck)": "Wins above or below what their play says they should have. Positive usually means winning close games. Tends to even out.",
    "Wins above an average team (résumé)": "How many more wins than an average team would have against the same schedule.",
    "Raw efficiency (off / def)": "Points scored and allowed per 100 possessions, before adjusting for opponents.",
    "Proj W": "Average wins across the simulated seasons.",
    "Left H/A": "Regular-season games left at home and away. The simulation plays each one at its real venue.",
    "Games left at home / away": "Regular-season games left at home and away. The simulation plays each one at its real venue.",
    "Proj. record": "Average wins and losses across the simulated seasons.",
    "7d": "Change in rank over the last week.",
    "eFG%": "Field-goal % with threes counted as 1.5 makes.",
    "Effective FG%": "Field-goal % with threes counted as 1.5 makes.",
    "TO%": "Turnovers per possession. On defense: turnovers forced.",
    "Turnover %": "Turnovers per possession. On defense: turnovers forced.",
    "OR%": "Share of missed shots the offense rebounds. On defense: what opponents grab.",
    "Off. rebound %": "Share of missed shots the offense rebounds. On defense: what opponents grab.",
    "FTRate": "Free-throw attempts per field-goal attempt. How often a team gets to the line.",
    "FT rate": "Free-throw attempts per field-goal attempt. How often a team gets to the line.",
    "Free-throw rate": "Free-throw attempts per field-goal attempt. How often a team gets to the line.",
    "3PA rate": "Share of shots that are threes.",
    "3PA share": "Share of shots that are threes.",
    "3PA share of shots": "Share of shots that are threes.",
    "Opp 3PA rate": "Share of opponents' shots that are threes.",
    "Ast/FGM": "Share of made shots that were assisted.",
    "Opp Ast/FGM": "Share of opponents' made shots that were assisted.",
    "Assists per made FG": "Share of made shots that were assisted.",
    "Steal%": "Steals per opponent possession.",
    "Block%": "Share of opponents' two-point attempts blocked.",
    "Steal % / Block %": "Steals per opponent possession / share of opponents' twos blocked.",
    "Bench pts share": "Share of points scored by players who didn't start.",
    "Bench share of points": "Share of points scored by players who didn't start.",
    "Time leading": "Share of game time this season the team was ahead on the scoreboard.",
    "Time tied": "Share of game time this season the scores were level.",
    "Time trailing": "Share of game time this season the team was behind on the scoreboard.",
    "TS%": "Shooting efficiency counting twos, threes and free throws together.",
    "True shooting": "Shooting efficiency counting twos, threes and free throws together.",
    "USG%": "How much of the offense runs through a player: the share of team possessions they finish (shot, free throws or turnover) while on court.",
    "Usage": "How much of the offense runs through a player: the share of team possessions they finish (shot, free throws or turnover) while on court.",
    "AST%": "Share of teammates' baskets he assisted while on court.",
    "ORB%": "Share of available offensive rebounds he grabbed while on court.",
    "DRB%": "Share of available defensive rebounds he grabbed while on court.",
    "REB%": "Share of available rebounds he grabbed while on court.",
    "STL%": "Steals per opponent possession while on court.",
    "BLK%": "Share of opponents' two-point attempts he blocked while on court.",
    "TOV%": "Turnovers per possession he used.",
    "ORtg": "Points per 100 possessions he used. A simple scoring-efficiency number.",
    "P/40": "Points per 40 minutes.",
    "Impact": "Estimated points per 100 possessions a player adds compared with an average NBL player, from his box score.",
    "+/- 40": "Team point margin while he was on court, per 40 minutes.",
    "Hype": "How watchable the game should be, 0-100, judged before tip-off, mostly from how evenly matched the teams are, plus a little for how good they are.",
    "Drama": "How the game actually played out, 0-100: how close it finished, overtime and team quality. Never says who won.",
    "Expected possessions": "How many possessions each team should get, from both teams' pace.",
    "Log loss": "Scores the win probabilities. Lower is better; a coin flip scores 0.693.",
    "Margin error": "Average points between the predicted and actual margin.",
    "Total error": "Average points between the predicted and actual combined score.",
    "Layups": "Share of shots that were layups, dunks or tip-ins (as logged by the scorers).",
    "Layup FG%": "Field-goal % on layups, dunks and tip-ins.",
    "Floaters": "Share of shots that were floaters or hook shots.",
    "2pt jumpers": "Share of shots that were two-point jump shots.",
    "Jumper FG%": "Field-goal % on two-point jump shots.",
    "Assisted": "Share of made field goals that were assisted.",
    "Opp layups": "Share of opponents' shots that were layups, dunks or tip-ins.",
    "Opp layup FG%": "Opponents' field-goal % on layups, dunks and tip-ins.",
    "Clutch minutes": "Minutes played in the last 5 minutes of the 4th quarter or overtime with the score within 5.",
    "Clutch net/100": "Point margin per 100 possessions in clutch time.",
    "Adj net": "Offense minus defense in that game, after taking out the opponent's strength and home court.",
    "On − off": "Team net rating with the player on court minus with them off.",
    "Layups & dunks": "Layups, dunks, tip-ins and alley-oops.",
    "Floaters & hooks": "Floaters and hook shots, mostly from 2 to 3 metres.",
    "2-point jumpers": "Two-point jump shots: pull-ups, turnarounds, fadeaways, step-backs and the rest.",
    "3-pointers": "Every three-point attempt.",
    "At the rim": "Within 1.5 metres of the basket.",
    "Short (1.5-3 m)": "Between 1.5 and 3 metres from the basket: floaters, hooks and short jumpers.",
    "Mid-range": "Two-pointers from more than 3 metres out.",
    "Corner 3": "Threes from within 3 metres of the baseline, where the line is closest.",
    "Above-the-break 3": "Threes from anywhere above the corners.",
    "All games": "Every game played this season.",
    "Home": "Home games only.",
    "Away": "Away games only.",
    "Last 5": "The five most recent games.",
    "Made baskets assisted": "Share of made field goals that came off a teammate's assist.",
    "GP": "Games played.",
    "MPG": "Minutes per game.",
    "PTS": "Points per game.",
    "REB": "Rebounds per game.",
    "AST": "Assists per game.",
    "3P%": "Three-pointers made per attempt.",
    "2P%": "Two-pointers made per attempt.",
    "FT%": "Free throws made per attempt.",
    "3PA": "Three-point attempts in the season.",
    "Opp 3P%": "Opponents' three-pointers made per attempt.",
    "Opp 2P%": "Opponents' two-pointers made per attempt.",
    "Opp FT%": "Opponents' free throws made per attempt. Defences have almost no say over it, so it's mostly luck."
  };
  var tipBox = document.createElement("div");
  tipBox.className = "tipbox"; tipBox.setAttribute("role", "tooltip"); tipBox.hidden = true;
  document.body.appendChild(tipBox);
  var TIP_TARGETS = "th, .card b, td.l, .tw td.l, .pctl .lab > span, [data-explain]";
  // data-explain is written for that spot on the page, so it beats the generic TIPS text
  function tipFor(el){
    return el.dataset.explain || TIPS[el.textContent.trim()] || el.dataset.tip || null;
  }
  function markTips(scope){
    (scope || document).querySelectorAll(TIP_TARGETS).forEach(function(el){
      if(el.title){ el.dataset.tip = el.title; el.removeAttribute("title"); }
      if(tipFor(el) && !el.classList.contains("has-tip")){ el.classList.add("has-tip"); el.tabIndex = 0; }
    });
  }
  function showTip(el){
    var text = tipFor(el); if(!text) return;
    tipBox.textContent = text; tipBox.hidden = false;
    var r = el.getBoundingClientRect(), w = tipBox.offsetWidth;
    var left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), document.documentElement.clientWidth - w - 8);
    var top = r.top - tipBox.offsetHeight - 8;
    if(top < 8) top = r.bottom + 8;
    tipBox.style.left = left + window.scrollX + "px";
    tipBox.style.top = top + window.scrollY + "px";
  }
  function hideTip(){ tipBox.hidden = true; }
  document.addEventListener("mouseover", function(e){
    var el = e.target.closest && e.target.closest(".has-tip"); if(el) showTip(el);
  });
  document.addEventListener("mouseout", function(e){
    if(e.target.closest && e.target.closest(".has-tip")) hideTip();
  });
  document.addEventListener("focusin", function(e){ if(e.target.classList && e.target.classList.contains("has-tip")) showTip(e.target); });
  document.addEventListener("focusout", hideTip);
  window.addEventListener("scroll", hideTip, {passive: true});
  markTips();
  window.markTips = markTips;  // the matchup page redraws its tables and calls this again

  // Player search and minutes filter
  var pq = document.getElementById("pq"), pmin = document.getElementById("pmin");
  function pf(){
    var q = (pq.value || "").toLowerCase(), m = pmin.checked;
    document.querySelectorAll("#ptable tbody tr").forEach(function(tr){
      var ok = (!q || tr.dataset.q.indexOf(q) >= 0) && (!m || parseFloat(tr.dataset.mpg) >= 10);
      tr.style.display = ok ? "" : "none";
    });
  }
  if(pq && pmin){ pq.oninput = pf; pmin.onchange = pf; pf(); }
})();
