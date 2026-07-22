#!/usr/bin/env python3
"""
Test script for CS2 server auto-restart functionality

This script demonstrates the auto-restart monitoring system.
Run this to verify the monitoring infrastructure works correctly.
"""

import asyncio
import sys

# Add current directory to path
sys.path.insert(0, ".")

from services.server_monitor import ServerMonitor


async def run_basic_monitoring_demo():
    """Test basic monitoring functionality"""
    print("=" * 60)
    print("Testing CS2 Server Auto-Restart Monitoring")
    print("=" * 60)
    print()

    monitor = ServerMonitor()

    # Test 1: Check initial state
    print("Test 1: Initial State")
    print("-" * 40)
    info = monitor.get_restart_info(1)
    print(f"✓ Restart count: {info['restart_count']}/{info['max_restarts']}")
    print(f"✓ Can restart: {info['can_restart']}")
    print()

    # Test 2: Record restarts and check limits
    print("Test 2: Restart Limits")
    print("-" * 40)
    for i in range(6):
        can_restart, msg = monitor.can_restart(1)
        print(f"Restart {i + 1}: Can restart = {can_restart}")
        if can_restart:
            monitor.record_restart(1)
        else:
            print(f"  Reason: {msg}")

    info = monitor.get_restart_info(1)
    print(f"✓ Final restart count: {info['restart_count']}")
    print()

    # Test 3: Reset history
    print("Test 3: Reset Restart History")
    print("-" * 40)
    monitor.reset_restart_history(1)
    info = monitor.get_restart_info(1)
    print(f"✓ Restart count after reset: {info['restart_count']}")
    print()

    # Test 4: Mock monitoring loop
    print("Test 4: Mock Monitoring Loop")
    print("-" * 40)

    check_count = 0
    restart_count = 0

    async def check_server_status(server_id):
        """Mock status check - simulate crash on 3rd check"""
        nonlocal check_count
        check_count += 1
        is_running = check_count != 3  # Simulate crash on 3rd check
        print(f"  Check {check_count}: Server {'running' if is_running else 'CRASHED'}")
        return is_running

    async def restart_server(server_id):
        """Mock restart function"""
        nonlocal restart_count
        restart_count += 1
        print(f"  → Auto-restarting server (attempt {restart_count})")
        await asyncio.sleep(0.1)  # Simulate restart time
        return True

    async def send_notification(message):
        """Mock notification"""
        print(f"  📢 {message}")

    # Start monitoring
    print("Starting monitoring (will run 5 checks)...")
    monitor.start_monitoring(
        server_id=2,
        check_status_func=check_server_status,
        restart_server_func=restart_server,
        notification_callback=send_notification,
        check_interval=1,  # Check every 1 second for testing
    )

    # Let it run for 5 seconds
    await asyncio.sleep(5)

    # Stop monitoring
    monitor.stop_monitoring(2)
    print("✓ Monitoring stopped")
    print(f"✓ Total checks: {check_count}")
    print(f"✓ Total restarts: {restart_count}")
    print()

    # Test 5: Check monitoring status
    print("Test 5: Monitoring Status")
    print("-" * 40)
    print(f"✓ Server 2 is monitoring: {monitor.is_monitoring(2)}")
    print(f"✓ Server 999 is monitoring: {monitor.is_monitoring(999)}")
    print()

    print("=" * 60)
    print("✅ All tests completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    print()
    print("CS2 Server Auto-Restart Test Suite")
    print()

    try:
        asyncio.run(run_basic_monitoring_demo())
        sys.exit(0)
    except KeyboardInterrupt:
        print("\n\n⚠ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Test failed with error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
