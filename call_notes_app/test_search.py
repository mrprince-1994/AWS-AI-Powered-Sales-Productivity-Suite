import urllib.request, urllib.parse, re, json

def ddg_search(query, max_results=5):
    encoded = urllib.parse.quote_plus(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    # Extract result blocks
    results = []
    # Find result snippets using a simpler pattern
    blocks = re.findall(r'result__body.*?(?=result__body|$)', html, re.DOTALL)
    for block in blocks[:max_results]:
        title_m = re.search(r'result__a[^>]*>(.*?)</a>', block, re.DOTALL)
        url_m = re.search(r'result__url[^>]*>\s*(.*?)\s*</span>', block, re.DOTALL)
        snip_m = re.search(r'result__snippet[^>]*>(.*?)</span>', block, re.DOTALL)
        def clean(s):
            return re.sub(r"<[^>]+>", "", s).strip() if s else ""
        title = clean(title_m.group(1)) if title_m else ""
        url = clean(url_m.group(1)) if url_m else ""
        snip = clean(snip_m.group(1)) if snip_m else ""
        if title or snip:
            results.append({"title": title, "url": url, "snippet": snip})

    return results

results = ddg_search("BQE Software company")
print(f"Got {len(results)} results")
for r in results:
    print(f"  - {r['title']}")
    print(f"    {r['url']}")
    print(f"    {r['snippet'][:150]}")
    print()
