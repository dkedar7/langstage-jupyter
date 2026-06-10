#!/usr/bin/env python3
"""
DeepAgent Lab launcher script.

This script wraps the 'jupyter lab' command to automatically configure
the Jupyter server settings and make them available to agents.

Usage:
    deepagent-lab [options] [jupyter lab args...]

Example:
    deepagent-lab --port 8889
    deepagent-lab --no-browser
    deepagent-lab -a my_agent.py:graph     # pick the agent, same spec format
                                           # as every deep-agent surface
    deepagent-lab --demo                   # keyless demo agent, no API key
    deepagent-lab --show-config            # print resolved config and exit
"""
import os
import sys
import socket
import secrets
import subprocess

# The keyless echo agent shipped with the shared core — see `--demo`.
DEMO_AGENT_SPEC = "langgraph_stream_parser.demo.stub:graph"


def extract_agent_args(args):
    """Split our agent flags out of the passthrough jupyter-lab args.

    Handles ``-a SPEC`` / ``--agent SPEC`` / ``--agent=SPEC`` and ``--demo``.
    Returns ``(agent_spec, demo, remaining_args)`` — remaining_args go to
    ``jupyter lab`` untouched.
    """
    agent_spec = None
    demo = False
    remaining = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-a", "--agent") and i + 1 < len(args):
            agent_spec = args[i + 1]
            i += 2
            continue
        if arg.startswith("--agent="):
            agent_spec = arg.split("=", 1)[1]
            i += 1
            continue
        if arg == "--demo":
            demo = True
            i += 1
            continue
        remaining.append(arg)
        i += 1
    return agent_spec, demo, remaining


def find_available_port(start_port=8888, max_attempts=10):
    """Find an available port starting from start_port."""
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"Could not find available port in range {start_port}-{start_port + max_attempts}")


def generate_token():
    """Generate a secure random token for Jupyter authentication."""
    return secrets.token_urlsafe(32)


def main():
    """Main launcher function."""
    # Parse command line arguments
    args = sys.argv[1:]

    # --show-config: print the resolved config (value, source, env var / TOML
    # key for each) and exit — no need to remember the DEEPAGENT_* names.
    if "--show-config" in args:
        from deepagent_lab.config import LabConfig
        print(LabConfig.resolve().describe())
        return

    # Our agent flags must not reach `jupyter lab` (it would reject them).
    agent_spec, demo, args = extract_agent_args(args)
    if demo and agent_spec:
        print("ERROR: --demo and -a/--agent are mutually exclusive")
        sys.exit(1)
    if demo:
        agent_spec = DEMO_AGENT_SPEC
    if agent_spec:
        # The sidebar extension reads DEEPAGENT_AGENT_SPEC (env beats the
        # built-in default; deepagents.toml still works when nothing is set).
        os.environ["DEEPAGENT_AGENT_SPEC"] = agent_spec
        print(f"Agent spec: {agent_spec}")

    # Check if user specified a port
    user_port = None
    port_specified = False
    for i, arg in enumerate(args):
        if arg == '--port' and i + 1 < len(args):
            try:
                user_port = int(args[i + 1])
                port_specified = True
            except ValueError:
                pass
            break
        elif arg.startswith('--port='):
            try:
                user_port = int(arg.split('=')[1])
                port_specified = True
            except ValueError:
                pass
            break

    # Find available port
    if user_port:
        port = user_port
        print(f"Using user-specified port: {port}")
    else:
        port = find_available_port()
        print(f"Auto-detected available port: {port}")

    # Generate token (or use existing if set)
    token = os.getenv('JUPYTER_TOKEN')
    if not token:
        token = generate_token()
        print(f"Generated secure authentication token")
    else:
        print(f"Using existing JUPYTER_TOKEN from environment")

    # Determine server URL
    # Use localhost for security (only local connections)
    server_url = f"http://localhost:{port}"

    # Set environment variables for the agent to use
    os.environ['DEEPAGENT_JUPYTER_SERVER_URL'] = server_url
    os.environ['DEEPAGENT_JUPYTER_TOKEN'] = token

    print(f"\n{'='*60}")
    print(f"DeepAgent Lab Configuration:")
    print(f"  Server URL: {server_url}")
    print(f"  Token: {'*' * 20} (hidden for security)")
    print(f"  Environment variables set:")
    print(f"    - DEEPAGENT_JUPYTER_SERVER_URL")
    print(f"    - DEEPAGENT_JUPYTER_TOKEN")
    print(f"{'='*60}\n")

    # Build jupyter lab command
    jupyter_args = ['jupyter', 'lab']

    # Add port if not already specified by user
    if not port_specified:
        jupyter_args.extend(['--port', str(port)])

    # Add token
    jupyter_args.extend(['--IdentityProvider.token', token])

    # Add any user-provided arguments
    jupyter_args.extend(args)

    # Launch Jupyter Lab
    print(f"Launching: {' '.join(jupyter_args)}\n")
    try:
        subprocess.run(jupyter_args, env=os.environ)
    except KeyboardInterrupt:
        print("\n\nShutting down DeepAgent Lab...")
        sys.exit(0)
    except FileNotFoundError:
        print("ERROR: 'jupyter' command not found. Please install JupyterLab:")
        print("  pip install jupyterlab")
        sys.exit(1)


if __name__ == '__main__':
    main()
