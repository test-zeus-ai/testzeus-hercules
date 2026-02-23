"""
Security patch for NLTK downloader to prevent zip slip attacks.

This module patches the vulnerable _unzip_iter function in nltk.downloader to:
1. Validate all paths before extraction to prevent zip slip attacks
2. Ensure extracted paths don't escape the target directory
3. Use secure extraction methods that validate each file path

CVE: Critical vulnerability in NLTK <=3.9.2 where zipfile.extractall() is used
without path validation, allowing arbitrary code execution via malicious zip packages.
"""

import os
import sys
from pathlib import Path
from typing import Any, Iterator, Optional
from zipfile import ZipFile, ZipInfo


def _is_safe_path(target_dir: Path, file_path: str) -> bool:
    """
    Validate that a file path is safe to extract (prevents zip slip attacks).
    
    This function prevents zip slip attacks by ensuring that:
    1. The normalized path doesn't contain '..' sequences that escape the target
    2. The resolved absolute path is within the target directory
    3. The path doesn't use absolute paths or drive letters to escape
    
    Args:
        target_dir: The target directory where files should be extracted
        file_path: The path from the zip file entry
        
    Returns:
        True if the path is safe, False otherwise
    """
    # Normalize the file path - remove any leading slashes or drive letters
    normalized_path = file_path.lstrip('/').lstrip('\\')
    
    # Check for path traversal sequences - check if '..' appears as a path component
    # Split by common path separators and check each component
    path_parts = normalized_path.replace('\\', '/').split('/')
    if '..' in path_parts or normalized_path.startswith('~'):
        return False
    
    # Check for absolute paths (Windows drive letters like C:)
    if len(normalized_path) > 1 and normalized_path[1] == ':':
        return False
    
    # Resolve the target directory to an absolute path
    try:
        target_dir = Path(target_dir).resolve()
    except (OSError, ValueError):
        return False
    
    # Join target_dir with the normalized file_path and resolve
    try:
        full_path = (target_dir / normalized_path).resolve()
        
        # Check if the resolved path is still within the target directory
        # Use os.path.commonpath to ensure the path is within target_dir
        target_str = str(target_dir)
        full_str = str(full_path)
        
        # On Windows, ensure case-insensitive comparison
        if os.name == 'nt':
            target_str = target_str.lower()
            full_str = full_str.lower()
        
        # Verify the full path starts with the target directory path
        if not full_str.startswith(target_str):
            return False
        
        # Additional check: ensure they share a common path
        common_path = os.path.commonpath([target_str, full_str])
        return common_path == target_str
    except (ValueError, OSError):
        # If paths are on different drives (Windows) or other errors occur
        return False


