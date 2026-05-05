from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum

class DriftFinding(BaseModel):
    finding_type: str          # "CALL_REMOVED", "CALL_ADDED", "NEW_SINK_PATH"
    severity: str              # "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"
    source_function: str
    target_function: str
    source_file: str
    target_file: str
    is_critical_sink: bool     # target matches critical sink list
    is_reachable_from_test: bool  # graph path from test file exists
    commit_mentions_removal: bool  # commit message references the change
    has_replacement_path: bool    # new edge to same target in same commit
    message: str    

