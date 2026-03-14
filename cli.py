#!/usr/bin/env python3
import argparse
import sys
from lib.core import AppController
from lib.tui.menu import InteractiveMenu
from lib.utils import print_info

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
        menu = InteractiveMenu(controller)
        menu.run()
        return

    # 2. CLI Mode
    if args.command == "list":
        for app in controller.list_apps():
            print(f"- {app['name']} ({app['type']})")
        return

    if args.command == "status":
        # Simple CLI status
        for app in controller.list_apps():
            status = controller.get_app_status(app['name'])
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

    for app_name in target_apps:
        if args.command == "start":
            controller.start_app(app_name)
        elif args.command == "stop":
            controller.stop_app(app_name)
        elif args.command == "restart":
            controller.stop_app(app_name)
            import time; time.sleep(1)
            controller.start_app(app_name)

if __name__ == "__main__":
    main()
