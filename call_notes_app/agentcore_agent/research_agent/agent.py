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


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web using DuckDuckGo for current information about a company or topic.

    Args:
        query: Search query string
        max_results: Maximum number of results to return (default 5)
    """
    try:
        encoded = urllib.parse.quote_plus(query)
        url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        results = []

        # Abstract (main result)
        if data.get("AbstractText"):
            results.append(
                f"**{data.get('Heading', 'Overview')}**\n"
                f"{data['AbstractText']}\n"
                f"Source: {data.get('AbstractURL', '')}"
            )

        # Related topics
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append(
                    f"- {topic['Text']}\n  {topic.get('FirstURL', '')}"
                )

        if not results:
            # Fallback: try news search via DuckDuckGo HTML (lite)
            news_url = f"https://api.duckduckgo.com/?q={encoded}&format=json&ia=news"
            req2 = urllib.request.Request(news_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                data2 = json.loads(resp2.read().decode())
            for item in data2.get("Results", [])[:max_results]:
                results.append(f"- {item.get('Text', '')}\n  {item.get('FirstURL', '')}")

        if not results:
            return f"No results found for: {query}"

        return f"Search results for '{query}':\n\n" + "\n\n".join(results)

    except Exception as e:
        return f"Search error for '{query}': {e}"


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
