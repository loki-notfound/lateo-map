# Static files of the site

Copied into the webroot by the site generator (`lateo_map/site.py`).

## Style
The pages use the lateo house style, version 0.1.0 of 2026-11-17. Unchanged
copies: `assets/tokens.css`, `assets/site.css` (its stylesheet for content
sites), `assets/data.css`, `assets/map.css`, the favicons, the
`map.lateo.dev` lockups and `world-map.svg` (Natural Earth 110m, public
domain), whose outlines the generator puts into the page. The mark files fall
outside the repository's licence ([TRADEMARKS.md](../../TRADEMARKS.md)).

`assets/map-site.css` holds the additions of this site: the markers for the
three kinds of node, the filter controls and the sortable table. To change
the look, change the house style and copy the new version in.

## Script
`assets/map.js` adds filtering, sorting and the link from a marker to its rows.
The page shows everything without it.

## Headers
`.htaccess` sets the Content Security Policy and the other security headers.
The page has no inline style or script and loads nothing from other hosts.
