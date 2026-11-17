# Security

## Reporting
Report a vulnerability privately through
[GitHub's private vulnerability reporting](https://github.com/loki-notfound/lateo-map/security/advisories/new)
for this repository. Please do not open a public issue for it. Expect a first
answer within a week; this is a one-person project.

## What belongs here
- The collector: something that makes it contact addresses it should not, send
  more than its stated calls, or exceed its stated pace.
- The site of map.lateo.dev: something that shows more about a node than the
  README says, or that runs code in a visitor's browser.
- The handling of data the collector stores.

## What belongs elsewhere
- A flaw in the Zephyr daemon:
  [ZephyrProtocol](https://github.com/ZephyrProtocol/zephyr). A daemon flaw
  inherited from Monero is best reported to Monero's vulnerability response
  process first.
- A wrong location or network operator for an address: the source of that
  data, [DB-IP](https://db-ip.com).

## Supported versions
The current `main`, which is what map.lateo.dev runs.
