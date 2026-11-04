<div dir="rtl">

# دستورها

روی سرور `sudo smartdns-menu` همه را نشان می‌دهد؛ پرکاربردها:

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

## تنظیمات کرنل

نصب `/etc/sysctl.d/99-smartdns-tuning.conf` را می‌نویسد (و اگر بخواهید BBR). علاوه بر بافرهای بزرگ برای مسیر
طولانی، داده‌ای را که هنوز فرستاده نشده برای هر سوکت محدود می‌کند تا دانلود ورود بازی را پشت خودش صف نکند، و
جدول ردیابی اتصال را بزرگ‌تر می‌کند که پر شدنش بی‌صدا بسته‌ها را دور می‌ریزد.

[English](commands.md) · [بازگشت به README](../README.fa.md)

</div>
