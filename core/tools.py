"""
System Tools - Low-level system operations.
"""

import asyncio
import os
from .logger import logger


async def run_bash(cmd):
    """Executes a bash command and returns output."""
    logger.debug(f"Executing Bash: {cmd}")
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    return stdout.decode().strip() or stderr.decode().strip()


def native_search(query):
    """Placeholder for native search."""
    return f"Search result for {query}"


async def browse_web(url):
    """Placeholder for web browsing."""
    return f"Content of {url}"


def native_list(path):
    """Lists files in a directory."""
    try:
        return "\n".join(os.listdir(path))
    except Exception as e:
        return str(e)


def native_read(path):
    """Reads content of a file."""
    try:
        with open(path, 'r') as f:
            return f.read()
    except Exception as e:
        return str(e)


def write_log(msg):
    """Compatibility function for logging."""
    logger.info(msg)
