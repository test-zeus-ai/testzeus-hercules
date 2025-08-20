import pytest
from testzeus_hercules.core.validation.response_validator import AgentResponseValidator, ValidationStatus, TaskCompletionStatus

class TestAgentResponseValidation:
    def test_valid_response_allows_termination(self):
        """Test that properly validated responses allow termination"""
        valid_response = """
        previous_step: Login completed successfully
        previous_step_status: success
        task_completion_validation: completed
        verification_result: User successfully logged in, dashboard visible with welcome message
        current_output: Login process completed
        ##FLAG::SAVE_IN_MEM##
        ##TERMINATE TASK##
        """
        
        result = AgentResponseValidator.validate_response(valid_response)
        assert result["is_valid"] == True
        assert result["can_terminate"] == True
        assert len(result["validation_errors"]) == 0
    
    def test_invalid_response_blocks_termination(self):
        """Test that responses with validation failures block termination"""
        invalid_response = """
        previous_step: Login failed
        previous_step_status: failed
        task_completion_validation: failed
        verification_result: Could not locate login button on the page
        current_output: Unable to complete login
        ##TERMINATE TASK##
        """
        
        result = AgentResponseValidator.validate_response(invalid_response)
        assert result["can_terminate"] == True  # Failed tasks can terminate if properly validated
        assert result["is_valid"] == True
    
    def test_missing_fields_blocks_termination(self):
        """Test that missing validation fields block termination"""
        incomplete_response = """
        previous_step: Some task
        current_output: Task completed
        ##TERMINATE TASK##
        """
        
        result = AgentResponseValidator.validate_response(incomplete_response)
        assert result["is_valid"] == False
        assert result["can_terminate"] == False
        assert "Missing required validation fields" in str(result["validation_errors"])
    
    def test_invalid_status_values_block_termination(self):
        """Test that invalid status values block termination"""
        invalid_status_response = """
        previous_step: Some task
        previous_step_status: invalid_status
        task_completion_validation: invalid_completion
        verification_result: Some verification
        current_output: Task completed
        ##TERMINATE TASK##
        """
        
        result = AgentResponseValidator.validate_response(invalid_status_response)
        assert result["is_valid"] == False
        assert result["can_terminate"] == False
        assert "Invalid previous_step_status" in str(result["validation_errors"])
        assert "Invalid task_completion_validation" in str(result["validation_errors"])
    
    def test_insufficient_verification_blocks_termination(self):
        """Test that insufficient verification details block termination"""
        insufficient_verification_response = """
        previous_step: Some task
        previous_step_status: success
        task_completion_validation: completed
        verification_result: Done
        current_output: Task completed
        ##TERMINATE TASK##
        """
        
        result = AgentResponseValidator.validate_response(insufficient_verification_response)
        assert result["is_valid"] == False
        assert result["can_terminate"] == False
        assert "verification_result must contain specific evidence" in str(result["validation_errors"])
    
    def test_failed_previous_step_with_wrong_task_status(self):
        """Test that failed previous step requires failed task status"""
        inconsistent_response = """
        previous_step: Previous task failed
        previous_step_status: failed
        task_completion_validation: completed
        verification_result: Task somehow completed despite previous failure
        current_output: Task completed
        ##TERMINATE TASK##
        """
        
        result = AgentResponseValidator.validate_response(inconsistent_response)
        assert result["is_valid"] == False
        assert result["can_terminate"] == False
        assert "previous step failed, current task must also be marked as failed" in str(result["validation_errors"])
