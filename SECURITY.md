# Security policy

## Reporting a vulnerability

Please report security problems privately through GitHub:
**[Report a vulnerability](https://github.com/zkm/skywatch/security/advisories/new)**
(the repository's Security tab → "Report a vulnerability").

Please don't open a public issue for a security problem. Include what you found,
how to reproduce it, and what an attacker could do with it. You should hear
back within a week. Once a fix is out, you'll be credited in the advisory
unless you'd rather not be.

## Supported versions

Fixes go to `main`, which deploys to https://skywatch.zkm.tw automatically.
Only the latest `main` is supported. Tagged
[releases](https://github.com/zkm/skywatch/releases) mark milestones and
don't get separate patches.

## Scope

In scope: the code in this repository, run as documented. For example:

- Script injection (XSS) through message data, transcripts, callsigns or ADS-B fields
- Getting around the crowd-correction rules: voting as many listeners from one
  client, bypassing the rate limit, or changing a copy without votes
- Subscribing, unsubscribing or sending push notifications for someone else
- Reading or writing files outside `DATA_DIR`, or anything that runs code on the server
- Leaking the VAPID private key, the voter salt, or listeners' IP addresses

Out of scope:

- The content of HFGCS broadcasts and ADS-B data. They're public by design, and
  EAMs are encrypted at the source; Skywatch doesn't decode them.
- Problems in third-party services (KiwiSDR receivers, adsb.lol, push services,
  map tiles) or in dependencies, unless Skywatch uses them unsafely. Report
  those upstream.
- Denial of service by flooding a self-hosted instance, and deployments that
  ignore the guidance in the README (no HTTPS, or `TRUST_PROXY=1` without a
  trusted proxy in front).
- The static demo site, which only serves bundled sample data.

## Running it safely

See [Deploying](README.md#deploying): serve it over HTTPS, set `TRUST_PROXY=1`
only behind a proxy you control, and keep `DATA_DIR` private and backed up.
It holds the push signing key and the salt that keeps listeners' IPs out of the
database.
