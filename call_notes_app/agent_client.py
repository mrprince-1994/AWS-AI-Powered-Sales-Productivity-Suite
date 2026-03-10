"""Client for invoking the AWS Q&A agent with streaming support.

Supports three modes (tried in order):
1. AgentCore Runtime (deployed) — calls the agent via WebSocket + SigV4 auth
2. Local Strands + MCP — runs agent in-process with AWS doc search tools
3. Direct Bedrock streaming — invoke_model_with_response_stream (no tools)

MCP servers are kept alive between questions for fast follow-ups.
"""
import json
import os
import shutil
import sys
import threading
import time
import boto3
from botocore.config import Config
from config import AWS_REGION, CLAUDE_MODEL_ID

# Set this to your agent's runtime ARN to use the deployed AgentCore agent
# Get it from: agentcore status --verbose (look for agentRuntimeArn)
# Set via environment variable or edit this line directly
AGENTCORE_RUNTIME_ARN = os.environ.get("AGENTCORE_RUNTIME_ARN", None)

SYSTEM_PROMPT = """You are an AWS AI/ML expert assistant.

## CRITICAL RULE — ALWAYS USE TOOLS FIRST

You MUST use your documentation search tools BEFORE generating any answer. NEVER answer
from memory alone. For EVERY question:

1. FIRST: Call search_documentation with a targeted search query.
2. THEN: If needed, call read_documentation to get full page content.
3. FINALLY: Synthesize your answer ONLY from the documentation you retrieved.

If tools are unavailable, clearly state you are answering from general knowledge.

Keep answers to 2-4 paragraphs. Include specific details: features, pricing, integrations.
Format in clean markdown with bullet points where helpful.

ALWAYS end your answer with a "📎 Sources" section containing markdown links to the
documentation pages you used. Use the exact URLs from your search/read tool results.
Format: `- [Page Title](https://docs.aws.amazon.com/...)`. Include at least one link.
If no tools were available, link to the most relevant AWS service page instead."""

_IS_WINDOWS = sys.platform == "win32"
_EXE_SUFFIX = ".exe" if _IS_WINDOWS else ""


def _find_uvx_path():
    uvx_path = shutil.which("uvx")
    if uvx_path:
        return uvx_path
    for candidate in [
        os.path.expanduser(r"~\AppData\Local\Programs\Python\Python313\Scripts\uvx.exe"),
        os.path.expanduser(r"~\AppData\Local\Programs\Python\Python312\Scripts\uvx.exe"),
    ]:
        if os.path.isfile(candidate):
            return candidate
    return "uvx"


# ---------------------------------------------------------------------------
# Persistent MCP Pool — keeps servers alive between questions
# ---------------------------------------------------------------------------

