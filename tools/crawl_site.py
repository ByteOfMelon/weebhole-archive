#!/usr/bin/env python3
"""Crawl a (WordPress) site into a static, read-only copy. Stdlib only; runs on the old server.

  python3 crawl_site.py http://192.168.55.5 weebhole.com /root/wh-static "WeebHole"

Requests go to the given origin with a Host header, so the live DNS isn't needed.
Pages -> /<path>/index.html. Assets are kept at their own paths.
"""
import hashlib
import re
import sys
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

ORIGIN, DOMAIN, OUT, SITENAME = sys.argv[1].rstrip("/"), sys.argv[2], Path(sys.argv[3]), sys.argv[4]
SKIP = re.compile(r"^/(wp-admin|wp-login\.php|xmlrpc\.php|wp-json|wp-comments-post\.php|feed|comments/feed)|/feed/?$|/embed/?$|/trackback/?$")
ASSET = re.compile(r"^/(wp-content|wp-includes)/")
BAD = re.compile(r'[<>:"|?*\\]')
OUT.mkdir(parents=True, exist_ok=True)
assets, failed, pages = {}, [], {}


def get(path):
    req = urllib.request.Request(ORIGIN + path, headers={"Host": DOMAIN, "User-Agent": "archive-crawler",
                                                          "X-Forwarded-Proto": "https"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(), r.headers.get("Content-Type", ""), urllib.parse.urlsplit(r.geturl()).path


def local_path(url):
    """Absolute same-site URL -> site path, else None."""
    url = url.replace("&amp;", "&").replace("&#038;", "&")
    if url.startswith("//"):
        url = "https:" + url
    sp = urllib.parse.urlsplit(url)
    if sp.scheme in ("http", "https") and sp.netloc not in (DOMAIN, "www." + DOMAIN):
        return None
    if not sp.scheme and not url.startswith("/"):
        return None
    return sp.path or "/", sp.query, sp.fragment


def fetch_asset(path, query=""):
    key = path + ("?" + query if query else "")
    if key in assets:
        return assets[key]
    name = urllib.parse.unquote(path).lstrip("/")
    name = "/".join(BAD.sub("_", p) for p in name.split("/"))
    assets[key] = "/" + urllib.parse.quote(name)          # drop ?ver=… cache busters
    try:
        body, ctype, _ = get(key)
    except Exception as e:
        failed.append(f"{key}: {e}")
        return assets[key]
    if name.endswith(".css"):
        css = body.decode("utf-8", "replace")
        base_dir = path.rsplit("/", 1)[0] + "/"

        def css_url(m):
            u = m.group(2).strip()
            if u.startswith(("data:", "#")):
                return m.group(0)
            lp = local_path(urllib.parse.urljoin("https://" + DOMAIN + base_dir, u))
            return f"url({m.group(1)}{fetch_asset(lp[0], lp[1]) if lp else u}{m.group(1)})"
        body = re.sub(r"url\((['\"]?)([^'\")]+)\1\)", css_url, css).encode()
    dst = OUT / urllib.parse.unquote(assets[key].lstrip("/"))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(body)
    return assets[key]


HIDE = """<style id="archive-overrides">
#respond,.comment-respond,.comment-reply-link,.search-form,form[role=search],.searchform,.wp-block-search,
#wpadminbar,.wp-block-loginout,.post-edit-link{display:none!important}
.archive-banner{background:#fff6dc;color:#5a4300;border-bottom:1px solid #f0d98a;padding:8px 16px;font:14px/1.4 system-ui,sans-serif;text-align:center}
</style>"""
BANNER = (f'<div class="archive-banner"><strong>Read-only archive.</strong> This is a static copy of '
          f'{SITENAME} ({DOMAIN}). Comments and search are turned off.</div>')
ATTR = re.compile(r"""(\s(?:href|src|data-src|poster|content))=(["'])(.*?)\2""", re.S)
SRCSET = re.compile(r"""(\s(?:srcset|data-srcset))=(["'])(.*?)\2""", re.S)
queue = deque(["/"])
seen = {"/"}


def rewrite(html):
    def fix(url, follow=True):
        lp = local_path(url)
        if not lp:
            return url
        path, query, frag = lp
        if ASSET.match(path):
            return fetch_asset(path, query)
        if SKIP.search(path) or query:
            return url if not url.startswith("http") else path + ("?" + query if query else "")
        if follow and path not in seen and not re.search(r"\.(xml|php|txt)$", path):
            seen.add(path)
            queue.append(path)
        return path + ("#" + frag if frag else "")

    html = ATTR.sub(lambda m: f'{m.group(1)}={m.group(2)}{fix(m.group(3), m.group(1).strip() == "href")}{m.group(2)}'
                    if m.group(1).strip() != "content" or m.group(3).startswith(("http", "/")) else m.group(0), html)
    html = SRCSET.sub(lambda m: m.group(1) + "=" + m.group(2) + ", ".join(
        " ".join([fix(p.split()[0], False)] + p.split()[1:]) for p in m.group(3).split(",") if p.strip()) + m.group(2), html)
    html = re.sub(r"url\((['\"]?)(https?://" + re.escape(DOMAIN) + r"[^'\")]*)\1\)",
                  lambda m: f"url({m.group(1)}{fix(m.group(2), False)}{m.group(1)})", html)
    html = html.replace("https:\\/\\/" + DOMAIN, "").replace("http:\\/\\/" + DOMAIN, "")
    for ap in set(re.findall(r"https?://" + re.escape(DOMAIN) + r"(/wp-(?:content|includes)/[^\s\"'?)]+)", html)):
        fetch_asset(ap)  # assets referenced only from inline JSON (emoji loader etc.)
    html = re.sub(r"https?://" + re.escape(DOMAIN) + r"/", "/", html)
    html = re.sub(r"action=\"https?://" + re.escape(DOMAIN) + r"\"", "action=\"/\"", html)
    html = re.sub(r"<script\b[^>]*independent-analytics[^>]*>.*?</script>", "", html, flags=re.S)
    html = html.replace("</head>", HIDE + "\n</head>", 1)
    html = re.sub(r"(<body\b[^>]*>)", r"\1" + BANNER, html, count=1)
    return html


while queue:
    path = queue.popleft()
    try:
        body, ctype, final = get(path)
    except Exception as e:
        failed.append(f"{path}: {e}")
        continue
    if "text/html" not in ctype:
        continue
    html = rewrite(body.decode("utf-8", "replace"))
    rel = urllib.parse.unquote(path).strip("/")
    dst = OUT / ("/".join(BAD.sub("_", p) for p in rel.split("/")) if rel else "") / "index.html"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(html, encoding="utf-8")
    pages[path] = dst
    print(f"  {len(pages):4d} {path}")

(OUT / "404.html").write_text(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Not in the archive</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px}}</style></head>
<body><h1>Not in the archive</h1><p>This page of {SITENAME} wasn't kept.</p><p><a href="/">Go to the home page</a></p></body></html>""",
                              encoding="utf-8")
(OUT / "CNAME").write_text(DOMAIN + "\n")
(OUT / ".nojekyll").write_text("")
print(f"pages: {len(pages)}  assets: {len(assets)}  failed: {len(failed)}")
for f in failed[:30]:
    print("  FAIL", f)
