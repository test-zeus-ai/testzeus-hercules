import argparse
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("McpServer")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b

@mcp.tool()
def multiply(a: int, b: int) -> int:
    """Multiply two numbers"""
    return a * b

@mcp.tool()
def fibonacci(n: int) -> int:
    """Find the nth term in the Fibonacci series (0-indexed: fib(0)=0, fib(1)=1)."""
    if n < 0:
        raise ValueError("n must be a non-negative integer")
    if n == 0:
        return 0
    if n == 1:
        return 1
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b


files = {
    "ag2": "AG has released 0.8.5 version on 2025-04-03",
}

@mcp.resource("server-file://{name}")
def get_server_file(name: str) -> str:
    """Get a file content"""
    return files.get(name, f"File not found: {name}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP Server")
    parser.add_argument("transport", choices=["stdio", "sse", "streamable-http"], help="Transport mode (stdio, sse or streamable-http)")
    args = parser.parse_args()
    print("mcp server started")
    mcp.run(transport=args.transport)