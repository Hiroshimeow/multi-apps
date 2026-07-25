#!/usr/bin/env python3
import argparse
import sys
from lib.core import AppController
from lib.utils import is_windows, print_error

def main():
    parser = argparse.ArgumentParser(description="Multi-Run Apps Manager")
    parser.add_argument("command", nargs="?", choices=["start", "stop", "restart", "status", "list"], help="Command to execute")
    parser.add_argument("apps", nargs="*", help="App names to apply command to")
    parser.add_argument("--all", "-a", action="store_true", help="Apply to all enabled apps")
    parser.add_argument("--config", "-c", help="Path to config file")
    
    args = parser.parse_args()
    
    controller = AppController(args.config)
    
    # 1. Interactive Mode
    if not args.command:
        if is_windows():
            print_error("Interactive TUI mode is only supported on Linux terminals.")
            sys.exit(1)
        from lib.tui.menu import InteractiveMenu
        menu = InteractiveMenu(controller)
        menu.run()
        return

    # 2. CLI Mode
    if args.command == "list":
        for app in controller.list_apps():
            print(f"- {app['name']}: {app['command']}")
        return

    if args.command == "status":
        apps = controller.list_apps()
        app_ids = tuple(str(app.get("id") or app["name"]) for app in apps)
        snapshot = controller.get_status_snapshot(app_ids)
        for app, app_id in zip(apps, app_ids):
            status = snapshot.get(app_id, {"status": "STOPPED", "instances": 0})
            print(f"{app['name']}: {status.get('status', 'UNKNOWN')} {status.get('session', '')}")
        return

    # Action Commands
    target_apps = []
    if args.all:
        target_apps = [app['name'] for app in controller.list_apps() if app['enabled']]
    elif args.apps:
        target_apps = args.apps
    else:
        print("Please specify app names or use --all")
        sys.exit(1)

    action_failed = False
    for app_name in target_apps:
        if args.command == "start":
            if not controller.start_app(app_name, wait_for_ready=True):
                action_failed = True
        elif args.command == "stop":
            if not controller.stop_app(app_name):
                action_failed = True
        elif args.command == "restart":
            stopped = controller.stop_app(app_name)
            import time

            time.sleep(1)
            started = controller.start_app(app_name, wait_for_ready=True)
            if not stopped or not started:
                action_failed = True

    if action_failed:
        sys.exit(1)

if __name__ == "__main__":
    main()
