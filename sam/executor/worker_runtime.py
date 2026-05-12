"""Phase 4: Worker-centric execution runtime with live task monitoring.

Tracks worker lifecycle throughout tool execution:
- pending: created, awaiting execution
- running: tool execution in progress
- observation: gathering output/results
- completed/failed: finished with result

Workers publish status updates to the monitor, enabling live UI feeds
and concurrent multi-worker execution.
"""

from __future__ import annotations

from typing import Any

from diagnostics.result import SamResult
from workers.monitor import WorkerMonitor, worker_monitor


def _worker_identity_for_tool(tool_name: str) -> tuple[str, str]:
    normalized = tool_name.lower()
    if any(token in normalized for token in ("scan", "inspect", "read", "list", "details")):
        return "Inspector", "inspect"
    if any(token in normalized for token in ("test", "syntax", "health", "compile")):
        return "Nigel", "test"
    if any(token in normalized for token in ("plan", "delegation", "progress", "status")):
        return "Coordinator", "planning"
    if any(token in normalized for token in ("run", "execute")):
        return "Priase", "dev"
    return "ToolExecutor", "executor"


class WorkerExecutionContext:
    """Tracks execution context for a tool invocation with worker lifecycle."""
    
    def __init__(self, tool_name: str, worker_monitor_instance: WorkerMonitor | None = None) -> None:
        self.tool_name = tool_name
        self.monitor = worker_monitor_instance or worker_monitor
        self.task = None
    
    def start(self, description: str = "") -> None:
        """Create and start a worker task."""
        worker_name, worker_type = _worker_identity_for_tool(self.tool_name)
        self.task = self.monitor.create_task(
            name=f"execute_{self.tool_name}",
            worker_type=worker_type,
            worker_name=worker_name,
            description=description or f"Execute {self.tool_name}",
        )
        self.monitor.mark_running(self.task.task_id)
    
    def observe(self, message: str) -> None:
        """Append observation/output from execution."""
        if self.task:
            self.monitor.append_output(self.task.task_id, message)
    
    def succeed(self, message: str = "Execution succeeded") -> None:
        """Mark task as successful."""
        if self.task:
            self.observe(message)
            self.monitor.mark_done(self.task.task_id)
    
    def fail(self, error: str) -> None:
        """Mark task as failed."""
        if self.task:
            self.monitor.mark_failed(self.task.task_id, error)


class WorkerCentricExecutor:
    """Executor wrapper that tracks tool execution through worker lifecycle.
    
    Wraps an existing ToolExecutor and adds worker monitoring, making
    execution visible through live worker feeds.
    
    Implements the same interface as ToolExecutor for drop-in replacement.
    """
    
    def __init__(self, executor: Any, monitor: WorkerMonitor | None = None) -> None:
        self.executor = executor
        self.monitor = monitor or worker_monitor
    
    def register(
        self,
        tool_name: str,
        handler: Any,
        *,
        description: str = "",
        action_category: str = "read_data",
        requires_write: bool = False,
    ) -> None:
        """Register a tool handler (delegates to underlying executor)."""
        self.executor.register(
            tool_name,
            handler,
            description=description,
            action_category=action_category,
            requires_write=requires_write,
        )
    
    def get(self, tool_name: str) -> Any | None:
        """Get a tool definition (delegates to underlying executor)."""
        return self.executor.get(tool_name)
    
    @property
    def available_tools(self) -> list[str]:
        """Get list of available tools (delegates to underlying executor)."""
        return self.executor.available_tools
    
    def list_metadata(self) -> list[dict[str, Any]]:
        """List tool metadata (delegates to underlying executor)."""
        return self.executor.list_metadata()
    
    def execute_with_tracking(self, tool_name: str, payload: dict[str, Any] | None = None) -> SamResult:
        """Execute a tool and track through worker lifecycle.
        
        Returns the tool result with worker metadata attached.
        """
        context = WorkerExecutionContext(tool_name, self.monitor)
        context.start(description=f"Executing tool: {tool_name}")
        
        try:
            # Extract request info for observation
            request_info = ""
            if payload and "request" in payload:
                request = payload.get("request")
                if hasattr(request, "raw_text"):
                    request_info = request.raw_text
                else:
                    request_info = str(request)[:100]
            
            if request_info:
                context.observe(f"Request: {request_info}")
            
            # Execute the tool
            context.observe(f"Executing {tool_name}...")
            result = self.executor.execute(tool_name, payload)
            
            # Capture result details
            if isinstance(result, SamResult):
                if result.ok:
                    context.observe(f"Result: {result.summary}")
                    context.succeed(f"Tool succeeded: {result.summary}")
                else:
                    error_detail = result.error_message or result.summary
                    context.observe(f"Error: {error_detail}")
                    context.fail(error_detail)
                
                # Attach worker metadata
                result.metadata.setdefault("worker_task_id", context.task.task_id if context.task else None)
                result.metadata.setdefault("worker_name", context.task.worker_name if context.task else "ToolExecutor")
                result.metadata.setdefault("worker_type", context.task.worker_type if context.task else "executor")
            
            return result
        
        except Exception as e:
            error_msg = f"Execution exception: {str(e)}"
            context.observe(error_msg)
            context.fail(error_msg)
            
            return SamResult(
                status="failed",
                summary=f"Tool execution failed: {tool_name}",
                error_type="EXECUTION_ERROR",
                error_message=error_msg,
                next_action="stop",
                metadata={
                    "tool": tool_name,
                    "worker_task_id": context.task.task_id if context.task else None,
                    "worker_name": context.task.worker_name if context.task else "ToolExecutor",
                    "worker_type": context.task.worker_type if context.task else "executor",
                },
            )


def create_worker_centric_executor(executor: Any, monitor: WorkerMonitor | None = None) -> WorkerCentricExecutor:
    """Factory for creating a worker-centric executor wrapper.
    
    Args:
        executor: ToolExecutor instance to wrap
        monitor: Optional WorkerMonitor instance (defaults to global worker_monitor)
    
    Returns:
        WorkerCentricExecutor with worker lifecycle tracking enabled
    """
    return WorkerCentricExecutor(executor, monitor)
