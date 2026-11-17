// map.lateo.dev: filters, sorting, zooming, and the link between map and table.
// The page works without this file: it then shows the whole world, every
// marker and every row.
"use strict";

(function () {
  var NS = "http://www.w3.org/2000/svg";
  var table = document.getElementById("node-table");
  var svg = document.querySelector(".lt-map-canvas");
  var layer = document.getElementById("markers");
  if (!table || !svg || !layer) return;

  var body = table.tBodies[0];
  var rows = Array.prototype.slice.call(body.rows);
  var controls = document.querySelector(".controls");
  var zoomBox = document.querySelector(".zoom");
  var search = document.getElementById("q");
  var reset = document.getElementById("reset");
  var shown = document.getElementById("shown");
  var tip = document.getElementById("tip");
  // The legend under the map becomes a row of switches: what is switched on
  // is what the map and the table show. The buttons at the table are the same
  // switches again.
  Array.prototype.forEach.call(document.querySelectorAll("#legend li[data-filter]"), function (item) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "filter legend-switch";
    button.dataset.filter = item.dataset.filter;
    button.dataset.value = item.dataset.value;
    while (item.firstChild.firstChild) button.appendChild(item.firstChild.firstChild);
    item.replaceChild(button, item.firstChild);
  });
  var legendHint = document.getElementById("legend-hint");
  if (legendHint) legendHint.hidden = false;
  var buttons = Array.prototype.slice.call(document.querySelectorAll(".filter"));

  // What is switched off, per filter. Everything is on at first except the
  // private nodes: those a visitor switches on.
  function defaults() { return { kind: { "private": true }, release: {} }; }
  var off = defaults();
  var picked = null;                    // ids of the rows picked on the map
  var WORLD = svg.getAttribute("viewBox").split(" ").map(Number);   // x, y, width, height
  var view = WORLD.slice();
  var MAX_ZOOM = 16;

  controls.hidden = false;
  if (zoomBox) zoomBox.hidden = false;

  // ---- the table ----------------------------------------------------------

  function matches(row, withPicked) {
    var words = search.value.trim().toLowerCase();
    return !off.kind[row.dataset.kind] && !off.release[row.dataset.release] &&
           (!withPicked || !picked || picked.indexOf(row.id) !== -1) &&
           (!words || row.textContent.toLowerCase().indexOf(words) !== -1);
  }

  function isDefault() {
    return JSON.stringify(off) === JSON.stringify(defaults());
  }

  function showSwitches() {
    buttons.forEach(function (button) {
      var isOff = off[button.dataset.filter][button.dataset.value];
      button.setAttribute("aria-pressed", isOff ? "false" : "true");
    });
  }

  function apply() {
    var count = 0;
    rows.forEach(function (row) {
      var ok = matches(row, true);
      row.hidden = !ok;
      if (ok) count += 1;
    });
    shown.textContent = count === rows.length ? "" : count + " of " + rows.length + " shown";
    reset.hidden = isDefault() && !picked && !search.value.trim();
    draw();
  }

  buttons.forEach(function (button) {
    button.addEventListener("click", function () {
      var set = off[button.dataset.filter], value = button.dataset.value;
      if (set[value]) delete set[value]; else set[value] = true;
      picked = null;
      showSwitches();
      apply();
    });
  });
  search.addEventListener("input", function () { picked = null; apply(); });
  reset.addEventListener("click", function () {
    off = defaults();
    picked = null;
    search.value = "";
    showSwitches();
    apply();
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

  // ---- the markers --------------------------------------------------------
  // Drawn here instead of taken from the page, so that they follow the
  // filters and come apart when the map is zoomed in.

  function el(name, attributes) {
    var node = document.createElementNS(NS, name);
    Object.keys(attributes || {}).forEach(function (key) { node.setAttribute(key, attributes[key]); });
    return node;
  }

  function size(count) { return count === 1 ? 8 : 8 + Math.min(count, 9) * 0.5; }

  function groupsOf(points, unit) {
    var groups = [], near = 13 * unit;
    points.forEach(function (p) {
      for (var i = 0; i < groups.length; i += 1) {
        var g = groups[i];
        if ((g.x - p.x) * (g.x - p.x) + (g.y - p.y) * (g.y - p.y) <= near * near) {
          g.rows.push(p.row);
          return;
        }
      }
      groups.push({ x: p.x, y: p.y, rows: [p.row] });
    });
    var merged = true;
    while (merged) {
      merged = false;
      for (var a = 0; a < groups.length && !merged; a += 1) {
        for (var b = a + 1; b < groups.length; b += 1) {
          var A = groups[a], B = groups[b];
          var reach = (size(A.rows.length) + size(B.rows.length) - 1) * unit;
          if ((A.x - B.x) * (A.x - B.x) + (A.y - B.y) * (A.y - B.y) < reach * reach) {
            var na = A.rows.length, nb = B.rows.length;
            A.x = (A.x * na + B.x * nb) / (na + nb);
            A.y = (A.y * na + B.y * nb) / (na + nb);
            A.rows = A.rows.concat(B.rows);
            groups.splice(b, 1);
            merged = true;
            break;
          }
        }
      }
    }
    return groups.sort(function (p, q) { return q.rows.length - p.rows.length; });
  }

  function say(text) {
    tip.textContent = text;
    tip.hidden = false;
  }

  function draw() {
    var unit = view[2] / WORLD[2];    // map units per unit of the unzoomed map
    var points = [];
    rows.forEach(function (row) {
      if (row.dataset.x && matches(row, false)) {
        points.push({ x: Number(row.dataset.x), y: Number(row.dataset.y), row: row });
      }
    });
    while (layer.firstChild) layer.removeChild(layer.firstChild);
    groupsOf(points, unit).forEach(function (group) {
      var first = group.rows[0], count = group.rows.length;
      var ids = group.rows.map(function (row) { return row.id; });
      var label;
      if (count === 1) {
        label = first.dataset.title;
      } else {
        var places = {};
        group.rows.forEach(function (row) { places[row.dataset.place] = true; });
        var names = Object.keys(places).sort();
        label = count + " nodes · " + names[0] + (names.length > 1 ? " and nearby" : "");
      }
      var link = el("a", { "class": "lt-map-marker", href: "#" + first.id, "aria-label": label });
      var title = el("title");
      title.textContent = label;
      link.appendChild(title);
      link.appendChild(el("circle", { "class": "mk-hit", cx: group.x, cy: group.y, r: 12 * unit }));
      if (count === 1) {
        var kind = first.dataset.kind;
        link.appendChild(el("use", {
          "class": "mk mk-" + kind + (first.dataset.status === "offline" ? " is-offline" : ""),
          href: "#m-" + kind, x: group.x - 8 * unit, y: group.y - 8 * unit,
          width: 16 * unit, height: 16 * unit
        }));
      } else {
        link.appendChild(el("circle", { "class": "mk-cluster", cx: group.x, cy: group.y,
                                         r: size(count) * unit }));
        var text = el("text", { "class": "mk-cluster-text", x: group.x, y: group.y });
        text.style.fontSize = (11 * unit) + "px";
        text.textContent = count;
        link.appendChild(text);
      }
      if (picked && picked[0] === ids[0] && picked.length === ids.length) {
        link.classList.add("is-selected");
      }
      link.addEventListener("pointerenter", function () { say(label); });
      link.addEventListener("focus", function () { say(label); });
      link.addEventListener("click", function (event) {
        event.preventDefault();
        if (moved) return;
        picked = ids;
        apply();
        say(label);
        document.getElementById("nodes").scrollIntoView({ block: "start" });
      });
      layer.appendChild(link);
    });
  }

  // ---- zooming and moving -------------------------------------------------

  var moved = false;      // a drag just ended: its click is not a pick

  function setView(x, y, width) {
    width = Math.min(WORLD[2], Math.max(WORLD[2] / MAX_ZOOM, width));
    var height = width * WORLD[3] / WORLD[2];
    x = Math.min(WORLD[0] + WORLD[2] - width, Math.max(WORLD[0], x));
    y = Math.min(WORLD[1] + WORLD[3] - height, Math.max(WORLD[1], y));
    view = [x, y, width, height];
    svg.setAttribute("viewBox", view.join(" "));
    svg.classList.toggle("is-zoomed", width < WORLD[2]);
    draw();
  }

  function toMap(clientX, clientY) {
    var box = svg.getBoundingClientRect();
    return [view[0] + (clientX - box.left) / box.width * view[2],
            view[1] + (clientY - box.top) / box.height * view[3]];
  }

  function zoomAt(factor, clientX, clientY) {
    var box = svg.getBoundingClientRect();
    if (clientX === undefined) { clientX = box.left + box.width / 2; clientY = box.top + box.height / 2; }
    var at = toMap(clientX, clientY);
    var width = Math.min(WORLD[2], Math.max(WORLD[2] / MAX_ZOOM, view[2] / factor));
    var ratio = width / view[2];
    setView(at[0] - (at[0] - view[0]) * ratio, at[1] - (at[1] - view[1]) * ratio, width);
  }

  if (zoomBox) {
    zoomBox.addEventListener("click", function (event) {
      var action = event.target.dataset ? event.target.dataset.zoom : null;
      if (action === "in") zoomAt(2);
      if (action === "out") zoomAt(0.5);
      if (action === "reset") setView(WORLD[0], WORLD[1], WORLD[2]);
    });
  }

  svg.addEventListener("dblclick", function (event) {
    event.preventDefault();
    zoomAt(2, event.clientX, event.clientY);
  });

  // The wheel zooms only with Ctrl or the command key held, so that the page
  // still scrolls past the map.
  svg.addEventListener("wheel", function (event) {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    zoomAt(event.deltaY < 0 ? 1.25 : 0.8, event.clientX, event.clientY);
  }, { passive: false });

  var fingers = {};       // pointers down on the map
  var last = null;        // distance and centre of two fingers

  function span() {
    var ids = Object.keys(fingers);
    var a = fingers[ids[0]], b = fingers[ids[1]];
    return { d: Math.hypot(a.x - b.x, a.y - b.y), x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  }

  svg.addEventListener("pointerdown", function (event) {
    fingers[event.pointerId] = { x: event.clientX, y: event.clientY };
    moved = false;
    if (Object.keys(fingers).length === 2) last = span();
  });
  svg.addEventListener("pointermove", function (event) {
    var before = fingers[event.pointerId];
    if (!before) return;
    var count = Object.keys(fingers).length;
    var dx = event.clientX - before.x, dy = event.clientY - before.y;
    fingers[event.pointerId] = { x: event.clientX, y: event.clientY };
    if (count === 2) {
      var now = span();
      if (last && last.d > 0) zoomAt(now.d / last.d, now.x, now.y);
      last = now;
      moved = true;
    } else if (count === 1 && view[2] < WORLD[2] && (moved || Math.abs(dx) + Math.abs(dy) > 3)) {
      var box = svg.getBoundingClientRect();
      if (!moved) { moved = true; svg.setPointerCapture(event.pointerId); }
      setView(view[0] - dx / box.width * view[2], view[1] - dy / box.height * view[3], view[2]);
    }
  });
  function release(event) {
    delete fingers[event.pointerId];
    last = null;
    setTimeout(function () { moved = false; }, 0);
  }
  svg.addEventListener("pointerup", release);
  svg.addEventListener("pointercancel", release);

  svg.setAttribute("tabindex", "0");
  svg.addEventListener("keydown", function (event) {
    var step = view[2] / 8;
    var keys = { "+": function () { zoomAt(2); }, "=": function () { zoomAt(2); },
                 "-": function () { zoomAt(0.5); }, "0": function () { setView(WORLD[0], WORLD[1], WORLD[2]); },
                 ArrowLeft: function () { setView(view[0] - step, view[1], view[2]); },
                 ArrowRight: function () { setView(view[0] + step, view[1], view[2]); },
                 ArrowUp: function () { setView(view[0], view[1] - step, view[2]); },
                 ArrowDown: function () { setView(view[0], view[1] + step, view[2]); } };
    if (event.target === svg && keys[event.key]) {
      event.preventDefault();
      keys[event.key]();
    }
  });

  showSwitches();
  apply();
})();
