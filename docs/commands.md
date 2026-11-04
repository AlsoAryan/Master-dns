# Commands

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

## Kernel settings

The install writes `/etc/sysctl.d/99-smartdns-tuning.conf` (and, on request, BBR). Besides large
buffers for a long path, it caps unsent data per socket so a download cannot queue a game's login
behind it, and raises the connection-tracking table, whose overflow drops packets without a word.

[فارسی](commands.fa.md) · [Back to the README](../README.md)
