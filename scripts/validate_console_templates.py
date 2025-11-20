#!/usr/bin/env python3
"""
Template Validation Script
Validates that all console templates are syntactically correct
"""

import os
import sys
from pathlib import Path

def validate_html_templates():
    """Validate HTML templates for basic syntax"""
    templates_dir = Path(__file__).parent.parent / "templates"
    console_templates = [
        "ssh_console.html",
        "game_console.html", 
        "console_popup.html"
    ]
    
    errors = []
    
    for template_name in console_templates:
        template_path = templates_dir / template_name
        
        if not template_path.exists():
            errors.append(f"❌ Template not found: {template_name}")
            continue
        
        with open(template_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Basic validation checks
        checks = {
            "Has <!DOCTYPE html>": content.strip().startswith('<!DOCTYPE html>'),
            "Has closing </html>": '</html>' in content,
            "Has closing </body>": '</body>' in content,
            "Has closing </head>": '</head>' in content,
            "Has xterm.js import": '/static/xterm/xterm.js' in content,
            "Has xterm.css import": '/static/xterm/xterm.css' in content,
            "Has server_id variable": '{{ server_id }}' in content,
            "Has WebSocket code": 'WebSocket' in content and ("'wss:'" in content or "'ws:'" in content),
            "Has xterm Terminal": 'new Terminal(' in content,
            "Has fit addon": 'FitAddon' in content,
        }
        
        print(f"\n✓ Validating {template_name}...")
        
        failed_checks = []
        for check_name, passed in checks.items():
            if not passed:
                failed_checks.append(check_name)
        
        if failed_checks:
            errors.append(f"❌ {template_name}: Failed checks - {', '.join(failed_checks)}")
        else:
            print(f"  ✓ All checks passed for {template_name}")
    
    return errors

def validate_static_files():
    """Validate xterm.js static files exist"""
    static_dir = Path(__file__).parent.parent / "static" / "xterm"
    required_files = [
        "xterm.js",
        "xterm.css",
        "xterm-addon-fit.js",
        "xterm-addon-web-links.js"
    ]
    
    errors = []
    
    print("\n✓ Validating static files...")
    
    if not static_dir.exists():
        errors.append(f"❌ Static directory not found: {static_dir}")
        return errors
    
    for filename in required_files:
        filepath = static_dir / filename
        if not filepath.exists():
            errors.append(f"❌ Missing file: static/xterm/{filename}")
        else:
            size = filepath.stat().st_size
            print(f"  ✓ {filename} ({size:,} bytes)")
    
    return errors

def validate_routes():
    """Validate routes are defined in main.py"""
    main_py = Path(__file__).parent.parent / "main.py"
    
    errors = []
    
    print("\n✓ Validating routes...")
    
    if not main_py.exists():
        errors.append("❌ main.py not found")
        return errors
    
    with open(main_py, 'r', encoding='utf-8') as f:
        content = f.read()
    
    required_routes = {
        "ssh-console": 'ssh_console.html',
        "game-console": 'game_console.html',
        "console-popup": 'console_popup.html'
    }
    
    for route, template in required_routes.items():
        if route in content and template in content:
            print(f"  ✓ Route exists: /servers/{{id}}/{route} -> {template}")
        else:
            errors.append(f"❌ Route not found or not using correct template: {route}")
    
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
        print("❌ VALIDATION FAILED")
        print("=" * 60)
        for error in all_errors:
            print(error)
        sys.exit(1)
    else:
        print("✅ ALL VALIDATIONS PASSED")
        print("=" * 60)
        print("\nWebSSH console templates are ready to use!")
        print("\nAccess URLs:")
        print("  - SSH Console:  /servers/{id}/ssh-console")
        print("  - Game Console: /servers/{id}/game-console")
        print("  - Popup (compat): /servers/{id}/console-popup/ssh")
        sys.exit(0)

if __name__ == "__main__":
    main()
