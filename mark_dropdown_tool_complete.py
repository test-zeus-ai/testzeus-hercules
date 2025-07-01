#!/usr/bin/env python3
"""
Mark dropdown tool as completed in the todo list and get next tool.
"""

def mark_dropdown_tool_complete():
    """Mark dropdown.py as completed in the todo list."""
    
    todo_file = "/home/ubuntu/testzeus-hercules/logging_verification_todo.txt"
    
    try:
        with open(todo_file, 'r') as f:
            content = f.read()
        
        updated_content = content.replace(
            "- [ ] dropdown.py - verify dropdown selection logging",
            "- [x] dropdown.py - VERIFIED: dropdown selection logging works correctly"
        )
        
        with open(todo_file, 'w') as f:
            f.write(updated_content)
        
        print("✅ Updated todo list - marked dropdown.py as verified")
        
        lines = updated_content.split('\n')
        remaining_tools = []
        
        for line in lines:
            if line.strip().startswith("- [ ]") and any(keyword in line.lower() for keyword in ['tools', 'operations', '.py']):
                tool_info = line.strip().replace("- [ ] ", "")
                remaining_tools.append(tool_info)
        
        print(f"📋 Remaining tools to verify: {len(remaining_tools)}")
        
        if remaining_tools:
            next_tool = remaining_tools[0]
            print(f"📌 Next tool to verify: {next_tool}")
            return next_tool
        else:
            print("🎉 All tools have been verified!")
            return None
        
    except Exception as e:
        print(f"❌ Failed to update todo list: {e}")
        return None

if __name__ == "__main__":
    next_tool = mark_dropdown_tool_complete()
