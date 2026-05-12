"""Shared library for JSON processing

Provides unified JSON loading, saving and manipulation utilities.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


def load_json(file_path: Path) -> Union[Dict, List]:
    """Load JSON file

    Args:
        file_path: JSON file path

    Returns:
        Parsed JSON data

    Raises:
        FileNotFoundError: File not found
        json.JSONDecodeError: JSON format error
    """
    if not file_path.exists():
        raise FileNotFoundError(f"JSON file not found: {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(
    file_path: Path,
    data: Union[Dict, List],
    indent: Optional[int] = None,
    ensure_ascii: bool = False
) -> None:
    """Save JSON file

    Args:
        file_path: Output file path
        data: Data to save
        indent: Indentation spaces (None for compact format)
        ensure_ascii: Whether to ensure ASCII encoding
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with file_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
        f.write("\n")


def load_json_safe(file_path: Path, default: Any = None) -> Any:
    """Safely load JSON file, return default on error

    Args:
        file_path: JSON file path
        default: Default value on error

    Returns:
        Parsed JSON data or default
    """
    try:
        return load_json(file_path)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json_pretty(file_path: Path, data: Union[Dict, List]) -> None:
    """Save formatted JSON file (with indentation)

    Args:
        file_path: Output file path
        data: Data to save
    """
    save_json(file_path, data, indent=2)


def load_json_compact(file_path: Path) -> str:
    """Load JSON and return compact format string

    Args:
        file_path: JSON file path

    Returns:
        Compact format JSON string
    """
    with file_path.open("r", encoding="utf-8") as f:
        return f.read()


def update_json_field(
    file_path: Path,
    field_path: List[str],
    value: Any,
    default: Optional[Union[Dict, List]] = None
) -> None:
    """Update field in JSON file

    Args:
        file_path: JSON file path
        field_path: Field path (e.g., ["tracks", "track1", "status"])
        value: New value
        default: Default data structure if file doesn't exist
    """
    if file_path.exists():
        data = load_json(file_path)
    else:
        data = default or {}

    # Create nested dictionary structure
    current = data
    for key in field_path[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]

    # Set final value
    current[field_path[-1]] = value

    # Save
    save_json_pretty(file_path, data)


def get_nested_value(data: Dict, field_path: List[str], default: Any = None) -> Any:
    """Get value from nested dictionary

    Args:
        data: Dictionary data
        field_path: Field path
        default: Default value

    Returns:
        Found value or default
    """
    current = data
    for key in field_path:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return current


def filter_json_keys(data: Dict, keep_keys: List[str]) -> Dict:
    """Filter dictionary, keeping only specified keys

    Args:
        data: Original dictionary
        keep_keys: Keys to keep

    Returns:
        Filtered dictionary
    """
    return {k: v for k, v in data.items() if k in keep_keys}


def json_to_string(data: Any, indent: Optional[int] = None) -> str:
    """Convert object to JSON string

    Args:
        data: Object to convert
        indent: Indentation spaces

    Returns:
        JSON string
    """
    return json.dumps(data, indent=indent, ensure_ascii=False)


def string_to_json(json_str: str) -> Union[Dict, List]:
    """Convert JSON string to object

    Args:
        json_str: JSON string

    Returns:
        Parsed object
    """
    return json.loads(json_str)


def merge_json(base: Dict, update: Dict) -> Dict:
    """Recursively merge two dictionaries

    Args:
        base: Base dictionary
        update: Update dictionary

    Returns:
        Merged dictionary
    """
    result = base.copy()
    for key, value in update.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_json(result[key], value)
        else:
            result[key] = value
    return result


# Convenience functions for common JSON file operations
def ensure_json_file(file_path: Path, default_data: Union[Dict, List]) -> Union[Dict, List]:
    """Ensure JSON file exists, create with default if not

    Args:
        file_path: JSON file path
        default_data: Default data

    Returns:
        JSON data
    """
    if not file_path.exists():
        save_json_pretty(file_path, default_data)
        return default_data
    else:
        return load_json(file_path)