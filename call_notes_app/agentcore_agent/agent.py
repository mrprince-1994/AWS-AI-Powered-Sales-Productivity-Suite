"""AgentCore Runtime agent for answering AWS AI/ML questions.

Uses Strands Agent with MCP tool integration to connect to:
- aws-docs MCP server (search/read AWS documentation)
- aws-knowledge MCP server (search AWS docs, check regional availability)

These MCP servers are spawned as subprocesses via uvx and connected
through the Strands MCPClient using stdio transport.
"""
import os
import sys
import shutil

os.environ["BYPASS_TOOL_CONSENT"] = "true"

from strands import Agent
from strands.tools.mcp import MCPClient
from mcp.client.stdio import StdioServerParameters, stdio_client
from bedrock_agentcore.runtime import BedrockAgentCoreApp

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
_IS_WINDOWS = sys.platform == "win32"
_EXE_SUFFIX = ".exe" if _IS_WINDOWS else ""
_UVX_PATH = shutil.which("uvx") or "uvx"

# --- MCP Server Configurations ---

aws_docs_mcp = MCPClient(
    transport_callable=lambda: stdio_client(
        StdioServerParameters(
            command=_UVX_PATH,
            args=[
                "--from",
                "awslabs.aws-documentation-mcp-server@latest",
                f"awslabs.aws-documentation-mcp-server{_EXE_SUFFIX}",
            ],
            env={
                **os.environ,
                "FASTMCP_LOG_LEVEL": "ERROR",
            },
        )
    ),
    tool_filters={
        "allowed": [
            "search_documentation",
            "read_documentation",
            "recommend",
        ]
    },
    startup_timeout=90,
)

aws_pricing_mcp = MCPClient(
    transport_callable=lambda: stdio_client(
        StdioServerParameters(
            command=_UVX_PATH,
            args=[
                "--from",
                "awslabs.aws-pricing-mcp-server@latest",
                f"awslabs.aws-pricing-mcp-server{_EXE_SUFFIX}",
            ],
            env={
                **os.environ,
                "FASTMCP_LOG_LEVEL": "ERROR",
            },
        )
    ),
    tool_filters={
        "allowed": [
            "get_pricing",
            "get_pricing_service_codes",
            "get_pricing_service_attributes",
            "get_pricing_attribute_values",
        ]
    },
    prefix="pricing",
    startup_timeout=90,
)

# aws-knowledge is an HTTP MCP server
try:
    from mcp.client.streamable_http import streamablehttp_client

    aws_knowledge_mcp = MCPClient(
        transport_callable=lambda: streamablehttp_client(
            url="https://knowledge-mcp.global.api.aws",
        ),
        tool_filters={
            "allowed": [
                "aws___search_documentation",
                "aws___read_documentation",
                "aws___get_regional_availability",
            ]
        },
        prefix="knowledge",
        startup_timeout=30,
    )
except ImportError:
    aws_knowledge_mcp = None


SYSTEM_PROMPT = """You are an AWS AI/ML expert assistant embedded in a live call transcription app.
Your job is to answer questions about AWS AI/ML services that come up during customer calls.

## CRITICAL RULE — ALWAYS USE TOOLS FIRST

You MUST use your documentation search tools BEFORE generating any answer. NEVER answer
from memory alone. Your training data may be outdated. The tools give you current, accurate
information straight from AWS documentation.

For EVERY question, follow this exact workflow:

1. FIRST: Call search_documentation with a targeted search query about the topic.
2. THEN: If the search results reference a specific page with details you need,
   call read_documentation to get the full content.
3. OPTIONALLY: Call knowledge_search_documentation for additional cross-references
   or call knowledge_get_regional_availability if the question involves region support.
4. OPTIONALLY: If the question involves pricing, costs, or comparisons, use the pricing
   tools (pricing_get_pricing, pricing_get_pricing_service_codes, etc.) to get real-time
   pricing data from the AWS Price List API.
5. FINALLY: Synthesize your answer ONLY from the documentation you retrieved.
   Cite specific details, features, and facts from the docs.

If a tool call fails or returns no results, try rephrasing your search query and search again.
Only after exhausting tool searches should you supplement with your own knowledge, and you must
clearly state when you are doing so.

## Services You Specialize In

Amazon SageMaker, Amazon Bedrock, Bedrock AgentCore, Amazon Q, Amazon QuickSight,
Amazon Comprehend, Amazon Rekognition, Amazon Textract, Amazon Transcribe, Amazon Polly,
Amazon Kendra, Amazon Personalize, AWS Trainium/Inferentia, Amazon Q Developer.

## Answer Format

- Be concise but accurate. 2-4 paragraphs max.
- Include specific details: pricing models, key features, integration points.
- If you're not sure about something, say so rather than guessing.
- Format in clean markdown with bullet points where helpful.
- Focus on what's most useful for a sales/solutions architect conversation.
- ALWAYS end your answer with a "📎 Sources" section containing markdown links to the
  documentation pages you used. Use the exact URLs from your search/read tool results.
  Format: `- [Page Title](https://docs.aws.amazon.com/...)`. Include at least one link.
"""

app = BedrockAgentCoreApp()


@app.entrypoint
def answer_question(payload, context):
    """Handle incoming question from the call notes app."""
    question = payload.get("prompt", "")
    if not question:
        return {"answer": "No question provided.", "status": "error"}

    try:
        # Start MCP servers and create agent with their tools
        mcp_clients = []
        aws_docs_mcp.start()
        mcp_clients.append(aws_docs_mcp)

        try:
            aws_pricing_mcp.start()
            mcp_clients.append(aws_pricing_mcp)
        except Exception:
            pass

        if aws_knowledge_mcp:
            try:
                aws_knowledge_mcp.start()
                mcp_clients.append(aws_knowledge_mcp)
            except Exception:
                pass

        try:
            tools = []
            for client in mcp_clients:
                tools.extend(client.list_tools_sync())

            agent = Agent(
                system_prompt=SYSTEM_PROMPT,
                tools=tools,
                model="us.anthropic.claude-sonnet-4-6",
            )

            result = agent(question)

            # Extract text from the response
            answer_text = ""
            if hasattr(result, "message") and isinstance(result.message, dict):
                for block in result.message.get("content", []):
                    if isinstance(block, dict) and "text" in block:
                        answer_text += block["text"]
            elif hasattr(result, "message"):
                answer_text = str(result.message)
            else:
                answer_text = str(result)

            return {
                "answer": answer_text,
                "status": "success",
            }
        finally:
            for client in mcp_clients:
                try:
                    client.__exit__(None, None, None)
                except Exception:
                    pass
    except Exception as e:
        return {
            "answer": f"Error generating answer: {str(e)}",
            "status": "error",
        }


if __name__ == "__main__":
    app.run()
