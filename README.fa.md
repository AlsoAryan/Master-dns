<div align="center" dir="rtl">

# مستر دی‌ان‌اس

**سرویس اسمارت DNS قابل‌فروش: ریلی، اگزیت، پنل‌ها، سهمیه و ربات، با یک اسکریپت.**

*[English](README.md)*

</div>

<div dir="rtl">

مستر دی‌ان‌اس به کاربرهای ایران اجازه می‌دهد به سرویس‌هایی که تحریم‌اند برسند.
یک **ریلی** داخل ایران جواب DNS دامنه‌های تحریمی را با آدرس خودش می‌دهد و
اتصال‌ها را از راه یک **اگزیت** در خارج می‌رساند. بقیهٔ سایت‌ها عادی باز
می‌شوند و مستقیم می‌روند.

فقط یک پروکسی نیست؛ یک سرویس کامل است: کنترل دسترسی هر مشتری، حجم و سهمیه،
محدودیت سرعت، پنل مشتری، پنل ادمین، فروش، ربات تلگرام و API. به چیزی جز
بسته‌های خود دبیان نیاز ندارد.

```
 مشتری ──DNS──▶ ریلی (ایران) ──TLS بدون باز کردن──▶ اگزیت (خارج) ──▶ سرویس
 PS5 / گوشی     dnsmasq + nginx + nftables          nginx (SNI)
```

ریلی هیچ‌وقت TLS را باز نمی‌کند؛ nginx فقط SNI را می‌خواند و یک تونل TCP به
اگزیت باز می‌کند. پس چیزی رمزگشایی نمی‌شود و گواهی‌ای به مشتری نشان داده
نمی‌شود. پورت ۸۰ هم رد می‌شود چون بستهٔ بازی‌های کنسول روی HTTP ساده می‌آید.

## نصب

```sh
curl -fsSLO https://raw.githubusercontent.com/AlsoAryan/Master-dns/main/master-dns.sh
sudo bash master-dns.sh
```

عمداً اول دانلود می‌شود و pipe نمی‌شود: کانفیگ‌ها داخل خود اسکریپت‌اند و
سؤال می‌پرسد. اگر فایل ناقص دانلود شده باشد اجرا نمی‌شود.

| نقش | کجا | کارش |
|---|---|---|
| **exit** | سرور خارج | پنل، دیتابیس، مسیر خروج. اول این را نصب کنید؛ یک توکن جفت‌سازی می‌دهد |
| **relay** | سرور ایران | جواب DNS و پنل مشتری. توکن را وقتی خواست بچسبانید |
| **single** | یک سرور خارج | همهٔ کارها روی یک ماشین، `ROLE=single`؛ برای امتحان |
| **node** | سرور خارج دیگر | اگزیت اضافه، از صفحهٔ *نودها* در پنل ادمین |

نصب‌کننده هر مرحله را شماره و زمان می‌زند و آخرش خلاصه می‌دهد. اجرای دوباره
مشکلی ندارد، از چیزی که عوض می‌کند پشتیبان می‌گیرد، و
`sudo bash master-dns.sh --uninstall` ماشین را برمی‌گرداند و قبل از پاک کردن
دیتابیس و بسته‌ها می‌پرسد. بعد از نصب `sudo smartdns-menu` همهٔ دستورها را
نشان می‌دهد.

در فایروال باز کنید: **tcp** 22, 53, 80, 443, 853, 1119, 4070, 8443 و پورت پنل ادمین
(پیش‌فرض 9443، و هیچ‌وقت 8446 که اگزیت برای مسیر خودش به گوگل روی IPv6 نگه می‌دارد)؛
**udp** 53 و 3478.

## امکانات

- **مسیریابی**: حدود ۴۸۰ دامنهٔ تحریمی، سرویس‌های بازی و کنسول، فیلتر AAAA و
  STUN/TURN برای اینکه تست NAT کنسول درست شود.
- **مشتری‌ها**: لیست مجاز روی آدرس مشتری با شمارندهٔ بایت در کرنل، سهمیهٔ
  ماهانه یا یک‌باره با هشدار ۸۰ و ۹۵ درصد، سقف سرعت هر مشتری، قالب سرویس تا
  سطح تک‌تک دامنه‌ها.
- **فروش**: پلن، رسید، کد تخفیف، تست رایگان، کیف پول، لینک دعوت، دستگاه
  اضافه، و نماینده‌هایی با پلن و ربات خودشان.
- **پنل‌ها**: پنل ادمین با ظاهر داشبوردی و پنل مشتری، فارسی یا انگلیسی، روشن و
  تیره، با رنگ تاکیدی آبی، صورتی، سبز یا قرمز. فرم‌های عمومی در برابر ربات و
  هجوم محافظت می‌شوند: فیلد تلهٔ پنهان، زمان‌سنج امضاشده و سقف تلاش برای هر
  آدرس؛ کاربر عادی چیزی حس نمی‌کند.
- **DNS رمزگذاری‌شده**: DoH و DoT برای هر سرور، یک آدرس برای هر مشتری، پروفایل
  آیفون و دستور ویندوز ۱۱. وایرگارد برای جاهایی که DNS تنها کافی نیست.
- **گسترش**: چند اگزیت و سرور تکی روی یک پنل، نود آماده‌باش که پنل را تحویل
  می‌گیرد، هشدار مانیتورینگ، پشتیبان رمزشده و ارتقا با یک دکمه.
- **ربات تلگرام** برای مشتری و اپراتور، که از خود پنل ادمین راه می‌افتد.

