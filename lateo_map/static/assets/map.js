// map.lateo.dev: filters, sorting, zooming, and the link between map and table.
// The page works without this file: it then shows the whole world, every
// marker and every row.
"use strict";

(function () {
  var NS = "http://www.w3.org/2000/svg";
  var table = document.getElementById("node-table");
  var svg = document.querySelector(".lt-map-canvas");
  var layer = document.getElementById("markers");
  // Without these the page is not one this script knows; it then stays as
  // the server wrote it. Everything else on the page is optional below, so
  // that a page written by an older or newer generator still gets its map.
  if (!table || !svg || !layer || !table.tBodies[0] || !table.tHead) return;

  var body = table.tBodies[0];
  var rows = Array.prototype.slice.call(body.rows);
  var controls = document.querySelector(".controls");
  var zoomBox = document.querySelector(".lt-map-zoom");
  var search = document.getElementById("q");
  var reset = document.getElementById("reset");
  var shown = document.getElementById("shown");
  var tip = document.getElementById("tip");
  var card = document.getElementById("hover");
  var frame = document.querySelector(".lt-map-frame");
  var clear = document.querySelector("[data-zoom=clear]");
  // The legend under the map becomes a row of switches: what is switched on
  // is what the map and the table show. The buttons at the table are the same
  // switches again.
  Array.prototype.forEach.call(document.querySelectorAll("#legend li[data-filter]"), function (item) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "lt-map-switch";
    button.dataset.filter = item.dataset.filter;
    button.dataset.value = item.dataset.value;
    while (item.firstChild.firstChild) button.appendChild(item.firstChild.firstChild);
    item.replaceChild(button, item.firstChild);
  });
  var legendHint = document.getElementById("legend-hint");
  if (legendHint) legendHint.hidden = false;
  var buttons = Array.prototype.slice.call(document.querySelectorAll(".lt-map-switch"));

  // The kinds that are switched off. Every kind is on at first except the
  // private nodes: those a visitor switches on. The version is a separate
  // choice (all, or one), so that switching kinds off never hides a version.
  function defaults() { return { kind: { "private": true } }; }
  var off = defaults();
  var version = document.getElementById("version");
  var nothing = document.getElementById("nothing");
  var picked = null;                    // ids of the rows picked on the map
  var WORLD = svg.getAttribute("viewBox").split(" ").map(Number);   // x, y, width, height
  var view = WORLD.slice();
  var MAX_ZOOM = 16;
  var coarse = !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
  function docked() { return !!card && card.classList.contains("is-docked"); }

  if (controls) controls.hidden = false;
  if (zoomBox) zoomBox.hidden = false;

  // ---- the table ----------------------------------------------------------

  function matches(row, withPicked) {
    var words = search ? search.value.trim().toLowerCase() : "";
    return !off.kind[row.dataset.kind] &&
           (!version || !version.value || version.value === row.dataset.release) &&
           (!withPicked || !picked || picked.indexOf(row.id) !== -1) &&
           (!words || row.textContent.toLowerCase().indexOf(words) !== -1);
  }

  function isDefault() {
    return JSON.stringify(off) === JSON.stringify(defaults());
  }

  function showSwitches() {
    buttons.forEach(function (button) {
      var isOff = (off[button.dataset.filter] || {})[button.dataset.value];
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
    if (shown) shown.textContent = count === rows.length ? "" : count + " of " + rows.length + " shown";
    if (reset) reset.hidden = isDefault() && !picked && !(search && search.value.trim()) &&
                              !(version && version.value);
    if (nothing) nothing.hidden = count > 0;
    showPicked();
    draw();
  }

  buttons.forEach(function (button) {
    button.addEventListener("click", function () {
      var set = off[button.dataset.filter], value = button.dataset.value;
      if (!set) return;
      if (set[value]) delete set[value]; else set[value] = true;
      picked = null;
      showSwitches();
      apply();
    });
  });
  if (search) search.addEventListener("input", function () { picked = null; apply(); });
  if (version) version.addEventListener("change", function () { picked = null; apply(); });
  if (reset) reset.addEventListener("click", function () {
    off = defaults();
    picked = null;
    if (search) search.value = "";
    if (version) version.value = "";
    showSwitches();
    apply();
  });

  // Sorting: a click on a column heading, a second click reverses.
  var sorted = null;      // { index, down } once a heading was clicked

  function sortRows() {
    if (!sorted) return;
    var index = sorted.index, down = sorted.down;
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
  }

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
      sorted = { index: index, down: down };
      sortRows();
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
  function ring(group) { return group.rows.length > 1 ? 4 : 0; }

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
          var reach = (size(A.rows.length) + size(B.rows.length) + ring(A) + ring(B) - 1) * unit;
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

  // ---- what a marker tells ---------------------------------------------------

  function cell(row, index) { return row.cells[index].textContent.trim(); }

  function line(parent, name, value, className) {
    if (!value) return;
    var p = document.createElement("p");
    if (className) p.className = className;
    if (name) {
      var label = document.createElement("span");
      label.className = "lt-map-card-label";
      label.textContent = name;
      p.appendChild(label);
    }
    p.appendChild(document.createTextNode(value));
    parent.appendChild(p);
  }

  // Everything the table has about the nodes of one marker, as a small card.
  function fill(group, label) {
    while (card.firstChild) card.removeChild(card.firstChild);
    if (group.length === 1) {
      var row = group[0];
      line(card, "", row.dataset.kind === "private" ? "Private node" : cell(row, 0), "lt-map-card-title");
      line(card, "Kind", cell(row, 1));
      line(card, "Ports", cell(row, 2));
      line(card, "Place", cell(row, 3));
      line(card, "Network", cell(row, 4));
      line(card, "Version", cell(row, 5));
      line(card, "Height", cell(row, 6));
      line(card, "Status", cell(row, 7));
      line(card, "Last answer", cell(row, 8));
    } else {
      line(card, "", label, "lt-map-card-title");
      group.slice(0, 8).forEach(function (row) {
        var name = row.dataset.kind === "private" ? "Private node" : cell(row, 0);
        line(card, "", [name, cell(row, 1), cell(row, 5), cell(row, 7)].filter(Boolean).join(" · "));
      });
      if (group.length > 8) line(card, "", "and " + (group.length - 8) + " more", "lt-map-card-more");
      line(card, "", "Click to list them in the table.", "lt-map-card-more");
    }
  }

  function showCard(group, label, x, y) {
    if (!card || !frame) return;
    fill(group, label);
    card.hidden = false;
    var box = frame.getBoundingClientRect();
    var left = x - box.left + 14, top = y - box.top + 14;
    if (left + card.offsetWidth > box.width - 8) left = x - box.left - card.offsetWidth - 14;
    if (top + card.offsetHeight > box.height - 8) top = y - box.top - card.offsetHeight - 14;
    card.style.left = Math.max(8, left) + "px";
    card.style.top = Math.max(8, top) + "px";
  }

  function hideCard() { if (card && !card.classList.contains("is-docked")) card.hidden = true; }

  // Under a finger there is no hovering: the card of what was tapped stands
  // under the map until the pick is let go.
  function dock(group, label) {
    if (!card) return;
    fill(group, label);
    card.style.left = card.style.top = "";
    card.classList.add("is-docked");
    card.hidden = false;
  }
  function undock() {
    if (!card || !card.classList.contains("is-docked")) return;
    card.classList.remove("is-docked");
    card.hidden = true;
  }

  // A note on the map says what is picked, with the way to the table.
  function showPicked() {
    if (clear) clear.hidden = !picked;
    if (!tip) return;
    while (tip.firstChild) tip.removeChild(tip.firstChild);
    tip.hidden = !picked;
    if (!picked) { undock(); return; }
    var count = picked.length;
    tip.appendChild(document.createTextNode(
      (count === 1 ? "1 node" : count + " nodes") + " picked. "));
    var link = document.createElement("a");
    link.href = "#nodes";
    link.textContent = count === 1 ? "See it in the table" : "See them in the table";
    tip.appendChild(link);
    tip.appendChild(document.createTextNode("."));
  }

  function same(a, b) {
    return !!a && !!b && a.length === b.length && a.every(function (id, i) { return id === b[i]; });
  }

  function draw() {
    // The size of markers: on a wide screen they grow with the map, as a map
    // of a thousand units across draws them; on a screen narrower than that
    // they keep the size of a thousand pixels, so that a phone shows them as
    // large as a desk does. Zooming never changes their size for the eye.
    var across = svg.getBoundingClientRect().width || WORLD[2];
    var unit = view[2] / Math.min(across, WORLD[2]);
    var points = [];
    rows.forEach(function (row) {
      if (row.dataset.x && matches(row, false)) {
        points.push({ x: Number(row.dataset.x), y: Number(row.dataset.y), row: row });
      }
    });
    while (layer.firstChild) layer.removeChild(layer.firstChild);
    hideCard();
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
      // No <title> inside: the browser would show it as a second, small
      // tooltip beside the card. The label is there for screen readers.
      var link = el("a", { "class": "lt-map-marker", href: "#" + first.id, "aria-label": label });
      // the target of a marker: larger than the marker, and larger still for a finger
      link.appendChild(el("circle", { "class": "lt-map-hit", cx: group.x, cy: group.y,
                                       r: Math.max(size(count) + 7, coarse ? 22 : 0) * unit }));
      if (count === 1) {
        var kind = first.dataset.kind;
        link.appendChild(el("use", {
          "class": "lt-map-kind lt-map-kind-" + kind + (first.dataset.status === "offline" ? " is-offline" : ""),
          href: "#lt-marker-" + kind, x: group.x - 8 * unit, y: group.y - 8 * unit,
          width: 16 * unit, height: 16 * unit
        }));
      } else {
        link.appendChild(el("circle", { "class": "lt-map-group", cx: group.x, cy: group.y,
                                         r: size(count) * unit }));
        // A ring around the group shows which kinds are in it, each kind an
        // arc as long as its share.
        var shares = {}, start = 0;
        group.rows.forEach(function (row) { shares[row.dataset.kind] = (shares[row.dataset.kind] || 0) + 1; });
        ["p2p", "rpc", "both", "private"].forEach(function (kind) {
          if (!shares[kind]) return;
          var part = shares[kind] / count * 100;
          var arc = el("circle", {
            "class": "lt-map-ring lt-map-ring-" + kind, cx: group.x, cy: group.y,
            r: (size(count) + 2.5) * unit, pathLength: 100,
            "stroke-dasharray": part + " " + (100 - part), "stroke-dashoffset": -start,
            transform: "rotate(-90 " + group.x + " " + group.y + ")"
          });
          // in map units, so that the arcs keep their length when zoomed
          arc.style.strokeWidth = (3.5 * unit) + "px";
          link.appendChild(arc);
          start += part;
        });
        var text = el("text", { "class": "lt-map-group-text", x: group.x, y: group.y });
        text.style.fontSize = (11 * unit) + "px";
        text.textContent = count;
        link.appendChild(text);
      }
      if (same(picked, ids)) link.classList.add("is-selected");
      link.addEventListener("pointerenter", function (event) {
        if (event.pointerType === "touch" || docked()) return;
        showCard(group.rows, label, event.clientX, event.clientY);
      });
      link.addEventListener("pointermove", function (event) {
        if (card && !card.hidden && !docked()) showCard(group.rows, label, event.clientX, event.clientY);
      });
      link.addEventListener("pointerleave", hideCard);
      link.addEventListener("focus", function () {
        var box = link.getBoundingClientRect();
        showCard(group.rows, label, box.right, box.bottom);
      });
      link.addEventListener("blur", hideCard);
      link.addEventListener("click", function (event) {
        event.preventDefault();
        if (moved) return;
        // a second click on the same marker lets go of it
        picked = same(picked, ids) ? null : ids;
        var byFinger = touched;
        apply();
        if (picked && byFinger) dock(group.rows, label);
      });
      layer.appendChild(link);
    });
  }

  // ---- zooming and moving -------------------------------------------------

  var moved = false;      // a drag just ended: its click is not a pick

  // The shape of the map on the screen, width by height. On an upright
  // phone the world's own shape leaves a strip too low to read, so the map
  // is higher there and opens on the part of the world with the most nodes;
  // the button for the whole world shows it all, with sea above and below.
  var shape = WORLD[2] / WORLD[3];
  function wantedShape() {
    var upright = window.innerWidth < 640 && window.innerHeight > window.innerWidth;
    return upright ? 4 / 3 : WORLD[2] / WORLD[3];
  }

  function setView(x, y, width) {
    width = Math.min(WORLD[2], Math.max(WORLD[2] / MAX_ZOOM, width));
    var height = width / shape;
    x = Math.min(WORLD[0] + WORLD[2] - width, Math.max(WORLD[0], x));
    if (height >= WORLD[3]) y = WORLD[1] - (height - WORLD[3]) / 2;     // all of it, centred
    else y = Math.min(WORLD[1] + WORLD[3] - height, Math.max(WORLD[1], y));
    view = [x, y, width, height];
    svg.setAttribute("viewBox", view.join(" "));
    svg.classList.toggle("is-zoomed", width < WORLD[2]);
    if (width <= WORLD[2] / 3) finer();
    draw();
  }

  // The page carries the coarse outlines of the world. Zoomed in three times
  // or more, the finer ones are fetched once and put in their place.
  var detail = svg.dataset.detail ? "wanted" : "none";
  function finer() {
    if (detail !== "wanted") return;
    detail = "asked";
    fetch(svg.dataset.detail, { credentials: "omit" })
      .then(function (response) { return response.ok ? response.text() : Promise.reject(); })
      .then(function (text) {
        var fine = new DOMParser().parseFromString(text, "image/svg+xml");
        [["lt-map-land", ".lt-map-land"], ["lt-map-borders", ".lt-map-borders"]].forEach(function (pair) {
          var from = fine.getElementById(pair[0]), to = svg.querySelector("path" + pair[1]);
          if (from && to && from.getAttribute("d")) to.setAttribute("d", from.getAttribute("d"));
        });
        detail = "there";
      })
      .catch(function () { detail = "wanted"; });
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
      var button = event.target.closest ? event.target.closest("button") : null;
      var action = button ? button.dataset.zoom : null;
      if (action === "in") zoomAt(2);
      if (action === "out") zoomAt(0.5);
      if (action === "reset") setView(WORLD[0], WORLD[1], WORLD[2]);
      if (action === "clear") { picked = null; apply(); }
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

  var touched = false;    // the last pointer on the map was a finger
  var fingers = {};       // pointers down on the map
  var last = null;        // distance and centre of two fingers

  function span() {
    var ids = Object.keys(fingers);
    var a = fingers[ids[0]], b = fingers[ids[1]];
    return { d: Math.hypot(a.x - b.x, a.y - b.y), x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  }

  svg.addEventListener("pointerdown", function (event) {
    touched = event.pointerType === "touch" || event.pointerType === "pen";
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
      // two fingers zoom the map and move it; one finger up or down is left
      // to the page, so that a visitor can scroll past the map
      var now = span();
      if (last && last.d > 0) {
        zoomAt(now.d / last.d, now.x, now.y);
        var area = svg.getBoundingClientRect();
        setView(view[0] - (now.x - last.x) / area.width * view[2],
                view[1] - (now.y - last.y) / area.height * view[3], view[2]);
      }
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

  // Where the map opens: the whole world, or on an upright phone the part
  // with the most nodes, as high as the world is.
  function home() {
    shape = wantedShape();
    var width = Math.min(WORLD[2], WORLD[3] * shape);
    var xs = rows.filter(function (row) { return row.dataset.x && !row.hidden; })
                 .map(function (row) { return Number(row.dataset.x); })
                 .sort(function (a, b) { return a - b; });
    var middle = xs.length ? xs[Math.floor(xs.length / 2)] : WORLD[0] + WORLD[2] / 2;
    setView(middle - width / 2, WORLD[1], width);
  }

  // a turned phone or a resized window: another shape, or markers sized anew
  var resized = null;
  window.addEventListener("resize", function () {
    clearTimeout(resized);
    resized = setTimeout(function () {
      if (wantedShape() !== shape) home(); else draw();
    }, 150);
  });

  // on a touch screen the hint speaks of fingers, not of a mouse
  var hint = document.getElementById("legend-hint");
  if (hint && coarse) {
    hint.textContent = " Tap a kind to show or hide it. Zoom and move the map with two fingers or the buttons.";
  }

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

  // ---- staying current -----------------------------------------------------
  // The site is written anew every five minutes. An open page fetches itself
  // at that pace and takes over the numbers and the rows, keeping the
  // switches, the search, the order, the pick and the view as they are. When
  // the page itself changed (other switches, a new script) it reloads.

  var REFRESH = 5 * 60 * 1000;
  var fetched = Date.now();

  function signature(doc) {
    var parts = [];
    Array.prototype.forEach.call(doc.querySelectorAll(".controls .lt-map-switch, script[src]"), function (node) {
      parts.push(node.getAttribute("src") || node.dataset.filter + "=" + node.dataset.value);
    });
    return parts.join("|");
  }

  // The versions on offer may change between two rounds; no reload for that.
  function takeVersions(doc) {
    var fresh = doc.getElementById("version");
    if (!fresh || !version) return;
    var chosen = version.value;
    while (version.firstChild) version.removeChild(version.firstChild);
    Array.prototype.forEach.call(fresh.options, function (option) {
      version.appendChild(document.importNode(option, true));
    });
    version.value = chosen;
    if (version.value !== chosen) version.value = "";
  }


  function takeOver(doc) {
    if (signature(doc) !== signature(document)) { location.reload(); return; }
    [".lt-stat-grid", ".summary", ".lead", "#nodes-caption"].forEach(function (selector) {
      var fresh = doc.querySelector(selector), old = document.querySelector(selector);
      if (!fresh || !old) return;
      while (old.firstChild) old.removeChild(old.firstChild);
      Array.prototype.forEach.call(fresh.childNodes, function (node) {
        old.appendChild(document.importNode(node, true));
      });
    });
    takeVersions(doc);
    var freshBody = doc.querySelector("#node-table tbody");
    if (!freshBody) return;
    while (body.firstChild) body.removeChild(body.firstChild);
    rows = Array.prototype.map.call(freshBody.rows, function (row) {
      return body.appendChild(document.importNode(row, true));
    });
    if (picked) {
      picked = picked.filter(function (id) { return document.getElementById(id); });
      if (!picked.length) picked = null;
    }
    sortRows();
    apply();
    checkAge();
  }

  function refresh() {
    if (document.hidden || Date.now() - fetched < REFRESH - 5000) return;
    fetched = Date.now();
    fetch(location.pathname, { cache: "no-store", credentials: "omit" })
      .then(function (response) { return response.ok ? response.text() : null; })
      .then(function (text) {
        if (text) takeOver(new DOMParser().parseFromString(text, "text/html"));
      })
      .catch(function () { /* the next round tries again */ });
  }

  // When the site itself stops being written, say so instead of showing old
  // numbers as if they were current.
  var stale = document.getElementById("stale");
  function checkAge() {
    var updated = document.getElementById("updated");
    if (!stale || !updated) return;
    var age = Date.now() / 1000 - Number(updated.dataset.epoch);
    stale.hidden = !(age > 20 * 60);
  }
  setInterval(checkAge, 60 * 1000);
  checkAge();

  setInterval(refresh, REFRESH);
  document.addEventListener("visibilitychange", refresh);

  // On a narrow screen a row is a small block that opens for the rest of
  // its details. Without this script every detail is shown.
  table.classList.add("can-open");
  body.addEventListener("click", function (event) {
    var target = event.target;
    if (target.closest && (target.closest("a") || target.closest("button"))) return;
    var row = target.closest ? target.closest("tr") : null;
    if (row) row.classList.toggle("is-open");
  });

  showSwitches();
  apply();
  if (wantedShape() !== shape) home();
})();
