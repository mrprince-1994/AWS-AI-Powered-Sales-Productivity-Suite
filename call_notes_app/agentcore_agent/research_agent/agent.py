"""AgentCore Runtime — Customer Research Agent.

Built with Strands. Uses DuckDuckGo web search to research customers,
find news, funding rounds, tech stack, and competitive context.

Payload schema:
  {
    "prompt": "<research question or customer name>",
    "customer": "<optional customer name hint>"
  }
"""
import os
import json
import re
import urllib.request
import urllib.parse

os.environ["BYPASS_TOOL_CONSENT"] = "true"

from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp

SONNET_MODEL_ID = "us.anthropic.claude-sonnet-4-6"

SYSTEM_PROMPT = """You are an expert customer research analyst helping an AWS account manager \
prepare for and follow up on customer calls.

You have a `web_search` tool. Use it to find current, accurate information about customers.

For every research request:
1. Search for the company name + relevant context (funding, news, tech stack, AWS usage, etc.)
2. Run 2-3 targeted searches to get a comprehensive picture
3. Synthesize findings into a structured research brief

Research brief format:
## Company Overview
- What they do, industry, size, stage

## Recent News & Developments
- Funding rounds, acquisitions, product launches, leadership changes

## Technology & Infrastructure
- Known tech stack, cloud usage, AI/ML initiatives

## AWS Relevance
- Current AWS usage (if known), potential use cases, competitive landscape

## Talking Points
- Key angles for an AWS conversation based on their current situation

Always cite your sources with URLs. If search returns limited results, say so clearly."""

app = BedrockAgentCoreApp()


def _clean_html(s: str) -> str:
    """Strip HTML tags and decode common entities."""
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")
    return s.strip()


def _ddg_html_search(query: str, max_results: int = 5) -> list[dict]:
    """Fallback: scrape DuckDuckGo HTML results."""
    encoded = urllib.parse.quote_plus(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    results = []
    blocks = re.findall(r"result__body.*?(?=result__body|$)", html, re.DOTALL)
    for block in blocks[:max_results]:
        title_m = re.search(r'result__a[^>]*>(.*?)</a>', block, re.DOTALL)
        url_m   = re.search(r'result__url[^>]*>\s*(.*?)\s*</span>', block, re.DOTALL)
        snip_m  = re.search(r'result__snippet[^>]*>(.*?)</span>', block, re.DOTALL)
        title = _clean_html(title_m.group(1)) if title_m else ""
        link  = _clean_html(url_m.group(1))   if url_m   else ""
        snip  = _clean_html(snip_m.group(1))  if snip_m  else ""
        if title or snip:
            results.append({"title": title, "href": link, "body": snip})
    return results


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web using DuckDuckGo for current information about a company or topic.

    Args:
        query: Search query string
        max_results: Maximum number of results to return (default 5)
    """
    results = []

    # Primary: duckduckgo-search package
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            hits = list(ddgs.text(query, max_results=max_results))
        results = hits
    except Exception as e1:
        # Fallback: HTML scraper
        try:
            results = _ddg_html_search(query, max_results)
        except Exception as e2:
            return f"Search error for '{query}': primary={e1}, fallback={e2}"

    if not results:
        return f"No results found for: {query}"

    parts = []
    for r in results:
        title = r.get("title", "")
        link  = r.get("href", r.get("url", ""))
        body  = r.get("body", r.get("snippet", ""))
        parts.append(f"**{title}**\n{link}\n{body}")

    return f"Search results for '{query}':\n\n" + "\n\n---\n\n".join(parts)


@app.entrypoint
def research_customer(payload, context):
    question = payload.get("prompt", "")
    customer_hint = payload.get("customer", "")

    if not question:
        return {"answer": "No research question provided.", "status": "error"}

    # Prepend customer context if provided
    full_prompt = question
    if customer_hint and customer_hint.lower() not in question.lower():
        full_prompt = f"Research customer: {customer_hint}\n\n{question}"

    try:
        agent = Agent(
            system_prompt=SYSTEM_PROMPT,
            tools=[web_search],
            model=SONNET_MODEL_ID,
        )

        result = agent(full_prompt)

        answer = ""
        if hasattr(result, "message") and isinstance(result.message, dict):
            for block in result.message.get("content", []):
                if isinstance(block, dict) and "text" in block:
                    answer += block["text"]
        else:
            answer = str(result)

        return {"answer": answer, "status": "success"}

    except Exception as e:
        return {"answer": f"Error: {e}", "status": "error"}


if __name__ == "__main__":
    app.run()
