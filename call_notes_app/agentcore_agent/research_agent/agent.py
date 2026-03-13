"""AgentCore Runtime — Customer Research Agent (streaming).

Built with Strands. Uses DuckDuckGo web search to research customers.
Streams results back via SSE.

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
from datetime import datetime

os.environ["BYPASS_TOOL_CONSENT"] = "true"

from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp

SONNET_MODEL_ID = "us.anthropic.claude-sonnet-4-6"

SYSTEM_PROMPT = """You are an expert customer research assistant helping an AWS account manager. \
Today's date is {today}. You have a `web_search` tool — use it to find current, accurate \
information. Always include the current year ({year}) in your search queries.

Your job is to directly answer the user's question using web search results. \
Be flexible — adapt your response format to match what was asked:

- If asked for latest news → search for recent news and present findings \
chronologically, highlighting any AI/ML relevance
- If asked for a business overview → provide company description, products, \
industry, size, key customers, and market position
- If asked about AI/ML use cases → search specifically for the company's \
AI/ML initiatives, products, and announcements
- If asked for talking points → tailor recommendations to the company's \
situation with specific AWS service mappings
- If asked a general question → answer it directly using search results

Guidelines:
- Run 2-3 targeted searches to get comprehensive results
- Always cite sources with URLs
- When discussing any topic, note AI/ML relevance if applicable — \
the user is an AWS account manager focused on AI/ML opportunities
- Use clean markdown formatting with headers and bullets
- If search returns limited results, say so and provide your best analysis
- Do NOT force a rigid template — answer naturally based on the question"""

app = BedrockAgentCoreApp()


def _clean_html(s: str) -> str:
    """Strip HTML tags and decode common entities."""
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")
    return s.strip()


def _ddg_html_search(query: str, max_results: int = 10) -> list[dict]:
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
def web_search(query: str, max_results: int = 10) -> str:
    """Search the web using DuckDuckGo for current information about a company or topic.

    Args:
        query: Search query string
        max_results: Maximum number of results to return (default 5)
    """
    results = []

    # Primary: ddgs package (formerly duckduckgo-search)
    try:
        from ddgs import DDGS as DDGS_New
        with DDGS_New() as ddgs:
            hits = list(ddgs.text(query, max_results=max_results))
        results = hits
    except ImportError:
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                hits = list(ddgs.text(query, max_results=max_results))
            results = hits
        except Exception:
            pass
    except Exception:
        pass

    # Fallback: HTML scraper if no results from package
    if not results:
        try:
            results = _ddg_html_search(query, max_results)
        except Exception as e2:
            return f"Search error: {e2}"

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
async def research_customer(payload, context):
    question = payload.get("prompt", "")
    customer_hint = payload.get("customer", "")

    if not question:
        yield {"text": "No research question provided.", "type": "error"}
        return

    full_prompt = question
    if customer_hint and customer_hint.lower() not in question.lower():
        full_prompt = f"Research customer: {customer_hint}\n\n{question}"

    try:
        now = datetime.now()
        prompt_with_date = SYSTEM_PROMPT.format(
            today=now.strftime("%B %d, %Y"),
            year=now.strftime("%Y"),
        )
        agent = Agent(
            system_prompt=prompt_with_date,
            tools=[web_search],
            model=SONNET_MODEL_ID,
            callback_handler=None,
        )

        stream = agent.stream_async(full_prompt)
        async for event in stream:
            yiept Exception as e:
        yield {"text": f"Error: {e}", "type": "error"}

    try:
        now = datetime.now()
        prompt_with_date = SYSTEM_PROMPT.format(
            today=now.strftime("%B %d, %Y"),
            year=now.strftime("%Y"),
        )
        agent = Agent(
            system_prompt=prompt_with_date,
