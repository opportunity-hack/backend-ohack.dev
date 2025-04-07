# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands
- Install dependencies: `pip install -r requirements.txt`
- Run the app: `flask run`
- Run tests: `pytest`
- Run a single test: `pytest path/to/test_file.py::test_function_name`
- Run linting: `pylint api/ common/ db/ model/ services/`

## Code Style Guidelines
- Python 3.9.13 (Flask backend)
- Imports: Group standard library imports first, then third-party, then local imports
- Types: Use type hints (from typing import *) for function parameters and return values
- Naming: Use snake_case for variables/functions, PascalCase for classes
- Error handling: Use try/except blocks with specific exceptions
- Docstrings: Not required for all functions, but recommended for complex logic
- Linting: pylint with customized rules (.pylintrc disables missing-module-docstring, missing-function-docstring, too-few-public-methods)
- Testing: pytest is used for tests