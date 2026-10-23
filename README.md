<div align="center">

# Master DNS

**A smart-DNS service you can sell: relay, exit, panels, quotas and a bot, installed with one script.**

*[فارسی](README.fa.md)*

</div>

Master DNS lets people in Iran reach services that block them. A **relay**
inside the country answers DNS for the blocked domains with its own address
and carries the connections abroad through an **exit** server. Everything else
resolves normally and goes direct.

It is a whole service, not only a proxy: per-customer access control, traffic
quotas, speed limits, a customer panel, an operator panel, selling, a Telegram
bot and an API. No dependencies beyond what Debian ships.

```
 customer ──DNS──▶ relay (Iran) ──TLS passthrough──▶ exit (abroad) ──▶ service
 PS5 / phone       dnsmasq + nginx + nftables         nginx (SNI)
```

The relay never terminates TLS: nginx reads the SNI from the handshake and
opens a TCP tunnel to the exit, so nothing is decrypted and no certificate is
shown to the client. Port 80 is forwarded as well, because console game
packages are served over plain HTTP.

## Install

```sh
curl -fsSLO https://raw.githubusercontent.com/AlsoAryan/Master-dns/main/master-dns.sh
sudo bash master-dns.sh
```

It downloads first instead of piping, on purpose: the configs live inside the
script and it asks questions. It refuses to run if the file is cut short.

| Role | Where | What it does |
|---|---|---|
| **exit** | a server abroad | the panel, the database, the way out. Install it first; it prints a pairing token |
| **relay** | a server in Iran | answers DNS, serves the customer panel. Paste the token when asked |
| **single** | one server abroad | all of the above on one machine, `ROLE=single`. Good for trying it out |
| **node** | another server abroad | one more exit, added from the admin panel's *Nodes* page |

The installer numbers and times each step and ends with a summary. It is safe
to re-run, backs up what it replaces, and `sudo bash master-dns.sh --uninstall`
puts the machine back, asking before it removes the database or packages.
`sudo smartdns-menu` lists every command afterwards.

Open in the firewall: **tcp** 22, 53, 80, 443, 853, 1119, 4070, 8443 and the
admin port (9443 by default, and never 8446, which the exit keeps for its own
route to Google over IPv6); **udp** 53, 3478.

## What is in it

- **Routing**: about 480 blocked domains, game and console services, AAAA
  filtering, STUN/TURN so console NAT checks pass.
- **Customers**: an allowlist on their address with kernel byte counters,
  monthly or one-off quotas with warnings at 80% and 95%, per-customer speed
  caps, service templates down to single domains.
- **Selling**: plans, receipts, discount codes, a free trial, a wallet,
  invitation links, extra devices, and resellers with their own plans and bot.
- **Panels**: a dashboard-style operator panel and a customer panel in Persian or
  English, light and dark, with a blue, pink, green or red accent. The public
  forms are guarded against bots and floods: a hidden trap field, a signed
  timer and a per-address limit, none of which a person notices.
- **Encrypted DNS**: DoH and DoT per server, an address per customer, an iPhone
  profile, a Windows 11 command. WireGuard for where DNS alone is not enough.
- **Scale**: more exits and single servers on one panel, a standby that takes
  over, monitoring alerts, encrypted backups, one-button upgrades.
- **Telegram bot** for customers and operators, set up from the admin panel.

## API

A sales bot of your own can plug into the panel: [docs/bot-api.md](docs/bot-api.md)
(Persian). To move one customer's days or traffic by hand, from an admin key:

```sh
curl -X POST https://PANEL/api/v1/admin/users/42/adjust \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"days": 7, "gb": -2}'
```

`days` and `gb` (or `bytes`) take a plus or a minus, together or alone. An
unlimited account is never made limited by it, and a quota is never taken to
zero, because zero means unlimited.

## Commands

On a server, `sudo smartdns-menu` lists everything; the ones used most:

