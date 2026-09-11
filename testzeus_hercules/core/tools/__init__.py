import importlib
import os
import pkgutil
import sys
from pathlib import Path

# Get the current directory path
package_path = Path(__file__).parent

# Sandbox child processes import one tool module directly; eagerly pkgutil-loading
# the whole package there costs ~1.4s of pure startup per execute_python_sandbox call.
if os.getenv("HERCULES_SANDBOX_CHILD") != "1":
    # Dynamically import all modules
    for _, module_name, _ in pkgutil.iter_modules([str(package_path)]):
        # Construct the full module path
        full_module_name = f"testzeus_hercules.core.tools.{module_name}"
        # Import the module
        module = importlib.import_module(full_module_name)
        # Add all objects from the module to the current namespace
        for attribute_name in dir(module):
            # Skip private attributes
            if not attribute_name.startswith("_"):
                globals()[attribute_name] = getattr(module, attribute_name)