class _MCPPool:
    """Manages long-lived MCP client connections.

    Starts both MCP servers in parallel on first use and reuses them
    for subsequent questions. Cuts ~15s off follow-up latency.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._tools = None          # cached tool list
        self._clients = []          # live MCPClient instances
        self._ready = False
        self._error = None

    @property
    def is_ready(self):
        return self._ready

    def ensure_started(self):
        """Start MCP servers if not already running. Thread-safe."""
        with self._lock:
            if self._ready:
                return
            if self._error:
                # Retry after previous failure
                self._error = None
            self._start_servers()

    def get_tools(self):
        """Return the cached tool list. Call ensure_started() first."""
        self.ensure_started()
        if self._error:
            raise RuntimeError(self._error)
        return self._tools

    def _start_servers(self):
        os.environ["BYPASS_TOOL_CONSENT"] = "true"

        from strands.tools.mcp import MCPClient
        from mcp.client.stdio import StdioServerParameters, stdio_client

        uvx_path = _find_uvx_path()
        print(f"[agent_client] Using uvx at: {uvx_path}")

        aws_docs_mcp = MCPClient(
            transport_callable=lambda: stdio_client(
                StdioServerParameters(
                    command=uvx_path,
                    args=[
                        "--from",
                        "awslabs.aws-documentation-mcp-server@latest",
                        f"awslabs.aws-documentation-mcp-server{_EXE_SUFFIX}",
                    ],
                    env={**os.environ, "FASTMCP_LOG_LEVEL": "ERROR"},
                )
            ),
            tool_filters={
                "allowed": ["search_documentation", "read_documentation", "recommend"]
            },
            startup_timeout=90,
        )

        aws_pricing_mcp = MCPClient(
            transport_callable=lambda: stdio_client(
                StdioServerParameters(
                    command=uvx_path,
                    args=[
                        "--from",
                        "awslabs.aws-pricing-mcp-server@latest",
                        f"awslabs.aws-pricing-mcp-server{_EXE_SUFFIX}",
                    ],
                    env={**os.environ, "FASTMCP_LOG_LEVEL": "ERROR"},
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

        aws_knowledge_mcp = None
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
            print("[agent_client] streamablehttp_client not available, skipping aws-knowledge")

        # Start both servers in parallel
        results = {}
        errors = {}

        def _start_client(name, client):
            try:
                t0 = time.time()
                client.start()
                results[name] = client
                print(f"[agent_client] {name} started in {time.time()-t0:.1f}s")
            except Exception as e:
                errors[name] = e
                print(f"[agent_client] {name} failed: {e}")

        threads = []
        threads.append(threading.Thread(target=_start_client, args=("aws-docs", aws_docs_mcp)))
        threads.append(threading.Thread(target=_start_client, args=("aws-pricing", aws_pricing_mcp)))
        if aws_knowledge_mcp:
            threads.append(threading.Thread(target=_start_client, args=("aws-knowledge", aws_knowledge_mcp)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Need at least aws-docs
        if "aws-docs" not in results:
            self._error = f"aws-docs MCP failed to start: {errors.get('aws-docs', 'unknown')}"
            return

        self._clients = list(results.values())
        self._tools = []
        for client in self._clients:
            self._tools.extend(client.list_tools_sync())
        print(f"[agent_client] MCP pool ready — {len(self._tools)} tools from {len(self._clients)} servers")
        self._ready = True

    def shutdown(self):
        """Cleanly stop all MCP servers."""
        with self._lock:
            for client in self._clients:
                try:
                    client.__exit__(None, None, None)
                except Exception:
                    pass
            self._clients.clear()
            self._tools = None
            self._ready = False
            print("[agent_client] MCP pool shut down")


# Module-level singleton
_mcp_pool = _MCPPool()


def warmup():
    """Pre-start MCP servers in background. Call from app startup."""
    threading.Thread(target=_mcp_pool.ensure_started, daemon=True).start()


def shutdown():
    """Shut down MCP servers. Call on app exit."""
    _mcp_pool.shutdown()


# ---------------------------------------------------------------------------
# Streaming callback handler
# ---------------------------------------------------------------------------

class _StreamingHandler:
    """Strands callback_handler that forwards text chunks to the UI."""

    def __init__(self, chunk_callback):
        self._on_chunk = chunk_callback
        self.tool_count = 0

    def __call__(self, **kwargs):
        data = kwargs.get("data", "")
        if data and self._on_chunk:
            self._on_chunk(data)
        tool_use = (kwargs.get("event", {})
                    .get("contentBlockStart", {})
                    .get("start", {})
                    .get("toolUse"))
        if tool_use:
            self.tool_count += 1
            print(f"[agent_client] Tool #{self.tool_count}: {tool_use.get('name', '?')}")


# ---------------------------------------------------------------------------
# Invocation modes
# ---------------------------------------------------------------------------

def _invoke_agentcore(question: str, on_chunk=None) -> str:
    """Invoke the deployed AgentCore agent via WebSocket."""
    import websocket as ws_lib
    from bedrock_agentcore.runtime import AgentCoreRuntimeClient

    print(f"[agent_client] Invoking AgentCore agent: {AGENTCORE_RUNTIME_ARN}")
    client = AgentCoreRuntimeClient(region=AWS_REGION)
    ws_url, headers = client.generate_ws_connection(
        runtime_arn=AGENTCORE_RUNTIME_ARN,
        endpoint_name="DEFAULT",
    )

    # websocket-client expects headers as a list of "Key: Value" strings
    header_list = [f"{k}: {v}" for k, v in headers.items()]

    payload = json.dumps({"prompt": question})
    full_response = []

    print(f"[agent_client] Connecting to WebSocket...")
    ws = ws_lib.create_connection(ws_url, header=header_list, timeout=180)
    try:
        ws.send(payload)
        print(f"[agent_client] Payload sent, waiting for response...")
        while True:
            try:
                result = ws.recv()
            except ws_lib.WebSocketConnectionClosedException:
                print("[agent_client] WebSocket closed by server")
                break

            if not result:
                break

            print(f"[agent_client] Received {len(result)} bytes")
            try:
                data = json.loads(result)
                # Handle different response shapes from AgentCore
                text = data.get("answer", data.get("result", data.get("text", "")))
                if text:
                    full_response.append(text)
                    if on_chunk:
                        on_chunk(text)
                elif isinstance(data, str):
                    full_response.append(data)
                    if on_chunk:
                        on_chunk(data)
            except json.JSONDecodeError:
                # Plain text response
                full_response.append(result)
                if on_chunk:
                    on_chunk(result)
    except Exception as e:
        print(f"[agent_client] WebSocket error: {e}")
        raise
    finally:
        ws.close()

    answer = "".join(full_response)
    print(f"[agent_client] Got response: {len(answer)} chars")
    return answer


def _invoke_local_with_mcp(question: str, on_chunk=None) -> str:
    """Local mode using persistent MCP pool."""
    from strands import Agent

    tools = _mcp_pool.get_tools()
    handler = _StreamingHandler(on_chunk) if on_chunk else None

    agent = Agent(
        system_prompt=SYSTEM_PROMPT,
        tools=tools,
        model=CLAUDE_MODEL_ID,
        callback_handler=handler,
    )

    result = agent(question)
    return _extract_text(result)


def _invoke_bedrock_streaming(question: str, on_chunk=None) -> str:
    """Fallback: stream directly from Bedrock without tools."""
    client = boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
        config=Config(read_timeout=120),
    )
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": question}],
    })
    response = client.invoke_model_with_response_stream(
        modelId=CLAUDE_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=body,
    )
    full_text = []
    for event in response["body"]:
        chunk = json.loads(event["chunk"]["bytes"])
        if chunk.get("type") == "content_block_delta":
            text = chunk["delta"].get("text", "")
            if text:
                full_text.append(text)
                if on_chunk:
                    on_chunk(text)
    return "".join(full_text)


def _extract_text(result) -> str:
    if hasattr(result, "message") and isinstance(result.message, dict):
        parts = []
        for block in result.message.get("content", []):
            if isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
        if parts:
            return "".join(parts)
    return str(result)


def ask_agent(question: str, callback=None, on_chunk=None):
    """Ask the AWS Q&A agent a question with streaming support."""
    def _run():
        try:
            if AGENTCORE_RUNTIME_ARN:
                try:
                    answer = _invoke_agentcore(question, on_chunk=on_chunk)
                except ImportError as e:
                    print(f"[agent_client] AgentCore SDK not installed ({e}), falling back")
                    try:
                        answer = _invoke_local_with_mcp(question, on_chunk=on_chunk)
                    except (ImportError, Exception) as e2:
                        print(f"[agent_client] MCP mode failed ({e2}), falling back to Bedrock streaming")
                        answer = _invoke_bedrock_streaming(question, on_chunk=on_chunk)
                except Exception as e:
                    print(f"[agent_client] AgentCore invoke failed ({e}), falling back")
                    try:
                        answer = _invoke_local_with_mcp(question, on_chunk=on_chunk)
                    except (ImportError, Exception) as e2:
                        print(f"[agent_client] MCP mode failed ({e2}), falling back to Bedrock streaming")
                        answer = _invoke_bedrock_streaming(question, on_chunk=on_chunk)
            else:
                try:
                    answer = _invoke_local_with_mcp(question, on_chunk=on_chunk)
                except (ImportError, Exception) as e:
                    print(f"[agent_client] MCP mode failed ({e}), falling back to Bedrock streaming")
                    answer = _invoke_bedrock_streaming(question, on_chunk=on_chunk)
            if callback:
                callback(answer, None)
        except Exception as e:
            print(f"[agent_client] All modes failed: {e}")
            if callback:
                callback(None, f"Error: {e}")

    threading.Thread(target=_run, daemon=True).start()