```sh
smartdns status                  # what this machine is doing
smartdns list                    # every domain being routed
smartdns find spotify            # which ones match
smartdns add example.com         # route one more
smartdns del example.com         # stop routing it
smartdns test example.com        # what this relay answers for it
smartdns-rules show test         # the full lists for one template
smartdns-rules check gemini.google.com
smartdns-acl list                # everyone, with usage
smartdns-acl usage 5.188.44.19   # one address
smartdns-acl add 5.188.44.19 ali # register one by hand
smartdns-acl del 5.188.44.19
smartdns-acl reset <ip>|--all    # zero the counters
smartdns-acl enforce status      # open, or only registered addresses?
smartdns-acl enforce on          # close it
smartdns-acl enforce off         # open it to everyone
smartdns-acl save                # persist to disk now
smartdns-shape list              # what is in force
smartdns-shape off               # remove all shaping
smartdns-cert panel.example.com  # get or renew a certificate for that name
smartdns-access username ali     # change the owner's username
smartdns-access port 9443        # move the panel, if a firewall is in the way
smartdns-access path [new]       # change the secret path, or roll a fresh one
smartdns-access password [new]   # set a new one; the old is not recoverable
smartdns-access rotate           # new path and new password at once
```

## Build and test

`master-dns.sh` is generated. The logic is in `tools/installer-logic.sh`, the
configs in `templates/`, `common/` and `domains/`.

```sh
python3 tools/build-installer.py   # rewrites master-dns.sh
bash -n master-dns.sh              # it stays valid bash
for t in tools/test-*.py; do python3 "$t"; done
```

## Known limits

- Only download is shaped, not upload.
- A relay without a certificate has no customer panel; it will not serve a
  password page over plain HTTP. Give it a domain.
- Sign-up alone gives no traffic; the account needs a plan.
- Traffic costs double: a customer's gigabyte is about two on the relay and two
  on the exit.

## Credits

- **[BackPack](https://github.com/AminMGMT/BackPack)**, by **Amin Mohammadi**
  ([@AminMGMT](https://github.com/AminMGMT)), carries the optional tunnel
  between the relay and the exit. It is his work, released under the AGPL-3.0:
  this project only downloads his own unmodified releases, checks them against
  a pinned hash, and runs them. Thank you, Amin.
- **[Vazirmatn](https://github.com/rastikerdar/vazirmatn)**, by **Saber Rastikerdar**,
  is the font of both panels. It is released under the SIL Open Font License 1.1
  and travels inside the installer with its licence. With thanks to him and the
  Vazirmatn project.
- The operator shown for each customer in the admin panel comes from
  [RIPEstat](https://stat.ripe.net/), RIPE NCC's public routing data.
- The services' logos on a template's page are from **[Simple Icons](https://simpleicons.org/)**
  (CC0), and the plain icons for rows that are no brand from **[Lucide](https://lucide.dev/)**
  (ISC). Each logo is its owner's trademark.
- **[HyperDNS](https://github.com/IzumiRain/HyperDNS)**, by **Rain**
  ([@IzumiRain](https://github.com/IzumiRain)), a standalone smart DNS written in Go
  and released under the AGPL-3.0. Reading it showed us what Doctor DNS was
  missing, and several features here began as its ideas: DNS over HTTPS and over
  TLS, with the customer usage dashboard and the choice of public resolvers planned
  beside them (0.7.0); the sealed, encrypted receipts (0.7.2); the DNS report that
  says which rule each name met (0.7.3); an answer that says the resolver failed,
  rather than silence (0.7.4); blocked domains and the single-server install (0.8.0);
  the services' logos, and groups of your own domains with an icon (0.9.10).
  Doctor DNS used none of its code - each was written here, in Python, for this
  design - but the ideas were theirs first. Thank you, Rain.
- The installer's check of whose name an exit's address is registered in asks the
  regional internet registries' public RDAP service, through [rdap.org](https://rdap.org/).
- The exit's nginx configuration started from
  [rohammosalli/smart-dns](https://github.com/rohammosalli/smart-dns).

## Licence

MIT, see [LICENSE](LICENSE). Master DNS is a fork of Doctor DNS, which is MIT
as well; its copyright notice stays in the licence file.
