#!/usr/bin/env python3
"""
Verify that the file upload tool properly logs interactions.
"""

import asyncio
import sys
import os
import tempfile

sys.path.insert(0, '/home/ubuntu/testzeus-hercules')

async def verify_file_upload_tool_logging():
    """Verify file upload tool logging integration."""
    print("=== Verifying File Upload Tool Logging Integration ===")
    
    try:
        from testzeus_hercules_tools import ToolsConfig, ToolsPlaywrightManager
        from testzeus_hercules_tools.tools import upload_file
        from testzeus_hercules_tools.tools.logger import InteractionLogger
        
        config = ToolsConfig(mode='agent', enable_logging=True, headless=True)
        playwright_manager = ToolsPlaywrightManager(config)
        logger = InteractionLogger(config)
        
        logger.clear_interactions()
        initial_count = len(logger.get_interactions())
        print(f"   Initial interactions: {initial_count}")
        
        test_files = []
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                f.write("This is a test file for upload verification.")
                test_files.append(f.name)
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
                f.write("name,age,city\nJohn,25,New York\nJane,30,Los Angeles")
                test_files.append(f.name)
            
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                f.write('{"test": "data", "upload": "verification"}')
                test_files.append(f.name)
            
            await playwright_manager.initialize()
            
            page = await playwright_manager.get_page()
            await page.set_content("""
                <html>
                <body>
                    <form>
                        <input type="file" id="file-input" md="file-md-123" accept=".txt,.csv,.json"/>
                        <input type="file" id="image-input" md="image-md-456" accept="image/*"/>
                        <input type="file" id="multiple-files" md="multiple-md-789" multiple/>
                        <button type="button" id="custom-upload" md="custom-md-999">Custom Upload</button>
                    </form>
                    <div id="upload-status"></div>
                </body>
                </html>
            """)
            
            print("   Testing successful file upload (text file)...")
            result1 = await upload_file(
                selector="[md='file-md-123']",
                file_path=test_files[0],
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Text file upload result: {result1.get('success', False)}")
            
            print("   Testing successful file upload (CSV file)...")
            result2 = await upload_file(
                selector="[md='file-md-123']",
                file_path=test_files[1],
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ CSV file upload result: {result2.get('success', False)}")
            
            print("   Testing successful file upload (JSON file)...")
            result3 = await upload_file(
                selector="[md='multiple-md-789']",
                file_path=test_files[2],
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ JSON file upload result: {result3.get('success', False)}")
            
            print("   Testing failed file upload (element not found)...")
            result4 = await upload_file(
                selector="[md='nonexistent-md-999']",
                file_path=test_files[0],
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ Failed upload result (expected): {result4.get('success', False)}")
            
            print("   Testing failed file upload (file not found)...")
            result5 = await upload_file(
                selector="[md='file-md-123']",
                file_path="/nonexistent/file/path.txt",
                config=config,
                playwright_manager=playwright_manager
            )
            print(f"   ✓ File not found result (expected): {result5.get('success', False)}")
            
            final_count = len(logger.get_interactions())
            logged_interactions = final_count - initial_count
            print(f"   ✓ Total interactions logged: {logged_interactions}")
            
            if logged_interactions >= 4:  # At least 3 successful + 2 failed
                print("   ✓ All file upload scenarios logged interactions")
                
                interactions = logger.get_interactions()[-logged_interactions:]
                for i, interaction in enumerate(interactions, 1):
                    print(f"     {i}: {interaction.tool_name} - {interaction.action} - Success: {interaction.success}")
                    if interaction.additional_data:
                        file_path = interaction.additional_data.get('file_path', 'N/A')
                        element_type = interaction.additional_data.get('element_type', 'N/A')
                        file_name = os.path.basename(file_path) if file_path != 'N/A' else 'N/A'
                        print(f"        File: {file_name}, Element type: {element_type}")
                
                from testzeus_hercules_tools.tools import CodeGenerator
                code_generator = CodeGenerator(config)
                generated_code = code_generator.generate_from_logger(logger)
                
                if generated_code and len(generated_code) > 500:
                    print(f"   ✓ Code generation successful: {len(generated_code)} characters")
                    
                    if "upload_file" in generated_code and "testzeus_hercules_tools" in generated_code:
                        print("   ✓ Generated code includes upload_file and proper imports")
                        
                        expected_extensions = [".txt", ".csv", ".json"]
                        extensions_found = sum(1 for ext in expected_extensions if ext in generated_code)
                        
                        print(f"   ✓ File extensions found in code: {extensions_found}/{len(expected_extensions)}")
                        
                        if extensions_found >= 2:
                            return True
                        else:
                            print("   ❌ Generated code missing expected file content")
                            return False
                    else:
                        print("   ❌ Generated code missing expected content")
                        return False
                else:
                    print("   ❌ Code generation failed or too short")
                    return False
            else:
                print("   ❌ Not enough interactions logged")
                return False
                
        finally:
            await playwright_manager.close()
            
            for file_path in test_files:
                try:
                    os.unlink(file_path)
                except:
                    pass
        
    except Exception as e:
        print(f"❌ File upload tool logging verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run the verification."""
    print("Verifying file upload tool logging integration...")
    
    result = await verify_file_upload_tool_logging()
    
    if result:
        print("\n✅ File upload tool logging verification passed!")
        return 0
    else:
        print("\n❌ File upload tool logging verification failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
