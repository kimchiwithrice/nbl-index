// Site-wide behaviour: spoiler toggle, theme, local times, row links, table sorting, player filter.
(function(){
  "use strict";
  var root = document.documentElement;
  function store(k, v){ try{ if(v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); }catch(e){ return null; } }

  // Spoiler-free mode, remembered per browser
  var sp = document.getElementById("sp-btn");
  function spLabel(){ if(sp) sp.textContent = root.dataset.sp === "hide" ? "Show scores" : "Hide scores"; }
  spLabel();
  if(sp) sp.onclick = function(){
    root.dataset.sp = root.dataset.sp === "hide" ? "show" : "hide";
    store("nbl-sp", root.dataset.sp); spLabel();
  };
  document.addEventListener("click", function(e){
    var s = e.target.closest && e.target.closest(".sp");
    if(s && root.dataset.sp === "hide"){ s.classList.toggle("peek"); e.preventDefault(); e.stopPropagation(); }
  }, true);

  document.querySelectorAll(".sp-reveal").forEach(function(b){
    b.onclick = function(){ b.closest(".spblock").classList.add("open"); };
  });

  // Light/dark theme
  var th = document.getElementById("th-btn");
  function isDark(){ return root.dataset.theme === "dark" || (!root.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches); }
  function thLabel(){ if(th) th.textContent = isDark() ? "Light mode" : "Dark mode"; }
  thLabel();
  if(th) th.onclick = function(){
    root.dataset.theme = isDark() ? "light" : "dark"; store("nbl-theme", root.dataset.theme); thLabel();
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