def _secure_extract_all(zip_file: ZipFile, target_dir: Path) -> None:
    """
    Securely extract all files from a zip archive with path validation.
    
    This function replaces the vulnerable zipfile.extractall() by:
    1. Iterating through each file in the zip
    2. Validating each path before extraction
    3. Skipping unsafe paths and logging warnings
    4. Extracting only validated files
    
    Args:
        zip_file: The ZipFile object to extract from
        target_dir: The target directory for extraction
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    
    unsafe_paths = []
    
    for member in zip_file.namelist():
        # Skip directories (they end with '/')
        if member.endswith('/'):
            continue
            
        # Validate the path
        if not _is_safe_path(target_dir, member):
            unsafe_paths.append(member)
            continue
        
        # Extract the file safely
        try:
            zip_file.extract(member, target_dir)
        except Exception as e:
            # Log extraction errors but continue with other files
            print(f"⚠️ Warning: Failed to extract {member}: {e}", file=sys.stderr)
    
    if unsafe_paths:
        error_msg = (
            f"🚨 Security: Blocked {len(unsafe_paths)} unsafe path(s) in zip archive:\n"
            + "\n".join(f"  - {path}" for path in unsafe_paths[:10])
        )
        if len(unsafe_paths) > 10:
            error_msg += f"\n  ... and {len(unsafe_paths) - 10} more"
        print(error_msg, file=sys.stderr)
        raise ValueError(
            f"Zip archive contains unsafe paths that would escape the target directory. "
            f"Blocked {len(unsafe_paths)} file(s). This may indicate a malicious package."
        )


def patch_nltk_downloader() -> None:
    """
    Patch NLTK's downloader module to use secure zip extraction.
    
    This function patches the _unzip_iter function in nltk.downloader to use
    secure extraction methods that validate paths before extraction.
    
    Should be called early in the application lifecycle, before NLTK is used.
    """
    try:
        import nltk.downloader
    except ImportError:
        # NLTK not installed, nothing to patch
        return
    
    # Check if already patched
    if hasattr(nltk.downloader, '_nltk_security_patched'):
        return
    
    # Store reference to original _unzip_iter if it exists
    if hasattr(nltk.downloader, '_unzip_iter'):
        original_unzip_iter = nltk.downloader._unzip_iter
        
        def patched_unzip_iter(
            zip_file: ZipFile,
            target_dir: Path,
            verbose: bool = False,
        ) -> Iterator[Optional[ZipInfo]]:
            """
            Patched version of _unzip_iter that validates paths before extraction.
            
            This prevents zip slip attacks by ensuring all extracted files
            remain within the target directory.
            """
            # Use our secure extraction function
            _secure_extract_all(zip_file, target_dir)
            
            # Yield ZipInfo objects for each extracted file (matching original behavior)
            for member in zip_file.namelist():
                if not member.endswith('/'):
                    try:
                        yield zip_file.getinfo(member)
                    except KeyError:
                        yield None
        
        # Replace the vulnerable function
        nltk.downloader._unzip_iter = patched_unzip_iter
        
        # Mark as patched
        nltk.downloader._nltk_security_patched = True
        
        print("✅ Applied security patch to NLTK downloader (zip slip protection)", file=sys.stderr)
    else:
        # If _unzip_iter doesn't exist, try to patch extractall usage directly
        # This is a fallback for different NLTK versions
        print(
            "⚠️ Warning: NLTK _unzip_iter not found. "
            "The vulnerability may still exist if NLTK uses extractall() directly.",
            file=sys.stderr
        )


def install_nltk_security_patcher() -> None:
    """
    Install the NLTK security patcher.
    
    This should be called early in the application lifecycle, ideally before
    any NLTK modules are imported. It's safe to call multiple times.
    
    The patcher uses an import hook to automatically patch nltk.downloader
    when it's imported, ensuring the fix is applied regardless of import order.
    """
    # Check if already installed
    if hasattr(sys, '_nltk_security_patcher_installed'):
        return
    
    # Try to patch immediately if NLTK is already imported
    patch_nltk_downloader()
    
    # Install import hook to patch when nltk.downloader is imported later
    from importlib.abc import MetaPathFinder
    from importlib.machinery import ModuleSpec
    
    class NLTKSecurityPatcher(MetaPathFinder):
        """Import hook to patch nltk.downloader when it's imported."""
        
        def find_spec(self, fullname: str, path: Any, target: Any = None) -> Optional[ModuleSpec]:
            """Intercept imports of nltk.downloader to patch it."""
            if fullname == 'nltk.downloader':
                # Find the original module spec
                for finder in sys.meta_path:
                    if finder is self:
                        continue
                    if hasattr(finder, 'find_spec'):
                        spec = finder.find_spec(fullname, path, target)
                        if spec is not None:
                            # Patch after the module is loaded
                            original_loader = spec.loader
                            if original_loader:
                                from importlib.abc import Loader
                                
                                class PatchingLoader(Loader):
                                    """Loader that patches the module after loading."""
                                    
                                    def __init__(self, original_loader: Any):
                                        self.original_loader = original_loader
                                    
                                    def create_module(self, spec: ModuleSpec) -> Any:
                                        if hasattr(self.original_loader, 'create_module'):
                                            return self.original_loader.create_module(spec)
                                        return None
                                    
                                    def exec_module(self, module: Any) -> None:
                                        """Execute the module and then patch it."""
                                        if hasattr(self.original_loader, 'exec_module'):
                                            self.original_loader.exec_module(module)
                                        # Patch immediately after loading
                                        patch_nltk_downloader()
                                
                                spec.loader = PatchingLoader(original_loader)
                            return spec
            return None
    
    # Install the import hook
    sys.meta_path.insert(0, NLTKSecurityPatcher())
    sys._nltk_security_patcher_installed = True
