# WeebHole archive

Static, read-only copy of the WordPress site **weebhole.com** ("A weeb's dream news source"),
served by GitHub Pages from `docs/` at https://weebhole.com.

Every post, page, category, tag, date and author page is included, with the theme's styles,
scripts and images. Comments, search and the WordPress admin are not. The database dump and
`wp-content` backup are kept privately.

Made with `tools/crawl_site.py`, run on the old server against the WordPress container:

```
python3 crawl_site.py http://<container-ip> weebhole.com ./docs WeebHole
```
