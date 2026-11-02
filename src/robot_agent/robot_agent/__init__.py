"""Safe LLM tool layer over the Robot API v1 (see docs/INTERFACES.md, "Ajan araç katmanı").

An LLM (or any planner) can only call the white-listed tools of :mod:`robot_agent.tools`; every call
passes :class:`robot_agent.executor.ToolExecutor` (argument validation, safe zone, confirmation,
dry run, emergency-stop priority, event log) and reaches the robot only through the HTTP API on
127.0.0.1. There is no free code execution anywhere in this package.
"""
