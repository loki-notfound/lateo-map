# Static files of the site

Copied into the webroot by the site generator (`lateo_map/site.py`).

## Style
The pages use the lateo house style, version 0.2.0 of 2026-11-18. Unchanged
copies: `assets/tokens.css`, `assets/site.css` (its stylesheet for content
sites), `assets/data.css`, `assets/map.css` (with the markers of the kinds of
node, the groups with their ring, the switches, the card, the note and the
zoom buttons), the favicons, the `map.lateo.dev` lockups, `assets/og.png`
(the picture of a link preview), `world-map.svg` (Natural Earth 110m, public
domain), whose outlines the generator puts into the page, and
`assets/world-map-50m.svg` (Natural Earth 50m), which the script fetches when
a visitor zooms in. The mark files fall outside the repository's licence
([TRADEMARKS.md](../../TRADEMARKS.md)).

`assets/map-site.css` holds only the layout of this one page: the controls
above the table and the sortable table. To change the look, change the house
style and copy the new version in.

## Script
`assets/map.js` adds the filters for kind and version, search, sorting,
zooming and moving the map, and the link from a marker to its rows. It draws
the markers itself, so that they follow the filters and come apart when the
map is zoomed in. Every five minutes an open page fetches itself and takes
over the new numbers and rows. The page shows everything without it.

## Headers
`.htaccess` sets the Content Security Policy and the other security headers.
The page has no inline style or script and loads nothing from other hosts.
