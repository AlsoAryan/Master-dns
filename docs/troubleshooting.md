# When a game will not start

A name that is not on port 443 cannot go through the relay, which carries only 80 and 443.
And a game that signs in from one address and plays from another is refused. Both show up as
a game that loads and stops, or logs in and is thrown out. The way to find the name:

```sh
sudo smartdns-watch           # which names went through the relay, which went direct
smartdns bypass NAME          # make one direct
smartdns add NAME             # route one through the relay
```

`smartdns-menu` has both. Most of the cases people have reported are already handled; this is
what each one looked like, so you can tell whether yours is one of them.

| What you see | Cause | What is already done |
|---|---|---|
| PS5 connection test says **NAT type: Failed**, games work | The console's STUN server is `us.np.stun.playstation.net`, not under `np.playstation.net`. A PS5 sends an old STUN attribute (0x0021) that coturn answers with `420 Unknown Attribute`, so the relay cannot stand in for Sony. | `stun.playstation.net` goes direct (0.1.2). The console then gets NAT Type 2. |
| GTA Online stops after login, error **0x50060190** | Rockstar's sign-in name went out through the exit, so Rockstar saw the exit's address while the console played from an Iranian one. | `ros.`, `cloud.` and `prod-locator-cloud.rockstargames.com` go direct (0.1.2). The site, Social Club and updates stay routed. |
| THE FINALS: **TFLA0020** at login | Two Embark names were not in its group and went direct. | `embark.net` and `es-dis.net` are in the group (0.1.0). |
| PlayStation or Call of Duty login fails on **Irancell** (DETRICK, PUGET-LAKES) | Irancell chokes part of Akamai's addresses; through the exit the customer is handed one of them. | *Settings, "Names that go out of the Iran relay itself"*: one tap adds the four names (0.1.0). |
| Call of Duty stops at **Networking failed to start** (HUENEME - NEGEV) | Demonware's lobby and STUN are not on 443. | `demonware.net` goes direct; the four HTTPS names under it stay routed in the `cod-https` group. |
| Call of Duty stops at **Checking for update** (HILLCAT) | Four Demonware HTTPS names answer an Iranian address with 403. | They are routed (`cod-https`). A template made before that group existed gets it once after an upgrade. |
| Fortnite will not get into a match | Matchmaking has to come from the address the console plays from. | Epic's backend goes direct, and a few dead addresses are pinned out. |
| EA games: "you must be signed in" | The EA redirector sees the exit's address while the game servers see Iran's. | EA's game stack goes direct. |
| Steam: "Connection Error" that eventually passes | Steam's connection managers are on ports 27018 to 27024. | `steamserver.net` goes direct; the store and community stay routed. |
| PUBG Mobile: "Server is busy", `restrict-area` | The login (`proximabeta.com`, 8085 and 8086) sees an Iranian address. DNS alone cannot change that. | Nothing; a template group for it exists, off by default. |
| War Thunder or War Thunder Mobile loads and stops | Gaijin's game names run on ports such as 20443 and 8090, which the relay does not carry. | Not known per name. Run `smartdns-watch` while it loads: since 0.1.2 it lists, at the end, the names that sent the game to a port the relay does not carry, each with its `smartdns bypass` command. If the login is refused and no such name shows, look at which Gaijin names went direct: the login is then seen from the exit's address and the game from Iran's. |

## Checking the catalogue before a customer does

```sh
python3 tools/audit-catalogue.py
```

It reads the services, games and bypass rules offline and says which name would be routed that
the table above says must go direct, which game needs a group that is gone, and which bypass
dnsmasq would ignore. It cannot tell whether a name answers from Iran or on which port; that
is what `smartdns-watch` is for.

## Taking a new case to us

If your game is not in the table, the most useful thing to send is the output of
`smartdns-watch` from the relay while the game goes from launch to the error, with the exact
error text and the operator. A name is usually one line in `common/bypass.conf` or one entry in
`domains/services.json`.

[فارسی](troubleshooting.fa.md) · [Back to the README](../README.md)
