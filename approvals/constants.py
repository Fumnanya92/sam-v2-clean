"""Authority levels and action categories for Sam v2 approvals."""

ACTION_CATEGORIES = [
    "read_data",
    "write_data",
    "delete_data",
    "send_message",
    "send_email",
    "execute_command",
    "install_software",
    "make_payment",
    "modify_settings",
    "spawn_agent",
    "terminate_agent",
    "access_browser",
    "control_app",
]

AUTHORITY_REQUIREMENTS: dict[str, int] = {
    "read_data": 1,
    "spawn_agent": 1,
    "write_data": 3,
    "send_message": 3,
    "execute_command": 5,
    "access_browser": 5,
    "control_app": 5,
    "send_email": 7,
    "install_software": 7,
    "make_payment": 9,
    "modify_settings": 9,
    "delete_data": 9,
    "terminate_agent": 9,
}


def describe_level(level: int) -> str:
    if level <= 2:
        return "Read-only access."
    if level <= 4:
        return "Read/write access."
    if level <= 6:
        return "Execution access."
    if level <= 8:
        return "Advanced operator access."
    return "Full administrative access."
