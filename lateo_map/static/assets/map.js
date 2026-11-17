// map.lateo.dev: filtering, sorting and the link between map and table.
// The page works without this file; it only adds convenience.
"use strict";

(function () {
  var table = document.getElementById("node-table");
  if (!table) return;
  var body = table.tBodies[0];
  var rows = Array.prototype.slice.call(body.rows);
  var controls = document.querySelector(".controls");
  var search = document.getElementById("q");
  var reset = document.getElementById("reset");
  var shown = document.getElementById("shown");
  var tip = document.getElementById("tip");
  var markers = Array.prototype.slice.call(document.querySelectorAll(".lt-map-marker"));
  var kinds = {};          // kinds switched on; none means all
  var picked = null;       // ids of the rows picked on the map

  controls.hidden = false;

  function apply() {
    var words = search.value.trim().toLowerCase();
    var anyKind = Object.keys(kinds).length > 0;
    var count = 0;
    rows.forEach(function (row) {
      var ok = (!anyKind || kinds[row.dataset.kind]) &&
               (!picked || picked.indexOf(row.id) !== -1) &&
               (!words || row.textContent.toLowerCase().indexOf(words) !== -1);
      row.hidden = !ok;
      if (ok) count += 1;
    });
    shown.textContent = count === rows.length ? "" : count + " of " + rows.length + " shown";
    reset.hidden = !(anyKind || picked || words);
    markers.forEach(function (marker) {
      marker.classList.toggle("is-selected", !!picked && marker.dataset.rows.split(" ")[0] === picked[0]);
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll(".filter"), function (button) {
    button.addEventListener("click", function () {
      var kind = button.dataset.filter;
      if (kinds[kind]) delete kinds[kind]; else kinds[kind] = true;
      button.setAttribute("aria-pressed", kinds[kind] ? "true" : "false");
      apply();
    });
  });
  search.addEventListener("input", apply);
  reset.addEventListener("click", function () {
    kinds = {};
    picked = null;
    search.value = "";
    Array.prototype.forEach.call(document.querySelectorAll(".filter"), function (button) {
      button.setAttribute("aria-pressed", "false");
    });
    apply();
  });

  // A marker shows its nodes in the table; its label appears under the map.
  markers.forEach(function (marker) {
    function say() {
      tip.textContent = marker.getAttribute("aria-label");
      tip.hidden = false;
    }
    marker.addEventListener("pointerenter", say);
    marker.addEventListener("focus", say);
    marker.addEventListener("click", function (event) {
      event.preventDefault();
      picked = marker.dataset.rows.split(" ");
      apply();
      say();
      document.getElementById("nodes").scrollIntoView({ block: "start" });
    });
  });

  // Sorting: a click on a column heading, a second click reverses.
  Array.prototype.forEach.call(table.tHead.rows[0].cells, function (cell, index) {
    var button = document.createElement("button");
    button.type = "button";
    button.textContent = cell.textContent;
    cell.textContent = "";
    cell.appendChild(button);
    button.addEventListener("click", function () {
      var down = cell.getAttribute("aria-sort") === "ascending";
      Array.prototype.forEach.call(table.tHead.rows[0].cells, function (other) {
        other.removeAttribute("aria-sort");
      });
      cell.setAttribute("aria-sort", down ? "descending" : "ascending");
      function key(row) {
        var td = row.cells[index];
        var value = td.dataset.sort !== undefined ? td.dataset.sort : td.textContent.trim();
        var number = Number(value);
        return value !== "" && !isNaN(number) ? number : value.toLowerCase();
      }
      rows.sort(function (a, b) {
        var x = key(a), y = key(b);
        if (typeof x !== typeof y) { x = String(x); y = String(y); }
        return (x < y ? -1 : x > y ? 1 : 0) * (down ? -1 : 1);
      });
      rows.forEach(function (row) { body.appendChild(row); });
    });
  });
})();