## API

ربات فروش خودتان را می‌شود به پنل وصل کرد: [docs/bot-api.md](docs/bot-api.md).
برای اضافه یا کم کردن دستی روز و حجم یک مشتری، با کلید ادمین:

```sh
curl -X POST https://PANEL/api/v1/admin/users/42/adjust \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"days": 7, "gb": -2}'
```

`days` و `gb` (یا `bytes`) مثبت یا منفی می‌گیرند، با هم یا جدا. حساب نامحدود
با این کار محدود نمی‌شود و حجم هیچ‌وقت به صفر نمی‌رسد، چون صفر یعنی نامحدود.

## دستورها

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

## وقتی بازی بالا نمی‌آید

نامی که روی پورت 443 نیست از رله رد نمی‌شود، چون رله فقط 80 و 443 را می‌برد. ببینید بازی چه
نام‌هایی را می‌پرسد و آن‌ها را از رله بردارید:

برای مشتری `sudo smartdns-watch` را اجرا کنید تا ببینید کدام نام‌ها از رله رفتند و کدام مستقیم،
بعد نام را با `smartdns bypass` مستقیم کنید (منو هر دو را دارد).

بررسی به‌روزرسانی کالاف چهار نام HTTPS از Demonware را از رله می‌خواهد؛ در گروه `cod-https` هستند و خود
بازی آن را می‌خواهد. خطای «Server is busy» با کد `restrict-area` در پابجی موبایل از این است که ورود
(proximabeta.com روی 8085 و 8086) آی‌پی ایران را می‌بیند، و DNS تنها نمی‌تواند آن را عوض کند.
وار ثاندر روی پورت‌هایی مثل 20443 و 8090 حرف می‌زند؛ نام‌ها را با `smartdns-watch` پیدا کنید و مستقیم کنید.

## ساخت و تست

فایل `master-dns.sh` ساخته می‌شود، دستی ویرایش نکنید. منطق در
`tools/installer-logic.sh` است و کانفیگ‌ها در `templates/`، `common/` و
`domains/`.

```sh
python3 tools/build-installer.py   # master-dns.sh را دوباره می‌سازد
bash -n master-dns.sh              # بش معتبر می‌ماند
for t in tools/test-*.py; do python3 "$t"; done
```

## محدودیت‌ها

- فقط دانلود شکل‌دهی می‌شود، آپلود نه.
- ریلی بدون گواهی پنل مشتری ندارد؛ صفحهٔ رمز را روی HTTP ساده نمی‌دهد. دامنه
  بدهید.
- فقط ثبت‌نام ترافیک نمی‌دهد؛ حساب باید پلن داشته باشد.
- ترافیک دو برابر حساب می‌شود: یک گیگ مشتری حدود دو گیگ روی ریلی و دو گیگ
  روی اگزیت است.

## سپاس

- **[BackPack](https://github.com/AminMGMT/BackPack)** از **امین محمدی**
  ([@AminMGMT](https://github.com/AminMGMT)) تونل اختیاری بین ریلی و اگزیت را
  می‌رساند. کار خود اوست با مجوز AGPL-3.0: این پروژه فقط انتشارهای دست‌نخوردهٔ
  خودش را دانلود می‌کند، با هش ثابت چک می‌کند و اجرا می‌کند. ممنون امین.
- **[Vazirmatn](https://github.com/rastikerdar/vazirmatn)** از **صابر راستی‌کردار**،
  فونت هر دو پنل است، با مجوز SIL Open Font License 1.1 و همراه مجوزش در نصب‌کننده.
- **[Poppins](https://github.com/itfoundry/Poppins)** از Poppins Project Authors حروف و ارقام لاتین
  هر دو پنل است؛ زیرمجموعهٔ کوچکی از آن داخل صفحه‌ها گذاشته شده. مجوزش SIL Open Font License 1.1 است
  و متن مجوز در `common/fonts/Poppins-OFL.txt` قرار دارد.
- اپراتور هر مشتری در پنل ادمین از [RIPEstat](https://stat.ripe.net/) می‌آید.
- لوگوی سرویس‌ها از **[Simple Icons](https://simpleicons.org/)** (CC0) و آیکن‌های
  ساده از **[Lucide](https://lucide.dev/)** (ISC) است؛ هر لوگو علامت صاحب خودش است.
- **[HyperDNS](https://github.com/IzumiRain/HyperDNS)** از **Rain**
  ([@IzumiRain](https://github.com/IzumiRain))، یک اسمارت DNS مستقل به Go با مجوز
  AGPL-3.0. چند قابلیت از ایده‌های آن شروع شد (DoH و DoT، رسیدهای رمزشده، گزارش
  DNS، جواب خطای ریزالور، دامنه‌های مسدود، نصب تک‌سرور). Doctor DNS از کدش چیزی
  برنداشت و هر کدام دوباره نوشته شد، ولی ایده‌ها اول از آن‌ها بود. ممنون Rain.
- بررسی نام ثبت‌کنندهٔ آدرس اگزیت از سرویس عمومی RDAP ثبت‌کنندگان اینترنت،
  از راه [rdap.org](https://rdap.org/)، می‌گذرد.
- کانفیگ nginx اگزیت از [rohammosalli/smart-dns](https://github.com/rohammosalli/smart-dns)
  شروع شد.

## مجوز

MIT، فایل [LICENSE](LICENSE). مستر دی‌ان‌اس فورکی از Doctor DNS است که آن هم MIT
است و اعلان کپی‌رایتش در فایل مجوز می‌ماند.

</div>
