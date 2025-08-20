from typing import Dict, Any, Optional, List
from enum import Enum
import re
from testzeus_hercules.utils.logger import logger
from testzeus_hercules.core.validation.constants import (
    PREVIOUS_STEP_STATUS, TASK_COMPLETION_VALIDATION, VERIFICATION_RESULT,
    VALIDATION_SUCCESS, VALIDATION_FAILED, VALIDATION_INCOMPLETE, VALIDATION_PENDING,
    TASK_COMPLETED, TASK_PARTIAL, TASK_FAILED, TASK_NOT_STARTED
)

class ValidationStatus(Enum):
    SUCCESS = VALIDATION_SUCCESS
    FAILED = VALIDATION_FAILED
    INCOMPLETE = VALIDATION_INCOMPLETE
    PENDING = VALIDATION_PENDING

class TaskCompletionStatus(Enum):
    COMPLETED = TASK_COMPLETED
    PARTIAL = TASK_PARTIAL
    FAILED = TASK_FAILED
    NOT_STARTED = TASK_NOT_STARTED

class AgentResponseValidator:
    """Validates agent responses for proper boundary enforcement"""
    
    REQUIRED_FIELDS = [
        PREVIOUS_STEP_STATUS,
        TASK_COMPLETION_VALIDATION,
        VERIFICATION_RESULT
    ]
    
    VALID_PREVIOUS_STEP_STATUS = [
        VALIDATION_SUCCESS, VALIDATION_FAILED, VALIDATION_INCOMPLETE, VALIDATION_PENDING
    ]
    
    VALID_TASK_COMPLETION_STATUS = [
        TASK_COMPLETED, TASK_PARTIAL, TASK_FAILED, TASK_NOT_STARTED
    ]
    
    @classmethod
    def validate_response(cls, response: str) -> Dict[str, Any]:
        """Validate agent response format and extract validation fields"""
        validation_result = {
            "is_valid": False,
            "can_terminate": False,
            "validation_errors": [],
            "parsed_fields": {}
        }
        
        try:
            parsed_fields = cls._extract_validation_fields(response)
            validation_result["parsed_fields"] = parsed_fields
            
            missing_fields = []
            for field in cls.REQUIRED_FIELDS:
                if field not in parsed_fields or not parsed_fields[field].strip():
                    missing_fields.append(field)
            
            if missing_fields:
                validation_result["validation_errors"].append(
                    f"Missing required validation fields: {missing_fields}"
                )
                return validation_result
            
            prev_status = parsed_fields.get(PREVIOUS_STEP_STATUS, "").strip().lower()
            if prev_status not in cls.VALID_PREVIOUS_STEP_STATUS:
                validation_result["validation_errors"].append(
                    f"Invalid previous_step_status: {prev_status}. Must be one of: {cls.VALID_PREVIOUS_STEP_STATUS}"
                )
            
            task_status = parsed_fields.get(TASK_COMPLETION_VALIDATION, "").strip().lower()
            if task_status not in cls.VALID_TASK_COMPLETION_STATUS:
                validation_result["validation_errors"].append(
                    f"Invalid task_completion_validation: {task_status}. Must be one of: {cls.VALID_TASK_COMPLETION_STATUS}"
                )
            
            verification = parsed_fields.get(VERIFICATION_RESULT, "").strip()
            if len(verification) < 10:
                validation_result["validation_errors"].append(
                    "verification_result must contain specific evidence of task completion"
                )
            
            if not validation_result["validation_errors"]:
                validation_result["is_valid"] = True
                
                
                can_terminate = (
                    prev_status in [VALIDATION_SUCCESS, VALIDATION_FAILED] and
                    task_status in [TASK_COMPLETED, TASK_FAILED] and
                    len(verification) >= 10
                )
                
                if prev_status == VALIDATION_FAILED and task_status != TASK_FAILED:
                    validation_result["validation_errors"].append(
                        "If previous step failed, current task must also be marked as failed"
                    )
                    can_terminate = False
                
                validation_result["can_terminate"] = can_terminate
                
                if not can_terminate and not validation_result["validation_errors"]:
                    validation_result["validation_errors"].append(
                        "Task completion validation requirements not met for termination"
                    )
            
        except Exception as e:
            validation_result["validation_errors"].append(f"Validation parsing error: {e}")
            logger.error(f"Error validating agent response: {e}")
        
        return validation_result
    
    @classmethod
    def _extract_validation_fields(cls, response: str) -> Dict[str, str]:
        """Extract validation fields from agent response text"""
        fields = {}
        
        match = re.search(r'previous_step_status:\s*([^\n\r]+)', response, re.IGNORECASE)
        if match:
            fields[PREVIOUS_STEP_STATUS] = match.group(1).strip()
        
        match = re.search(r'task_completion_validation:\s*([^\n\r]+)', response, re.IGNORECASE)
        if match:
            fields[TASK_COMPLETION_VALIDATION] = match.group(1).strip()
        
        match = re.search(r'verification_result:\s*([^\n\r]+)', response, re.IGNORECASE)
        if match:
            fields[VERIFICATION_RESULT] = match.group(1).strip()
        
        return fields
    
    @classmethod
    def format_validation_error(cls, validation_result: Dict[str, Any]) -> str:
        """Format validation errors into a user-friendly message"""
        if validation_result["is_valid"]:
            return ""
        
        errors = validation_result["validation_errors"]
        error_msg = "Task termination blocked due to validation failures:\n"
        for i, error in enumerate(errors, 1):
            error_msg += f"{i}. {error}\n"
        
        error_msg += "\nPlease ensure your response includes:\n"
        error_msg += "- previous_step_status: success|failed|incomplete|pending\n"
        error_msg += "- task_completion_validation: completed|partial|failed|not_started\n"
        error_msg += "- verification_result: [specific evidence of task completion]\n"
        
        return error_msg
