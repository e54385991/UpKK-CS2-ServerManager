#!/usr/bin/env python3
"""
Template Validation Script
Validates that all console templates are syntactically correct
"""

import sys
from pathlib import Path

from dotenv import load_dotenv


def validate_html_templates():
    """Validate HTML templates for basic syntax"""
    templates_dir = Path(__file__).parent.parent / "templates"
    console_templates = ["ssh_console.html", "game_console.html", "console_popup.html"]

    errors = []

    for template_name in console_templates:
        template_path = templates_dir / template_name

        if not template_path.exists():
            errors.append(f"FAIL: Template not found: {template_name}")
            continue

        with open(template_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Basic validation checks
        checks = {
            "Has <!DOCTYPE html>": content.strip().startswith("<!DOCTYPE html>"),
            "Has closing </html>": "</html>" in content,
            "Has closing </body>": "</body>" in content,
            "Has closing </head>": "</head>" in content,
            "Has xterm.js import": "/static/xterm/xterm.js" in content
            or "static_url('xterm/xterm.js')" in content,
            "Has xterm.css import": "/static/xterm/xterm.css" in content
            or "static_url('xterm/xterm.css')" in content,
            "Has server_id variable": "{{ server_id }}" in content,
            "Has WebSocket code": "WebSocket" in content
            and ("'wss:'" in content or "'ws:'" in content),
            "Has xterm Terminal": "new Terminal(" in content,
            "Has fit addon": "FitAddon" in content,
        }

        print(f"\nPASS: Validating {template_name}...")

        failed_checks = []
        for check_name, passed in checks.items():
            if not passed:
                failed_checks.append(check_name)

        if failed_checks:
            errors.append(f"FAIL: {template_name}: Failed checks - {', '.join(failed_checks)}")
        else:
            print(f"  PASS: All checks passed for {template_name}")

    return errors


def validate_static_files():
    """Validate xterm.js static files exist"""
    static_dir = Path(__file__).parent.parent / "static" / "xterm"
    required_files = ["xterm.js", "xterm.css", "xterm-addon-fit.js", "xterm-addon-web-links.js"]

    errors = []

    print("\nPASS: Validating static files...")

    if not static_dir.exists():
        errors.append(f"FAIL: Static directory not found: {static_dir}")
        return errors

    for filename in required_files:
        filepath = static_dir / filename
        if not filepath.exists():
            errors.append(f"FAIL: Missing file: static/xterm/{filename}")
        else:
            size = filepath.stat().st_size
            print(f"  PASS: {filename} ({size:,} bytes)")

    return errors


def validate_routes():
    """Validate routes through the assembled FastAPI application."""
    errors = []
    print("\nPASS: Validating routes...")

    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))
    load_dotenv(project_root / ".env.example", override=False)
    from api.application import create_app

    paths = create_app(lifespan=None).openapi()["paths"]
    required_routes = {
        "/servers/{server_id}/ssh-console": "ssh_console.html",
        "/servers/{server_id}/game-console": "game_console.html",
        "/servers/{server_id}/console-popup/{console_type}": "console_popup.html",
    }

    for route, template in required_routes.items():
        template_path = project_root / "templates" / template
        if route in paths and template_path.is_file():
            print(f"  PASS: Route exists: {route} -> {template}")
        else:
            errors.append(f"FAIL: Route not found or not using correct template: {route}")

    return errors


def main():
    """Run all validations"""
    print("=" * 60)
    print("WebSSH Console Template Validation")
    print("=" * 60)

    all_errors = []

    # Run validations
    all_errors.extend(validate_html_templates())
    all_errors.extend(validate_static_files())
    all_errors.extend(validate_routes())

    # Print results
    print("\n" + "=" * 60)
    if all_errors:
        print("VALIDATION FAILED")
        print("=" * 60)
        for error in all_errors:
            print(error)
        sys.exit(1)
    else:
        print("ALL VALIDATIONS PASSED")
        print("=" * 60)
        print("\nWebSSH console templates are ready to use!")
        print("\nAccess URLs:")
        print("  - SSH Console:  /servers/{id}/ssh-console")
        print("  - Game Console: /servers/{id}/game-console")
        print("  - Popup (compat): /servers/{id}/console-popup/ssh")
        sys.exit(0)


if __name__ == "__main__":
    main()
