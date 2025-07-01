"""
Setup script for testzeus_hercules_tools package.
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="testzeus_hercules_tools",
    version="1.0.0",
    author="TestZeus AI",
    author_email="support@testzeus.com",
    description="Dual-mode browser automation tools for TestZeus Hercules",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/test-zeus-ai/testzeus-hercules",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Testing",
        "Topic :: Internet :: WWW/HTTP :: Browsers",
    ],
    python_requires=">=3.8",
    install_requires=[
        "playwright>=1.40.0",
        "asyncio",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-asyncio>=0.21.0",
            "black>=22.0.0",
            "flake8>=4.0.0",
            "mypy>=0.991",
        ],
    },
    entry_points={
        "console_scripts": [
            "testzeus-tools=testzeus_hercules_tools.cli:main",
        ],
    },
)
