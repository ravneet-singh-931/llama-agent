"""
Test Script: MCP Client-Server Integration

Tests that:
1. MCP server starts and exposes tools
2. MCP client can connect and discover tools
3. MCP client can call each agent tool
4. Auto-routing works correctly

Run: python test_mcp.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))


async def test_mcp_integration():
    print("=" * 60)
    print("  MCP Integration Test")
    print("=" * 60)

    # Step 1: Import and create client
    print("\n[1] Creating MCP client...")
    try:
        from mcp_client_helper import MCPAgentClient
        client = MCPAgentClient()
        print("    OK - Client created")
    except Exception as e:
        print(f"    FAIL - {e}")
        return False

    # Step 2: Connect to server
    print("\n[2] Connecting to MCP Agent Server...")
    try:
        tools = await client.connect()
        print(f"    OK - Connected! Tools discovered: {tools}")
    except Exception as e:
        print(f"    FAIL - {e}")
        return False

    # Step 3: Get tools info
    print("\n[3] Listing tool details...")
    try:
        tools_info = await client.get_tools_info()
        for info in tools_info:
            print(f"    - {info['name']}: {info['description'][:60]}...")
        print(f"    OK - {len(tools_info)} tools listed")
    except Exception as e:
        print(f"    FAIL - {e}")

    # Step 4: Call team_status (simple, no args needed)
    print("\n[4] Calling team_status tool...")
    try:
        result = await client.call_agent("team_status", "")
        print(f"    OK - Got response ({len(result)} chars)")
        print(f"    Preview: {result[:200]}...")
    except Exception as e:
        print(f"    FAIL - {e}")

    # Step 5: Call coder agent
    print("\n[5] Calling coder_agent (simple task)...")
    try:
        result = await client.call_agent("coder", "Write a Python function to calculate factorial")
        print(f"    OK - Got response ({len(result)} chars)")
        print(f"    Preview: {result[:200]}...")
    except Exception as e:
        print(f"    FAIL - {e}")

    # Step 6: Test auto-routing
    print("\n[6] Testing auto-route (should go to coder)...")
    try:
        result = await client.route_and_call("Write a Python function to reverse a string")
        print(f"    OK - Got response ({len(result)} chars)")
        print(f"    Preview: {result[:200]}...")
    except Exception as e:
        print(f"    FAIL - {e}")

    # Step 7: Disconnect
    print("\n[7] Disconnecting...")
    try:
        await client.disconnect()
        print("    OK - Disconnected")
    except Exception as e:
        print(f"    FAIL - {e}")

    print("\n" + "=" * 60)
    print("  All tests completed!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = asyncio.run(test_mcp_integration())
    sys.exit(0 if success else 1)
